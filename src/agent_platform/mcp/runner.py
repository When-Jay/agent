"""Server runner port for sandbox-hosted MCP servers (mcp-gateway-spec.md section 6.2).

Local stdio execution is prohibited: a stdio declaration is materialized
as a sandbox workload running a sidecar that bridges the server to
Streamable HTTP, and the platform connects to it like any remote server.
The gateway knows only this port; implementations (composition root) see
the sandbox stack. Endpoints change across recovery, so callers resolve
them per (re)connect instead of caching (sandbox-spec.md section 9.2).
"""

from typing import Protocol

from agent_platform.errors import PlatformError
from agent_platform.mcp.credentials import CredentialMaterial
from agent_platform.mcp.sessions import McpServerConfig


class RunnerUnavailableError(PlatformError):
    """Raised when a runner-hosted server has no reachable endpoint."""


class ServerRunner(Protocol):
    """Lifecycle for MCP servers hosted in sandbox workloads.

    All operations are synchronous and may block while the workload
    starts; callers on event loops must invoke them from worker threads
    (the session holder's factory path already does). ``ensure_running``
    is idempotent per server name, restarts the workload when it died,
    and doubles as the activity marker for idle sweeping.
    """

    def ensure_running(self, config: McpServerConfig, material: CredentialMaterial) -> str:
        """Return the server's endpoint URL, starting the workload if needed."""
        ...

    def stop(self, name: str) -> None:
        """Destroy one server's workload; best-effort, never raises for a
        missing server."""
        ...

    def close(self) -> None:
        """Destroy all workloads and stop background activity (gateway
        shutdown); best-effort."""
        ...
