"""MCP Gateway: unified tool access layer (03-mcp-gateway-architecture.md).

Implements the V1 scope (Tool Registry, MCP Tool Invocation, Permission,
Timeout, Basic Retry, Audit) plus the V2 Stage B invocation hardening
(mcp-gateway-spec.md): server__tool namespacing (section 5), declared
side_effects classes with the retry matrix (section 7), idempotency keys
over request _meta with an in-flight dedup window (section 7.3), input
validation against advertised schemas (section 10) and output size
limits (section 11). Automatic tool discovery and agentic tool selection
stay out of scope; sources are registered explicitly and their tools are
enumerated once at registration time.

The gateway implements the platform ToolCapability interface, so the
Agent Runtime consumes it exactly like the in-memory registry without
knowing MCP exists (dependency direction: runtime -> capabilities ->
this adapter).
"""

import concurrent.futures
import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from agent_platform.errors import (
    NotFoundError,
    PlatformError,
    ToolPermissionDeniedError,
)
from agent_platform.runtime.capabilities.tool import ToolCallRequest, ToolResult, ToolSpec
from agent_platform.runtime.capabilities.tool_capability import ToolCapability

from agent_platform.mcp.audit import AuditEntry, AuditSink
from agent_platform.mcp.source import McpToolCallError, McpToolSource
from agent_platform.mcp.validation import validate_against_schema

logger = logging.getLogger(__name__)

# Declared side-effects classes (spec section 7); "mutating" is the
# conservative default because it disables the riskier retries.
_SIDE_EFFECT_CLASSES = ("readonly", "idempotent", "mutating")
# Idempotency key namespace inside request _meta (spec section 7.3).
_IDEMPOTENCY_META_KEY = "platform.idempotency_key"


class ToolRegistrationError(PlatformError):
    """Raised when a tool name collides with an already registered tool."""


class ToolTimeoutError(PlatformError):
    """Internal: one execution attempt exceeded the configured timeout."""


def _fingerprint(arguments: dict[str, Any]) -> tuple[int, str]:
    """Byte size and digest of the canonical arguments; values never leave here."""
    raw = json.dumps(arguments, sort_keys=True, default=str).encode("utf-8")
    return len(raw), hashlib.sha256(raw).hexdigest()


