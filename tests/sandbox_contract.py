"""Reusable SandboxProvider contract suite.

Every provider implementation (Fake, Docker, Kubernetes, ...) must pass
run_provider_contract_suite. This proves the SandboxManager core works
with any conforming provider and does not depend on Docker/Kubernetes.
"""

import asyncio

import pytest

from agent_platform.sandbox.errors import SandboxUnavailable
from agent_platform.sandbox.models import (
    ExecutionRequest,
    FileUpload,
    SandboxSpec,
    SandboxStatus,
    WorkspaceMount,
)


def run_provider_contract_suite(manager_factory, spec: SandboxSpec | None = None) -> None:
    """manager_factory() -> SandboxManager with the provider under test as default.

    ``spec`` overrides the default test spec (e.g. a provider-specific image).
    """

    async def scenario() -> None:
        manager = manager_factory()
        sandbox = await manager.create(spec or _spec())
        assert sandbox.status is SandboxStatus.READY
        assert sandbox.sandbox_id

        # Execute
        result = await manager.execute(
            sandbox.sandbox_id,
            ExecutionRequest(request_id="req-1", command="echo hello"),
        )
        assert result.exit_code == 0
        assert result.stdout.strip() == "hello"  # trailing newline is shell-dependent
        assert result.request_id == "req-1"

        # Upload + Download roundtrip
        uploads = await manager.upload(
            sandbox.sandbox_id,
            [FileUpload(path="/workspace/output/a.txt", content=b"data")],
        )
        assert uploads[0].ok
        downloads = await manager.download(sandbox.sandbox_id, ["/workspace/output/a.txt"])
        assert downloads[0].content == b"data"

        # Health check
        health = await manager.health_check(sandbox.sandbox_id)
        assert health.healthy

        # Idempotency: same request_id must not execute twice
        replay = await manager.execute(
            sandbox.sandbox_id,
            ExecutionRequest(request_id="req-1", command="echo hello"),
        )
        assert replay == result

        # Destroy
        await manager.destroy(sandbox.sandbox_id)
        destroyed = await manager.get(sandbox.sandbox_id)
        assert destroyed.status is SandboxStatus.DESTROYED

        with pytest.raises(SandboxUnavailable):
            await manager.execute(
                sandbox.sandbox_id,
                ExecutionRequest(request_id="req-2", command="echo x"),
            )

    asyncio.run(scenario())


def _spec() -> SandboxSpec:
    return SandboxSpec(
        image="platform/test:latest",
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        workspace=WorkspaceMount(workspace_id="ws-1"),
    )
