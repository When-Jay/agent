"""Port exposure contract tests (sandbox-spec.md sections 9.1-9.3).

Policy validation, the docker provider's pure publish/resolve helpers,
and endpoint passthrough through the SandboxManager.
"""

import asyncio

import pytest

from agent_platform.sandbox.errors import PermissionDenied, ProviderError
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.models import (
    Endpoint,
    NetworkMode,
    NetworkPolicy,
    PortSpec,
    Sandbox,
    SandboxResources,
    SandboxSpec,
    SandboxStatus,
    WorkspaceMount,
)
from agent_platform.sandbox.policy import PolicyValidator
from agent_platform.sandbox.providers.docker import published_ports, resolve_endpoints
from agent_platform.sandbox.registry import SandboxProviderRegistry


def _spec(**overrides) -> SandboxSpec:
    fields = dict(
        image="platform/test:latest",
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        workspace=WorkspaceMount(workspace_id="ws-1"),
    )
    fields.update(overrides)
    return SandboxSpec(**fields)


# --- policy validation --------------------------------------------------------


def _policy():
    from agent_platform.sandbox.policy import SandboxPolicy

    return SandboxPolicy()


def test_ports_with_network_none_is_rejected():
    """Declaring ports in NONE mode is a specification error (spec 9.1)."""
    validator = PolicyValidator()
    spec = _spec(
        ports=(PortSpec(name="mcp", container_port=8080),),
        network_policy=NetworkPolicy(mode=NetworkMode.NONE),
    )
    with pytest.raises(PermissionDenied, match="NONE"):
        validator.validate_spec(_policy(), spec)


@pytest.mark.parametrize("mode", [NetworkMode.INTERNET_ONLY, NetworkMode.FULL, NetworkMode.INTERNAL])
def test_ports_with_reachable_mode_pass_validation(mode):
    validator = PolicyValidator()
    spec = _spec(
        ports=(PortSpec(name="mcp", container_port=8080),),
        network_policy=NetworkPolicy(mode=mode),
    )
    validator.validate_spec(_policy(), spec)


@pytest.mark.parametrize(
    "ports",
    [
        (PortSpec(name="mcp", container_port=0),),
        (PortSpec(name="mcp", container_port=70000),),
        (PortSpec(name="", container_port=8080),),
        (PortSpec(name="Mcp", container_port=8080),),
        (PortSpec(name="1x", container_port=8080),),
        (PortSpec(name="a", container_port=8080), PortSpec(name="a", container_port=9090)),
    ],
)
def test_invalid_port_declarations_are_rejected(ports):
    validator = PolicyValidator()
    spec = _spec(ports=ports)
    with pytest.raises(PermissionDenied):
        validator.validate_spec(_policy(), spec)


# --- docker publish/resolve helpers (pure, no daemon) --------------------------


def test_published_ports_bind_loopback_ephemeral():
    mapping = published_ports((PortSpec(name="mcp", container_port=8080),))
    assert mapping == {"8080/tcp": ("127.0.0.1", None)}


def test_published_ports_empty_for_no_declarations():
    assert published_ports(()) == {}


def test_resolve_endpoints_maps_declared_ports():
    endpoints = resolve_endpoints(
        (PortSpec(name="mcp", container_port=8080),),
        {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32771"}]},
    )
    assert endpoints == [Endpoint(name="mcp", address="127.0.0.1:32771")]


def test_resolve_endpoints_missing_binding_is_provider_error():
    with pytest.raises(ProviderError, match="not published"):
        resolve_endpoints((PortSpec(name="mcp", container_port=8080),), {})


def test_resolve_endpoints_empty_host_port_is_provider_error():
    with pytest.raises(ProviderError, match="no resolved host port"):
        resolve_endpoints(
            (PortSpec(name="mcp", container_port=8080),),
            {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]},
        )


# --- manager passthrough --------------------------------------------------------


class _EndpointProvider:
    """Fake provider: publishes one endpoint per declared port."""

    provider_name = "fake"

    async def create(self, spec: SandboxSpec) -> Sandbox:
        return Sandbox(
            sandbox_id=spec.sandbox_id,
            provider=self.provider_name,
            image=spec.image,
            tenant_id=spec.tenant_id,
            user_id=spec.user_id,
            session_id=spec.session_id,
            workspace=spec.workspace,
            resources=SandboxResources(),
            network_policy=spec.network_policy,
            status=SandboxStatus.READY,
            endpoints=[Endpoint(name=p.name, address=f"127.0.0.1:{p.container_port}") for p in spec.ports],
        )

    async def execute(self, sandbox, request):  # pragma: no cover - unused here
        raise NotImplementedError

    async def upload_files(self, sandbox, files):  # pragma: no cover - unused here
        raise NotImplementedError

    async def download_files(self, sandbox, paths):  # pragma: no cover - unused here
        raise NotImplementedError

    async def health_check(self, sandbox):
        from agent_platform.sandbox.models import HealthStatus

        return HealthStatus(healthy=True, detail="ok")

    async def destroy(self, sandbox):  # pragma: no cover - unused here
        pass


def test_manager_returns_endpoints_resolved_by_provider():
    registry = SandboxProviderRegistry()
    registry.register("fake", _EndpointProvider())
    manager = SandboxManager(registry, default_provider="fake")

    async def scenario():
        sandbox = await manager.create(
            _spec(ports=(PortSpec(name="mcp", container_port=8080),))
        )
        assert sandbox.status is SandboxStatus.READY
        assert sandbox.endpoints == [Endpoint(name="mcp", address="127.0.0.1:8080")]
        await manager.destroy(sandbox.sandbox_id)

    asyncio.run(scenario())
