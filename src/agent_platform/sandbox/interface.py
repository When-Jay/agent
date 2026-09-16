"""Sandbox provider contract.

Providers implement this protocol for a specific infrastructure
(Docker, Kubernetes, ...). All operations are async. Implementations
must raise the platform sandbox errors (agent_platform.sandbox.errors),
never infrastructure-native exceptions.
"""

from typing import Protocol, runtime_checkable

from agent_platform.sandbox.models import (
    ExecutionRequest,
    ExecutionResult,
    FileDownloadResult,
    FileUpload,
    FileUploadResult,
    HealthStatus,
    Sandbox,
    SandboxSpec,
)


@runtime_checkable
class SandboxProvider(Protocol):
    async def create(self, spec: SandboxSpec) -> Sandbox: ...

    async def execute(self, sandbox: Sandbox, request: ExecutionRequest) -> ExecutionResult: ...

    async def upload_files(
        self, sandbox: Sandbox, files: list[FileUpload]
    ) -> list[FileUploadResult]: ...

    async def download_files(
        self, sandbox: Sandbox, paths: list[str]
    ) -> list[FileDownloadResult]: ...

    async def health_check(self, sandbox: Sandbox) -> HealthStatus: ...

    async def destroy(self, sandbox: Sandbox) -> None: ...