class McpToolGateway(ToolCapability):
    """Registry + permission + validation + idempotency + timeout + retry + audit."""

    def __init__(
        self,
        *,
        allowed_tools: set[str] | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 0,
        audit_sink: AuditSink | None = None,
        output_limit_bytes: int = 256 * 1024,
        idempotency_ttl_seconds: float = 60.0,
    ) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._native_handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        # Namespaced tool name -> (owning source, server-local tool name).
        self._mcp: dict[str, tuple[McpToolSource, str]] = {}
        self._side_effects: dict[str, str] = {}
        self._allowed_tools = allowed_tools
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._audit_sink = audit_sink
        self._audit_lock = threading.Lock()
        self._output_limit_bytes = output_limit_bytes
        self._idempotency_ttl = idempotency_ttl_seconds
        self._idem_lock = threading.Lock()
        self._idem_inflight: set[tuple[str, str, str]] = set()
        self._idem_recent: dict[tuple[str, str, str], float] = {}

        self._close_lock = threading.Lock()
        self._closed = False
        self._close_callbacks: list[Callable[[], None]] = []

    def on_close(self, callback: Callable[[], None]) -> None:
        """Register a best-effort shutdown callback (pools, runners)."""
        self._close_callbacks.append(callback)

    def close(self) -> None:
        """Best-effort shutdown: close registered sources, then callbacks.

        Safe to call repeatedly; failures are logged, never raised.
        """
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        seen: set[int] = set()
        for source, _local in list(self._mcp.values()):
            if id(source) in seen:
                continue
            seen.add(id(source))
            closer = getattr(source, "close", None)
            if closer is None:
                continue
            try:
                closer()
            except Exception:  # noqa: BLE001 - shutdown is best-effort
                logger.warning("source close failed for %s", source.name, exc_info=True)
        for callback in self._close_callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001 - shutdown is best-effort
                logger.warning("gateway close callback failed", exc_info=True)

    # --- registration ---------------------------------------------------------

    def register_native(
        self,
        spec: ToolSpec,
        handler: Callable[[dict[str, Any]], Any],
        *,
        side_effects: str = "mutating",
    ) -> None:
        """Register a native (in-process) tool."""
        self._check_class(side_effects)
        self._claim(spec.name)
        self._specs[spec.name] = spec
        self._native_handlers[spec.name] = handler
        self._side_effects[spec.name] = side_effects
        self._audit(action="register", tool=spec.name, server=None, outcome="ok")

    def register_server(self, source: McpToolSource, *, side_effects: str = "mutating") -> None:
        """Enumerate an MCP server's tools once and register them namespaced.

        Registration-time discovery only: no automatic or agentic tool
        discovery (03-mcp-gateway-architecture.md section 5). Tool names
        are claimed as ``{server}__{tool}`` (spec section 5), so servers
        cannot collide with each other; a collision with a native tool or
        a double registration rejects the whole server instead of leaving
        partial state. Upstream calls always use the server-local name.
        """
        self._check_class(side_effects)
        descriptors = source.list_tools()
        namespaced = {
            f"{source.name}__{descriptor.name}": descriptor for descriptor in descriptors
        }
        for name in namespaced:
            self._claim(name)
        for name, descriptor in namespaced.items():
            self._specs[name] = ToolSpec(
                name=name,
                description=descriptor.description,
                parameters=descriptor.input_schema,
            )
            self._mcp[name] = (source, descriptor.name)
            self._side_effects[name] = side_effects
            self._audit(action="register", tool=name, server=source.name, outcome="ok")

    def list_tools(self) -> list[ToolSpec]:
        return list(self._specs.values())

    # --- invocation -----------------------------------------------------------

    def invoke(self, request: ToolCallRequest) -> ToolResult:
        spec = self._specs.get(request.name)
        mcp = self._mcp.get(request.name)
        server = mcp[0] if mcp else None
        if spec is None:
            self._audit(
                action="invoke",
                tool=request.name,
                server=None,
                outcome="not_found",
                error=f"tool not found: {request.name}",
            )
            raise NotFoundError(f"tool not found: {request.name}")
        if self._allowed_tools is not None and request.name not in self._allowed_tools:
            self._audit(
                action="invoke",
                tool=request.name,
                server=server.name if server else None,
                outcome="denied",
                error=f"tool not allowed: {request.name}",
            )
            raise ToolPermissionDeniedError(f"tool not allowed: {request.name}")

        # Input validation (spec section 10): model-visible error result,
        # no dispatch, no exception.
        errors = validate_against_schema(request.arguments, spec.parameters)
        if errors:
            message = "; ".join(errors)
            self._invoke_audit(
                request, server, outcome="invalid", error=message
            )
            return ToolResult(call_id=request.id, name=request.name, content="", error=message)

        # In-flight dedup for remote dispatch (spec section 7.3). The key
        # is the logical call id: retries of one logical invocation happen
        # inside _execute and reuse it; a concurrent caller with the same
        # key is rejected as a duplicate instead of double-dispatched.
        local_name = mcp[1] if mcp else None
        guard_key = (server.name, local_name, request.id) if server else None
        if guard_key is not None:
            with self._idem_lock:
                self._purge_expired()
                if guard_key in self._idem_inflight:
                    message = (
                        f"duplicate invocation of {request.name}: an identical call "
                        f"is already in flight (key: {_IDEMPOTENCY_META_KEY})"
                    )
                    self._invoke_audit(request, server, outcome="duplicate", error=message)
                    return ToolResult(
                        call_id=request.id, name=request.name, content="", error=message
                    )
                self._idem_inflight.add(guard_key)
        try:
            return self._execute(
                request,
                server=server,
                local_name=local_name,
                handler=self._native_handlers.get(request.name),
                side_effects=self._side_effects.get(request.name, "mutating"),
            )
        finally:
            if guard_key is not None:
                with self._idem_lock:
                    self._idem_inflight.discard(guard_key)
                    self._idem_recent[guard_key] = time.monotonic() + self._idempotency_ttl

    # --- execution ------------------------------------------------------------

    def _execute(
        self,
        request: ToolCallRequest,
        *,
        server: McpToolSource | None,
        local_name: str | None,
        handler: Callable[[dict[str, Any]], Any] | None,
        side_effects: str,
    ) -> ToolResult:
        """Run a tool with a per-attempt hard timeout and the retry matrix.

        Matrix (spec section 7.2): ``McpToolCallError`` is a server-side
        failure result — the tool already executed, never retried for any
        class. A gateway timeout is unknown server progress — retried for
        readonly/idempotent only, final for mutating. Every other
        exception is treated as a dispatch-phase error — retriable for
        all classes. Execution failures are returned as error results so
        the model loop can see them; only lookup and permission failures
        raise.
        """
        attempts = 1 + self._max_retries
        last_error: str | None = None
        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                if handler is not None:
                    content = self._run_attempt(lambda: handler(request.arguments))
                else:
                    content = self._run_attempt(
                        lambda: server.call_tool(
                            local_name,
                            request.arguments,
                            meta={_IDEMPOTENCY_META_KEY: request.id},
                        )
                    )
            except ToolTimeoutError:
                last_error = f"tool {request.name} timed out after {self._timeout_seconds}s"
                self._invoke_audit(
                    request, server, outcome="timeout", duration_ms=_elapsed(started),
                    error=last_error,
                )
                if attempt + 1 < attempts and side_effects in ("readonly", "idempotent"):
                    continue
                return ToolResult(
                    call_id=request.id, name=request.name, content="", error=last_error
                )
            except McpToolCallError as exc:
                # Final for every class (spec section 7.1): the server
                # already executed the tool.
                self._invoke_audit(
                    request, server, outcome="error", duration_ms=_elapsed(started),
                    error=str(exc),
                )
                return ToolResult(
                    call_id=request.id, name=request.name, content="",
                    error=self._truncate(str(exc)),
                )
            except Exception as exc:  # noqa: BLE001 - tool failures become results
                last_error = str(exc)
                self._invoke_audit(
                    request, server, outcome="error", duration_ms=_elapsed(started),
                    error=last_error,
                )
                continue
            self._invoke_audit(request, server, outcome="ok", duration_ms=_elapsed(started))
            return ToolResult(
                call_id=request.id,
                name=request.name,
                content=self._truncate(content if isinstance(content, str) else str(content)),
            )
        return ToolResult(
            call_id=request.id, name=request.name, content="", error=self._truncate(last_error or "")
        )

    def _run_attempt(self, fn: Callable[[], Any]) -> Any:
        """Run fn on a worker thread with a hard timeout.

        The executor is shut down without waiting so a timed-out call
        does not block the caller past the deadline; its worker thread
        ends when the underlying call eventually returns.
        """
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mcp-gateway"
        )
        try:
            future = executor.submit(fn)
            try:
                return future.result(timeout=self._timeout_seconds)
            except concurrent.futures.TimeoutError:
                if not future.done():
                    raise ToolTimeoutError(
                        f"tool timed out after {self._timeout_seconds}s"
                    ) from None
                raise  # the worker itself raised TimeoutError; not a gateway timeout
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    # --- helpers --------------------------------------------------------------

    @staticmethod
    def _check_class(side_effects: str) -> None:
        if side_effects not in _SIDE_EFFECT_CLASSES:
            raise ValueError(
                f"invalid side_effects class {side_effects!r}; "
                f"expected one of {_SIDE_EFFECT_CLASSES}"
            )

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [key for key, expiry in self._idem_recent.items() if expiry <= now]
        for key in expired:
            del self._idem_recent[key]

    def _claim(self, name: str) -> None:
        if name in self._specs:
            owner = self._mcp[name][0].name if name in self._mcp else "native"
            raise ToolRegistrationError(f"tool name already registered: {name} (owner: {owner})")

    def _truncate(self, text: str) -> str:
        """Bound output size with an explicit marker (spec section 11)."""
        raw = text.encode("utf-8")
        if len(raw) <= self._output_limit_bytes:
            return text
        return raw[: self._output_limit_bytes].decode("utf-8", "ignore") + (
            f"\n[truncated by mcp gateway, {len(raw)} bytes total]"
        )

    def _invoke_audit(
        self,
        request: ToolCallRequest,
        server: McpToolSource | None,
        *,
        outcome: str,
        duration_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        size, digest = _fingerprint(request.arguments)
        self._audit(
            action="invoke",
            tool=request.name,
            server=server.name if server else None,
            outcome=outcome,
            duration_ms=duration_ms,
            error=error,
            arguments_bytes=size,
            arguments_digest=digest,
        )

    def _audit(
        self,
        *,
        action: str,
        tool: str,
        server: str | None,
        outcome: str,
        duration_ms: float | None = None,
        error: str | None = None,
        arguments_bytes: int | None = None,
        arguments_digest: str | None = None,
    ) -> None:
        if self._audit_sink is None:
            return
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc),
            action=action,
            tool=tool,
            server=server,
            outcome=outcome,
            duration_ms=duration_ms,
            error=error,
            arguments_bytes=arguments_bytes,
            arguments_digest=arguments_digest,
        )
        # Failure isolation (same contract as the EventBus): a broken audit
        # sink must never break tool invocation.
        try:
            with self._audit_lock:
                self._audit_sink.record(entry)
        except Exception:  # noqa: BLE001 - audit is best-effort
            logger.warning("audit sink failed for action %s on %s", action, tool, exc_info=True)


def _elapsed(started: float) -> float:
    return (time.perf_counter() - started) * 1000
