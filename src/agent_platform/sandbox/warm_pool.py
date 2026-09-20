"""Sandbox warm pool (sandbox-warm-pool-spec.md, plan 043).

Pre-provisioned, health-checked sandboxes handed out on demand to
reduce cold-start latency. Best-effort by contract: every pool failure
degrades to the cold creation path and never propagates to callers.

Default OFF: with ``SANDBOX_WARM_POOL_ENABLED`` unset nothing here
runs -- no Redis keys are read or written and no beat task registers.

Claim matching note: spec section 4.2 makes the *provider* a
creation-fixed matching field, but the provider name is not part of
``SandboxSpec`` -- the SandboxManager resolves it
(``provider or default_provider``). ``claim`` therefore takes the
requested provider as a second argument; the normative matching table
(section 4.2) requires it even though the section 5.1 sketch omits it.
"""

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, Sequence
from uuid import uuid4

from agent_platform.sandbox.errors import SandboxError
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
from agent_platform.sandbox.policy import PolicyValidator, SandboxPolicy

logger = logging.getLogger(__name__)

# Pool-issued system identity (spec section 3.1).
POOL_TENANT_ID = "system"
POOL_USER_ID = "sandbox-warm-pool"
POOL_SESSION_ID = "sandbox-warm-pool"

RECORD_VERSION = 1
DEFAULT_MOUNT_PATH = "/workspace"

REDIS_KEY_PREFIX = "sandbox:warmpool:"
REDIS_MAINTAIN_LOCK_KEY = "sandbox:warmpool:maintain"

_TEMPLATE_REQUIRED = ("name", "provider", "image", "target_size")
_TEMPLATE_OPTIONAL = (
    "resources",
    "network_policy",
    "ports",
    "env",
    "metadata",
    "mount_path",
)
_TEMPLATE_FIELDS = frozenset(_TEMPLATE_REQUIRED) | frozenset(_TEMPLATE_OPTIONAL)
_RESOURCE_FIELDS = frozenset({"cpu", "memory", "pids", "ephemeral_storage"})
_NETWORK_FIELDS = frozenset({"mode", "allowlist"})
_PORT_FIELDS = frozenset({"name", "container_port"})


# --- templates ---------------------------------------------------------------


@dataclass(frozen=True)
class WarmPoolTemplate:
    """One poolable sandbox shape; optional fields mirror ``SandboxSpec``."""

    name: str
    provider: str
    image: str
    target_size: int
    resources: SandboxResources = field(default_factory=SandboxResources)
    network_policy: NetworkPolicy = field(default_factory=NetworkPolicy)
    ports: tuple[PortSpec, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    mount_path: str = DEFAULT_MOUNT_PATH


def parse_templates(raw: str) -> list[WarmPoolTemplate]:
    """Parse the template configuration JSON (spec sections 5 and 12).

    Pure function; raises ``ValueError`` with an operator-readable
    message on any invalid configuration (unknown fields, duplicate
    names, ``metadata.workspace_pvc``, negative target_size, malformed
    sub-structures). An empty string or empty array is a valid no-op
    configuration.
    """
    if not (raw or "").strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"warm pool templates: invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("warm pool templates: expected a JSON array")
    templates: list[WarmPoolTemplate] = []
    seen: set[str] = set()
    for index, entry in enumerate(data):
        template = _parse_template(entry, index)
        if template.name in seen:
            raise ValueError(f"warm pool templates: duplicate name {template.name!r}")
        seen.add(template.name)
        templates.append(template)
    if not templates:
        logger.warning("warm pool enabled with zero templates; pool is a no-op")
    return templates


def _parse_template(entry: Any, index: int) -> WarmPoolTemplate:
    where = f"warm pool templates[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected an object")
    unknown = sorted(set(entry) - _TEMPLATE_FIELDS)
    if unknown:
        raise ValueError(f"{where}: unknown fields {unknown}")
    missing = [key for key in _TEMPLATE_REQUIRED if key not in entry]
    if missing:
        raise ValueError(f"{where}: missing required fields {missing}")
    name = entry["name"]
    provider = entry["provider"]
    image = entry["image"]
    if not all(isinstance(value, str) and value for value in (name, provider, image)):
        raise ValueError(f"{where}: name/provider/image must be non-empty strings")
    target_size = entry["target_size"]
    if isinstance(target_size, bool) or not isinstance(target_size, int) or target_size < 0:
        raise ValueError(f"{where}: target_size must be an integer >= 0")
    metadata = entry.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"{where}: metadata must be an object")
    if "workspace_pvc" in metadata:
        raise ValueError(
            f"{where}: metadata.workspace_pvc is not poolable "
            "(PVC workspaces are session-specific; spec section 4.2)"
        )
    return WarmPoolTemplate(
        name=name,
        provider=provider,
        image=image,
        target_size=target_size,
        resources=_parse_resources(entry.get("resources"), where),
        network_policy=_parse_network_policy(entry.get("network_policy"), where),
        ports=_parse_ports(entry.get("ports"), where),
        env=_parse_env(entry.get("env"), where),
        metadata=dict(metadata),
        mount_path=entry.get("mount_path") or DEFAULT_MOUNT_PATH,
    )


