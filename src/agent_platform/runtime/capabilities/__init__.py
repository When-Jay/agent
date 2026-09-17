"""Runtime capability interfaces.

Capabilities are the boundary between runtime execution logic and
external infrastructure (models, tools, budget accounting, ...).
Runtimes depend on these interfaces only.
"""

from agent_platform.runtime.capabilities.budget import (
    BudgetCapability,
    BudgetDecision,
    BudgetSpec,
    BudgetState,
    InMemoryBudget,
)
from agent_platform.runtime.capabilities.model import ModelMessage, ModelResponse
from agent_platform.runtime.capabilities.model_capability import ModelCapability
from agent_platform.runtime.capabilities.steering import (
    InMemorySteeringChannel,
    SteeringChannel,
    SteeringMessage,
)
from agent_platform.runtime.capabilities.tool import ToolCallRequest, ToolResult, ToolSpec
from agent_platform.runtime.capabilities.tool_capability import ToolCapability

__all__ = [
    "BudgetCapability",
    "BudgetDecision",
    "BudgetSpec",
    "BudgetState",
    "InMemoryBudget",
    "InMemorySteeringChannel",
    "ModelCapability",
    "ModelMessage",
    "ModelResponse",
    "SteeringChannel",
    "SteeringMessage",
    "ToolCallRequest",
    "ToolCapability",
    "ToolResult",
    "ToolSpec",
]
