"""Sandbox core unit tests + provider contract suite run against the Fake provider."""

import asyncio
import contextlib
from pathlib import PurePosixPath

import pytest

from agent_platform.sandbox.errors import (
    PermissionDenied,
    ProviderError,
    ResourceLimitExceeded,
    SandboxError,
    SandboxNotFound,
    SandboxUnavailable,
)
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.models import (
    ExecutionRequest,
    FileDownloadResult,
    FileUpload,
    FileUploadResult,
    HealthStatus,
    NetworkMode,
    Sandbox,
    SandboxResources,
    SandboxSpec,
    SandboxStatus,
    WorkspaceMount,
)
from agent_platform.sandbox.policy import SandboxPolicy, is_path_under, parse_cpu, parse_memory
from agent_platform.sandbox.registry import SandboxProviderRegistry

from sandbox_contract import run_provider_contract_suite


# --- Fake provider (test double) --------------------------------------------


class FakeSandboxProvider:
    """Deterministic in-memory provider used for manager and contract tests."""

    name = "fake"

    def __init__(self) -> None:
        self.executions: list[ExecutionRequest] = []
        self.files: dict[str, bytes] = {}
        self.destroyed: list[str] = []
        self.healthy = True
        self.raise_on_execute: Exception | None = None

    async def create(self, spec: SandboxSpec) -> Sandbox:
        return Sandbox(
            sandbox_id=spec.sandbox_id,
            provider=self.name,
            image=spec.image,
            tenant_id=spec.tenant_id,
            user_id=spec.user_id,
            session_id=spec.session_id,
            workspace=spec.workspace,
            resources=spec.resources,
            network_policy=spec.network_policy,
            metadata=dict(spec.metadata),
        )

    async def execute(self, sandbox: Sandbox, request: ExecutionRequest):
        self.executions.append(request)
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        command = request.command if isinstance(request.command, str) else " ".join(request.command)
        if command.startswith("echo "):
            stdout = command[len("echo "):]
        else:
            stdout = ""
        return _result(request, exit_code=0, stdout=stdout)

    async def upload_files(self, sandbox: Sandbox, files: list[FileUpload]) -> list[FileUploadResult]:
        for f in files:
            self.files[f.path] = f.content
        return [FileUploadResult(path=f.path, ok=True) for f in files]

    async def download_files(self, sandbox: Sandbox, paths: list[str]) -> list[FileDownloadResult]:
        return [
            FileDownloadResult(path=p, content=self.files.get(p))
            if p in self.files
            else FileDownloadResult(path=p, error="file not found")
            for p in paths
        ]

    async def health_check(self, sandbox: Sandbox) -> HealthStatus:
        return HealthStatus(healthy=self.healthy, detail="fake")

    async def destroy(self, sandbox: Sandbox) -> None:
        self.destroyed.append(sandbox.sandbox_id)


def _result(request: ExecutionRequest, *, exit_code: int, stdout: str = ""):
    from agent_platform.sandbox.models import ExecutionResult

    return ExecutionResult(
        request_id=request.request_id,
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        duration_ms=1,
    )


def _spec(**overrides) -> SandboxSpec:
    defaults = dict(
        image="platform/test:latest",
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        workspace=WorkspaceMount(workspace_id="ws-1"),
    )
    defaults.update(overrides)
    return SandboxSpec(**defaults)


def _manager(provider: FakeSandboxProvider | None = None, *, policy: SandboxPolicy | None = None) -> SandboxManager:
    registry = SandboxProviderRegistry()
    registry.register("fake", provider or FakeSandboxProvider())
    return SandboxManager(registry, policy=policy, default_provider="fake")


# --- Contract ----------------------------------------------------------------


def test_fake_provider_passes_full_contract_suite():
    run_provider_contract_suite(_manager)


# --- Registry ----------------------------------------------------------------


