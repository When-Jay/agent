"""Run Orchestrator: routes dispatched runs to their runtime.

Worker-side entry point (runtime-dispatch-spec.md section 4):

1. Load the Run from the durable store.
2. Skip terminal runs (idempotency: retried tasks never re-execute).
3. Route by `runtime_type`: "agent" to the DeepAgents adapter,
   "workflow" to the WorkflowRunner (definition resolved by
   name@version from application metadata through the registry).
"""

import asyncio
import logging
from typing import Any

from agent_platform.config import Settings
from agent_platform.runtime.checkpointing import StoreCheckpointSaver
from agent_platform.runtime.core import (
    EventBus,
    InMemoryRuntimeStore,
    RunManager,
    RunStatus,
    RuntimeEventType,
    RuntimeStore,
    SessionManager,
)

logger = logging.getLogger(__name__)

# Runs the orchestrator may start executing; everything else (terminal,
# RUNNING, WAITING_FOR_HUMAN) is skipped so worker retries stay idempotent.
_EXECUTABLE_STATUSES = {RunStatus.CREATED, RunStatus.QUEUED}

# In-memory stores are process-wide singletons so the eager worker path
# (tests, local dev) shares durable state with the API in the same process.
_memory_stores: dict[str, InMemoryRuntimeStore] = {}


def create_runtime_store(database_url: str) -> RuntimeStore:
    """Build a RuntimeStore for a database URL.

    `sqlite:///:memory:` keeps one shared in-memory store per URL;
    every durable URL gets its own engine against the shared database.
    """
    if database_url.startswith("sqlite") and ":memory:" in database_url:
        store = _memory_stores.get(database_url)
        if store is None:
            store = InMemoryRuntimeStore()
            _memory_stores[database_url] = store
        return store
    from agent_platform.infrastructure.sqlalchemy_store import SQLAlchemyRuntimeStore

    return SQLAlchemyRuntimeStore(database_url)


