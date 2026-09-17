"""Sandbox-backed ServerRunner (mcp-gateway-spec.md section 6.2).

Composition-root adapter: implements the mcp/ ServerRunner port over the
SandboxManager. A stdio declaration becomes a sandbox workload running a
bridge image; the platform reaches it over Streamable HTTP at the
sandbox's declared-port endpoint (sandbox-spec.md sections 9.1-9.2).

Bridge image contract (documented, not enforced here): the image reads
``MCP_RUNNER_COMMAND`` (JSON argv) and ``MCP_RUNNER_BRIDGE_PORT`` from
its environment, spawns the stdio server with that argv merged into its
environment, and bridges stdio to Streamable HTTP on the bridge port.

Lifecycle (spec section 6.2): workloads start lazily at registration,
are restarted on demand after death (endpoints may change — callers
re-resolve per connect), are swept after an idle TTL, and are destroyed
on close. Destroy is best-effort and never blocks callers.

All state mutations run on one dedicated event loop, so no extra locking
is needed; sync callers (gateway shutdown, sweeper thread, session
factories) submit coroutines to that loop.
"""

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable

from agent_platform.mcp.credentials import CredentialMaterial
from agent_platform.mcp.runner import RunnerUnavailableError
from agent_platform.mcp.sessions import McpServerConfig
from agent_platform.sandbox.errors import SandboxError, SandboxNotFound
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.models import PortSpec, Sandbox, SandboxSpec, WorkspaceMount

logger = logging.getLogger(__name__)

BRIDGE_COMMAND_VAR = "MCP_RUNNER_COMMAND"
BRIDGE_PORT_VAR = "MCP_RUNNER_BRIDGE_PORT"
DEFAULT_BRIDGE_PORT = 8080

# Sandbox statuses under which an existing runner workload is still usable.
_USABLE_STATUSES = {"ready", "busy"}


