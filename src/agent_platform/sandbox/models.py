"""Provider-independent sandbox domain models."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SandboxStatus(str, Enum):
    CREATING = "creating"
    READY = "ready"
    BUSY = "busy"
    UNHEALTHY = "unhealthy"
    RECOVERING = "recovering"
    RELEASING = "releasing"
    DESTROYED = "destroyed"


class NetworkMode(str, Enum):
    NONE = "none"
    INTERNAL = "internal"
    ALLOWLIST = "allowlist"
    INTERNET_ONLY = "internet_only"
    FULL = "full"


@dataclass(frozen=True)
class SandboxResources:
    cpu: str = "1"  # k8s-style quantity: "1", "500m"
    memory: str = "2Gi"  # k8s-style quantity: "2Gi", "512Mi"
    pids: int = 256
    ephemeral_storage: str = "5Gi"


@dataclass(frozen=True)
class NetworkPolicy:
    mode: NetworkMode = NetworkMode.INTERNET_ONLY
    allowlist: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkspaceMount:
    """Persistent workspace identity; providers map it to their own storage."""

    workspace_id: str
    mount_path: str = "/workspace"


@dataclass(frozen=True)
class SandboxSpec:
    """Creation request. sandbox_id is assigned by the SandboxManager."""

    image: str
    tenant_id: str
    user_id: str
    session_id: str
    resources: SandboxResources = field(default_factory=SandboxResources)
    network_policy: NetworkPolicy = field(default_factory=NetworkPolicy)
    workspace: WorkspaceMount = field(default_factory=lambda: WorkspaceMount(workspace_id=""))
    env: dict[str, str] = field(default_factory=dict)
    sandbox_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Sandbox:
    """A managed sandbox instance. Only the SandboxManager mutates status."""

    sandbox_id: str
    provider: str
    image: str
    tenant_id: str
    user_id: str
    session_id: str
    workspace: WorkspaceMount
    resources: SandboxResources
    network_policy: NetworkPolicy
    status: SandboxStatus = SandboxStatus.CREATING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionRequest:
    """Command execution request. request_id is used for idempotency."""

    request_id: str
    command: str | list[str]
    cwd: str = "/workspace"
    timeout: float = 60.0
    env: dict[str, str] = field(default_factory=dict)
    stdin: str = ""
    output_limit: int = 64 * 1024


@dataclass(frozen=True)
class ExecutionResult:
    request_id: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FileUpload:
    path: str
    content: bytes


@dataclass(frozen=True)
class FileDownload:
    path: str


@dataclass(frozen=True)
class FileUploadResult:
    path: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class FileDownloadResult:
    path: str
    content: bytes | None = None
    error: str | None = None


@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    detail: str = ""