def _parse_resources(raw: Any, where: str) -> SandboxResources:
    if raw is None:
        return SandboxResources()
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: resources must be an object")
    unknown = sorted(set(raw) - _RESOURCE_FIELDS)
    if unknown:
        raise ValueError(f"{where}: unknown resource fields {unknown}")
    current = SandboxResources()
    values = {
        name: raw.get(name, getattr(current, name)) for name in _RESOURCE_FIELDS
    }
    try:
        return SandboxResources(
            cpu=str(values["cpu"]),
            memory=str(values["memory"]),
            pids=int(values["pids"]),
            ephemeral_storage=str(values["ephemeral_storage"]),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where}: invalid resources: {exc}") from exc


def _parse_network_policy(raw: Any, where: str) -> NetworkPolicy:
    if raw is None:
        return NetworkPolicy()
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: network_policy must be an object")
    unknown = sorted(set(raw) - _NETWORK_FIELDS)
    if unknown:
        raise ValueError(f"{where}: unknown network_policy fields {unknown}")
    mode_raw = raw.get("mode", NetworkMode.INTERNET_ONLY.value)
    try:
        mode = NetworkMode(mode_raw)
    except ValueError as exc:
        raise ValueError(f"{where}: unknown network mode {mode_raw!r}") from exc
    allowlist_raw = raw.get("allowlist", ())
    if not isinstance(allowlist_raw, (list, tuple)) or not all(
        isinstance(item, str) for item in allowlist_raw
    ):
        raise ValueError(f"{where}: network_policy.allowlist must be a list of strings")
    return NetworkPolicy(mode=mode, allowlist=tuple(allowlist_raw))


def _parse_ports(raw: Any, where: str) -> tuple[PortSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{where}: ports must be an array")
    ports: list[PortSpec] = []
    for position, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) - _PORT_FIELDS:
            raise ValueError(
                f"{where}: ports[{position}] must be an object with "
                "name/container_port only"
            )
        try:
            ports.append(PortSpec(name=item["name"], container_port=item["container_port"]))
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{where}: invalid ports[{position}]: {exc}") from exc
    return tuple(ports)


def _parse_env(raw: Any, where: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw.items()
    ):
        raise ValueError(f"{where}: env must be an object of strings")
    return dict(raw)


def validate_templates(
    templates: Sequence[WarmPoolTemplate], *, policy: SandboxPolicy | None = None
) -> None:
    """Validate every template spec against platform policy (spec section 9.2).

    Same validator as request paths -- templates get no policy bypass.
    Called at composition time when (and only when) the pool is enabled.
    """
    validator = PolicyValidator()
    for template in templates:
        try:
            validator.validate_spec(policy or SandboxPolicy(), pool_system_spec(template))
        except SandboxError as exc:
            raise ValueError(
                f"warm pool template {template.name!r} violates sandbox policy: {exc}"
            ) from exc


# --- matching and system specs -----------------------------------------------


