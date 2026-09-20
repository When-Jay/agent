# Plan 043 — Sandbox Warm Pool

> **Status: PLANNED** (not implemented). Specification:
> `docs/specs/sandbox-warm-pool-spec.md`.
> Default OFF: with `SANDBOX_WARM_POOL_ENABLED` unset, no code path
> activates, no Redis keys are read or written, no beat task is
> registered.

## 1. Objective

Implement the sandbox warm pool per the specification:

* Pre-provision READY sandboxes from configurable templates
* Atomic cross-process claim through `SandboxManager.create`
* Best-effort semantics: always fall back to cold creation
* Zero behavior change when disabled

---

## 2. Architecture

```text
                       (any process)
SandboxManager.create(spec)
   |
   |-- policy validate (unchanged)
   |
   |-- WarmPoolStore.claim(spec)          Redis LPOP (atomic)
   |        |
   |        +-- hit -> health check -> specialize identity -> READY
   |        |
   |        +-- miss/error --------------> cold provider.create
   |
   v
SandboxProvider (unchanged contract)

                       (Celery beat, default queue)
agent_platform.sandbox.maintain_warm_pools
   |
   v
WarmPoolMaintainer (Redis lock guarded)
   |
   |-- top up    provider.create(system spec) + health -> RPUSH
   |-- health    provider.health_check -> destroy + LREM
   |-- evict     age > max_idle -> destroy + LREM + destroy_workspace
```

Cross-process constraint that shapes this design: Celery workers
rebuild the orchestrator per task and the API runs multiple uvicorn
workers, so pool state MUST live in Redis. An in-process pool would
orphan warm sandboxes when the owning process exits.

---

## 3. Module Layout

```text
src/agent_platform/sandbox/
├── warm_pool.py            # NEW: templates, store, maintainer, matching
├── manager.py              # EDIT: optional warm_pool collaborator in create()
├── models.py               # EDIT: none (no new statuses; metadata marker only)
├── interface.py            # EDIT: none (optional destroy_workspace documented
│                           #        as capability detection, not Protocol change)
└── providers/
    ├── docker.py           # EDIT: optional destroy_workspace
    └── kubernetes.py       # EDIT: none (emptyDir needs no cleanup)

src/agent_platform/
├── config.py               # EDIT: warm pool settings + template JSON parsing
└── runtime/dispatch/tasks.py   # EDIT: beat task registration when enabled

tests/
└── test_sandbox_warm_pool.py   # NEW
```

`warm_pool.py` contents:

```text
WarmPoolTemplate          parsed template (frozen dataclass)
parse_templates(json) -> list[WarmPoolTemplate]     # pure, validated
pool_system_spec(t) -> SandboxSpec                   # system identity + fresh ids
is_pool_eligible(spec) -> bool                      # pure
template_matches(t, spec) -> bool                    # pure, exact equality
WarmPoolStore            # Redis-backed; claim/size/replenish/remove
InMemoryPoolStore       # same contract, for unit tests
WarmPoolMaintainer      # cycle logic (top-up/health/evict)
```

The manager stays pool-logic-free: it receives a `warm_pool` object
satisfying `claim(spec) -> Sandbox | None` and nothing else.

---

## 4. Manager Integration

`SandboxManager.__init__` gains `warm_pool=None`. `create()` becomes:

```text
validate_spec                    (unchanged, first)
resolve provider                 (unchanged)
if warm_pool and is_pool_eligible(spec):
    sandbox = warm_pool.claim(spec)          # None on miss/error
    if sandbox:
        health = provider.health_check(sandbox)
        if health.healthy:
            sandbox.tenant_id/user_id/session_id = spec values   # specialize
            sandbox.metadata["warm_pool_template"] = template name
            register; status = READY; return sandbox
        else:
            provider.destroy(sandbox)        # never hand out unhealthy
cold path                        (unchanged)
```

Cold-path addition (independent of the pool, per spec section 4.1):
when `spec.workspace.workspace_id == ""`, the manager assigns a fresh
uuid workspace id before calling the provider (previously such
requests failed inside providers).

Serialization detail: the Redis record embeds the full `Sandbox`
object (dataclasses.asdict round-trip) including `metadata` (pod
name / namespace / container id) and `endpoints`, so any process can
reconstruct and use the sandbox without the creating process.

---

## 5. Template Configuration

Parsed from `SANDBOX_WARM_POOL_TEMPLATES_JSON` at startup, validated
when (and only when) the pool is enabled:

* required: `name`, `provider`, `image`, `target_size >= 0`
* optional with defaults mirroring `SandboxSpec`
  (`resources`, `network_policy`, `ports`, `env`, `metadata`,
  `mount_path`)
* rejected: duplicate names, unknown fields, `metadata.workspace_pvc`,
  `target_size < 0`
* empty array + enabled -> warning, no-op pool

Template specs pass `PolicyValidator.validate_spec` at startup (same
validation as requests — no policy bypass path exists).

---

## 6. Store Design

```text
key: sandbox:warmpool:{template_name}   LIST of JSON records
key: sandbox:warmpool:maintain          SET NX PX = maintain interval

record: {"version": 1, "template_name": ..., "sandbox": {...}, "created_at": ...}
```

* `claim(spec)`: resolve matching template names (exact-match rule,
  spec section 4.2), `LPOP` the first non-empty key, reconstruct
  `Sandbox`; `None` on miss
* claim bounded by `claim_timeout_ms`; any Redis error -> `None` +
  warning (cold path continues)
* unknown record `version` -> skip + warn
* `size(name)`: `LLEN`; `replenish(record)`: `RPUSH`;
  `remove(record)`: `LREM`
* global live count = sum of `LLEN` over templates (checked against
  `max_total` before each replenish create)

---

