"""MCP tool sources: the sync port the gateway invokes through.

The gateway never imports the MCP SDK. Deployments connect a session
with the MCP client SDK (or any compatible implementation) and hand it
to SdkMcpToolSource, which bridges the async session onto a background
event loop so the gateway's synchronous ToolCapability contract holds.
"""

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_platform.errors import PlatformError


class McpToolCallError(PlatformError):
    """Raised when an MCP server reports a tool failure."""


@dataclass(frozen=True)
class McpToolDescriptor:
    """Tool metadata advertised by an MCP server."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


class McpToolSource(Protocol):
    """Sync facade over one MCP server (duck-typed, SDK-free)."""

    @property
    def name(self) -> str: ...

    def list_tools(self) -> list[McpToolDescriptor]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...


class AsyncMcpSession(Protocol):
    """The subset of an MCP SDK ClientSession the bridge needs."""

    async def list_tools(self) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class SdkMcpToolSource:
    """Bridges a connected async MCP session into the sync source port.

    The session is used exclusively from a dedicated background thread
    running its own event loop (MCP sessions are loop-bound), so calls
    from worker threads are safe.
    """

    def __init__(self, name: str, session: AsyncMcpSession) -> None:
        self._name = name
        self._session = session
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name=f"mcp-session-{name}", daemon=True
        )
        self._thread.start()

    @property
    def name(self) -> str:
        return self._name

    def list_tools(self) -> list[McpToolDescriptor]:
        result = self._call(self._session.list_tools())
        return [
            McpToolDescriptor(
                name=tool.name,
                description=getattr(tool, "description", None) or "",
                # mcp 2.x renamed wire fields to snake_case attributes
                # (input_schema); older SDKs and other conforming sessions
                # use the camelCase spelling.
                input_schema=dict(
                    getattr(tool, "input_schema", None)
                    or getattr(tool, "inputSchema", None)
                    or {}
                ),
            )
            for tool in result.tools
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self._call(self._session.call_tool(name, arguments))
        text = "\n".join(
            block.text for block in (result.content or []) if hasattr(block, "text")
        )
        # mcp 2.x: is_error; older SDKs: isError. None means the session
        # does not expose the flag at all (treat as not failed).
        is_error = getattr(result, "is_error", None)
        if is_error is None:
            is_error = getattr(result, "isError", False)
        if is_error:
            raise McpToolCallError(text or f"tool {name} failed on server {self._name}")
        return text

    def close(self) -> None:
        """Stop the background loop; safe to call more than once."""
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    # --- internal ------------------------------------------------------------

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()
