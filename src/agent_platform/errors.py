class PlatformError(Exception):
    """Base error for platform-level failures."""


class NotFoundError(PlatformError):
    """Raised when a requested platform resource does not exist."""


class InvalidStateTransitionError(PlatformError, ValueError):
    """Raised when a lifecycle transition violates the runtime contract."""


class ToolPermissionDeniedError(PlatformError):
    """Raised when a tool call is rejected by a permission policy."""
