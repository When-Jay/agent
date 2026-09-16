"""Shared data structures for the Model capability."""

from dataclasses import dataclass, field
from typing import Any

from agent_platform.runtime.capabilities.tool import ToolCallRequest, ToolSpec


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ModelResponse:
    message: ModelMessage
    tool_calls: tuple[ToolCallRequest, ...] = ()
    usage: dict[str, float] = field(default_factory=dict)
    finish_reason: str = "stop"
    metadata: dict[str, Any] = field(default_factory=dict)