## 7. Maintainer Design

Registered in `dispatch/tasks.py` beat schedule only when enabled
(same pattern as the evaluation patrol), interval
`SANDBOX_WARM_POOL_MAINTAIN_INTERVAL_SECONDS`. Guarded by the Redis
maintain lock: a second beat pair skips the cycle.

Cycle per template (providers used directly, NOT via
`SandboxManager.create` — see spec section 7.1):

1. **Top up**: while `size < target_size` and global count <
   `max_total`: `pool_system_spec(template)` -> `provider.create` ->
   `provider.health_check` -> `RPUSH` (unhealthy create result:
   destroy, do not push). One cycle may create multiple sandboxes.
2. **Health pass**: `LRANGE` records -> `provider.health_check` ->
   unhealthy: `provider.destroy` + `LREM`
3. **Age pass**: `created_at` older than `max_idle_seconds` ->
   `provider.destroy` + `LREM` + workspace cleanup (next section)

All provider exceptions inside a cycle are logged and the cycle
continues; the task itself must not raise.

### Workspace cleanup

On evicting a never-claimed sandbox: `hasattr(provider,
"destroy_workspace")` -> `await provider.destroy_workspace(workspace)`.
The Docker provider implements it as removal of the host directory
under its workspace root; the Kubernetes provider does not implement
it (`emptyDir` dies with the pod). Never called for claimed
sandboxes (pool members are never-claimed by construction).

---

## 8. Docker Provider Change

```python
async def destroy_workspace(self, workspace: WorkspaceMount) -> None:
    # remove <workspace_root>/<workspace_id>; must never be called
    # for a sandbox that has been claimed
```

Reuses the provider's existing workspace-root constructor argument.
No other provider changes (both providers already satisfy the
stateless-addressing requirement: infra addressing in
`Sandbox.metadata`; the timeout-wrapper probe cache populates
lazily on first use).

---

## 9. Config Additions (config.py)

Following house conventions (frozen Settings, `_env_bool/_env_int`,
JSON mirror of `MCP_SERVERS_JSON`):

```text
sandbox_warm_pool_enabled              default False
sandbox_warm_pool_templates_json       default ""
sandbox_warm_pool_max_total            default 32
sandbox_warm_pool_max_idle_seconds     default 900
sandbox_warm_pool_maintain_interval_seconds   default 30
sandbox_warm_pool_claim_timeout_ms     default 1000
```

`parse_templates` lives in `warm_pool.py` (pure function, unit
tested); `Settings` stores raw strings only.

Deployment hygiene: add commented-out entries to `.env.example`
(default-off) and mention the beat task in
`docs/architecture/07-deployment-architecture.md` worker/beat section
(one paragraph, no schema change).

---

## 10. Tests

`tests/test_sandbox_warm_pool.py` (pure unit tests, no Redis, no
Docker, no cluster):

1. **Template parsing**: valid JSON round-trip; duplicate names,
   unknown fields, `workspace_pvc`, negative target_size rejected;
   enabled + empty array -> allowed no-op
2. **Matching**: exact equality table — each creation-fixed field
   mismatch (image / cpu / memory / pids / ephemeral_storage /
   network mode / allowlist / ports order+content / env / metadata /
   mount_path) -> miss; eligible only with empty workspace_id
3. **System spec**: system identity, fresh sandbox/workspace ids,
   mount path honored
4. **Store (in-memory)**: claim hit depletes (two claims, one hit),
   claim returns None when empty, replenish/remove/size consistent
5. **Store (Redis)**: contract suite gated on Redis availability
   (skip when 6379 down — existing pattern); concurrent claim
   (two readers, one record) yields exactly one winner
6. **Manager claim path**: fake provider + in-memory store —
   hit returns READY + specialized identity + registered; template
   marker in metadata; health-fail -> destroy called + cold create;
   store raising -> cold create; miss -> provider.create called
7. **Cold-path workspace assignment**: empty workspace_id gets a
   fresh id and reaches the provider (improves current behavior:
   previously ProviderError)
8. **Maintainer**: top-up respects target and global cap; unhealthy
   record destroyed + removed; age eviction by `created_at`; lock
   held -> cycle skipped; provider create failure logged, cycle
   continues
9. **Docker destroy_workspace**: tmp workspace root + created dir
   removed; missing dir tolerated
10. **Default-off**: manager without `warm_pool` behaves exactly as
    today (covered additionally by the untouched existing suite)
11. **Beat registration**: schedule contains the task only when
    enabled

Integration (deferred, mirroring 042): cluster/docker verification
of a warm claim round-trip (`create -> claim -> execute -> destroy`).

---

## 11. Implementation Order

1. `warm_pool.py` pure parts: parsing, matching, system spec
2. In-memory store + manager integration + cold-path workspace
   assignment (with tests 1-7, 10)
3. Redis store (test 8 redis-gated) + config wiring
4. Maintainer + beat registration (tests 8, 11)
5. Docker `destroy_workspace` (test 9)
6. `.env.example` + deployment doc note
7. Full suite: `uv pip install -e . --python .venv/Scripts/python.exe`
   unchanged; full pytest (Windows: 371+ passed baseline must hold)

---

## 12. Acceptance

* All spec acceptance criteria (sandbox-warm-pool-spec.md section 13)
  checked
* Full existing test suite passes unmodified (default-off proof)
* With pool enabled and Redis up: claim hit path measurable
  (create latency ~= one health check vs. full provisioning)
* With pool enabled and Redis down: creation still succeeds

---

## 13. Out of Scope

Everything in sandbox-warm-pool-spec.md section 14, notably: adaptive
sizing, return-to-pool, near-match allocation, PVC pooling, k8s
label patching at claim, per-tenant pools, GPU/cross-cluster pools.
