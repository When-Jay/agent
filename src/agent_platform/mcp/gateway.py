"""MCP Gateway V1: unified tool access layer (03-mcp-gateway-architecture.md).

Implements the V1 scope: Tool Registry, MCP Tool Invocation, Permission,
Timeout, Basic Retry, Audit/Trace. Automatic tool discovery and agentic
tool selection stay out of scope; sources are registered explicitly and
their tools are enumerated once at registration time.

The gateway implements the platform ToolCapability interface, so the
Agent Runtime consumes it exactly like the in-memory registry without
knowing MCP exists (dependency direction: runtime -> capabilities ->
this adapter).
"""

import concurrent.futures
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
from agent_platform.mcp.source import McpToolSource

logger = logging.getLogger(__name__)


class ToolRegistrationError(PlatformError):
    """Raised when a tool name collides with an already registered tool."""


class ToolTimeoutError(PlatformError):
    """Internal: one execution attempt exceeded the configured timeout."""


class McpToolGateway(ToolCapability):
    """Registry + permission + timeout + retry + audit over native and MCP tools."""

    def __init__(
        self,
        *,
        allowed_tools: set[str] | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 0,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._native_handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._servers: dict[str, McpToolSource] = {}  # tool name -> owning source
        self._allowed_tools = allowed_tools
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._audit_sink = audit_sink
        self._audit_lock = threading.Lock()

    def register_native(
        self, spec: ToolSpec, handler: Callable[[dict[str, Any]], Any]
    ) -> None:
        """Register a native (in-process) tool."""
        self._claim(spec.name)
        self._specs[spec.name] = spec
        self._native_handlers[spec.name] = handler
        self._audit(action="register", tool=spec.name, server=None, outcome="ok")

    def register_server(self, source: McpToolSource) -> None:
        """Enumerate an MCP server's tools once and register them.

        Registration-time discovery only: V1 has no automatic or agentic
        tool discovery (03-mcp-gateway-architecture.md section 5). All
        names are claimed before anything is registered, so a collision
        rejects the whole server instead of leaving partial state.
        """
        descriptors = source.list_tools()
        for descriptor in descriptors:
            self._claim(descriptor.name)
        for descriptor in descriptors:
            self._specs[descriptor.name] = ToolSpec(
                name=descriptor.name,
                description=descriptor.description,
                parameters=descriptor.input_schema,
            )
            self._servers[descriptor.name] = source
            self._audit(action="register", tool=descriptor.name, server=source.name, outcome="ok")

    def list_tools(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def invoke(self, request: ToolCallRequest) -> ToolResult:
        spec = self._specs.get(request.name)
        server = self._servers.get(request.name)
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

        handler = self._native_handlers.get(request.name)
        return self._execute(request, server=server, handler=handler)

    # --- execution -----------------------------------------------------------

    def _execute(
        self,
        request: ToolCallRequest,
        *,
        server: McpToolSource | None,
        handler: Callable[[dict[str, Any]], Any] | None,
    ) -> ToolResult:
        """Run a tool with a per-attempt hard timeout and basic retry.

        Retry applies to raised exceptions; a timed-out attempt aborts
        immediately (retrying would multiply the wall-clock budget).
        Execution failures are returned as error results so the model
        loop can see them; only lookup and permission failures raise.
        """
        attempts = 1 + self._max_retries
        last_error: str | None = None
        for _ in range(attempts):
            started = time.perf_counter()
            try:
                if handler is not None:
                    content = self._run_attempt(lambda: handler(request.arguments))
                else:
                    content = self._run_attempt(
                        lambda: server.call_tool(request.name, request.arguments)
                    )
            except ToolTimeoutError:
                self._invoke_audit(
                    request, server, outcome="timeout", duration_ms=_elapsed(started),
                    error=f"tool {request.name} timed out after {self._timeout_seconds}s",
                )
                return ToolResult(
                    call_id=request.id,
                    name=request.name,
                    content="",
                    error=f"tool {request.name} timed out after {self._timeout_seconds}s",
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
                content=content if isinstance(content, str) else str(content),
            )
        return ToolResult(call_id=request.id, name=request.name, content="", error=last_error)

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

    # --- helpers -------------------------------------------------------------

    def _claim(self, name: str) -> None:
        if name in self._specs:
            owner = self._servers[name].name if name in self._servers else "native"
            raise ToolRegistrationError(f"tool name already registered: {name} (owner: {owner})")

    def _invoke_audit(
        self,
        request: ToolCallRequest,
        server: McpToolSource | None,
        *,
        outcome: str,
        duration_ms: float,
        error: str | None = None,
    ) -> None:
        self._audit(
            action="invoke",
            tool=request.name,
            server=server.name if server else None,
            outcome=outcome,
            duration_ms=duration_ms,
            error=error,
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