def test_provider_registry_registers_and_resolves():
    provider = FakeSandboxProvider()
    registry = SandboxProviderRegistry()
    registry.register("fake", provider)
    registry.register("other", FakeSandboxProvider())

    assert registry.get("fake") is provider
    assert registry.names() == ["fake", "other"]

    with pytest.raises(ProviderError):
        registry.get("missing")
    with pytest.raises(ProviderError):
        registry.register("fake", provider)


# --- Lifecycle ---------------------------------------------------------------


def test_lifecycle_create_acquire_release_destroy():
    async def scenario():
        provider = FakeSandboxProvider()
        manager = _manager(provider)

        sandbox = await manager.create(_spec())
        assert sandbox.status is SandboxStatus.READY
        assert provider.destroyed == []

        acquired = await manager.acquire(sandbox.sandbox_id)
        assert acquired.status is SandboxStatus.BUSY

        released = await manager.release(sandbox.sandbox_id)
        assert released.status is SandboxStatus.READY

        await manager.destroy(sandbox.sandbox_id)
        assert sandbox.status is SandboxStatus.DESTROYED
        assert provider.destroyed == [sandbox.sandbox_id]

        # Destroy is idempotent.
        await manager.destroy(sandbox.sandbox_id)

    asyncio.run(scenario())


def test_invalid_state_transitions_are_rejected():
    async def scenario():
        manager = _manager()
        sandbox = await manager.create(_spec())

        with pytest.raises(SandboxUnavailable):
            await manager.release(sandbox.sandbox_id)  # not BUSY

        await manager.acquire(sandbox.sandbox_id)
        with pytest.raises(SandboxUnavailable):
            await manager.acquire(sandbox.sandbox_id)  # already BUSY

        await manager.destroy(sandbox.sandbox_id)
        with pytest.raises(SandboxUnavailable):
            await manager.acquire(sandbox.sandbox_id)

    asyncio.run(scenario())


def test_get_unknown_sandbox_raises():
    async def scenario():
        with pytest.raises(SandboxNotFound):
            await _manager().get("missing")

    asyncio.run(scenario())


def test_create_without_provider_selection_raises():
    registry = SandboxProviderRegistry()
    registry.register("fake", FakeSandboxProvider())
    manager = SandboxManager(registry)  # no default

    async def scenario():
        with pytest.raises(ProviderError):
            await manager.create(_spec())

    asyncio.run(scenario())


# --- Policy ------------------------------------------------------------------


def test_policy_rejects_disallowed_image_and_network_mode():
    policy = SandboxPolicy(
        allowed_images=("platform/allowed:latest",),
        allowed_network_mode=NetworkMode.NONE,
    )
    manager = _manager(policy=policy)

    async def scenario():
        with pytest.raises(PermissionDenied):
            await manager.create(_spec(image="platform/other:latest"))
        with pytest.raises(PermissionDenied):
            await manager.create(_spec())  # default INTERNET_ONLY != NONE

    asyncio.run(scenario())


def test_policy_rejects_resources_over_limits():
    policy = SandboxPolicy(max_cpu="1", max_memory="2Gi")
    manager = _manager(policy=policy)

    async def scenario():
        with pytest.raises(ResourceLimitExceeded):
            await manager.create(_spec(resources=SandboxResources(cpu="4")))
        with pytest.raises(ResourceLimitExceeded):
            await manager.create(_spec(resources=SandboxResources(memory="8Gi")))

    asyncio.run(scenario())


def test_policy_rejects_execution_over_timeout_output_and_workspace():
    policy = SandboxPolicy(max_timeout=30.0, max_output=1024, allowed_workspace_root="/workspace")
    manager = _manager(policy=policy)

    async def scenario():
        sandbox = await manager.create(_spec())

        with pytest.raises(ResourceLimitExceeded):
            await manager.execute(
                sandbox.sandbox_id, ExecutionRequest(request_id="t1", command="echo x", timeout=60.0)
            )
        with pytest.raises(ResourceLimitExceeded):
            await manager.execute(
                sandbox.sandbox_id,
                ExecutionRequest(request_id="t2", command="echo x", timeout=10.0, output_limit=999999),
            )
        with pytest.raises(PermissionDenied):
            await manager.execute(
                sandbox.sandbox_id,
                ExecutionRequest(request_id="t3", command="echo x", cwd="/etc", timeout=10.0, output_limit=512),
            )
        with pytest.raises(PermissionDenied):
            await manager.upload(
                sandbox.sandbox_id, [FileUpload(path="/etc/passwd", content=b"no")]
            )

    asyncio.run(scenario())


