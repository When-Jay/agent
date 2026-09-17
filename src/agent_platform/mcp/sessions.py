"""Durable session holders for remote MCP servers (mcp-gateway-spec.md section 12).

Holder design (Stage A finding): anyio cancel scopes are task-bound, so
each holder owns a background loop where ONE persistent task runs the
session factory (transport context + ClientSession + initialize) and
keeps it alive; per-call coroutines only await session methods.

The holder is SDK-free: the session factory is injected by the
composition root (official SDK builders live there); in tests the same
shape is provided over in-memory transports. Robustness layers, outer
to inner: circuit breaker (section 12.3), concurrency cap (section
12.4), lazy reconnect with backoff, idle health probe via ``send_ping``
(section 12.2).
"""

import asyncio
import threading
import time
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from agent_platform.errors import PlatformError

from agent_platform.mcp.credentials import credential_fingerprint
from agent_platform.mcp.source import (
    McpToolDescriptor,
    McpToolSource,
    descriptor_from_sdk_tool,
    text_from_sdk_result,
)


class SessionUnavailableError(PlatformError):
    """Raised when a server connection cannot be used right now."""


class CircuitOpenError(SessionUnavailableError):
    """Raised while a server's circuit breaker is open."""


class ReconnectBackoffError(SessionUnavailableError):
    """Raised when a call arrives during the reconnect backoff window.

    A distinct type so the call path can refuse it WITHOUT counting a
    breaker failure or triggering a transparent reconnect — no attempt
    was made, so there is nothing to recover from.
    """


class RemoteMcpSession(Protocol):
    """The subset of an MCP SDK ClientSession the holder drives."""

    async def list_tools(self) -> Any: ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, meta: dict[str, Any] | None = None
    ) -> Any: ...

    async def send_ping(self) -> Any: ...


@dataclass(frozen=True)
class McpServerConfig:
    """One MCP server declaration (spec section 4).

    ``streamable-http``/``sse`` servers carry ``url``; ``stdio`` servers
    carry ``command`` (the stdio argv) plus non-sensitive ``env`` — they
    run inside a sandbox workload reached over HTTP (spec section 6.2),
    so their ``url`` is assigned by the runner, not declared here.
    """

    name: str
    url: str = ""
    transport: str = "streamable-http"  # "streamable-http" | "sse" | "stdio"
    credential_ref: str | None = None
    side_effects: str = "mutating"
    command: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


# Builds a READY session (transport + initialize) when entered; must run
# on the caller's loop — holders enter it in one task on their loop.
SessionFactory = Callable[[str, dict[str, str]], AbstractAsyncContextManager[Any]]
SessionFactoryBuilder = Callable[[str], SessionFactory]


