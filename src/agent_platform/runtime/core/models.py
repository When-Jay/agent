from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    QUEUED = "queued"  # dispatch: created by API, waiting for a runtime worker
    CREATED = "created"  # created outside dispatch (direct/test usage)
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Application:
    id: str
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Session:
    id: str
    application_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Run:
    id: str
    application_id: str
    session_id: str
    runtime_type: str
    status: RunStatus
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class Execution:
    id: str
    run_id: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class State:
    run_id: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Checkpoint:
    id: str
    run_id: str
    state: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class Artifact:
    id: str
    run_id: str
    name: str
    uri: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
