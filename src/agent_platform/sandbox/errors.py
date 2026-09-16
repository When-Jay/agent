"""Sandbox error model.

Provider-specific failures must be normalized into these types before
they reach Agent/Workflow execution code.
"""

from agent_platform.errors import PlatformError


class SandboxError(PlatformError):
    """Base error for sandbox failures."""


class SandboxNotFound(SandboxError):
    """Raised when a sandbox id is unknown."""


class SandboxUnavailable(SandboxError):
    """Raised when the sandbox cannot accept work in its current state."""


class ExecutionTimeout(SandboxError):
    """Raised when a command exceeded its execution timeout."""


class ExecutionCancelled(SandboxError):
    """Raised when a command was cancelled before completion."""


class ExecutionFailed(SandboxError):
    """Raised when a command failed without a more specific error."""


class ResourceLimitExceeded(SandboxError):
    """Raised when a request exceeds configured resource limits."""


class PermissionDenied(SandboxError):
    """Raised when policy denies the requested operation or path."""


class NetworkDenied(SandboxError):
    """Raised when policy denies the requested network access."""


class ProviderError(SandboxError):
    """Raised when a provider fails with an unclassified error."""