class HttpMcpToolSource(McpToolSource):
    """Sync facade over one remote MCP server; owns the connection lifecycle.

    Robustness contract (spec section 12): dispatch failures of a healthy
    server (is_error results) never trip the breaker — the server is
    alive and answered. Transport-level failures kill the connection and
    count toward the breaker; after ``breaker_threshold`` consecutive
    failures the circuit opens for ``breaker_cooldown`` seconds; the next
    call after the cooldown is a half-open probe. One transparent
    reconnect attempt is made per call before the failure surfaces.
    """

    def __init__(
        self,
        *,
        name: str,
        url: str,
        session_factory: SessionFactory,
        headers: dict[str, str] | None = None,
        health_interval: float = 30.0,
        reconnect_backoff: float = 2.0,
        breaker_threshold: int = 5,
        breaker_cooldown: float = 30.0,
        max_concurrency: int = 8,
        acquire_timeout: float = 10.0,
        connect_timeout: float = 15.0,
        call_timeout: float = 60.0,
    ) -> None:
        self._name = name
        self._url = url
        self._session_factory = session_factory
        self._headers = dict(headers or {})
        self._health_interval = health_interval
        self._reconnect_backoff = reconnect_backoff
        self._breaker_threshold = breaker_threshold
        self._breaker_cooldown = breaker_cooldown
        self._acquire_timeout = acquire_timeout
        self._connect_timeout = connect_timeout
        self._call_timeout = call_timeout

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name=f"mcp-session-{name}", daemon=True
        )
        self._thread.start()

        self._connect_lock = threading.Lock()
        self._lifecycle: dict[str, Any] | None = None
        self._session: dict[str, Any] = {}
        self._ready = threading.Event()
        self._closed = False

        self._state = threading.Lock()  # breaker counters
        self._breaker_failures = 0
        self._open_until = 0.0
        self._half_open = False
        self._next_connect_at = 0.0
        self._last_used = 0.0
        self._semaphore = threading.BoundedSemaphore(max_concurrency)

    @property
    def name(self) -> str:
        return self._name

    # --- McpToolSource facade -------------------------------------------------

    def list_tools(self) -> list[McpToolDescriptor]:
        result = self._call("list_tools")
        return [descriptor_from_sdk_tool(tool) for tool in result.tools]

    def call_tool(
        self, name: str, arguments: dict[str, Any], *, meta: dict[str, Any] | None = None
    ) -> str:
        result = self._call("call_tool", name, arguments, meta=meta)
        return text_from_sdk_result(result, tool_name=name, server_name=self._name)

    def close(self) -> None:
        """Tear down the connection and the loop; safe to call repeatedly."""
        self._closed = True
        self._kill_connection()
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    # --- call path --------------------------------------------------------------

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if self._closed:
            raise SessionUnavailableError(f"server {self._name} is closed")
        self._guard_breaker()
        # Concurrency cap (spec section 12.4): bounded concurrent calls;
        # excess callers fail fast instead of queueing unboundedly.
        if not self._semaphore.acquire(timeout=self._acquire_timeout):
            raise SessionUnavailableError(
                f"server {self._name} concurrency cap exceeded ({self._acquire_timeout}s wait)"
            )
        try:
            return self._attempt(method, args, kwargs)
        finally:
            self._semaphore.release()

    def _attempt(self, method: str, args: tuple, kwargs: dict, *, allow_retry: bool = True) -> Any:
        try:
            session = self._connected_session(ignore_gate=not allow_retry)
            if self._idle_expired():
                self._submit(session.send_ping())
            self._last_used = time.monotonic()
            result = self._submit(getattr(session, method)(*args, **kwargs))
        except ReconnectBackoffError:
            # Throttle state, not a transport failure: no attempt was
            # made, so no breaker count and no reconnect path.
            raise
        except Exception as exc:  # noqa: BLE001 - transport failures take the reconnect path
            self._record_failure()
            self._kill_connection()
            if allow_retry and not self._closed:
                # One transparent reconnect per call (spec section 12.2).
                # The backoff gate does not apply here: the retry must
                # surface the root cause, not the throttle message.
                return self._attempt(method, args, kwargs, allow_retry=False)
            raise SessionUnavailableError(f"server {self._name} unavailable: {exc}") from exc
        self._record_success()
        return result

    def _idle_expired(self) -> bool:
        return self._last_used > 0 and (time.monotonic() - self._last_used) > self._health_interval

    # --- connection lifecycle -----------------------------------------------------

    def _connected_session(self, *, ignore_gate: bool = False) -> Any:
        with self._connect_lock:
            lifecycle = self._lifecycle
            if lifecycle is not None and not lifecycle["future"].done() and self._ready.is_set():
                return self._session["session"]
            if self._closed:
                raise SessionUnavailableError(f"server {self._name} is closed")
            now = time.monotonic()
            if not ignore_gate and now < self._next_connect_at:
                raise ReconnectBackoffError(
                    f"server {self._name} is reconnecting after a failed attempt; "
                    f"retry in {self._next_connect_at - now:.1f}s"
                )
            self._start_lifecycle()
            return self._session["session"]

    def _start_lifecycle(self) -> None:
        """Connect the session in ONE task on the holder loop (task-bound scopes).

        A failed connect attempt arms the reconnect backoff gate; a
        successful one clears it. Lifecycle death is detected promptly
        (polled) instead of waiting out the full connect timeout.
        """
        self._ready.clear()
        self._session.clear()
        done = threading.Event()  # per-lifecycle: wakes exactly this task

        async def lifecycle() -> None:
            async with self._session_factory(self._url, self._headers) as session:
                self._session["session"] = session
                self._ready.set()
                await asyncio.to_thread(done.wait)

        future = asyncio.run_coroutine_threadsafe(lifecycle(), self._loop)
        self._lifecycle = {"future": future, "done": done}
        deadline = time.monotonic() + self._connect_timeout
        while not self._ready.wait(timeout=0.05):
            if future.done():
                done.set()
                try:
                    future.result()  # surface the underlying connect error
                except Exception as exc:
                    self._lifecycle = None
                    self._next_connect_at = time.monotonic() + self._reconnect_backoff
                    raise SessionUnavailableError(
                        f"server {self._name} connection failed: {exc}"
                    ) from exc
            if time.monotonic() >= deadline:
                done.set()
                self._lifecycle = None
                self._next_connect_at = time.monotonic() + self._reconnect_backoff
                raise SessionUnavailableError(
                    f"server {self._name} did not connect within {self._connect_timeout}s"
                )
        self._next_connect_at = 0.0

    def _kill_connection(self) -> None:
        with self._connect_lock:
            lifecycle = self._lifecycle
            self._lifecycle = None
            self._session.clear()
            self._ready.clear()
        if lifecycle is not None and not lifecycle["future"].done():
            lifecycle["done"].set()  # release the waiting lifecycle task
            try:
                lifecycle["future"].result(timeout=5.0)
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass

    # --- breaker (spec section 12.3) ----------------------------------------------

    def _guard_breaker(self) -> None:
        with self._state:
            now = time.monotonic()
            if self._open_until > now:
                raise CircuitOpenError(
                    f"server {self._name} circuit open for another "
                    f"{self._open_until - now:.1f}s"
                )
            if self._open_until > 0:
                # Cooldown elapsed: allow a single half-open probe.
                self._open_until = 0.0
                self._half_open = True

    def _record_failure(self) -> None:
        with self._state:
            self._breaker_failures += 1
            if self._half_open or self._breaker_failures >= self._breaker_threshold:
                self._open_until = time.monotonic() + self._breaker_cooldown
                self._half_open = False
                self._breaker_failures = 0

    def _record_success(self) -> None:
        with self._state:
            self._breaker_failures = 0
            self._half_open = False
            self._open_until = 0.0

    # --- loop -----------------------------------------------------------------------

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=self._call_timeout)


class SessionPool:
    """Cache of session holders keyed by (server name, credential fingerprint).

    Same server + same resolved material shares one connection and its
    health/reconnect state; different credentials (once per-user identity
    is propagated) get isolated holders (spec section 9.1).
    """

    def __init__(self, builder: SessionFactoryBuilder) -> None:
        self._builder = builder
        self._holders: dict[tuple[str, str], HttpMcpToolSource] = {}
        self._lock = threading.Lock()

    def get(self, config: McpServerConfig, material: Any) -> HttpMcpToolSource:
        key = (config.name, credential_fingerprint(material))
        with self._lock:
            holder = self._holders.get(key)
            if holder is None:
                holder = HttpMcpToolSource(
                    name=config.name,
                    url=config.url,
                    session_factory=self._builder(config.transport),
                    headers=dict(material.headers),
                )
                self._holders[key] = holder
            return holder

    def close_all(self) -> None:
        with self._lock:
            holders, self._holders = list(self._holders.values()), {}
        for holder in holders:
            holder.close()
