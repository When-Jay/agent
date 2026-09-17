"""Audit trail for the MCP gateway (03-mcp-gateway-architecture.md section 5).

V1 treats audit and trace as one structured record stream: every gateway
action (register / invoke) produces an AuditEntry. Distributed tracing
stays with observability through the runtime TOOL_CALL_* events; the
audit trail is the gateway-local, always-on record.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class AuditEntry:
    """One recorded gateway action.

    Redaction rules (mcp-gateway-spec.md section 9.3): arguments are
    recorded as byte size and SHA-256 digest only — never as values.
    """

    timestamp: datetime
    action: str  # "register" | "invoke"
    tool: str
    server: str | None = None  # None for native tools
    outcome: str = "ok"  # "ok" | "error" | "timeout" | "denied" | "not_found" | "invalid" | "duplicate"
    duration_ms: float | None = None
    error: str | None = None
    arguments_bytes: int | None = None
    arguments_digest: str | None = None


class AuditSink(Protocol):
    """Destination for audit entries; implementations must never raise."""

    def record(self, entry: AuditEntry) -> None: ...


class InMemoryAuditSink:
    """Thread-safe in-process audit sink (local development and tests)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    @property
    def entries(self) -> list[AuditEntry]:
        with self._lock:
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc)
