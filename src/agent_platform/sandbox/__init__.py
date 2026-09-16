"""Sandbox: isolated execution environments behind provider-independent contracts."""

from agent_platform.sandbox.errors import (
    ExecutionCancelled,
    ExecutionFailed,
    ExecutionTimeout,
    NetworkDenied,
    PermissionDenied,
    ProviderError,
    ResourceLimitExceeded,
    SandboxError,
    SandboxNotFound,
    SandboxUnavailable,
)
from agent_platform.sandbox.interface import SandboxProvider
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.models import (
    ExecutionRequest,
    ExecutionResult,
    FileDownload,
    FileDownloadResult,
    FileUpload,
    FileUploadResult,
    HealthStatus,
    NetworkMode,
    NetworkPolicy,
    Sandbox,
    SandboxResources,
    SandboxSpec,
    SandboxStatus,
    WorkspaceMount,
)
from agent_platform.sandbox.policy import SandboxPolicy
from agent_platform.sandbox.registry import SandboxProviderRegistry


def __getattr__(name: str):
    # Lazy export: DockerSandboxProvider needs the docker SDK, so it is
    # only imported on first access, keeping the sandbox core importable
    # without infrastructure SDKs.
    if name == "DockerSandboxProvider":
        from agent_platform.sandbox.providers.docker import DockerSandboxProvider

        return DockerSandboxProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "DockerSandboxProvider",
    "ExecutionCancelled",
    "ExecutionFailed",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionTimeout",
    "FileDownload",
    "FileDownloadResult",
    "FileUpload",
    "FileUploadResult",
    "HealthStatus",
    "NetworkDenied",
    "NetworkMode",
    "NetworkPolicy",
    "PermissionDenied",
    "ProviderError",
    "ResourceLimitExceeded",
    "Sandbox",
    "SandboxError",
    "SandboxNotFound",
    "SandboxPolicy",
    "SandboxProvider",
    "SandboxProviderRegistry",
    "SandboxResources",
    "SandboxSpec",
    "SandboxStatus",
    "SandboxUnavailable",
    "SandboxManager",
    "WorkspaceMount",
]
