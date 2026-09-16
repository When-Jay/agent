"""Tool capability interface.

Concrete implementations include native tools, internal services and
MCP-backed tool providers. Agent Runtime must not depend on any of them
directly.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from agent_platform.errors import NotFoundError
from agent_platform.runtime.capabilities.tool import ToolCallRequest, ToolResult, ToolSpec


class ToolCapability(ABC):
    """Tool registry and invocation abstraction."""

    @abstractmethod
    def list_tools(self) -> list[ToolSpec]:
        """Return the specs of all available tools."""

    def describe_tool(self, name: str) -> ToolSpec | None:
        """Return the spec for one tool; default implementation scans list_tools()."""
        for spec in self.list_tools():
            if spec.name == name:
                return spec
        return None

    @abstractmethod
    def invoke(self, request: ToolCallRequest) -> ToolResult:
        """Execute a tool call and return its result."""

    # Reserved extension points (see runtime-capabilities-spec.md):
    # authorize (per-call policy check), cancel (in-flight cancellation).


class InMemoryToolCapability(ToolCapability):
    """Minimal in-process tool registry (local development and tests)."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}

    def register(self, spec: ToolSpec, handler: Callable[[dict[str, Any]], Any]) -> None:
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler

    def list_tools(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def invoke(self, request: ToolCallRequest) -> ToolResult:
        handler = self._handlers.get(request.name)
        if handler is None:
            raise NotFoundError(f"tool not found: {request.name}")
        try:
            content = handler(request.arguments)
        except Exception as exc:
            return ToolResult(call_id=request.id, name=request.name, content=str(exc), error=str(exc))
        return ToolResult(
            call_id=request.id,
            name=request.name,
            content=content if isinstance(content, str) else str(content),
        )