def pool_system_spec(template: WarmPoolTemplate) -> SandboxSpec:
    """Build the creation spec for one warm sandbox (spec section 3.1).

    System identity plus pool-issued sandbox/workspace ids. sandbox_id
    is assigned here rather than by the manager because the maintainer
    creates through the provider directly (spec section 7.1).
    """
    return SandboxSpec(
        image=template.image,
        tenant_id=POOL_TENANT_ID,
        user_id=POOL_USER_ID,
        session_id=POOL_SESSION_ID,
        resources=template.resources,
        network_policy=template.network_policy,
        workspace=WorkspaceMount(workspace_id=str(uuid4()), mount_path=template.mount_path),
        env=dict(template.env),
        ports=template.ports,
        sandbox_id=str(uuid4()),
        metadata=dict(template.metadata),
    )


def is_pool_eligible(spec: SandboxSpec) -> bool:
    """Request eligibility (spec section 4.1): ephemeral workspace only.

    An empty ``workspace_id`` declares "any fresh workspace". Template
    matching (including the provider) happens at claim time.
    """
    return spec.workspace.workspace_id == ""


def template_matches(template: WarmPoolTemplate, spec: SandboxSpec) -> bool:
    """Exact equality of every creation-fixed field (spec section 4.2).

    The provider field is matched by the caller (``claim`` receives the
    requested provider; ``SandboxSpec`` has no provider field).
    """
    return (
        template.image == spec.image
        and template.resources == spec.resources
        and template.network_policy == spec.network_policy
        and template.ports == spec.ports
        and template.env == spec.env
        and template.metadata == spec.metadata
        and template.mount_path == (spec.workspace.mount_path or DEFAULT_MOUNT_PATH)
    )


# --- record serialization ----------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_record(
    template_name: str, sandbox: Sandbox, *, created_at: datetime | None = None
) -> dict:
    """Build one warm-sandbox record (spec section 6.2)."""
    return {
        "version": RECORD_VERSION,
        "template_name": template_name,
        "sandbox": _sandbox_to_dict(sandbox),
        "created_at": (created_at or _utcnow()).isoformat(),
    }


def record_template_name(record: dict) -> str:
    return str(record.get("template_name", ""))


def record_created_at(record: dict) -> datetime:
    return datetime.fromisoformat(str(record["created_at"]))


def decode_record(record: dict) -> Sandbox:
    """Reconstruct the ``Sandbox`` from a record; ValueError on unknown version."""
    if record.get("version") != RECORD_VERSION:
        raise ValueError(f"unsupported warm pool record version: {record.get('version')!r}")
    return _sandbox_from_dict(record["sandbox"])


def _sandbox_to_dict(sandbox: Sandbox) -> dict:
    data = asdict(sandbox)
    data["status"] = sandbox.status.value
    data["created_at"] = sandbox.created_at.isoformat()
    data["last_heartbeat"] = (
        sandbox.last_heartbeat.isoformat() if sandbox.last_heartbeat else None
    )
    return data


def _sandbox_from_dict(data: dict) -> Sandbox:
    network = data["network_policy"]
    return Sandbox(
        sandbox_id=data["sandbox_id"],
        provider=data["provider"],
        image=data["image"],
        tenant_id=data["tenant_id"],
        user_id=data["user_id"],
        session_id=data["session_id"],
        workspace=WorkspaceMount(**data["workspace"]),
        resources=SandboxResources(**data["resources"]),
        network_policy=NetworkPolicy(
            mode=NetworkMode(network["mode"]), allowlist=tuple(network["allowlist"])
        ),
        status=SandboxStatus(data["status"]),
        created_at=datetime.fromisoformat(data["created_at"]),
        last_heartbeat=(
            datetime.fromisoformat(data["last_heartbeat"]) if data.get("last_heartbeat") else None
        ),
        metadata=dict(data["metadata"]),
        endpoints=[Endpoint(**endpoint) for endpoint in data["endpoints"]],
    )


def _dumps(record: dict) -> str:
    # sort_keys keeps replenish/remove/LREM byte-identical across processes.
    return json.dumps(record, sort_keys=True)


