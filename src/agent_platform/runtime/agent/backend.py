"""PlatformSandboxBackend: DeepAgents backend over the platform Sandbox.

Maps DeepAgents file operations and command execution onto the platform
Sandbox capability (deepagents-runtime-spec.md section 7). Extending
BaseSandbox means only the three primitives (upload/download/execute)
are implemented; read/write/edit/ls/glob/grep come from the DeepAgents
default implementation on top of those primitives.

Host paths must never be exposed to the model: the sandbox working
directory is /workspace inside the isolated environment.

Successful file writes are registered as platform Artifacts
("DeepAgents artifact creation -> Artifact store", section 7) under a
`sandbox://` URI that references the sandbox copy, never a host path.
"""

from uuid import uuid4

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from agent_platform.runtime.core import ArtifactStore
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.models import ExecutionRequest, FileUpload


class PlatformSandboxBackend(BaseSandbox):
    """Executes agent file/command operations inside a platform sandbox."""

    def __init__(
        self,
        sandbox_manager: SandboxManager,
        sandbox_id: str,
        *,
        artifacts: ArtifactStore | None = None,
        run_id: str | None = None,
    ) -> None:
        self._manager = sandbox_manager
        self._sandbox_id = sandbox_id
        self._artifacts = artifacts
        self._run_id = run_id

    @property
    def id(self) -> str:
        """Unique identifier of the backing platform sandbox."""
        return self._sandbox_id

    async def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        uploads = await self._manager.upload(
            self._sandbox_id,
            [FileUpload(path=path, content=content) for path, content in files],
        )
        self._register_artifacts(files, uploads)
        return [
            FileUploadResponse(path=result.path, error=result.error)
            for result in uploads
        ]

    async def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        downloads = await self._manager.download(self._sandbox_id, list(paths))
        return [
            FileDownloadResponse(
                path=result.path,
                content=result.content,
                error=result.error,
            )
            for result in downloads
        ]

    async def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        result = await self._manager.execute(
            self._sandbox_id,
            ExecutionRequest(
                request_id=uuid4().hex,
                command=command,
                timeout=float(timeout) if timeout is not None else 60.0,
            ),
        )
        output = result.stdout
        if result.stderr:
            output = f"{output}\n{result.stderr}" if output else result.stderr
        if result.truncated:
            await self._persist_large_output(result, output)
        return ExecuteResponse(
            output=output,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )

    async def _persist_large_output(self, result, output: str) -> None:
        """Truncated output -> Artifact (sandbox-spec section 6).

        The inline response stays truncated; the full output is written
        into the sandbox's output area and registered as an Artifact with
        a sandbox:// URI. A failed upload produces no Artifact. Skipped
        entirely when no artifact store/run is wired in.
        """
        if self._artifacts is None or self._run_id is None:
            return
        path = f".platform/outputs/{result.request_id}.txt"
        [upload] = await self._manager.upload(
            self._sandbox_id, [FileUpload(path=path, content=output.encode("utf-8"))]
        )
        if upload.error is not None:
            return
        self._artifacts.create(
            run_id=self._run_id,
            name=path,
            uri=f"sandbox://{self._sandbox_id}/{path}",
            metadata={
                "sandbox_id": self._sandbox_id,
                "request_id": result.request_id,
                "exit_code": result.exit_code,
                "size": len(output.encode("utf-8")),
            },
        )

    def _register_artifacts(
        self, files: list[tuple[str, bytes]], uploads: list
    ) -> None:
        """Register successful writes as Artifacts (deepagents-runtime-spec §7).

        Failed uploads (result.error set) produce no Artifact. Registration
        is skipped entirely when no artifact store/run is wired in.
        """
        if self._artifacts is None or self._run_id is None:
            return
        for (_path, content), result in zip(files, uploads):
            if result.error is not None:
                continue
            self._artifacts.create(
                run_id=self._run_id,
                name=result.path,
                uri=f"sandbox://{self._sandbox_id}/{result.path}",
                metadata={"sandbox_id": self._sandbox_id, "size": len(content)},
            )
