"""Stage D tests: sandbox-backed stdio servers (mcp-gateway-spec.md section 6.2).

Covers SandboxServerRunner over a fake SandboxManager, the composition
wiring (stdio config -> runner -> session holder -> namespaced gateway
tools) and gateway shutdown chaining runner teardown.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from agent_platform.mcp.credentials import CredentialMaterial
from agent_platform.mcp.sessions import McpServerConfig
from agent_platform.mcp.runner import RunnerUnavailableError
from agent_platform.runtime.dispatch.mcp_composition import (
    build_mcp_gateway,
    parse_mcp_servers_json,
)
from agent_platform.runtime.dispatch.sandbox_runner import (
    BRIDGE_COMMAND_VAR,
    BRIDGE_PORT_VAR,
    SandboxServerRunner,
)
from agent_platform.runtime.capabilities.tool import ToolCallRequest
from agent_platform.sandbox.errors import SandboxNotFound
from agent_platform.sandbox.models import (
    Endpoint,
    PortSpec,
    Sandbox,
    SandboxResources,
    SandboxSpec,
    SandboxStatus,
    WorkspaceMount,
)


class FakeRunnerManager:
    """Records SandboxManager calls; assigns counter-based endpoints."""

    def __init__(self, *, with_endpoints: bool = True):
        self.created: list[SandboxSpec] = []
        self.destroyed: list[str] = []
        self._counter = 0
        self._sandboxes: dict[str, Sandbox] = {}
        self._with_endpoints = with_endpoints

    async def create(self, spec: SandboxSpec, provider: str | None = None) -> Sandbox:
        self._counter += 1
        sandbox = Sandbox(
            sandbox_id=f"sbx-{self._counter}",
            provider="fake",
            image=spec.image,
            tenant_id=spec.tenant_id,
            user_id=spec.user_id,
            session_id=spec.session_id,
            workspace=spec.workspace,
            resources=SandboxResources(),
            network_policy=spec.network_policy,
            status=SandboxStatus.READY,
            endpoints=(
                [
                    Endpoint(name=p.name, address=f"127.0.0.1:{30000 + self._counter}")
                    for p in spec.ports
                ]
                if self._with_endpoints
                else []
            ),
        )
        self._sandboxes[sandbox.sandbox_id] = sandbox
        self.created.append(spec)
        return sandbox

    async def get(self, sandbox_id: str) -> Sandbox:
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise SandboxNotFound(f"sandbox not found: {sandbox_id}")
        return sandbox

    async def destroy(self, sandbox_id: str) -> None:
        self.destroyed.append(sandbox_id)
        self._sandboxes.pop(sandbox_id, None)

    def kill(self, sandbox_id: str) -> None:
        """Simulate external death without recording a destroy call."""
        self._sandboxes.pop(sandbox_id, None)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _stdio_config(**overrides) -> McpServerConfig:
    fields = dict(
        name="files",
        transport="stdio",
        command=("npx", "-y", "mcp-server-files"),
        env={"FILES_ROOT": "/data"},
    )
    fields.update(overrides)
    return McpServerConfig(**fields)


@pytest.fixture
def runner_factory():
    """Create runners that are always closed after the test."""
    created = []

    def _make(manager, **kwargs):
        kwargs.setdefault("image", "platform/mcp-runner:latest")
        kwargs.setdefault("sweep_interval", None)  # no background sweeper in tests
        instance = SandboxServerRunner(manager, **kwargs)
        created.append(instance)
        return instance

    yield _make
    for instance in created:
        instance.close()


# --- SandboxServerRunner --------------------------------------------------------


def test_ensure_running_starts_workload_and_returns_endpoint(runner_factory):
    manager = FakeRunnerManager()
    runner = runner_factory(manager, image="platform/mcp-runner:latest")

    endpoint = runner.ensure_running(_stdio_config(), CredentialMaterial(env={"TOKEN": "t"}))

    assert endpoint == "http://127.0.0.1:30001"
    assert len(manager.created) == 1
    spec = manager.created[0]
    assert spec.image == "platform/mcp-runner:latest"
    assert spec.ports == (PortSpec(name="mcp", container_port=8080),)
    # Bridge contract env + config env + credential env, merged at creation.
    assert spec.env[BRIDGE_COMMAND_VAR] == '["npx", "-y", "mcp-server-files"]'
    assert spec.env[BRIDGE_PORT_VAR] == "8080"
    assert spec.env["FILES_ROOT"] == "/data"
    assert spec.env["TOKEN"] == "t"


def test_ensure_running_is_idempotent_while_workload_lives(runner_factory):
    manager = FakeRunnerManager()
    runner = runner_factory(manager)

    first = runner.ensure_running(_stdio_config(), CredentialMaterial())
    second = runner.ensure_running(_stdio_config(), CredentialMaterial())

    assert first == second
    assert len(manager.created) == 1


def test_ensure_running_recreates_after_external_death(runner_factory):
    manager = FakeRunnerManager()
    runner = runner_factory(manager)

    first = runner.ensure_running(_stdio_config(), CredentialMaterial())
    manager.kill("sbx-1")
    second = runner.ensure_running(_stdio_config(), CredentialMaterial())

    assert first != second
    assert "sbx-1" in manager.destroyed  # best-effort destroy of the corpse
    assert len(manager.created) == 2


def test_ensure_running_recreates_unhealthy_workload(runner_factory):
    manager = FakeRunnerManager()
    runner = runner_factory(manager)

    runner.ensure_running(_stdio_config(), CredentialMaterial())
    manager._sandboxes["sbx-1"].status = SandboxStatus.UNHEALTHY
    second = runner.ensure_running(_stdio_config(), CredentialMaterial())

    assert second == "http://127.0.0.1:30002"
    assert "sbx-1" in manager.destroyed


def test_missing_endpoint_is_runner_unavailable(runner_factory):
    manager = FakeRunnerManager(with_endpoints=False)
    runner = runner_factory(manager)

    with pytest.raises(RunnerUnavailableError, match="endpoint"):
        runner.ensure_running(_stdio_config(), CredentialMaterial())


def test_stop_destroys_the_workload(runner_factory):
    manager = FakeRunnerManager()
    runner = runner_factory(manager)

    runner.ensure_running(_stdio_config(), CredentialMaterial())
    runner.stop("files")

    assert manager.destroyed == ["sbx-1"]
    runner.stop("files")  # idempotent, no error
    assert manager.destroyed == ["sbx-1"]


def test_close_destroys_all_workloads(runner_factory):
    manager = FakeRunnerManager()
    runner = runner_factory(manager)

    runner.ensure_running(_stdio_config(), CredentialMaterial())
    runner.ensure_running(_stdio_config(name="search", command=("searchd",)), CredentialMaterial())
    runner.close()

    assert sorted(manager.destroyed) == ["sbx-1", "sbx-2"]


def test_close_is_idempotent(runner_factory):
    # Regression: a second close() used to submit to the stopped loop and hang.
    manager = FakeRunnerManager()
    runner = runner_factory(manager)

    runner.ensure_running(_stdio_config(), CredentialMaterial())
    runner.close()
    runner.close()

    assert manager.destroyed == ["sbx-1"]


def test_sweep_destroys_only_idle_workloads(runner_factory):
    clock = _Clock()
    manager = FakeRunnerManager()
    runner = runner_factory(manager, idle_ttl_seconds=100.0, clock=clock)

    runner.ensure_running(_stdio_config(), CredentialMaterial())
    clock.now = 50.0
    runner.ensure_running(_stdio_config(name="search", command=("searchd",)), CredentialMaterial())

    clock.now = 160.0  # files idle 160s (> ttl), search idle 110s (> ttl)
    runner.sweep()
    assert sorted(manager.destroyed) == ["sbx-1", "sbx-2"]

    clock.now = 161.0
    runner.ensure_running(_stdio_config(), CredentialMaterial())
    clock.now = 200.0  # files restarted at 161, only 39s idle -> survives
    runner.sweep()
    assert sorted(manager.destroyed) == ["sbx-1", "sbx-2"]

    clock.now = 300.0  # files now idle 139s > ttl -> swept too
    runner.sweep()
    assert sorted(manager.destroyed) == ["sbx-1", "sbx-2", "sbx-3"]


def test_invalid_runner_configs_are_rejected(runner_factory):
    manager = FakeRunnerManager()
    runner = runner_factory(manager)

    with pytest.raises(ValueError, match="stdio"):
        runner.ensure_running(
            McpServerConfig(name="remote", url="http://x"), CredentialMaterial()
        )
    with pytest.raises(ValueError, match="command"):
        runner.ensure_running(_stdio_config(command=()), CredentialMaterial())
    assert manager.created == []


# --- composition wiring ----------------------------------------------------------


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, CredentialMaterial]] = []
        self.closed = False

    def ensure_running(self, config: McpServerConfig, material: CredentialMaterial) -> str:
        self.calls.append((config.name, material))
        return "http://127.0.0.1:65123"

    def stop(self, name: str) -> None:  # pragma: no cover - unused here
        pass

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """Duck-typed ClientSession over an in-memory transport."""

    async def list_tools(self):
        tool = SimpleNamespace(name="echo", description="", input_schema={"type": "object"})
        return SimpleNamespace(tools=[tool])

    async def call_tool(self, name, arguments, *, meta=None):
        return SimpleNamespace(content=[SimpleNamespace(text=f"{name}:{meta['platform.idempotency_key']}")], is_error=False)

    async def send_ping(self):  # pragma: no cover - unused here
        return SimpleNamespace()


def _fake_factory_builder(transport):
    assert transport == "streamable-http"  # the bridge serves streamable HTTP

    @asynccontextmanager
    async def factory(url: str, headers: dict[str, str]):
        yield FakeSession()

    return factory


def test_stdio_server_is_served_through_the_runner():
    runner = FakeRunner()
    gateway = build_mcp_gateway(
        [_stdio_config()],
        runner=runner,
        factory_builder=_fake_factory_builder,
    )
    try:
        names = [spec.name for spec in gateway.list_tools()]
        assert names == ["files__echo"]
        assert runner.calls and runner.calls[0][0] == "files"

        result = gateway.invoke(
            ToolCallRequest(id="req-1", name="files__echo", arguments={})
        )
        assert result.error is None
        assert result.content == "echo:req-1"
    finally:
        gateway.close()


def test_stdio_server_without_runner_is_rejected():
    with pytest.raises(ValueError, match="ServerRunner"):
        build_mcp_gateway([_stdio_config()], factory_builder=_fake_factory_builder)


def test_gateway_close_runs_runner_and_pool_callbacks_once():
    runner = FakeRunner()
    gateway = build_mcp_gateway(
        [_stdio_config()],
        runner=runner,
        factory_builder=_fake_factory_builder,
    )
    gateway.close()
    gateway.close()  # idempotent
    assert runner.closed is True


# --- config parsing -----------------------------------------------------------------


def test_parse_stdio_server_config():
    servers = parse_mcp_servers_json(
        '[{"name":"files","transport":"stdio","command":["npx","mcp-server-files"],'
        '"env":{"FILES_ROOT":"/data"},"credential_ref":"files-main","side_effects":"readonly"}]'
    )
    assert servers == [
        McpServerConfig(
            name="files",
            url="",
            transport="stdio",
            credential_ref="files-main",
            side_effects="readonly",
            command=("npx", "mcp-server-files"),
            env={"FILES_ROOT": "/data"},
        )
    ]


def test_parse_stdio_without_command_is_rejected():
    with pytest.raises(ValueError, match="command"):
        parse_mcp_servers_json('[{"name":"files","transport":"stdio"}]')


def test_parse_unknown_transport_is_rejected():
    with pytest.raises(ValueError, match="transport"):
        parse_mcp_servers_json('[{"name":"x","transport":"carrier-pigeon","url":"http://x"}]')


def test_parse_http_without_url_is_rejected():
    with pytest.raises(ValueError, match="url"):
        parse_mcp_servers_json('[{"name":"x","transport":"sse"}]')


def test_parse_empty_stays_empty():
    assert parse_mcp_servers_json("") == []