def _pool_key(template_name: str) -> str:
    return f"{REDIS_KEY_PREFIX}{template_name}"


# --- stores ------------------------------------------------------------------


@dataclass(frozen=True)
class PoolEntry:
    """One stored record: exact raw JSON plus its parsed form.

    ``raw`` lets Redis LREM remove by value without re-serialization
    ambiguity.
    """

    raw: str
    record: dict


class PoolStore(Protocol):
    """Pool storage contract shared by the Redis and in-memory backends."""

    def claim(self, spec: SandboxSpec, provider: str) -> Sandbox | None: ...

    def size(self, template_name: str) -> int: ...

    def total(self) -> int: ...

    def records(self, template_name: str) -> list[PoolEntry]: ...

    def replenish(self, record: dict) -> None: ...

    def remove(self, record: dict) -> None: ...

    def acquire_maintain_lock(self, ttl_seconds: float) -> bool: ...


class WarmPoolStore:
    """Redis-backed cross-process pool store (spec section 6).

    ``claim`` never raises: any Redis error degrades to ``None`` so the
    caller falls back to the cold path (bounded by ``claim_timeout_ms``
    through the client socket timeouts). Maintenance-facing methods
    raise so the maintainer can log and retry next cycle. The ``redis``
    package is imported lazily (mirrors redis_stream.py).
    """

    def __init__(
        self,
        redis_url: str,
        templates: Sequence[WarmPoolTemplate],
        *,
        claim_timeout_ms: int = 1000,
    ) -> None:
        self._redis_url = redis_url
        self._templates = list(templates)
        self._claim_timeout_s = max(claim_timeout_ms, 1) / 1000
        self._client = None

    def claim(self, spec: SandboxSpec, provider: str) -> Sandbox | None:
        try:
            client = self._redis()
            for template in self._matching(spec, provider):
                raw = client.lpop(_pool_key(template.name))
                while raw is not None:
                    try:
                        return self._specialize(decode_record(json.loads(raw)), template)
                    except (ValueError, KeyError, TypeError) as exc:
                        logger.warning(
                            "warm pool: corrupt record dropped from %s: %s", template.name, exc
                        )
                        raw = client.lpop(_pool_key(template.name))
            return None
        except Exception as exc:  # noqa: BLE001 - pool never breaks creation
            logger.warning("warm pool claim failed; falling back to cold create: %s", exc)
            return None

    def size(self, template_name: str) -> int:
        return int(self._redis().llen(_pool_key(template_name)))

    def total(self) -> int:
        return sum(self.size(template.name) for template in self._templates)

    def records(self, template_name: str) -> list[PoolEntry]:
        client = self._redis()
        key = _pool_key(template_name)
        entries: list[PoolEntry] = []
        corrupt: list[str] = []
        for raw in client.lrange(key, 0, -1):
            try:
                entries.append(PoolEntry(raw=raw, record=json.loads(raw)))
            except ValueError:
                corrupt.append(raw)
        for raw in corrupt:
            logger.warning("warm pool: dropping corrupt record from %s", template_name)
            client.lrem(key, 1, raw)
        return entries

    def replenish(self, record: dict) -> None:
        self._redis().rpush(_pool_key(record_template_name(record)), _dumps(record))

    def remove(self, record: dict) -> None:
        self._redis().lrem(_pool_key(record_template_name(record)), 1, _dumps(record))

    def acquire_maintain_lock(self, ttl_seconds: float) -> bool:
        return bool(
            self._redis().set(
                REDIS_MAINTAIN_LOCK_KEY, "1", nx=True, px=max(1, int(ttl_seconds * 1000))
            )
        )

    # -- internal -------------------------------------------------------------

    def _redis(self):
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(
                self._redis_url,
                socket_timeout=self._claim_timeout_s,
                socket_connect_timeout=self._claim_timeout_s,
            )
        return self._client

    def _matching(self, spec: SandboxSpec, provider: str) -> list[WarmPoolTemplate]:
        return [
            template
            for template in self._templates
            if template.provider == provider and template_matches(template, spec)
        ]

    @staticmethod
    def _specialize(sandbox: Sandbox, template: WarmPoolTemplate) -> Sandbox:
        # Platform-record marker (spec section 5.2); identity specialization
        # itself happens in the manager, which owns the request identity.
        sandbox.metadata["warm_pool_template"] = template.name
        return sandbox


