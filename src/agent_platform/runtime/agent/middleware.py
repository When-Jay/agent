"""Platform middleware for LangChain / DeepAgents (deepagents-runtime-spec.md section 5).

Platform-specific behavior is implemented as LangChain AgentMiddleware
instead of a custom middleware framework:

* RuntimeEventMiddleware - emits RuntimeEvents for LLM and tool calls
* BudgetMiddleware - enforces BudgetSpec limits on model usage
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
from langchain_core.messages import AIMessage

from agent_platform.errors import PlatformError, ToolPermissionDeniedError
from agent_platform.runtime.capabilities.budget import BudgetCapability
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


class BudgetMiddleware(AgentMiddleware):
    """Enforces platform budget limits around model calls."""

    def __init__(self, budget: BudgetCapability) -> None:
        self._budget = budget

    async def awrap_model_call(self, request: ModelRequest, handler) -> Any:
        result = await handler(request)
        message = _extract_ai_message(result)
        usage = getattr(message, "usage_metadata", None) or {}
        self._budget.record(
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
        )
        dimension = self._budget.exceeded()
        if dimension is not None:
            raise BudgetExceededError(dimension)
        return result


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
