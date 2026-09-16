"""Thin DeepAgents compatibility adapter.

Maps platform sandbox operations onto the shape DeepAgents' sandbox
contract expects, and returns plain Python types only. The platform
stays independent of DeepAgents: this module imports nothing from it
(duck-typed contract). TODO: align exact method signatures when the
DeepAgents dependency is added in the Agent framework adapter phase.
"""

from typing import Any
from uuid import uuid4

from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.models import ExecutionRequest, ExecutionResult, FileUpload


class DeepAgentsSandboxAdapter:
    """Exposes one sandbox as a simple command/file surface."""

    def __init__(self, manager: SandboxManager, sandbox_id: str) -> None:
        self._manager = manager
        self._sandbox_id = sandbox_id

    async def run_command(self, command: str, *, timeout: float | None = None) -> str:
        result = await self.execute(command, timeout=timeout)
        return result.stdout if result.exit_code == 0 else result.stderr

    async def execute(self, command: str, *, timeout: float | None = None) -> ExecutionResult:
        request = ExecutionRequest(
            request_id=str(uuid4()),
            command=command,
            timeout=timeout if timeout is not None else 60.0,
        )
        return await self._manager.execute(self._sandbox_id, request)

    async def write_file(self, path: str, content: bytes) -> bool:
        results = await self._manager.upload(self._sandbox_id, [FileUpload(path=path, content=content)])
        return results[0].ok

    async def read_file(self, path: str) -> bytes:
        results = await self._manager.download(self._sandbox_id, [path])
        download = results[0]
        if download.error is not None or download.content is None:
            raise IOError(f"download failed for {path}: {download.error}")
        return download.content

    def platform_sandbox_id(self) -> str:
        """Preserve the platform sandbox identity across the boundary."""
        return self._sandbox_id

    def metadata(self) -> dict[str, Any]:
        return {"sandbox_id": self._sandbox_id}