class InMemoryPoolStore:
    """Process-local store with the ``PoolStore`` contract; unit tests only.

    NOT for production: pool state must be cross-process (spec section 6).
    """

    def __init__(self, templates: Sequence[WarmPoolTemplate]) -> None:
        self._templates = list(templates)
        self._lock = threading.Lock()
        self._records: dict[str, list[str]] = {}
        self._lock_held_until: datetime | None = None

    def claim(self, spec: SandboxSpec, provider: str) -> Sandbox | None:
        with self._lock:
            for template in self._matching(spec, provider):
                bucket = self._records.setdefault(template.name, [])
                while bucket:
                    raw = bucket.pop(0)
                    try:
                        sandbox = decode_record(json.loads(raw))
                    except (ValueError, KeyError, TypeError) as exc:
                        logger.warning(
                            "warm pool: corrupt record dropped from %s: %s", template.name, exc
                        )
                        continue
                    sandbox.metadata["warm_pool_template"] = template.name
                    return sandbox
            return None

    def size(self, template_name: str) -> int:
        with self._lock:
            return len(self._records.get(template_name, []))

    def total(self) -> int:
        with self._lock:
            return sum(len(bucket) for bucket in self._records.values())

    def records(self, template_name: str) -> list[PoolEntry]:
        with self._lock:
            return [
                PoolEntry(raw=raw, record=json.loads(raw))
                for raw in self._records.get(template_name, [])
            ]

    def replenish(self, record: dict) -> None:
        with self._lock:
            self._records.setdefault(record_template_name(record), []).append(_dumps(record))

    def remove(self, record: dict) -> None:
        with self._lock:
            bucket = self._records.get(record_template_name(record), [])
            try:
                bucket.remove(_dumps(record))
            except ValueError:
                pass  # already gone; removal is idempotent

    def acquire_maintain_lock(self, ttl_seconds: float) -> bool:
        with self._lock:
            now = _utcnow()
            if self._lock_held_until is not None and self._lock_held_until > now:
                return False
            self._lock_held_until = now + timedelta(seconds=ttl_seconds)
            return True

    def _matching(self, spec: SandboxSpec, provider: str) -> list[WarmPoolTemplate]:
        return [
            template
            for template in self._templates
            if template.provider == provider and template_matches(template, spec)
        ]


# --- maintainer --------------------------------------------------------------


