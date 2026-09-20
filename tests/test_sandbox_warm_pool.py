"""Warm pool tests (plan 043 section 10).

Pure unit tests: no Redis, no Docker daemon, no cluster. The Redis
store contract suite is gated on Redis availability (existing pattern,
skips when 6379 is down). Default-off behavior is additionally covered
by the untouched existing sandbox suite.
"""

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from agent_platform.config import Settings
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.models import (
    Endpoint,
    HealthStatus,
    NetworkMode,
    NetworkPolicy,
    PortSpec,
    Sandbox,
    SandboxResources,
    SandboxSpec,
    WorkspaceMount,
)
from agent_platform.sandbox.providers.docker import DockerSandboxProvider
from agent_platform.sandbox.registry import SandboxProviderRegistry
from agent_platform.sandbox.warm_pool import (
    InMemoryPoolStore,
    WarmPoolMaintainer,
    WarmPoolTemplate,
    WarmPoolStore,
    is_pool_eligible,
    make_record,
    parse_templates,
    pool_system_spec,
    record_created_at,
    template_matches,
    validate_templates,
)


def _run(coro):
    return asyncio.run(coro)


# --- fakes -------------------------------------------------------------------


class FakeProvider:
    """Minimal SandboxProvider double recording calls."""

    def __init__(
        self, *, healthy: bool = True, create_error: Exception | None = None, name: str = "fake"
    ) -> None:
        self.provider_name = name
        self.healthy = healthy
        self.create_error = create_error
        self.created: list[SandboxSpec] = []
        self.destroyed: list[Sandbox] = []

    async def create(self, spec: SandboxSpec) -> Sandbox:
        if self.create_error is not None:
            raise self.create_error
        self.created.append(spec)
        return Sandbox(
            sandbox_id=spec.sandbox_id,
            provider=self.provider_name,
            image=spec.image,
            tenant_id=spec.tenant_id,
            user_id=spec.user_id,
            session_id=spec.session_id,
            workspace=spec.workspace,
            resources=spec.resources,
            network_policy=spec.network_policy,
            metadata=dict(spec.metadata),
        )

    async def execute(self, sandbox, request):  # pragma: no cover - unused here
        raise AssertionError("not expected in warm pool tests")

    async def upload_files(self, sandbox, files):  # pragma: no cover
        raise AssertionError("not expected in warm pool tests")

    async def download_files(self, sandbox, paths):  # pragma: no cover
        raise AssertionError("not expected in warm pool tests")

    async def health_check(self, sandbox: Sandbox) -> HealthStatus:
        return HealthStatus(healthy=self.healthy, detail="fake")

    async def destroy(self, sandbox: Sandbox) -> None:
        self.destroyed.append(sandbox)


_TEMPLATE = WarmPoolTemplate(name="t", provider="fake", image="img:1", target_size=1)


def _request(**overrides) -> SandboxSpec:
    base = dict(
        image="img:1",
        tenant_id="tenant-a",
        user_id="u1",
        session_id="s1",
        resources=SandboxResources(),
        network_policy=NetworkPolicy(),
        workspace=WorkspaceMount(workspace_id=""),
        env={},
        ports=(),
        metadata={},
    )
    base.update(overrides)
    return SandboxSpec(**base)


def _sandbox(**overrides) -> Sandbox:
    base = dict(
        sandbox_id="sbx-1",
        provider="fake",
        image="img:1",
        tenant_id="system",
        user_id="sandbox-warm-pool",
        session_id="sandbox-warm-pool",
        workspace=WorkspaceMount(workspace_id="ws-1", mount_path="/workspace"),
        resources=SandboxResources(),
        network_policy=NetworkPolicy(),
        metadata={"container_id": "abc"},
        endpoints=[Endpoint(name="http", address="127.0.0.1:8080")],
    )
    base.update(overrides)
    return Sandbox(**base)


def _replenish(store, template, sandbox):
    store.replenish(make_record(template.name, sandbox))


# --- 1. template parsing -----------------------------------------------------


