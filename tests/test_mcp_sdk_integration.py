"""Real-SDK integration tests for SdkMcpToolSource (mcp-gateway-spec.md stage A).

Pins the bridge assumptions against the official `mcp` package over
InMemoryTransport (no network, no subprocess):

* list_tools shape: ``result.tools`` with ``tool.name`` / ``.description``
  / ``.input_schema`` (mcp 2.x snake_case; older camelCase sessions are
  covered by the duck-typed fallback in source.py and its unit tests).
* call_tool shape: positional ``(name, arguments)``; ``result.content``
  blocks expose ``.text``; server-side tool failures surface as
  ``result.is_error`` and must raise McpToolCallError, never pass as
  success text.
* ``close()`` terminates the background loop/thread and is idempotent.

Loop-binding constraint (drives Stage C session-holder design): MCP
sessions are bound to the loop they were entered on, so a handed-over
session MUST be connected on the bridge's background loop. The tests use
``source._loop`` (white-box) with a forwarding shim because the bridge
contract receives a ready session object.
"""

import asyncio
import threading
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, Iterator

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession
from mcp.server.mcpserver import MCPServer

from agent_platform.mcp.source import McpToolCallError, SdkMcpToolSource

_CALL_TIMEOUT = 10.0


def _demo_server() -> MCPServer:
    server = MCPServer("demo")

    @server.tool()
    def echo(text: str) -> str:
        """Echo the given text back."""
        return f"echo:{text}"

    @server.tool()
    def boom() -> str:
        """Always fails."""
        raise ValueError("kaboom")

    return server


class _DeferredSession:
    """Forwards to the real session once connected on the bridge loop."""

    def __init__(self, holder: dict[str, Any]) -> None:
        self._holder = holder

    async def list_tools(self) -> Any:
        return await self._holder["session"].list_tools()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._holder["session"].call_tool(name, arguments)


@contextmanager
def _bridged_session() -> Iterator[tuple[SdkMcpToolSource, Callable[..., Any]]]:
    holder: dict[str, Any] = {}
    source = SdkMcpToolSource("demo", _DeferredSession(holder))
    loop = source._loop

    def run(coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=_CALL_TIMEOUT)

    ready = threading.Event()
    done = threading.Event()

    async def lifecycle() -> None:
        # Anyio cancel scopes are task-bound: connect/initialize/teardown
        # must live in ONE task on the bridge loop (not per-call tasks).
        async with InMemoryTransport(_demo_server(), raise_exceptions=True) as streams:
            session = ClientSession(*streams)
            async with session:
                await session.initialize()
                holder["session"] = session
                ready.set()
                await asyncio.to_thread(done.wait)

    future = asyncio.run_coroutine_threadsafe(lifecycle(), loop)
    if not ready.wait(timeout=_CALL_TIMEOUT):
        done.set()
        future.result(timeout=_CALL_TIMEOUT)  # surface the lifecycle failure
        raise RuntimeError("mcp session did not become ready in time")
    try:
        yield source, run
    finally:
        holder.pop("session", None)
        done.set()
        future.result(timeout=_CALL_TIMEOUT)
        source.close()


def test_list_tools_pins_real_sdk_shape() -> None:
    with _bridged_session() as (source, _run):
        descriptors = {tool.name: tool for tool in source.list_tools()}

    echo = descriptors["echo"]
    assert echo.description == "Echo the given text back."
    assert echo.input_schema["properties"]["text"]["type"] == "string"
    assert descriptors["boom"].input_schema["properties"] == {}


def test_call_tool_joins_text_content_blocks() -> None:
    with _bridged_session() as (source, _run):
        assert source.call_tool("echo", {"text": "hi"}) == "echo:hi"


def test_server_side_tool_failure_raises() -> None:
    with _bridged_session() as (source, _run):
        # Servers may wrap the original message (mcpserver surfaces
        # "Error executing tool <name>"); the pin is the is_error ->
        # McpToolCallError mapping, not the exact text.
        with pytest.raises(McpToolCallError, match="Error executing tool boom"):
            source.call_tool("boom", {})


def test_close_terminates_background_thread_idempotently() -> None:
    with _bridged_session() as (source, _run):
        thread = source._thread
        assert thread.is_alive()
    assert not thread.is_alive()
    assert not source._loop.is_running()
    source.close()  # second close must be a no-op, not an error


def test_close_joins_within_timeout_under_pending_idle_loop() -> None:
    source = SdkMcpToolSource("idle", _DeferredSession({}))
    thread = source._thread
    source.close()
    assert not thread.is_alive()
    assert not source._loop.is_running()