class WarmPoolMaintainer:
    """One maintenance cycle: top-up, health pass, age pass (spec section 7.2).

    Talks to providers directly through the registry -- never through
    ``SandboxManager.create``, which would attempt claims and register
    pool sandboxes in the wrong process (spec section 7.1). Provider
    exceptions inside a pass are logged; the cycle continues.
    """

    def __init__(
        self,
        registry,
        store: PoolStore,
        templates: Sequence[WarmPoolTemplate],
        *,
        max_total: int,
        max_idle_seconds: float,
        maintain_interval_seconds: float,
    ) -> None:
        self._registry = registry
        self._store = store
        self._templates = list(templates)
        self._max_total = max_total
        self._max_idle_seconds = max_idle_seconds
        self._maintain_interval_seconds = maintain_interval_seconds

    async def run_cycle(self) -> None:
        if not self._store.acquire_maintain_lock(self._maintain_interval_seconds):
            logger.debug("warm pool: maintain lock held; skipping cycle")
            return
        for template in self._templates:
            for step in (self._top_up, self._health_pass, self._age_pass):
                try:
                    await step(template)
                except Exception:  # noqa: BLE001 - a failing pass never aborts the cycle
                    logger.warning(
                        "warm pool: %s failed for template %s",
                        getattr(step, "__name__", step),
                        template.name,
                        exc_info=True,
                    )

    # -- passes ---------------------------------------------------------------

    async def _top_up(self, template: WarmPoolTemplate) -> None:
        while self._store.size(template.name) < template.target_size:
            if self._store.total() >= self._max_total:
                logger.info(
                    "warm pool: global cap %s reached; stop replenishing", self._max_total
                )
                return
            sandbox = await self._create_warm(template)
            if sandbox is None:
                return  # provider failed; retry next cycle
            self._store.replenish(make_record(template.name, sandbox))
            logger.info(
                "warm pool: replenished sandbox %s (template %s)",
                sandbox.sandbox_id,
                template.name,
            )

    async def _health_pass(self, template: WarmPoolTemplate) -> None:
        provider = self._registry.get(template.provider)
        for entry in self._store.records(template.name):
            sandbox = self._decode(entry, template.name)
            if sandbox is None:
                continue
            try:
                health = await provider.health_check(sandbox)
            except Exception:  # noqa: BLE001 - transient evidence, retry next cycle
                logger.warning(
                    "warm pool: health probe failed for %s", sandbox.sandbox_id, exc_info=True
                )
                continue
            if not health.healthy:
                await self._evict(template, entry, sandbox, reason="health")

    async def _age_pass(self, template: WarmPoolTemplate) -> None:
        provider = self._registry.get(template.provider)
        now = _utcnow()
        for entry in self._store.records(template.name):
            sandbox = self._decode(entry, template.name)
            if sandbox is None:
                continue
            age = (now - record_created_at(entry.record)).total_seconds()
            if age <= self._max_idle_seconds:
                continue
            await self._evict(template, entry, sandbox, reason="age")

    # -- internal -------------------------------------------------------------

    async def _create_warm(self, template: WarmPoolTemplate) -> Sandbox | None:
        try:
            provider = self._registry.get(template.provider)
            sandbox = await provider.create(pool_system_spec(template))
            health = await provider.health_check(sandbox)
        except Exception:  # noqa: BLE001 - logged; retried next cycle
            logger.warning(
                "warm pool: create failed for template %s", template.name, exc_info=True
            )
            return None
        if health.healthy:
            # Mirror the manager's cold path: post-create health success
            # marks the sandbox READY with a fresh heartbeat.
            sandbox.status = SandboxStatus.READY
            sandbox.last_heartbeat = _utcnow()
            return sandbox
        try:
            await provider.destroy(sandbox)
        except Exception:  # noqa: BLE001 - destroy of a dead sandbox is best effort
            logger.warning(
                "warm pool: destroy of unhealthy warm sandbox %s failed",
                sandbox.sandbox_id,
                exc_info=True,
            )
        return None

    async def _evict(
        self,
        template: WarmPoolTemplate,
        entry: PoolEntry,
        sandbox: Sandbox,
        *,
        reason: str,
    ) -> None:
        provider = self._registry.get(template.provider)
        try:
            await provider.destroy(sandbox)
        except Exception:  # noqa: BLE001 - destroy is idempotent; retried next cycle
            logger.warning(
                "warm pool: destroy failed for sandbox %s", sandbox.sandbox_id, exc_info=True
            )
        self._store.remove(entry.record)
        # Never-claimed invariant (spec section 5.3) makes this cleanup safe.
        cleanup = getattr(provider, "destroy_workspace", None)
        if cleanup is not None:
            try:
                await cleanup(sandbox.workspace)
            except Exception:  # noqa: BLE001 - residue cleanup is best effort
                logger.warning(
                    "warm pool: workspace cleanup failed for sandbox %s",
                    sandbox.sandbox_id,
                    exc_info=True,
                )
        logger.info(
            "warm pool: evicted sandbox %s (template %s, reason %s)",
            sandbox.sandbox_id,
            template.name,
            reason,
        )

    @staticmethod
    def _decode(entry: PoolEntry, template_name: str) -> Sandbox | None:
        try:
            return decode_record(entry.record)
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "warm pool: skipping unsupported record in %s: %s", template_name, exc
            )
            return None