def test_policy_quantity_parsers_and_path_helper():
    assert parse_cpu("1") == 1.0
    assert parse_cpu("500m") == 0.5
    assert parse_memory("2Gi") == 2 * 2**30
    assert parse_memory("512Mi") == 512 * 2**20
    assert is_path_under("/workspace/output/a.txt", "/workspace")
    assert not is_path_under("/etc/passwd", "/workspace")
    assert is_path_under("/workspace", "/workspace")


# --- Idempotency & error normalization ---------------------------------------


def test_duplicate_request_id_does_not_execute_twice():
    provider = FakeSandboxProvider()
    manager = _manager(provider)

    async def scenario():
        sandbox = await manager.create(_spec())
        request = ExecutionRequest(request_id="same-id", command="echo once")

        first = await manager.execute(sandbox.sandbox_id, request)
        second = await manager.execute(sandbox.sandbox_id, request)

        assert first is second
        assert len(provider.executions) == 1

    asyncio.run(scenario())


def test_provider_exceptions_are_normalized_to_sandbox_errors():
    provider = FakeSandboxProvider()
    provider.raise_on_execute = ValueError("docker said something cryptic")
    manager = _manager(provider)

    async def scenario():
        sandbox = await manager.create(_spec())
        with pytest.raises(ProviderError, match="cryptic"):
            await manager.execute(sandbox.sandbox_id, ExecutionRequest(request_id="x", command="echo y"))
        with pytest.raises(SandboxError):
            await manager.execute(sandbox.sandbox_id, ExecutionRequest(request_id="y", command="echo y"))

    asyncio.run(scenario())


# --- Health & recovery -------------------------------------------------------


def test_health_check_transitions_unhealthy_and_back_to_ready():
    provider = FakeSandboxProvider()
    manager = _manager(provider)

    async def scenario():
        sandbox = await manager.create(_spec())
        assert sandbox.status is SandboxStatus.READY

        provider.healthy = False
        health = await manager.health_check(sandbox.sandbox_id)
        assert not health.healthy
        assert sandbox.status is SandboxStatus.UNHEALTHY

        with pytest.raises(SandboxUnavailable):
            await manager.acquire(sandbox.sandbox_id)

        provider.healthy = True
        health = await manager.health_check(sandbox.sandbox_id)
        assert health.healthy
        assert sandbox.status is SandboxStatus.READY
        assert sandbox.last_heartbeat is not None

    asyncio.run(scenario())


# --- DeepAgents adapter (thin, duck-typed) -----------------------------------


def test_deepagents_adapter_translates_platform_operations():
    async def scenario():
        from agent_platform.sandbox.adapters.deepagents import DeepAgentsSandboxAdapter

        manager = _manager()
        sandbox = await manager.create(_spec())
        adapter = DeepAgentsSandboxAdapter(manager, sandbox.sandbox_id)

        output = await adapter.run_command("echo hi")
        assert output == "hi"
        assert await adapter.write_file("/workspace/output/b.txt", b"payload") is True
        assert await adapter.read_file("/workspace/output/b.txt") == b"payload"
        assert adapter.platform_sandbox_id() == sandbox.sandbox_id

    asyncio.run(scenario())


@contextlib.contextmanager
def _unused():
    yield


def test_pure_posix_root_not_treated_as_relative():
    # Guards against pathlib treating "/workspace" == "." on some platforms.
    assert PurePosixPath("/workspace") != PurePosixPath(".")