class SandboxServerRunner:
    """ServerRunner over SandboxManager for stdio MCP servers."""

    def __init__(
        self,
        manager: SandboxManager,
        *,
        image: str,
        provider: str | None = None,
        tenant_id: str = "platform",
        user_id: str = "mcp-runner",
        workspace_prefix: str = "mcp-runner",
        port_name: str = "mcp",
        bridge_port: int = DEFAULT_BRIDGE_PORT,
        idle_ttl_seconds: float = 900.0,
        sweep_interval: float | None = 60.0,
        operation_timeout: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._manager = manager
        self._image = image
        self._provider = provider
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._workspace_prefix = workspace_prefix
        self._port_name = port_name
        self._bridge_port = bridge_port
        self._idle_ttl = idle_ttl_seconds
        self._operation_timeout = operation_timeout
        self._clock = clock

        # name -> {"sandbox_id", "last_used", "config", "material"}; only
        # the runner loop touches this dict.
        self._servers: dict[str, dict] = {}

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="mcp-sandbox-runner", daemon=True
        )
        self._thread.start()

        self._stop_sweeper = threading.Event()
        self._sweeper: threading.Thread | None = None
        self._close_lock = threading.Lock()
        self._closed = False
        if sweep_interval is not None:
            self._sweeper = threading.Thread(
                target=self._sweep_loop, args=(sweep_interval,), daemon=True
            )
            self._sweeper.start()

    # --- ServerRunner port ----------------------------------------------------

    def ensure_running(self, config: McpServerConfig, material: CredentialMaterial) -> str:
        """Return the server's endpoint URL, (re)starting the workload if needed."""
        self._check_config(config)
        return self._submit(self._ensure(config, material), timeout=self._operation_timeout)

    def stop(self, name: str) -> None:
        """Destroy one workload; best-effort (spec section 6.2)."""
        try:
            self._submit(self._destroy(name), timeout=self._operation_timeout)
        except Exception:  # noqa: BLE001 - destroy never blocks callers
            logger.warning("runner stop failed for server %s", name, exc_info=True)

    def sweep(self) -> None:
        """Destroy workloads idle beyond the TTL; best-effort.

        Called periodically by the internal sweeper thread; also usable
        from external schedulers (``sweep_interval=None`` disables the
        built-in thread).
        """
        try:
            self._submit(self._sweep(), timeout=self._operation_timeout)
        except Exception:  # noqa: BLE001 - sweeping is best-effort
            logger.warning("runner sweep failed", exc_info=True)

    def close(self) -> None:
        """Destroy all workloads and stop background activity; best-effort.

        Idempotent: calls after the first return immediately (submitting
        work to the already-stopped loop would hang forever).
        """
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._stop_sweeper.set()
        if self._sweeper is not None and self._sweeper.is_alive():
            self._sweeper.join(timeout=5)
        try:
            self._submit(self._close_all(), timeout=self._operation_timeout)
        except Exception:  # noqa: BLE001 - shutdown is best-effort
            logger.warning("runner close failed", exc_info=True)
        if self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    # --- implementation (runs on the runner loop) ------------------------------

    async def _ensure(self, config: McpServerConfig, material: CredentialMaterial) -> str:
        entry = self._servers.get(config.name)
        if entry is not None:
            endpoint = await self._existing_endpoint(entry)
            if endpoint is not None:
                entry["last_used"] = self._clock()
                return endpoint
            # Dead/unusable workload: destroy best-effort, then recreate.
            await self._destroy(config.name)

        spec = SandboxSpec(
            image=self._image,
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            session_id=config.name,
            workspace=WorkspaceMount(
                workspace_id=f"{self._workspace_prefix}-{config.name}"
            ),
            env=self._runner_env(config, material),
            ports=(PortSpec(name=self._port_name, container_port=self._bridge_port),),
            metadata={"mcp_server": config.name, "mcp_runner": "true"},
        )
        sandbox = await self._manager.create(spec, provider=self._provider)
        self._servers[config.name] = {
            "sandbox_id": sandbox.sandbox_id,
            "last_used": self._clock(),
            "config": config,
            "material": material,
        }
        return self._endpoint(config.name, sandbox)

    async def _existing_endpoint(self, entry: dict) -> str | None:
        try:
            sandbox = await self._manager.get(entry["sandbox_id"])
        except SandboxNotFound:
            return None
        if sandbox.status.value not in _USABLE_STATUSES:
            return None
        for endpoint in sandbox.endpoints:
            if endpoint.name == self._port_name:
                return f"http://{endpoint.address}"
        return None

    async def _destroy(self, name: str) -> None:
        entry = self._servers.pop(name, None)
        if entry is None:
            return
        try:
            await self._manager.destroy(entry["sandbox_id"])
        except SandboxNotFound:
            pass
        except SandboxError as exc:
            logger.warning("runner destroy failed for server %s: %s", name, exc)

    async def _close_all(self) -> None:
        for name in list(self._servers):
            await self._destroy(name)

    async def _sweep(self) -> None:
        now = self._clock()
        for name, entry in list(self._servers.items()):
            if now - entry["last_used"] > self._idle_ttl:
                logger.info("sweeping idle runner workload for server %s", name)
                await self._destroy(name)

    def _endpoint(self, name: str, sandbox: Sandbox) -> str:
        for endpoint in sandbox.endpoints:
            if endpoint.name == self._port_name:
                return f"http://{endpoint.address}"
        raise RunnerUnavailableError(
            f"runner workload for server {name!r} has no {self._port_name!r} endpoint"
        )

    def _runner_env(self, config: McpServerConfig, material: CredentialMaterial) -> dict[str, str]:
        # CredentialMaterial.env rides on the sandbox environment (spec
        # section 6.4): resolved at create time, never on the command line.
        return {
            **config.env,
            **material.env,
            BRIDGE_COMMAND_VAR: json.dumps(list(config.command)),
            BRIDGE_PORT_VAR: str(self._bridge_port),
        }

    @staticmethod
    def _check_config(config: McpServerConfig) -> None:
        if config.transport != "stdio":
            raise ValueError(
                f"sandbox runner hosts stdio servers only, got transport {config.transport!r}"
            )
        if not config.command:
            raise ValueError(f"stdio server {config.name!r} has no command")

    # --- infrastructure ---------------------------------------------------------

    def _sweep_loop(self, interval: float) -> None:
        while not self._stop_sweeper.wait(interval):
            try:
                self._submit(self._sweep(), timeout=self._operation_timeout)
            except Exception:  # noqa: BLE001 - sweeping is best-effort
                logger.warning("runner sweep failed", exc_info=True)

    def _submit(self, coro, *, timeout: float | None = None):
        """Run one coroutine on the runner loop and wait for it."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