class RuntimeOrchestrator:
    """Executes dispatched runs; subscriber-aware through `events`."""

    def __init__(
        self,
        store: RuntimeStore,
        *,
        agent_adapter=None,
        workflow_runner=None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._store = store
        self._runs = RunManager(store)
        self._apps = SessionManager(store)
        self.events = event_bus or EventBus(store)
        self._agent_adapter = agent_adapter
        self._workflow_runner = workflow_runner

    def execute(self, run_id: str) -> None:
        run = self._runs.get_run(run_id)
        # Only fresh runs execute; terminal runs are skipped (worker
        # retries must be idempotent), WAITING_FOR_HUMAN runs must not be
        # re-executed behind the caller's back.
        if run.status not in _EXECUTABLE_STATUSES:
            logger.info(
                "run %s not executable from %s; skipping", run_id, run.status.value
            )
            return
        if run.runtime_type == "agent":
            self._execute_agent(run_id)
        elif run.runtime_type == "workflow":
            self._execute_workflow(run_id)
        else:
            self._fail(run_id, f"unknown runtime_type: {run.runtime_type}")

    def resume(self, run_id: str, response: Any = None) -> None:
        """Resume a Run (recovery path, runtime-spec.md section 3).

        Two resumable states:
        * FAILED (no response): the run continues from its last durable
          checkpoint. Workflow runs replay engine in-process state; agent
          runs replay the LangGraph thread through the configured
          checkpointer (durable with StoreCheckpointSaver, deepagents-
          runtime-spec.md section 8).
        * WAITING_FOR_HUMAN (response required): the response is folded
          into the paused node (workflow Human node or agent approval
          interrupt) and execution continues.
        """
        run = self._runs.get_run(run_id)
        if response is None:
            if run.status is not RunStatus.FAILED:
                logger.info("run %s not resumable from %s; skipping", run_id, run.status.value)
                return
        elif run.status is not RunStatus.WAITING_FOR_HUMAN:
            logger.info(
                "run %s does not wait for human input (%s); skipping response",
                run_id,
                run.status.value,
            )
            return
        if run.runtime_type == "workflow":
            self._resume_workflow(run_id, response=response)
        elif run.runtime_type == "agent":
            if self._agent_adapter is None:
                self._fail(run_id, "agent runtime adapter is not configured")
                return
            asyncio.run(self._agent_adapter.resume(run_id, response=response))
        else:
            logger.info(
                "resume not supported for runtime_type %s (run %s); skipping",
                run.runtime_type,
                run_id,
            )

    # --- routing ------------------------------------------------------------

    def _execute_agent(self, run_id: str) -> None:
        if self._agent_adapter is None:
            self._fail(run_id, "agent runtime adapter is not configured")
            return
        asyncio.run(self._agent_adapter.run(run_id))

    def _execute_workflow(self, run_id: str) -> None:
        if self._workflow_runner is None:
            self._fail(run_id, "workflow runtime is not configured")
            return
        run = self._runs.get_run(run_id)
        reference = self._workflow_reference(run)
        try:
            definition = self._workflow_runner.resolve(
                reference.get("name", ""), reference.get("version")
            )
        except KeyError as exc:
            self._fail(run_id, exc.args[0] if exc.args else str(exc))
            return
        try:
            self._workflow_runner.run(run_id=run_id, definition=definition, input=run.input)
        except Exception as exc:  # noqa: BLE001 - normalize dispatch failures
            logger.exception("workflow run %s failed", run_id)
            self._fail(run_id, str(exc))

    def _resume_workflow(self, run_id: str, response: Any = None) -> None:
        if self._workflow_runner is None:
            self._fail_resume(run_id, "workflow runtime is not configured")
            return
        run = self._runs.get_run(run_id)
        reference = self._workflow_reference(run)
        try:
            definition = self._workflow_runner.resolve(
                reference.get("name", ""), reference.get("version")
            )
        except KeyError as exc:
            self._fail_resume(run_id, exc.args[0] if exc.args else str(exc))
            return
        if response is None:
            # Failure recovery: announce before the runner restarts the run.
            self.events.publish(
                run_id=run_id,
                event_type=RuntimeEventType.RUN_RESUMED,
                payload={"reason": "manual"},
            )
        try:
            self._workflow_runner.resume(run_id=run_id, definition=definition, response=response)
        except Exception as exc:  # noqa: BLE001 - normalize resume failures
            logger.exception("workflow resume %s failed", run_id)
            self._fail_resume(run_id, str(exc))

    def _fail_resume(self, run_id: str, error: str) -> None:
        """Record a resume failure without clobbering an already-failed run.

        The runner restarts FAILED -> RUNNING before execution; a failure
        before that point leaves the run FAILED, and the original error
        must be preserved instead of re-failing (which is not a legal
        transition from FAILED).
        """
        run = self._runs.get_run(run_id)
        if run.status is RunStatus.RUNNING:
            self._fail(run_id, error)
        else:
            logger.warning("resume of run %s aborted before restart: %s", run_id, error)

    def _workflow_reference(self, run) -> dict[str, Any]:
        application = self._apps.get_application(run.application_id)
        return dict((application.metadata or {}).get("workflow", {}))

    def _fail(self, run_id: str, error: str) -> None:
        self._runs.fail_run(run_id, error=error)
        self.events.publish(
            run_id=run_id,
            event_type=RuntimeEventType.RUN_FAILED,
            payload={"error": error, "reason": "dispatch"},
        )


def _default_model_factory(model_spec: str):
    if model_spec == "default":
        raise ValueError(
            "no model configured: set application metadata "
            "{'agent': {'model': '<provider>:<model-name>'}}"
        )
    from langchain.chat_models import init_chat_model

    return init_chat_model(model_spec)


def build_agent_adapter(
    store: RuntimeStore, *, event_bus: EventBus, tool_capability=None, steering_channel=None
):
    """Compose the default DeepAgents adapter over Runtime Core.

    Durable checkpoint payloads ride on the store's own engine when one
    exists (SQLAlchemy store); in-memory stores get no checkpointer and
    agent resume is unavailable there (spec section 8). The steering
    channel enables the SteeringMiddleware stack (budget-steering-spec).
    """
    from agent_platform.runtime.agent import DeepAgentsRuntimeAdapter
    from agent_platform.runtime.capabilities.tool_capability import InMemoryToolCapability

    engine = getattr(store, "engine", None)
    checkpointer = StoreCheckpointSaver(engine) if engine is not None else None
    return DeepAgentsRuntimeAdapter(
        store,
        model_factory=_default_model_factory,
        tool_capability=tool_capability or InMemoryToolCapability(),
        event_bus=event_bus,
        checkpointer=checkpointer,
        steering_channel=steering_channel,
    )


def build_workflow_runner(store: RuntimeStore):
    """Compose the default WorkflowRunner (LangGraph engine) over Runtime Core.

    Durable engine checkpoints ride on the store's own engine when one
    exists (SQLAlchemy store), mirroring the agent adapter: HITL pauses
    and failures resume across processes; in-memory stores keep the
    per-run MemorySaver (in-process resume only).
    """
    from agent_platform.runtime.workflow import LangGraphWorkflowEngine, WorkflowRunner

    engine = getattr(store, "engine", None)
    langgraph_engine = (
        LangGraphWorkflowEngine(StoreCheckpointSaver(engine)) if engine is not None else None
    )
    return WorkflowRunner(store, engine=langgraph_engine)


def build_default_orchestrator(settings: Settings | None = None) -> RuntimeOrchestrator:
    """Worker-side default composition: store + runtimes + shared bus."""
    settings = settings or Settings()
    store = create_runtime_store(settings.database_url)
    events = EventBus(store)
    from agent_platform.runtime.dispatch.redis_stream import create_steering_channel

    return RuntimeOrchestrator(
        store,
        agent_adapter=build_agent_adapter(
            store,
            event_bus=events,
            tool_capability=_build_tool_capability(settings),
            steering_channel=create_steering_channel(settings),
        ),
        workflow_runner=build_workflow_runner(store),
        event_bus=events,
    )


def _build_tool_capability(settings: Settings):
    """Native registry beside the MCP gateway when servers are configured.

    The knowledge tool registers only when KNOWLEDGE_TOOL_ENABLED=true
    (default off: existing agent tool surfaces must not change). The
    service holds (store URL, provider) and rebuilds per orchestrator,
    mirroring the per-task composition; no in-process caches.
    """
    from agent_platform.runtime.capabilities.tool_capability import (
        CompositeToolCapability,
        InMemoryToolCapability,
    )

    native = InMemoryToolCapability()
    if settings.knowledge_tool_enabled:
        from agent_platform.infrastructure.knowledge_sqlalchemy_store import (
            create_knowledge_store,
        )
        from agent_platform.knowledge.application import KnowledgeService
        from agent_platform.knowledge.embeddings import create_embedding_provider
        from agent_platform.knowledge.tool import (
            KNOWLEDGE_TOOL_SPEC,
            create_knowledge_tool_handler,
        )

        service = KnowledgeService(
            create_knowledge_store(settings.database_url),
            create_embedding_provider(settings),
        )
        native.register(KNOWLEDGE_TOOL_SPEC, create_knowledge_tool_handler(service))
    if not settings.mcp_servers_json.strip():
        return native
    from agent_platform.mcp.credentials import EnvCredentialResolver
    from agent_platform.runtime.dispatch.mcp_composition import (
        build_mcp_gateway,
        parse_mcp_servers_json,
    )

    servers = parse_mcp_servers_json(settings.mcp_servers_json)
    gateway = build_mcp_gateway(servers, resolver=EnvCredentialResolver())
    return CompositeToolCapability([native, gateway])