def test_parse_templates_valid_round_trip():
    raw = json.dumps(
        [
            {
                "name": "python-sbx",
                "provider": "kubernetes",
                "image": "python:3.12-slim",
                "resources": {"cpu": "1", "memory": "2Gi", "pids": 256},
                "network_policy": {"mode": "internet_only"},
                "ports": [{"name": "http", "container_port": 8080}],
                "env": {"A": "1"},
                "metadata": {"k": "v"},
                "mount_path": "/workspace",
                "target_size": 4,
            }
        ]
    )
    templates = parse_templates(raw)
    assert templates == parse_templates(raw)
    template = templates[0]
    assert template.name == "python-sbx"
    assert template.provider == "kubernetes"
    assert template.resources == SandboxResources(memory="2Gi")
    assert template.ports == (PortSpec(name="http", container_port=8080),)
    assert template.env == {"A": "1"}
    assert template.target_size == 4


def test_parse_templates_defaults():
    (template,) = parse_templates('[{"name":"t","provider":"fake","image":"i","target_size":2}]')
    assert template.resources == SandboxResources()
    assert template.network_policy == NetworkPolicy()
    assert template.ports == ()
    assert template.env == {}
    assert template.metadata == {}
    assert template.mount_path == "/workspace"


def test_parse_templates_rejects_invalid_config():
    duplicate = '[{"name":"t","provider":"p","image":"i","target_size":1},{"name":"t","provider":"p","image":"i","target_size":1}]'
    unknown_field = '[{"name":"t","provider":"p","image":"i","target_size":1,"bogus":1}]'
    pvc = '[{"name":"t","provider":"p","image":"i","target_size":1,"metadata":{"workspace_pvc":"pvc"}}]'
    negative = '[{"name":"t","provider":"p","image":"i","target_size":-1}]'
    missing = '[{"name":"t","provider":"p","target_size":1}]'
    bad_mode = '[{"name":"t","provider":"p","image":"i","target_size":1,"network_policy":{"mode":"warp"}}]'
    not_json = "{not json"
    not_array = '{"name":"t"}'
    for raw in (duplicate, unknown_field, pvc, negative, missing, bad_mode, not_json, not_array):
        with pytest.raises(ValueError):
            parse_templates(raw)


def test_parse_templates_empty_is_noop():
    assert parse_templates("") == []
    assert parse_templates("[]") == []


def test_validate_templates_rejects_policy_violation():
    template = WarmPoolTemplate(
        name="t",
        provider="fake",
        image="img:1",
        target_size=1,
        network_policy=NetworkPolicy(mode=NetworkMode.NONE),
        ports=(PortSpec(name="http", container_port=8080),),
    )
    with pytest.raises(ValueError, match="violates sandbox policy"):
        validate_templates([template])
    validate_templates([_TEMPLATE])  # default policy: no restriction


# --- 2. matching -------------------------------------------------------------


def test_template_matches_exact_equality():
    assert template_matches(_TEMPLATE, _request())


def test_template_matching_mismatch_table():
    two_port_template = WarmPoolTemplate(
        name="t2",
        provider="fake",
        image="img:1",
        target_size=1,
        ports=(PortSpec(name="a", container_port=80), PortSpec(name="b", container_port=81)),
    )
    mismatches = [
        _request(image="img:2"),
        _request(resources=SandboxResources(cpu="2")),
        _request(resources=SandboxResources(memory="4Gi")),
        _request(resources=SandboxResources(pids=1)),
        _request(resources=SandboxResources(ephemeral_storage="2Gi")),
        _request(network_policy=NetworkPolicy(mode=NetworkMode.NONE)),
        _request(network_policy=NetworkPolicy(allowlist=("example.com",))),
        _request(ports=(PortSpec(name="a", container_port=80),)),
        _request(env={"A": "1"}),
        _request(metadata={"m": "1"}),
        _request(workspace=WorkspaceMount(workspace_id="", mount_path="/other")),
    ]
    for spec in mismatches:
        assert not template_matches(_TEMPLATE, spec), spec
    # ports are order-sensitive: same members, swapped order -> miss
    assert not template_matches(
        two_port_template,
        _request(ports=(PortSpec(name="b", container_port=81), PortSpec(name="a", container_port=80))),
    )
    assert template_matches(
        two_port_template,
        _request(ports=(PortSpec(name="a", container_port=80), PortSpec(name="b", container_port=81))),
    )
    # explicit default mount path still matches
    assert template_matches(_TEMPLATE, _request(workspace=WorkspaceMount(workspace_id="", mount_path="/workspace")))


