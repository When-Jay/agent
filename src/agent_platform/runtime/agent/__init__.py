"""Agent Runtime: an adapter layer around LangChain / DeepAgents (ADR-0002).

The platform does not implement its own Agent Loop. Execution is
delegated to `create_deep_agent()` / LangChain, while the platform owns
Run lifecycle, policy/budget middleware, tool registry adaptation,
sandbox backend adaptation and RuntimeEvent mapping.
"""

from agent_platform.runtime.agent.adapter import AgentRunResult, DeepAgentsRuntimeAdapter
from agent_platform.runtime.agent.ask_user import AskUserMiddleware
from agent_platform.runtime.agent.backend import PlatformSandboxBackend
from agent_platform.runtime.agent.middleware import (
    BudgetExceededError,
    BudgetMiddleware,
    HumanApprovalMiddleware,
    RuntimeEventMiddleware,
    ToolPermissionDeniedError,
    ToolPermissionMiddleware,
)
from agent_platform.runtime.agent.tools import LangChainToolAdapter, ToolAdaptationError

__all__ = [
    "AgentRunResult",
    "AskUserMiddleware",
    "BudgetExceededError",
    "BudgetMiddleware",
    "DeepAgentsRuntimeAdapter",
    "HumanApprovalMiddleware",
    "LangChainToolAdapter",
    "PlatformSandboxBackend",
    "RuntimeEventMiddleware",
    "ToolAdaptationError",
    "ToolPermissionDeniedError",
    "ToolPermissionMiddleware",
]
