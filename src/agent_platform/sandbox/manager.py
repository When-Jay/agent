"""SandboxManager: lifecycle, policy enforcement, idempotency, error normalization.

The manager contains no Docker/Kubernetes details; it orchestrates
SandboxProvider implementations selected through the registry.
"""

import logging
from dataclasses import replace
from uuid import uuid4

from agent_platform.sandbox.errors import (
    ProviderError,
    SandboxError,
    SandboxNotFound,
    SandboxUnavailable,
)
from agent_platform.sandbox.interface import SandboxProvider
from agent_platform.sandbox.models import (
    ExecutionRequest,
    ExecutionResult,
    FileDownloadResult,
    FileUpload,
    FileUploadResult,
    HealthStatus,
    Sandbox,
    SandboxSpec,
    SandboxStatus,
    WorkspaceMount,
)
from agent_platform.sandbox.policy import PolicyValidator, SandboxPolicy
from agent_platform.sandbox.registry import SandboxProviderRegistry
from agent_platform.sandbox.warm_pool import is_pool_eligible

logger = logging.getLogger(__name__)

_EXECUTABLE_STATUSES = {SandboxStatus.READY, SandboxStatus.BUSY}


class SandboxManager:
    def __init__(
        self,
        registry: SandboxProviderRegistry,
        *,
        policy: SandboxPolicy | None = None,
        default_provider: str | None = None,
        warm_pool=None,
    ) -> None:
        self._registry = registry
        self._policy = policy or SandboxPolicy()
        self._default_provider = default_provider
        # Optional warm-pool collaborator (sandbox-warm-pool-spec.md): the
        # manager knows only "claim(spec, provider) -> Sandbox | None" and
        # contains no pool internals. None = pool disabled, zero overhead.
        self._warm_pool = warm_pool
        self._validator = PolicyValidator()
        self._sandboxes: dict[str, Sandbox] = {}
        self._results: dict[tuple[str, str], ExecutionResult] = {}

    async def create(self, spec: SandboxSpec, *, provider: str | None = None) -> Sandbox:
        self._validator.validate_spec(self._policy, spec)
        provider_name = provider or self._default_provider
        if provider_name is None:
            raise ProviderError("no provider selected and no default provider configured")
        provider_obj = self._registry.get(provider_name)

        if self._warm_pool is not None and is_pool_eligible(spec):
            warm = await self._claim_warm(provider_name, provider_obj, spec)
            if warm is not None:
                return warm

        assigned = self._assign_ids(spec)
        sandbox = await self._normalize(provider_name, lambda: provider_obj.create(assigned))
        sandbox.provider = provider_name
        sandbox.status = SandboxStatus.CREATING
        self._sandboxes[sandbox.sandbox_id] = sandbox

        health = await self._normalize(provider_name, lambda: provider_obj.health_check(sandbox))
        sandbox.last_heartbeat = _now()
        sandbox.status = SandboxStatus.READY if health.healthy else SandboxStatus.UNHEALTHY
        logger.info("sandbox %s created via %s: %s", sandbox.sandbox_id, provider_name, sandbox.status.value)
        return sandbox

    async def get(self, sandbox_id: str) -> Sandbox:
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise SandboxNotFound(f"sandbox not found: {sandbox_id}")
        return sandbox

    async def acquire(self, sandbox_id: str) -> Sandbox:
        sandbox = await self.get(sandbox_id)
        if sandbox.status is not SandboxStatus.READY:
            raise SandboxUnavailable(f"sandbox not acquireable in state {sandbox.status.value}")
        sandbox.status = SandboxStatus.BUSY
        return sandbox

    async def release(self, sandbox_id: str) -> Sandbox:
        sandbox = await self.get(sandbox_id)
        if sandbox.status is not SandboxStatus.BUSY:
            raise SandboxUnavailable(f"sandbox not releaseable in state {sandbox.status.value}")
        sandbox.status = SandboxStatus.READY
        return sandbox

    async def execute(self, sandbox_id: str, request: ExecutionRequest) -> ExecutionResult:
        cache_key = (sandbox_id, request.request_id)
        cached = self._results.get(cache_key)
        if cached is not None:
            return cached

        sandbox = await self.get(sandbox_id)
        if sandbox.status not in _EXECUTABLE_STATUSES:
            raise SandboxUnavailable(f"sandbox not executable in state {sandbox.status.value}")
        self._validator.validate_execution(self._policy, request)

        provider_obj = self._require_provider(sandbox)
        try:
            result = await provider_obj.execute(sandbox, request)
        except SandboxError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider failures
            raise ProviderError(f"execution failed: {exc}") from exc
        self._results[cache_key] = result
        return result

    async def upload(self, sandbox_id: str, files: list[FileUpload]) -> list[FileUploadResult]:
        sandbox = await self.get(sandbox_id)
        if sandbox.status not in _EXECUTABLE_STATUSES:
            raise SandboxUnavailable(f"sandbox not writable in state {sandbox.status.value}")
        self._validator.validate_paths(self._policy, [f.path for f in files])
        provider_obj = self._require_provider(sandbox)
        return await self._normalize(provider_name=sandbox.provider, call=lambda: provider_obj.upload_files(sandbox, files))

    async def download(self, sandbox_id: str, paths: list[str]) -> list[FileDownloadResult]:
        sandbox = await self.get(sandbox_id)
        if sandbox.status not in _EXECUTABLE_STATUSES:
            raise SandboxUnavailable(f"sandbox not readable in state {sandbox.status.value}")
        self._validator.validate_paths(self._policy, paths)
        provider_obj = self._require_provider(sandbox)
        return await self._normalize(provider_name=sandbox.provider, call=lambda: provider_obj.download_files(sandbox, paths))

    async def health_check(self, sandbox_id: str) -> HealthStatus:
        sandbox = await self.get(sandbox_id)
        provider_obj = self._require_provider(sandbox)
        health = await self._normalize(provider_name=sandbox.provider, call=lambda: provider_obj.health_check(sandbox))
        sandbox.last_heartbeat = _now()
        if health.healthy:
            if sandbox.status is SandboxStatus.UNHEALTHY:
                sandbox.status = SandboxStatus.RECOVERING
            if sandbox.status is SandboxStatus.RECOVERING:
                sandbox.status = SandboxStatus.READY
        elif sandbox.status in {SandboxStatus.READY, SandboxStatus.BUSY}:
            sandbox.status = SandboxStatus.UNHEALTHY
        return health

    async def destroy(self, sandbox_id: str) -> None:
        sandbox = await self.get(sandbox_id)
        if sandbox.status is SandboxStatus.DESTROYED:
            return
        sandbox.status = SandboxStatus.RELEASING
        provider_obj = self._require_provider(sandbox)
        await self._normalize(provider_name=sandbox.provider, call=lambda: provider_obj.destroy(sandbox))
        sandbox.status = SandboxStatus.DESTROYED
        logger.info("sandbox %s destroyed (workspace retained)", sandbox_id)

    # --- internal -----------------------------------------------------------

    def _require_provider(self, sandbox: Sandbox) -> SandboxProvider:
        return self._registry.get(sandbox.provider)

    def _assign_ids(self, spec: SandboxSpec) -> SandboxSpec:
        # Warm-pool spec section 4.1: an empty workspace_id declares an
        # ephemeral workspace ("any fresh workspace"). Providers require a
        # non-empty id, so the manager assigns one (previously ProviderError).
        assigned = replace(spec, sandbox_id=spec.sandbox_id or str(uuid4()))
        if not assigned.workspace.workspace_id:
            workspace = replace(assigned.workspace, workspace_id=str(uuid4()))
            assigned = replace(assigned, workspace=workspace)
        return assigned

    async def _claim_warm(self, provider_name: str, provider_obj: SandboxProvider, spec: SandboxSpec) -> Sandbox | None:
        """Claim path (sandbox-warm-pool-spec.md section 5.2). Never raises."""
        try:
            sandbox = self._warm_pool.claim(spec, provider_name)
        except Exception:  # noqa: BLE001 - any pool failure degrades to cold create
            logger.warning("warm pool claim failed; falling back to cold create", exc_info=True)
            return None
        if sandbox is None:
            return None
        try:
            health = await provider_obj.health_check(sandbox)
        except Exception as exc:  # noqa: BLE001 - treat probe crash as unhealthy
            logger.warning(
                "warm pool: health check of claimed sandbox %s failed", sandbox.sandbox_id, exc_info=True
            )
            health = HealthStatus(healthy=False, detail=str(exc))
        if not health.healthy:
            try:
                await provider_obj.destroy(sandbox)
            except Exception:  # noqa: BLE001 - never hand out unhealthy; cold path continues
                logger.warning(
                    "warm pool: destroy of unhealthy claimed sandbox %s failed",
                    sandbox.sandbox_id,
                    exc_info=True,
                )
            return None
        # Specialize identity (platform records only; infra labels stay the
        # pool system identity -- spec section 9.4). sandbox_id/workspace_id/
        # endpoints are pool-issued and unchanged (section 5.2).
        sandbox.tenant_id = spec.tenant_id
        sandbox.user_id = spec.user_id
        sandbox.session_id = spec.session_id
        sandbox.last_heartbeat = _now()
        sandbox.status = SandboxStatus.READY
        self._sandboxes[sandbox.sandbox_id] = sandbox
        logger.info(
            "sandbox %s claimed from warm pool (template %s)",
            sandbox.sandbox_id,
            sandbox.metadata.get("warm_pool_template", ""),
        )
        return sandbox

    async def _normalize(self, provider_name: str, call):
        try:
            return await call()
        except SandboxError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider failures
            raise ProviderError(f"provider {provider_name} failed: {exc}") from exc


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