def test_is_pool_eligible_requires_ephemeral_workspace():
    assert is_pool_eligible(_request())
    assert not is_pool_eligible(_request(workspace=WorkspaceMount(workspace_id="ws-1")))


# --- 3. system spec ----------------------------------------------------------


def test_pool_system_spec_identity_and_fresh_ids():
    first = pool_system_spec(_TEMPLATE)
    second = pool_system_spec(_TEMPLATE)
    assert first.tenant_id == "system"
    assert first.user_id == "sandbox-warm-pool"
    assert first.session_id == "sandbox-warm-pool"
    assert first.sandbox_id and first.sandbox_id != second.sandbox_id
    assert first.workspace.workspace_id and first.workspace.workspace_id != second.workspace.workspace_id
    assert first.image == "img:1"
    assert first.workspace.mount_path == "/workspace"
    custom = WarmPoolTemplate(
        name="t", provider="fake", image="img:1", target_size=1, mount_path="/opt/ws"
    )
    assert pool_system_spec(custom).workspace.mount_path == "/opt/ws"


# --- 4. in-memory store ------------------------------------------------------


def test_in_memory_store_claim_depletes():
    store = InMemoryPoolStore([_TEMPLATE])
    _replenish(store, _TEMPLATE, _sandbox())
    first = store.claim(_request(), "fake")
    assert first is not None and first.sandbox_id == "sbx-1"
    assert first.metadata["warm_pool_template"] == "t"
    assert store.claim(_request(), "fake") is None  # one-shot: pool empty


def test_in_memory_store_miss_and_provider_mismatch():
    store = InMemoryPoolStore([_TEMPLATE])
    _replenish(store, _TEMPLATE, _sandbox())
    assert store.claim(_request(image="img:2"), "fake") is None
    assert store.claim(_request(), "other") is None  # provider is a matching field
    assert store.size("t") == 1


def test_in_memory_store_size_remove_replenish():
    store = InMemoryPoolStore([_TEMPLATE])
    record = make_record("t", _sandbox())
    store.replenish(record)
    assert store.size("t") == 1 and store.total() == 1
    store.remove(record)
    assert store.size("t") == 0
    store.remove(record)  # idempotent
    entries = store.records("t")
    assert entries == []


def test_record_round_trip_preserves_sandbox():
    original = _sandbox()
    record = make_record("t", original)
    from agent_platform.sandbox.warm_pool import decode_record

    restored = decode_record(json.loads(json.dumps(record)))
    assert restored.sandbox_id == original.sandbox_id
    assert restored.metadata == original.metadata
    assert restored.endpoints == original.endpoints
    assert restored.status is original.status
    assert restored.created_at == original.created_at
    assert restored.workspace == original.workspace
    assert restored.network_policy == original.network_policy


# --- 5. Redis store (gated) --------------------------------------------------


def _redis_available() -> bool:
    try:
        import redis

        redis.Redis.from_url("redis://localhost:6379/0", socket_timeout=0.5).ping()
        return True
    except Exception:  # noqa: BLE001 - any failure means "skip"
        return False


def test_redis_store_claim_round_trip_and_corrupt_record():
    if not _redis_available():
        pytest.skip("Redis server not available")
    template = WarmPoolTemplate(
        name=f"wp-{uuid4().hex[:8]}", provider="fake", image="img:1", target_size=1
    )
    store = WarmPoolStore("redis://localhost:6379/0", [template])
    client = store._redis()  # noqa: SLF001 - test-only direct access for cleanup
    key = f"sandbox:warmpool:{template.name}"
    try:
        sandbox = _run(FakeProvider().create(pool_system_spec(template)))
        _replenish(store, template, sandbox)
        client.lpush(key, "{not json")  # corrupt entry sits before the good one
        assert store.size(template.name) == 2

        claimed = store.claim(_request(), "fake")
        assert claimed is not None and claimed.sandbox_id == sandbox.sandbox_id
        # corrupt record skipped, not returned; pop continues to the good one
        assert store.size(template.name) == 0
        assert store.claim(_request(), "fake") is None
    finally:
        client.delete(key)


