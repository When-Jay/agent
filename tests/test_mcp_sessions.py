"""Session holder tests (mcp-gateway-spec.md section 12).

Holders drive a real official-SDK ClientSession over InMemoryTransport
through an injected session factory — the same shape the composition
root builds for http/sse transports, without network or subprocess.
"""

import threading
import time
from contextlib import asynccontextmanager

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession
from mcp.server.mcpserver import MCPServer

from agent_platform.mcp.sessions import (
    CircuitOpenError,
    HttpMcpToolSource,
    SessionUnavailableError,
)


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

    @server.tool()
    def slow() -> str:
        """Takes a moment."""
        time.sleep(0.4)
        return "slow-done"

    return server


def memory_session_factory(server: MCPServer, *, wrapper=None):
    """A session factory like the composition root's, over memory streams."""

    @asynccontextmanager
    async def factory(url: str, headers: dict[str, str]):
        async with InMemoryTransport(server, raise_exceptions=True) as streams:
            session = ClientSession(*streams)
            async with session:
                await session.initialize()
                yield wrapper(session) if wrapper is not None else session

    return factory


class _RecordingSession:
    """Proxies a session, counting health probes."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.pings = 0

    async def list_tools(self):
        return await self._inner.list_tools()

    async def call_tool(self, name, arguments, *, meta=None):
        return await self._inner.call_tool(name, arguments, meta=meta)

    async def send_ping(self):
        self.pings += 1
        return await self._inner.send_ping()


def _holder(factory, **overrides) -> HttpMcpToolSource:
    options = dict(
        name="demo",
        url="memory://demo",
        session_factory=factory,
        connect_timeout=5.0,
        call_timeout=5.0,
    )
    options.update(overrides)
    return HttpMcpToolSource(**options)


def test_facade_over_real_sdk_session():
    holder = _holder(memory_session_factory(_demo_server()))
    try:
        descriptors = {tool.name: tool for tool in holder.list_tools()}
        assert set(descriptors) == {"echo", "boom", "slow"}

        assert holder.call_tool("echo", {"text": "hi"}) == "echo:hi"
        with pytest.raises(Exception, match="Error executing tool boom"):
            holder.call_tool("boom", {})
    finally:
        holder.close()


def test_idle_health_probe_runs_before_reuse():
    state = {"recorder": None}

    def wrap(session):
        proxy = _RecordingSession(session)
        state["recorder"] = proxy
        return proxy

    holder = _holder(memory_session_factory(_demo_server(), wrapper=wrap), health_interval=0.0)
    try:
        holder.call_tool("echo", {"text": "1"})  # first call: no last_used yet
        time.sleep(0.02)  # ensure measurable idle elapsed on coarse clocks
        holder.call_tool("echo", {"text": "2"})  # idle expired -> ping first
        assert state["recorder"].pings == 1
    finally:
        holder.close()


def test_connect_failure_arms_backoff_then_recovers():
    attempts = {"count": 0}
    switch = {"server": None}

    def flaky_factory(transport: str):
        @asynccontextmanager
        async def factory(url: str, headers: dict[str, str]):
            attempts["count"] += 1
            server = switch["server"]
            if server is None:
                raise RuntimeError("server down")
            async with InMemoryTransport(server, raise_exceptions=True) as streams:
                session = ClientSession(*streams)
                async with session:
                    await session.initialize()
                    yield session

        return factory

    holder = _holder(
        flaky_factory("streamable-http"), reconnect_backoff=0.3, breaker_threshold=50
    )
    try:
        # Connect fails; the transparent retry (gate bypassed) surfaces the
        # root cause instead of the throttle message.
        with pytest.raises(SessionUnavailableError, match="connection failed: server down"):
            holder.call_tool("echo", {"text": "x"})

        # Backoff gate: an immediate retry is refused without a new attempt.
        with pytest.raises(SessionUnavailableError, match="reconnecting"):
            holder.call_tool("echo", {"text": "x"})
        assert attempts["count"] == 2  # 1st call = 2 attempts, 2nd call = gate

        time.sleep(0.35)
        switch["server"] = _demo_server()
        assert holder.call_tool("echo", {"text": "x"}) == "echo:x"
        assert attempts["count"] == 3
    finally:
        holder.close()


def test_circuit_breaker_opens_then_half_open_probe_recovers():
    attempts = {"count": 0}
    switch = {"mode": "broken"}

    def factory_builder(transport: str):
        @asynccontextmanager
        async def factory(url: str, headers: dict[str, str]):
            attempts["count"] += 1
            if switch["mode"] == "broken":
                raise RuntimeError("nope")
            async with InMemoryTransport(_demo_server(), raise_exceptions=True) as streams:
                session = ClientSession(*streams)
                async with session:
                    await session.initialize()
                    yield session

        return factory

    holder = _holder(
        factory_builder("streamable-http"),
        reconnect_backoff=0.0,  # every call is a real attempt
        breaker_threshold=5,
        breaker_cooldown=0.3,
    )
    try:
        # Each failing call counts TWO breaker failures (original attempt
        # + transparent reconnect), so threshold 5 opens mid-third-call.
        for _ in range(3):
            with pytest.raises(SessionUnavailableError, match="nope"):
                holder.call_tool("echo", {"text": "x"})
        assert attempts["count"] == 6

        # Circuit open: refused without attempting (spec section 12.3).
        with pytest.raises(CircuitOpenError, match="circuit open"):
            holder.call_tool("echo", {"text": "x"})
        assert attempts["count"] == 6

        time.sleep(0.35)  # cooldown elapsed -> half-open probe
        switch["mode"] = "up"
        assert holder.call_tool("echo", {"text": "x"}) == "echo:x"
        assert attempts["count"] == 7  # probe = one more real attempt

        # Circuit closed again: failure counter was reset.
        assert holder.call_tool("echo", {"text": "y"}) == "echo:y"
    finally:
        holder.close()


def test_concurrency_cap_bounds_simultaneous_calls():
    entered = threading.Event()

    def slow() -> str:
        """Signals entry, then occupies the slot for a moment."""
        entered.set()
        time.sleep(0.4)
        return "slow-done"

    server = MCPServer("demo")

    @server.tool()
    def echo(text: str) -> str:
        """Echo the given text back."""
        return f"echo:{text}"

    server.tool()(slow)

    def factory_builder(transport: str):
        @asynccontextmanager
        async def factory(url: str, headers: dict[str, str]):
            async with InMemoryTransport(server, raise_exceptions=True) as streams:
                session = ClientSession(*streams)
                async with session:
                    await session.initialize()
                    yield session

        return factory

    holder = _holder(
        factory_builder("streamable-http"), max_concurrency=1, acquire_timeout=0.2
    )
    try:
        first: dict = {}

        def worker():
            first["result"] = holder.call_tool("slow", {})

        thread = threading.Thread(target=worker)
        thread.start()
        assert entered.wait(2.0)  # worker holds the single slot inside slow()

        with pytest.raises(SessionUnavailableError, match="concurrency cap"):
            holder.call_tool("echo", {"text": "x"})

        thread.join(5.0)
        assert first["result"] == "slow-done"
        assert holder.call_tool("echo", {"text": "y"}) == "echo:y"
    finally:
        holder.close()


def test_close_is_idempotent_and_terminates_thread():
    holder = _holder(memory_session_factory(_demo_server()))
    holder.call_tool("echo", {"text": "x"})
    thread = holder._thread
    holder.close()
    holder.close()
    assert not thread.is_alive()
    with pytest.raises(SessionUnavailableError, match="closed"):
        holder.call_tool("echo", {"text": "x"})
