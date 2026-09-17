"""Platform middleware for LangChain / DeepAgents (deepagents-runtime-spec.md section 5).

Platform-specific behavior is implemented as LangChain AgentMiddleware
instead of a custom middleware framework:

* RuntimeEventMiddleware - emits RuntimeEvents for LLM and tool calls
* BudgetMiddleware - multi-dimensional budget (time/token/turn) with soft
  limit graceful finishing and hard limit stop (budget-steering-spec.md)
* SteeringMiddleware - injects pending user steering messages as Runtime
  Notices before the next model decision (budget-steering-spec.md section 7)
* ToolPermissionMiddleware - restricts which tools the model may call
* HumanApprovalMiddleware - pauses tool calls for human approval (HITL)

Middleware may read platform configuration and emit events, but must
not import API request handlers.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, HumanInTheLoopMiddleware
from langchain.agents.middleware.types import ModelRequest, ToolCallRequest
from langchain_core.messages import AIMessage, SystemMessage

from agent_platform.errors import PlatformError, ToolPermissionDeniedError
from agent_platform.runtime.capabilities.budget import (
    BudgetCapability,
    PHASE_EXHAUSTED,
)
from agent_platform.runtime.capabilities.steering import SteeringChannel
from agent_platform.runtime.core.events import RuntimeEventType

logger = logging.getLogger(__name__)

EventEmitter = Callable[[RuntimeEventType, dict[str, Any]], Any]


class BudgetExceededError(PlatformError):
    """Raised by BudgetMiddleware when a budget dimension is exhausted."""

    def __init__(self, dimension: str) -> None:
        self.dimension = dimension
        super().__init__(f"budget exceeded: {dimension}")


# ToolPermissionDeniedError lives in agent_platform.errors so the MCP
# gateway and agent middleware enforce the same permission error type.


def _extract_ai_message(result: Any) -> AIMessage | None:
    """Pull the AIMessage out of a model-call result (AIMessage or wrapper)."""
    if isinstance(result, AIMessage):
        return result
    for attr in ("message", "result", "ai_message", "output"):
        candidate = getattr(result, attr, None)
        if isinstance(candidate, AIMessage):
            return candidate
    return None


def runtime_notice(title: str, body: str) -> SystemMessage:
    """Build a Runtime Control Notice (budget-steering-spec.md section 8/34).

    Runtime notices are System messages injected for the *current* model
    call only — they never masquerade as historical user messages.
    """
    return SystemMessage(content=f"[SYSTEM NOTICE — {title}]\n\n{body}")


class RuntimeEventMiddleware(AgentMiddleware):
    """Maps model/tool activity to RuntimeEvents (spec section 9)."""

    def __init__(self, emit: EventEmitter) -> None:
        self._emit = emit

    async def awrap_model_call(self, request: ModelRequest, handler) -> Any:
        self._emit(
            RuntimeEventType.LLM_STARTED,
            {"model": getattr(request.model, "model_name", None) or type(request.model).__name__},
        )
        try:
            result = await handler(request)
        except Exception as exc:
            self._emit(RuntimeEventType.LLM_FAILED, {"error": str(exc)})
            raise
        message = _extract_ai_message(result)
        usage = getattr(message, "usage_metadata", None) or {}
        self._emit(
            RuntimeEventType.LLM_COMPLETED,
            {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            },
        )
        return result

    async def awrap_tool_call(self, request: ToolCallRequest, handler) -> Any:
        tool_name = getattr(request.tool, "name", None) or request.tool_call.get("name", "unknown")
        self._emit(RuntimeEventType.TOOL_CALL_STARTED, {"tool": tool_name})
        try:
            result = await handler(request)
        except Exception as exc:
            self._emit(
                RuntimeEventType.TOOL_CALL_FAILED, {"tool": tool_name, "error": str(exc)}
            )
            raise
        self._emit(RuntimeEventType.TOOL_CALL_COMPLETED, {"tool": tool_name})
        return result


class SteeringMiddleware(AgentMiddleware):
    """Injects pending user steering before the next model decision
    (budget-steering-spec.md sections 4/7/9).

    * 一次性合并注入：所有 pending steering 在同一次 Model Call 前注入，
      不产生额外 Model Turn（spec section 9）。
    * 不终止正在执行的 Tool：steering 只影响下一次 Model Decision
      （spec section 10）。
    * consume-after-successful-injection：模型调用成功后才消费
      （spec section 36 第一版要求）；调用失败时 steering 保持 pending，
      下次重试仍会注入，配合 steering_id 幂等。
    """

    def __init__(
        self,
        channel: SteeringChannel,
        run_id: str,
        emit: EventEmitter | None = None,
    ) -> None:
        self._channel = channel
        self._run_id = run_id
        self._emit = emit

    async def awrap_model_call(self, request: ModelRequest, handler) -> Any:
        pending = self._channel.pending(self._run_id)
        if pending:
            lines = "\n".join(f'- "{m.message}"' for m in pending)
            request = request.override(
                messages=[
                    *request.messages,
                    runtime_notice(
                        "USER STEERING",
                        "The user has provided runtime steering instruction(s):\n\n"
                        f"{lines}\n\n"
                        "Adjust your next actions according to this instruction.",
                    ),
                ]
            )
            if self._emit is not None:
                self._emit(
                    RuntimeEventType.STEERING_INJECTED,
                    {"steering_ids": [m.id for m in pending], "count": len(pending)},
                )
        result = await handler(request)
        if pending:
            # consume-after-successful-injection (spec section 36): the model
            # call succeeded, so the steering is consumed exactly once.
            self._channel.acknowledge(self._run_id, [m.id for m in pending])
            if self._emit is not None:
                self._emit(
                    RuntimeEventType.STEERING_CONSUMED,
                    {"steering_ids": [m.id for m in pending]},
                )
        return result


BUDGET_FINISHING_NOTICE = (
    "Execution budget is nearly exhausted.\n\n"
    "Stop new discovery/verification work now.\n"
    "Produce the required final deliverable (answer/JSON/summary) from the "
    "state you already have, completing only mandatory writes.\n\n"
    "Do not start new non-essential tool calls.\n"
    "Prioritize completing the final response."
)


class BudgetMiddleware(AgentMiddleware):
    """Multi-dimensional budget around model calls (budget-steering-spec.md).

    单一 hook（awrap_model_call）承担 before/after 两个职责：

    * before：turn 计数 + soft/hard 检查。Hard → 抛 BudgetExceededError
      （强制终止）；首次进入 FINISHING → 注入 Runtime Notice（graceful
      finishing，不 kill Agent，spec sections 14/15）。
    * after：累计 token usage 并复查（token 用量只能在调用后获得，
      允许少量 overshoot，spec section 20）。

    兼容仅实现 record/usage/exceeded 的旧 BudgetCapability：缺省
    decision() 退化为 hard-only 语义（无 notice 注入路径）。
    """

    def __init__(self, budget: BudgetCapability, emit: EventEmitter | None = None) -> None:
        self._budget = budget
        self._emit = emit
        self._notice_injected = False

    async def awrap_model_call(self, request: ModelRequest, handler) -> Any:
        self._budget.enter_turn()
        decision = self._budget.decision()
        self._emit_budget_events(decision)
        if decision.hard_exceeded:
            raise BudgetExceededError(
                decision.newly_hard[-1]
                if decision.newly_hard
                else (decision.triggered_dimensions[-1] if decision.triggered_dimensions else "budget")
            )
        if decision.entering_finishing and not self._notice_injected:
            self._notice_injected = True
            request = request.override(
                messages=[*request.messages, runtime_notice("BUDGET", BUDGET_FINISHING_NOTICE)]
            )
        result = await handler(request)
        message = _extract_ai_message(result)
        usage = getattr(message, "usage_metadata", None) or {}
        self._budget.record(
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
        )
        post = self._budget.decision()
        self._emit_budget_events(post)
        if post.hard_exceeded:
            raise BudgetExceededError(
                post.newly_hard[-1] if post.newly_hard else "budget"
            )
        return result

    def _emit_budget_events(self, decision) -> None:
        """Runtime control events (budget-steering-spec.md section 35)."""
        if self._emit is None:
            return
        for dimension in decision.newly_soft:
            self._emit(
                RuntimeEventType.BUDGET_SOFT_LIMIT,
                {
                    "dimension": dimension,
                    "remaining": decision.remaining.get(dimension),
                    "phase": decision.phase,
                },
            )
        for dimension in decision.newly_hard:
            self._emit(
                RuntimeEventType.BUDGET_HARD_LIMIT,
                {
                    "dimension": dimension,
                    "remaining": decision.remaining.get(dimension),
                    "phase": decision.phase,
                },
            )
        if decision.entering_finishing:
            self._emit(
                RuntimeEventType.BUDGET_FINISHING,
                {"phase": decision.phase, "triggered": decision.triggered_dimensions},
            )
        if decision.phase == PHASE_EXHAUSTED:
            self._emit(
                RuntimeEventType.BUDGET_EXHAUSTED,
                {"phase": decision.phase, "triggered": decision.triggered_dimensions},
            )


class ToolPermissionMiddleware(AgentMiddleware):
    """Denies tool calls for tools outside the configured allowlist.

    `None` means no restriction.
    """

    def __init__(self, allowed_tools: list[str] | None = None) -> None:
        self._allowed = set(allowed_tools) if allowed_tools is not None else None

    async def awrap_tool_call(self, request: ToolCallRequest, handler) -> Any:
        if self._allowed is not None:
            tool_name = getattr(request.tool, "name", None) or request.tool_call.get("name")
            if tool_name not in self._allowed:
                raise ToolPermissionDeniedError(f"tool not allowed: {tool_name}")
        return await handler(request)


class HumanApprovalMiddleware(HumanInTheLoopMiddleware):
    """Platform HITL gate over LangChain's interrupt mechanic (spec sections 5, 9).

    Delegates the pause/resume mechanics to `HumanInTheLoopMiddleware`
    (after_model hook: pending tool calls are interrupted before any tool
    executes, so replay never re-runs approved tools). This subclass only
    adapts the platform approval policy shape: `{tool: [decisions] | True}`.

    On resume the response must be a LangChain HITLResponse:
    `{"decisions": [{"type": "approve"} | {"type": "reject", "message": ...}]}`.
    """

    def __init__(self, approvals: dict[str, Any]) -> None:
        interrupt_on: dict[str, Any] = {}
        for tool_name, policy in approvals.items():
            interrupt_on[tool_name] = (
                {"allowed_decisions": list(policy)}
                if isinstance(policy, (list, tuple))
                else policy
            )
        super().__init__(interrupt_on)