def test_redis_store_concurrent_claim_single_winner():
    if not _redis_available():
        pytest.skip("Redis server not available")
    template = WarmPoolTemplate(
        name=f"wp-{uuid4().hex[:8]}", provider="fake", image="img:1", target_size=1
    )
    store = WarmPoolStore("redis://localhost:6379/0", [template])
    client = store._redis()  # noqa: SLF001
    key = f"sandbox:warmpool:{template.name}"
    try:
        sandbox = _run(FakeProvider().create(pool_system_spec(template)))
        _replenish(store, template, sandbox)

        barrier = threading.Barrier(2)
        winners: list[str] = []

        def _claim():
            barrier.wait()
            result = store.claim(_request(), "fake")
            if result is not None:
                winners.append(result.sandbox_id)

        threads = [threading.Thread(target=_claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert winners == [sandbox.sandbox_id]  # LPOP atomicity: exactly one winner

        assert store.acquire_maintain_lock(60) is True
        assert store.acquire_maintain_lock(60) is False  # held
    finally:
        client.delete(key)


# --- 6. manager claim path ---------------------------------------------------


def _manager_with_pool(provider: FakeProvider, store) -> SandboxManager:
    registry = SandboxProviderRegistry()
    registry.register(provider.provider_name, provider)
    return SandboxManager(registry, default_provider="fake", warm_pool=store)


def test_manager_claim_hit_specializes_and_registers():
    provider = FakeProvider()
    store = InMemoryPoolStore([_TEMPLATE])
    _replenish(store, _TEMPLATE, _run(provider.create(pool_system_spec(_TEMPLATE))))
    creations_at_setup = len(provider.created)
    manager = _manager_with_pool(provider, store)

    sandbox = _run(manager.create(_request()))

    # A hit must not create anything: no cold create happens.
    assert sandbox.status.value == "ready"
    assert sandbox.tenant_id == "tenant-a"
    assert sandbox.user_id == "u1"
    assert sandbox.session_id == "s1"
    assert sandbox.metadata["warm_pool_template"] == "t"
    assert manager._sandboxes[sandbox.sandbox_id] is sandbox  # noqa: SLF001
    assert len(provider.created) == creations_at_setup  # no cold create


def test_manager_claim_hit_uses_pool_issued_ids():
    provider = FakeProvider()
    store = InMemoryPoolStore([_TEMPLATE])
    warm = _run(provider.create(pool_system_spec(_TEMPLATE)))
    _replenish(store, _TEMPLATE, warm)
    manager = _manager_with_pool(provider, store)

    sandbox = _run(manager.create(_request()))

    assert sandbox.sandbox_id == warm.sandbox_id  # pool-issued, unchanged
    assert sandbox.workspace == warm.workspace
    assert len(provider.created) == 1  # only the original pool creation


def test_manager_claim_health_failure_destroys_and_goes_cold():
    provider = FakeProvider(healthy=False)
    store = InMemoryPoolStore([_TEMPLATE])
    warm = _run(provider.create(pool_system_spec(_TEMPLATE)))
    _replenish(store, _TEMPLATE, warm)
    manager = _manager_with_pool(provider, store)

    sandbox = _run(manager.create(_request()))

    # Claimed sandbox destroyed (unhealthy), then a fresh cold create runs.
    assert provider.destroyed and provider.destroyed[0].sandbox_id == warm.sandbox_id
    assert len(provider.created) == 2
    assert sandbox.sandbox_id != warm.sandbox_id
    assert store.size("t") == 0  # one-shot: never returned to the pool


def test_manager_claim_store_error_falls_back_to_cold():
    class ExplodingStore:
        def claim(self, spec, provider):
            raise RuntimeError("redis down")

    provider = FakeProvider()
    manager = _manager_with_pool(provider, ExplodingStore())

    sandbox = _run(manager.create(_request()))

    assert sandbox.status.value == "ready"
    assert len(provider.created) == 1


class _EmptyStore:
    def claim(self, spec, provider):
        return None

    def size(self, template_name):
        return 0

    def total(self):
        return 0

    def records(self, template_name):
        return []

    def replenish(self, record):
        raise AssertionError("not expected")

    def remove(self, record):
        raise AssertionError("not expected")

    def acquire_maintain_lock(self, ttl_seconds):
        return True


def test_manager_claim_miss_goes_cold():
    provider = FakeProvider()
    manager = _manager_with_pool(provider, _EmptyStore())

    sandbox = _run(manager.create(_request()))

    assert sandbox.status.value == "ready"
    assert len(provider.created) == 1


def test_manager_non_eligible_spec_bypasses_pool():
    provider = FakeProvider()
    store = InMemoryPoolStore([_TEMPLATE])
    _replenish(store, _TEMPLATE, _sandbox(sandbox_id="warm-1", workspace=WorkspaceMount(workspace_id="ws-warm")))
    manager = _manager_with_pool(provider, store)

    sandbox = _run(
        manager.create(_request(workspace=WorkspaceMount(workspace_id="ws-explicit")))
    )

    assert sandbox.workspace.workspace_id == "ws-explicit"  # cold path honored the id
    assert len(provider.created) == 1
    assert store.size("t") == 1  # pool untouched


# --- 7. cold-path workspace assignment ---------------------------------------


def test_cold_path_assigns_fresh_workspace_id():
    provider = FakeProvider()
    manager = _manager_with_pool(provider, _EmptyStore())

    sandbox = _run(manager.create(_request()))

    (created_spec,) = provider.created
    assert created_spec.workspace.workspace_id == sandbox.workspace.workspace_id
    assert created_spec.workspace.workspace_id  # non-empty for the provider


# --- 8. maintainer -----------------------------------------------------------


def _maintainer(provider: FakeProvider, store, templates, **kwargs) -> WarmPoolMaintainer:
    registry = SandboxProviderRegistry()
    registry.register(provider.provider_name, provider)
    defaults = dict(max_total=10, max_idle_seconds=900, maintain_interval_seconds=30)
    defaults.update(kwargs)
    return WarmPoolMaintainer(registry, store, templates, **defaults)


def test_maintainer_tops_up_to_target_size():
    provider = FakeProvider()
    store = InMemoryPoolStore([_TEMPLATE])
    maintainer = _maintainer(provider, store, [_TEMPLATE])

    _run(maintainer.run_cycle())

    assert store.size("t") == 1
    assert len(provider.created) == 1
    stored = store.records("t")[0].record
    assert stored["version"] == 1
    assert stored["template_name"] == "t"
    assert record_created_at(stored) <= datetime.now(timezone.utc)


def test_maintainer_respects_global_cap():
    provider = FakeProvider()
    first = WarmPoolTemplate(name="t1", provider="fake", image="img:1", target_size=2)
    second = WarmPoolTemplate(name="t2", provider="fake", image="img:2", target_size=2)
    store = InMemoryPoolStore([first, second])
    maintainer = _maintainer(provider, store, [first, second], max_total=3)

    _run(maintainer.run_cycle())

    assert store.total() == 3


def test_maintainer_health_pass_evicts_unhealthy():
    provider = FakeProvider()
    store = InMemoryPoolStore([_TEMPLATE])
    warm = _run(provider.create(pool_system_spec(_TEMPLATE)))
    _replenish(store, _TEMPLATE, warm)
    provider.healthy = False
    maintainer = _maintainer(provider, store, [_TEMPLATE])

    _run(maintainer.run_cycle())

    assert store.size("t") == 0
    assert [sandbox.sandbox_id for sandbox in provider.destroyed] == [warm.sandbox_id]


def test_maintainer_age_pass_evicts_old_records():
    provider = FakeProvider()
    store = InMemoryPoolStore([_TEMPLATE])
    old = _sandbox(sandbox_id="old-1")
    fresh = _sandbox(sandbox_id="fresh-1")
    store.replenish(
        make_record("t", old, created_at=datetime.now(timezone.utc) - timedelta(seconds=2000))
    )
    store.replenish(make_record("t", fresh))
    maintainer = _maintainer(provider, store, [_TEMPLATE], max_idle_seconds=900)

    _run(maintainer.run_cycle())

    remaining = {entry.record["sandbox"]["sandbox_id"] for entry in store.records("t")}
    assert remaining == {"fresh-1"}
    assert [sandbox.sandbox_id for sandbox in provider.destroyed] == ["old-1"]


def test_maintainer_eviction_cleans_workspace():
    class WorkspaceCleaningProvider(FakeProvider):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.cleaned: list[str] = []

        async def destroy_workspace(self, workspace):
            self.cleaned.append(workspace.workspace_id)

    provider = WorkspaceCleaningProvider()
    store = InMemoryPoolStore([_TEMPLATE])
    old = _sandbox(sandbox_id="old-1")
    store.replenish(
        make_record("t", old, created_at=datetime.now(timezone.utc) - timedelta(seconds=2000))
    )
    maintainer = _maintainer(provider, store, [_TEMPLATE], max_idle_seconds=900)

    _run(maintainer.run_cycle())

    assert provider.cleaned == ["ws-1"]


def test_maintainer_lock_held_skips_cycle():
    provider = FakeProvider()
    store = InMemoryPoolStore([_TEMPLATE])
    assert store.acquire_maintain_lock(60) is True
    maintainer = _maintainer(provider, store, [_TEMPLATE])

    _run(maintainer.run_cycle())

    assert store.size("t") == 0
    assert provider.created == []


def test_maintainer_create_failure_logged_cycle_continues():
    broken = FakeProvider(create_error=RuntimeError("docker down"), name="broken")
    good = FakeProvider(name="good")
    broken_template = WarmPoolTemplate(name="bad", provider="broken", image="img:1", target_size=1)
    good_template = WarmPoolTemplate(name="good", provider="good", image="img:2", target_size=1)
    store = InMemoryPoolStore([broken_template, good_template])
    maintainer = WarmPoolMaintainer(
        _registry_of(broken, good),
        store,
        [broken_template, good_template],
        max_total=10,
        max_idle_seconds=900,
        maintain_interval_seconds=30,
    )

    _run(maintainer.run_cycle())  # must not raise

    assert store.size("bad") == 0
    assert store.size("good") == 1


def _registry_of(*providers) -> SandboxProviderRegistry:
    registry = SandboxProviderRegistry()
    for provider in providers:
        registry.register(provider.provider_name, provider)
    return registry


# --- 9. docker destroy_workspace ---------------------------------------------


def test_docker_destroy_workspace_removes_directory(tmp_path):
    provider = DockerSandboxProvider(workspace_root=str(tmp_path))
    target = tmp_path / "ws-1"
    target.mkdir()
    (target / "keep.txt").write_text("data")

    _run(provider.destroy_workspace(WorkspaceMount(workspace_id="ws-1")))

    assert not target.exists()


def test_docker_destroy_workspace_tolerates_missing(tmp_path):
    provider = DockerSandboxProvider(workspace_root=str(tmp_path))

    _run(provider.destroy_workspace(WorkspaceMount(workspace_id="does-not-exist")))
    _run(provider.destroy_workspace(WorkspaceMount(workspace_id="")))

    assert tmp_path.exists()


# --- 10. default-off manager -------------------------------------------------


def test_manager_without_pool_unchanged():
    provider = FakeProvider()
    manager = _manager_with_pool(provider, None)

    sandbox = _run(manager.create(_request()))

    assert sandbox.status.value == "ready"
    assert len(provider.created) == 1
    assert provider.created[0].tenant_id == "tenant-a"


# --- 11. beat registration ---------------------------------------------------


def test_warm_pool_beat_registered_only_when_enabled(monkeypatch):
    from agent_platform.runtime.dispatch import tasks as dispatch_tasks
    from agent_platform.runtime.dispatch.celery_app import WARM_POOL_MAINTAIN_TASK_NAME

    monkeypatch.setattr(dispatch_tasks, "_settings", Settings())
    assert dispatch_tasks._warm_pool_beat_schedule() == {}

    monkeypatch.setattr(
        dispatch_tasks,
        "_settings",
        Settings(sandbox_warm_pool_enabled=True, sandbox_warm_pool_maintain_interval_seconds=30),
    )
    schedule = dispatch_tasks._warm_pool_beat_schedule()
    assert schedule["sandbox-warm-pool-maintain"]["task"] == WARM_POOL_MAINTAIN_TASK_NAME

    monkeypatch.setattr(
        dispatch_tasks,
        "_settings",
        Settings(sandbox_warm_pool_enabled=True, sandbox_warm_pool_maintain_interval_seconds=0),
    )
    assert dispatch_tasks._warm_pool_beat_schedule() == {}


def test_warm_pool_task_noop_when_disabled(monkeypatch):
    from agent_platform.runtime.dispatch import tasks as dispatch_tasks

    monkeypatch.setattr(dispatch_tasks, "_settings", Settings())
    assert dispatch_tasks.maintain_warm_pools.apply().get() == ""
