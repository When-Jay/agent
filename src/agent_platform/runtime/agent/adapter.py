"""DeepAgentsRuntimeAdapter: the platform Agent Runtime entry point.

Per ADR-0002 the platform does not implement its own Agent Loop. This
adapter loads the Run from Runtime Core, assembles a DeepAgents agent
(model, tools, middleware, backend, checkpointer), delegates execution
to `create_deep_agent()` / LangGraph, and maps the result back to
platform Run state, events and checkpoints.

Runtime Control (budget-steering-spec.md): the adapter wires the
Steering/Budget middleware stack and acts as the loop-level watchdog —
`asyncio.wait_for` enforces the budget's max_time hard cap so hung
model/tool calls cannot stall a run indefinitely (middleware cannot
observe a hang; spec section 26).
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from agent_platform.errors import InvalidStateTransitionError
from agent_platform.runtime.agent.ask_user import AskUserMiddleware
from agent_platform.runtime.agent.backend import PlatformSandboxBackend
from agent_platform.runtime.agent.middleware import (
    BudgetExceededError,
    BudgetMiddleware,
    HumanApprovalMiddleware,
    RuntimeEventMiddleware,
    SteeringMiddleware,
    ToolPermissionMiddleware,
)
from agent_platform.runtime.agent.tools import LangChainToolAdapter
from agent_platform.runtime.capabilities.budget import BudgetCapability, BudgetSpec
from agent_platform.runtime.capabilities.steering import SteeringChannel
from agent_platform.runtime.capabilities.tool_capability import ToolCapability
from agent_platform.runtime.core import (
    ArtifactStore,
    CheckpointStore,
    EventBus,
    RunManager,
    RunStatus,
    RuntimeEventType,
    RuntimeStore,
    SessionManager,
    StateManager,
)
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.models import SandboxSpec, WorkspaceMount

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentRunResult:
    """Platform-facing result of one agent run."""

    run_id: str
    status: str  # "completed" | "failed" | "waiting_for_human"
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


ModelFactory = Callable[[str], BaseChatModel]
BudgetFactory = Callable[[dict[str, Any] | None], BudgetCapability]


class DeepAgentsRuntimeAdapter:
    """Adapts platform Runs to DeepAgents / LangChain execution."""

    def __init__(
        self,
        store: RuntimeStore,
        *,
        model_factory: ModelFactory,
        tool_capability: ToolCapability,
        sandbox_manager: SandboxManager | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        budget_factory: BudgetFactory | None = None,
        steering_channel: SteeringChannel | None = None,
        extra_middleware: tuple = (),
        default_sandbox_image: str = "platform/agent:latest",
        event_bus: EventBus | None = None,
    ) -> None:
        self._store = store
        self._runs = RunManager(store)
        self._apps = SessionManager(store)
        self._states = StateManager(store)
        # Injected buses let dispatch-owned subscribers observe adapter events.
        self._events = event_bus or EventBus(store)
        self._checkpoints = CheckpointStore(store)
        self._model_factory = model_factory
        self._tool_capability = tool_capability
        self._sandbox_manager = sandbox_manager
        self._checkpointer = checkpointer
        self._budget_factory = budget_factory or self._default_budget
        self._steering_channel = steering_channel
        self._extra_middleware = tuple(extra_middleware)
        self._default_sandbox_image = default_sandbox_image

    async def run(self, run_id: str) -> AgentRunResult:
        run = self._runs.get_run(run_id)
        if run.status not in {RunStatus.CREATED, RunStatus.QUEUED}:
            raise InvalidStateTransitionError(f"cannot start run from {run.status.value}")

        # A start is always a fresh execution (first dispatch or retry):
        # drop any durable thread left by an earlier attempt so the
        # add_messages reducer cannot accumulate stale messages into the
        # new run. Resume never clears — it reattaches to the interrupted
        # thread (same fresh-start semantics as the workflow engine).
        if self._checkpointer is not None:
            await self._checkpointer.adelete_thread(run_id)

        self._runs.start_run(run_id)
        self._publish(run_id, RuntimeEventType.RUN_STARTED, {"runtime_type": run.runtime_type})

        input_message = run.input.get("message") or _dumps(run.input)
        agent_input: Any = {"messages": [HumanMessage(content=input_message)]}
        return await self._drive(run, agent_input, reason="error")

    async def resume(self, run_id: str, response: Any = None) -> AgentRunResult:
        """Resume an interrupted or failed agent run (runtime-spec section 3).

        * WAITING_FOR_HUMAN + response: the response folds into the pending
          approval interrupt (LangChain HITLResponse: {"decisions": [...]}) —
          only the middleware hook node replays, the model is not re-invoked.
        * FAILED without response: continue from the last durable checkpoint
          (requires a durable checkpointer, deepagents-runtime-spec section 8).
        """
        run = self._runs.get_run(run_id)
        if run.status not in {RunStatus.WAITING_FOR_HUMAN, RunStatus.FAILED}:
            raise InvalidStateTransitionError(f"cannot resume run from {run.status.value}")
        waiting = run.status is RunStatus.WAITING_FOR_HUMAN
        if waiting and response is None:
            raise InvalidStateTransitionError(
                f"run {run_id} waits for human input; a response is required to resume"
            )
        self._runs.restart_run(run_id)
        if waiting:
            self._publish(
                run_id, RuntimeEventType.HUMAN_RESPONSE_RECEIVED, {"node": "agent", "response": response}
            )
            self._publish(run_id, RuntimeEventType.RUN_RESUMED, {"reason": "human_response"})
        agent_input: Any = Command(resume=response) if waiting else None
        return await self._drive(run, agent_input, reason="resume")

    async def _drive(self, run, agent_input: Any, *, reason: str) -> AgentRunResult:
        run_id = run.id
        config = self._agent_config(run)
        budget = self._budget_factory(config.get("budget"))
        sandbox = None
        try:
            sandbox = await self._create_sandbox(run, config)
            backend = (
                PlatformSandboxBackend(
                    self._sandbox_manager,
                    sandbox.sandbox_id,
                    artifacts=ArtifactStore(self._store),
                    run_id=run_id,
                )
                if sandbox is not None
                else None
            )
            middleware = self._build_middleware(run_id, config, budget)
            agent = create_deep_agent(
                model=self._model_factory(config.get("model", "default")),
                tools=LangChainToolAdapter(self._tool_capability).adapt(config.get("tools")),
                system_prompt=config.get("system_prompt"),
                middleware=middleware,
                backend=backend,
                checkpointer=self._checkpointer,
            )

            # Watchdog (budget-steering-spec.md section 26): middleware hooks
            # cannot observe a hung model/tool call, so the loop-level hard
            # cap (budget max_time) is enforced here, outside the agent loop.
            budget_timeout = budget.timeout_seconds() if budget is not None else None
            try:
                state = await asyncio.wait_for(
                    agent.ainvoke(
                        agent_input,
                        config={"configurable": {"thread_id": run_id}},
                    ),
                    timeout=budget_timeout,
                )
            except asyncio.TimeoutError:
                raise BudgetExceededError("time") from None
            return await self._finish_or_pause(agent, run_id, state)
        except BudgetExceededError as exc:
            # Budget is a governance stop, not an execution bug: record and
            # surface as failed run.
            return self._fail(run_id, exc, reason="budget")
        except Exception as exc:  # noqa: BLE001 - normalize any agent failure
            logger.exception("agent run %s failed", run_id)
            return self._fail(run_id, exc, reason=reason)
        finally:
            if sandbox is not None and self._sandbox_manager is not None:
                await self._destroy_sandbox(sandbox)

    async def _finish_or_pause(self, agent, run_id: str, state: Any) -> AgentRunResult:
        """Map an ainvoke result to completion or a HITL pause (spec section 9)."""
        pending = None
        if self._checkpointer is not None:
            # The result state may carry a stale __interrupt__ entry from an
            # earlier superstep; the snapshot is authoritative about whether
            # the graph is still paused right now (same mechanism as the
            # workflow engine).
            snapshot = await agent.aget_state({"configurable": {"thread_id": run_id}})
            if snapshot.next:
                pending = [task.interrupts[0].value for task in snapshot.tasks if task.interrupts]
        elif isinstance(state, dict):
            interrupts = state.get("__interrupt__")
            pending = [item.value for item in interrupts] if interrupts else None
        if pending:
            # Pending tool approval: park the run and surface the request
            # (HITLRequest payload) for the respond API.
            self._runs.pause_run(run_id)
            self._publish(
                run_id,
                RuntimeEventType.RUN_PAUSED,
                {"node": "agent", "reason": "approval_required"},
            )
            self._publish(
                run_id,
                RuntimeEventType.APPROVAL_REQUIRED,
                {"node": "agent", "request": pending[0]},
            )
            return AgentRunResult(run_id=run_id, status="waiting_for_human")
        output = self._map_output(state)
        self._bridge_checkpoint(run_id)
        self._runs.complete_run(run_id, output=output)
        self._publish(run_id, RuntimeEventType.RUN_COMPLETED, {"output": output})
        return AgentRunResult(run_id=run_id, status="completed", output=output)

    # --- assembly -----------------------------------------------------------

    def _agent_config(self, run) -> dict[str, Any]:
        application = self._apps.get_application(run.application_id)
        config = dict((application.metadata or {}).get("agent", {})) if application else {}
        return config

    async def _create_sandbox(self, run, config: dict[str, Any]):
        if self._sandbox_manager is None:
            return None
        application = self._apps.get_application(run.application_id)
        app_metadata = (application.metadata or {}) if application else {}
        spec = SandboxSpec(
            image=config.get("sandbox_image", self._default_sandbox_image),
            tenant_id=str(app_metadata.get("tenant_id", "tenant-default")),
            user_id=str(app_metadata.get("user_id", "user-default")),
            session_id=run.session_id,
            workspace=WorkspaceMount(workspace_id=f"ws-{run.session_id}"),
            metadata={"run_id": run.id},
        )
        return await self._sandbox_manager.create(spec)

    def _build_middleware(self, run_id: str, config: dict[str, Any], budget) -> list:
        def emit(event_type: RuntimeEventType, payload: dict[str, Any]) -> None:
            self._events.publish(run_id=run_id, event_type=event_type, payload=payload)

        allowed_tools = config.get("tools")
        middleware: list = [
            RuntimeEventMiddleware(emit=emit),
            ToolPermissionMiddleware(allowed_tools),
        ]
        # Runtime control stack order (budget-steering-spec.md section 29):
        # steering first, then budget, so both notices reach the same
        # model call when they coincide (spec section 33).
        if self._steering_channel is not None:
            middleware.append(
                SteeringMiddleware(self._steering_channel, run_id, emit=emit)
            )
        middleware.append(BudgetMiddleware(budget, emit=emit))
        # AskUser (budget-steering-spec.md sections 27/28): contributes the
        # ask_user tool; a call interrupts the graph and the run parks in
        # WAITING_FOR_HUMAN until POST /runs/{id}/respond resumes it.
        middleware.append(AskUserMiddleware())
        middleware.extend(self._extra_middleware)
        # HITL policy (deepagents-runtime-spec section 5): {"tool": [decisions]}
        # pauses the run before that tool executes; the decision arrives via
        # POST /runs/{id}/respond and adapter.resume().
        approvals = config.get("approvals")
        if approvals:
            middleware.append(HumanApprovalMiddleware(approvals))
        return middleware

    @staticmethod
    def _default_budget(spec: dict[str, Any] | None) -> BudgetCapability:
        from agent_platform.runtime.capabilities.budget import InMemoryBudget

        limits = spec or {}
        return InMemoryBudget(
            BudgetSpec(
                max_tokens=limits.get("max_tokens"),
                max_cost=limits.get("max_cost"),
                max_tool_calls=limits.get("max_tool_calls"),
                max_time_ms=limits.get("max_time_ms"),
                max_turns=limits.get("max_turns"),
                soft_ratio=limits.get("soft_ratio", 0.8),
                grace_time_ms=limits.get("grace_time_ms", 60_000),
                max_final_turns=limits.get("max_final_turns", 1),
            )
        )

    # --- mapping ------------------------------------------------------------

    @staticmethod
    def _map_output(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages", [])
        final = ""
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                final = _text_of(message)
                break
        return {"final": final, "message_count": len(messages)}

    def _bridge_checkpoint(self, run_id: str) -> None:
        """Persist a platform checkpoint *reference* to the LangGraph thread."""
        if self._checkpointer is None:
            return
        checkpoint = self._checkpoints.create(
            run_id=run_id, state={"thread_id": run_id, "bridge": "langgraph"}
        )
        self._publish(
            run_id,
            RuntimeEventType.CHECKPOINT_CREATED,
            {"checkpoint_id": checkpoint.id, "thread_id": run_id},
        )

    # --- helpers ------------------------------------------------------------

    def _publish(self, run_id: str, event_type: RuntimeEventType, payload: dict[str, Any]) -> None:
        self._events.publish(run_id=run_id, event_type=event_type, payload=payload)

    def _fail(self, run_id: str, exc: Exception, *, reason: str) -> AgentRunResult:
        self._runs.fail_run(run_id, error=str(exc))
        self._publish(run_id, RuntimeEventType.RUN_FAILED, {"error": str(exc), "reason": reason})
        return AgentRunResult(run_id=run_id, status="failed", output={}, error=str(exc))

    async def _destroy_sandbox(self, sandbox) -> None:
        try:
            await self._sandbox_manager.destroy(sandbox.sandbox_id)
        except Exception:  # noqa: BLE001 - destroy is best effort
            logger.warning("sandbox %s destroy failed", sandbox.sandbox_id, exc_info=True)


def _text_of(message: Any) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        else:
            parts.append(str(block))
    return "".join(parts)


def _dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
