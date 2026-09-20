# Sandbox Warm Pool Specification

> Status: PLANNED. Optional extension to the Sandbox specification
> (sandbox-spec.md). **The warm pool is disabled by default.** When
> disabled, platform behavior is byte-identical to the current
> contract; no code path, config entry, or deployment component is
> required. Implementation plan: docs/plans/043-sandbox-warm-pool.md.

## 1. Purpose

The warm pool reduces sandbox cold-start latency by pre-provisioning
fully created, health-checked sandboxes that wait in a pool and are
handed out on demand.

Motivation: a cold sandbox requires image pull + container/pod
scheduling + startup + health check (seconds on Kubernetes). For
interactive Agent sessions this delays the first tool response.

```text
cold path:   create request -> provision -> health -> READY     (slow)
warm path:   create request -> claim from pool -> READY        (fast)
```

The pool is a **latency optimization only**. It must never reduce
availability, weaken isolation, or change the provider contract.

### Position

The warm pool is an extension of the Sandbox capability. It is not a
new top-level module. It lives inside `agent_platform.sandbox` and
wraps the existing `SandboxManager` / `SandboxProvider` abstractions.

---

## 2. Responsibilities

### 2.1 Required (when enabled)

The warm pool must provide:

1. Template configuration (a poolable subset of `SandboxSpec`)
2. Background maintenance: replenish, health-check, evict
3. Atomic cross-process claim
4. Transparent integration through `SandboxManager.create`
5. Guaranteed fallback to cold creation on any pool failure

### 2.2 Out of Scope

The warm pool does not decide:

* Which provider or image a request should use
* Whether a spec passes Policy (PolicyValidator runs unchanged,
  before any pool interaction)
* When a claimed sandbox is destroyed (normal lifecycle semantics)
* Scheduling or placement (the provider/infrastructure decides)

---

## 3. Core Concepts

```text
WarmPoolTemplate
├── name                  # unique, used in Redis keys and metrics
├── provider
├── image
├── resources
├── network_policy
├── ports
├── env
├── metadata              # creation-fixed provider metadata
├── mount_path            # workspace mount path (default /workspace)
└── target_size           # warm sandboxes to keep READY
```

```text
Warm Sandbox
├── created from a template via the normal provider path
├── status READY, health-checked
├── never claimed: no tenant session has ever executed in it
├── pool-issued identity: sandbox_id, workspace_id
└── waiting in the pool store
```

```text
Claim
├── atomic pop of one warm sandbox matching the request
├── specialization: tenant/user/session recorded platform-side
└── the claimed sandbox enters the normal lifecycle
```

### 3.1 Poolable Identity

Warm sandboxes are created with a reserved system identity:

```text
tenant_id  = "system"
user_id    = "sandbox-warm-pool"
session_id = "sandbox-warm-pool"
sandbox_id = fresh uuid (pool-issued, never changes)
workspace_id = fresh uuid (pool-issued, never changes)
```

`sandbox_id` and `workspace_id` are assigned by the pool and survive
the claim unchanged. All other identity is a platform record updated
at claim time (specialization).

---

## 4. Eligibility and Matching

### 4.1 Request Eligibility

A `create(spec)` request is pool-eligible only when ALL hold:

1. The warm pool is enabled.
2. `spec.workspace.workspace_id == ""` — the request declares an
   **ephemeral workspace** (no reattach to an existing workspace).
3. Some configured template matches the spec (section 4.2).

Any other request bypasses the pool entirely (cold path).

Rule 2 defines a new request semantic: an empty `workspace_id` means
"any fresh workspace". `SandboxManager` assigns a fresh `workspace_id`
on the cold path when the request omits one (providers require a
non-empty id; today such requests fail with `ProviderError`).

Ephemeral-workspace semantics per provider:

* Kubernetes: `emptyDir` workspace; data does not survive sandbox
  destruction (inherent to `emptyDir`).
* Docker: host directory under the provider's workspace root; the
  directory is retained after destroy like any workspace (pre-existing
  platform behavior), but no session is expected to reattach by the
  generated id.

### 4.2 Template Matching

Providers fix the following at creation time and cannot change them
afterwards (Docker container config and Kubernetes Pod specs are
immutable). Therefore a warm sandbox can serve a request only on
**exact equality** of every creation-fixed field:

```text
request field          must equal template field
-----------------------------------------------
provider               provider
image                  image
resources              resources           (cpu, memory, pids, ephemeral_storage)
network_policy         network_policy      (mode + allowlist)
ports                  ports               (name + container_port, ordered)
env                    env                 (exact dict equality)
metadata               metadata            (exact dict equality)
workspace.mount_path   mount_path
```

V1 uses exact equality only. Superset/near-match allocation (e.g.
resources >= requested) is future work (section 14).

Templates must not declare `workspace_pvc` metadata (a PVC mount is
session-specific and creation-fixed; PVC workspaces are never
poolable). Template validation rejects such configuration at startup.

### 4.3 Matching Order

`SandboxManager.create` evaluation order is fixed:

```text
1. PolicyValidator.validate_spec     (unchanged, always first)
2. pool eligibility check
3. pool claim attempt               (only if eligible)
4. cold create                      (on miss or any pool failure)
```

The pool never bypasses policy validation.

---

## 5. Claim Contract

### 5.1 Integration

The pool hooks into `SandboxManager.create` as an optional
collaborator. The manager contains no pool internals; it only knows
"claim(spec) -> Sandbox | None".

```python
class WarmPoolStore(Protocol):
    def claim(self, spec: SandboxSpec) -> Sandbox | None: ...
    def size(self, template_name: str) -> int: ...
```

### 5.2 Claim Flow

```text
SandboxManager.create(spec)
   |
   v
validate policy
   |
   v
eligible? ---- no ----> cold create
   |
   yes
   v
store.claim(spec)  -- atomic pop, Redis LPOP
   |
   +-- miss / store error --> cold create (warn log)
   |
   +-- record
        v
   provider.health_check(sandbox)
        |
        +-- unhealthy --> provider.destroy --> cold create
        |
        v
   specialize identity (tenant/user/session)   [platform record only]
   register in manager
   status = READY
   return sandbox
```

Rules:

* Claim is **one-shot**: a claimed sandbox never returns to the pool,
  regardless of session outcome. There is no return-to-pool API.
* The claimed sandbox keeps its pool-issued `sandbox_id`,
  `workspace_id`, `endpoints`, and all infra attributes.
* Specialization mutates platform records only. Infrastructure labels
  are NOT patched at claim time (Docker container labels are immutable;
  Kubernetes pod labels could be patched but are not, in V1 — see
  section 9.4).
* A health check runs at claim, mirroring the cold path's
  post-create health check.

### 5.3 One-Shot Justification

A sandbox that has executed tenant commands holds tenant residue
(filesystem, env, process state). Returning it to the pool would leak
data across sessions and tenants. Pool members are therefore
guaranteed never-used; this invariant is what makes eviction-time
workspace cleanup safe (section 7.4).

---

## 6. Pool Store

The pool must be shared across processes: Celery workers rebuild the
orchestrator per task and the API runs multiple uvicorn workers. An
in-process pool would orphan warm sandboxes when the owning process
exits. The store is therefore Redis-backed (Redis is already a hard
platform dependency).

### 6.1 Key Schema

```text
sandbox:warmpool:{template_name}      LIST of warm-sandbox records
sandbox:warmpool:maintain              lock (SET NX PX)
```

### 6.2 Record Schema

Each record is a self-contained JSON document sufficient to
reconstruct the `Sandbox` object in any process (providers address
infrastructure through `Sandbox.metadata`: pod name / namespace /
container id):

```text
WarmSandboxRecord
├── version          # record schema version
├── template_name
├── sandbox          # serialized Sandbox (incl. metadata, endpoints)
└── created_at
```

### 6.3 Atomicity

* Claim = single `LPOP`. Two concurrent claimers can never receive the
  same record.
* Replenish = `RPUSH` after create + health succeeded.
* Evict/remove = `LREM` by value.
* Record schema carries `version`; unknown versions are skipped with
  a warning rather than crashing the maintainer.

### 6.4 Availability Rule

If Redis is unavailable, `claim` must fail fast (bounded by
`claim_timeout_ms`) and `create` falls back to the cold path. The pool
must never make sandbox creation fail.

---

## 7. Maintenance

### 7.1 Maintainer

A periodic task (Celery beat, mirroring the evaluation patrol
pattern) owns pool upkeep. It runs on the default queue, registered
only when the pool is enabled, guarded by the Redis lock so HA beat
pairs cannot double-maintain.

Task name: `agent_platform.sandbox.maintain_warm_pools`.

The maintainer talks to providers **directly** (create /
health_check / destroy via the registry), not through
`SandboxManager.create` (which would attempt claims and register
sandboxes in the wrong process).

### 7.2 Duties per Cycle

```text
acquire maintain lock (skip cycle if held)
   |
   v
for each template:
   |-- top up: while size < target_size and total < max_total:
   |       provider.create(pool system spec) -> health -> RPUSH
   |
   |-- health pass: for each record:
   |       provider.health_check -> unhealthy: destroy + LREM
   |
   |-- age pass: for each record older than max_idle_seconds:
           destroy + LREM + workspace cleanup (7.4)
```

### 7.3 Eviction Reasons

```text
age      -- sandbox older than max_idle_seconds (hygiene rotation:
            bounds sandbox lifetime, image drift, and memory
            fragmentation in long-idle containers)
health   -- failed the health pass
```

### 7.4 Workspace Cleanup on Eviction

Every pool member is never-claimed, so its workspace is provably
unused. On eviction the maintainer destroys the sandbox and removes
workspace residue:

* Kubernetes `emptyDir`: dies with the pod; nothing to do.
* Docker host directory: removed explicitly.

Providers expose this through an **optional** capability so the pool
stays provider-agnostic:

```python
# optional SandboxProvider extension
async def destroy_workspace(self, workspace: WorkspaceMount) -> None:
    """Remove workspace storage owned by a never-claimed sandbox.
    Must never be called for a sandbox that has been claimed."""
```

Providers without the capability document their residue (the default
is no-op and the maintainer logs). Claimed sandboxes follow normal
workspace semantics (retention after destroy); cleanup never applies
to them.

### 7.5 Resource Caps

```text
per template: target_size          # steady-state warm count
global:       max_total             # hard cap across templates
```

Replenishment stops at the global cap even if per-template targets
are unmet. The cap bounds idle resource spend.

---

## 8. Provider Requirements

Warm pooling imposes one requirement and one option on providers:

1. **Stateless addressing (required)**: all infrastructure addressing
   must live in `Sandbox.metadata` so any process can `execute` /
   `health_check` / `destroy` a sandbox it did not create. Both current
   providers satisfy this (pod name + namespace; container id).
   Per-instance caches (e.g. the timeout-wrapper probe) must populate
   lazily on first use, not assume the instance created the sandbox.
2. **Workspace cleanup (optional)**: `destroy_workspace` (section 7.4).

The security posture of a pooled sandbox is identical to a cold one:
the pool calls the same `provider.create` with the same manifest
builders. Pooling must not introduce any privileged or alternate
creation path.

---

## 9. Security Requirements

### 9.1 Never-Used Invariant

Pool members have never executed session code. This is enforced by
the one-shot claim rule (section 5.3) and is the foundation for
cross-claim safety.

### 9.2 No Policy Bypass

Claims pass the same `PolicyValidator` as cold creates, before any
pool interaction (section 4.3). Template specs are validated by the
same validator at startup; invalid templates fail fast.

### 9.3 Isolation Continuity

`sandbox_id` is never changed by a claim, so provider-scoped
isolation keyed on it (e.g. the Kubernetes NetworkPolicy selector on
the sandbox-id label) remains attached to the claimed sandbox.

### 9.4 Identity in Infrastructure (V1 limitation)

Infra labels keep the pool system identity after a claim:

* Docker: container labels are immutable; no post-claim update exists.
* Kubernetes: pod labels are patchable but are NOT patched in V1.

Platform records and audit carry the claim identity; infrastructure
labels may show the pool identity. This is a traceability (not
enforcement) limitation: enforcement keys on `sandbox_id`, which is
stable. Label patching is future work (section 14).

### 9.5 Template Hygiene

* Template `env` must not contain credentials or secrets (policy
  already restricts sandbox env; operators must not relax it for
  templates).
* Template `metadata` must not declare `workspace_pvc`.
* `target_size` and `max_total` bound idle resource consumption.

---

## 10. Failure Semantics

```text
failure                          behavior
---------------------------------------------------------------
Redis down at claim              cold create (warn), bounded by
                                 claim_timeout_ms
corrupt / unknown-version record skip record + warn + cold create
health fail after claim          destroy + cold create
claimer process crash after pop  sandbox orphaned; identical to a
                                 cold-sandbox owner crash (watchdog
                                 cleanup is a separate concern)
maintainer create failure        log + retry next cycle
maintainer Redis down            retry next cycle; pool starves to
                                 empty; all requests go cold
beat not running                 pool starves; degradation only
```

Principle: **the pool degrades to the cold path; it never propagates
errors to sandbox creation.**

---

## 11. Observability and Audit

### 11.1 Metrics

```text
sandbox_warm_pool_size{template}
sandbox_warm_pool_claims_total{result=hit|miss|error}
sandbox_warm_pool_create_latency{path=warm|cold}
sandbox_warm_pool_evictions_total{reason=age|health}
sandbox_warm_pool_replenish_total{result=ok|error}
```

### 11.2 Events

```text
sandbox.warm_pool.claimed     {sandbox_id, template, tenant, user, session}
sandbox.warm_pool.missed      {template?}
sandbox.warm_pool.evicted     {sandbox_id, template, reason}
sandbox.warm_pool.replenished {sandbox_id, template}
```

### 11.3 Audit Chain

Warm creation is audited with the system identity (who: warm pool,
template recorded). The claim event carries the request identity.
Because `sandbox_id` is stable across creation and claim, the existing
audit chain (section 18 of sandbox-spec.md) remains unbroken:

```text
tenant -> user -> session -> run -> tool_call -> sandbox_execution
                                                     ^
                                     sandbox_id stable from pool creation
```

---

## 12. Configuration

All settings default to disabled/inert. Conventions follow config.py
(frozen dataclass, `_env_*` helpers; JSON-in-env mirrors
`MCP_SERVERS_JSON`).

```text
SANDBOX_WARM_POOL_ENABLED=false
# JSON array of templates; validated at startup when enabled
SANDBOX_WARM_POOL_TEMPLATES_JSON='[{
  "name": "python-sbx",
  "provider": "kubernetes",
  "image": "python:3.12-slim",
  "resources": {"cpu": "1", "memory": "2Gi", "pids": 256, "ephemeral_storage": "5Gi"},
  "network_policy": {"mode": "internet_only"},
  "ports": [],
  "env": {},
  "metadata": {},
  "mount_path": "/workspace",
  "target_size": 4
}]'
SANDBOX_WARM_POOL_MAX_TOTAL=32
SANDBOX_WARM_POOL_MAX_IDLE_SECONDS=900
SANDBOX_WARM_POOL_MAINTAIN_INTERVAL_SECONDS=30
SANDBOX_WARM_POOL_CLAIM_TIMEOUT_MS=1000
```

Startup validation (fail fast only when enabled):

* invalid template JSON / unknown fields / duplicate names -> error
* template declaring `workspace_pvc` -> error
* enabled with zero templates -> warning (no-op pool)
* template naming an unregistered provider -> error at first use

---

## 13. Acceptance Criteria

### Default-Off Safety

* [ ] Pool disabled: existing `SandboxManager` behavior unchanged;
      full existing test suite passes without modification
* [ ] Pool disabled: no Redis keys, no beat task, no config required

### Claim

* [ ] Claim hit returns a READY sandbox with request tenant/user/
      session recorded; latency is a health check (no provisioning)
* [ ] Claim miss (no matching template) falls back to cold create
      with no error surfaced to the caller
* [ ] Any creation-fixed field mismatch (image / resources / network
      policy / ports / env / metadata / mount_path) -> miss
* [ ] Request with a non-empty `workspace_id` bypasses the pool
* [ ] Claimed sandbox keeps pool-issued `sandbox_id` / `workspace_id`;
      endpoints usable by the claiming process
* [ ] Claimed sandbox never returns to the pool; destroy works
      normally afterwards
* [ ] Health failure after claim destroys the sandbox and cold-creates

### Maintenance

* [ ] Maintainer tops a template up to `target_size`
* [ ] Unhealthy warm sandbox is destroyed and replaced
* [ ] Warm sandbox older than `max_idle_seconds` is evicted
* [ ] `max_total` cap stops replenishment across templates
* [ ] Maintain lock prevents concurrent cycles
* [ ] Docker eviction removes the never-claimed workspace host
      directory (`destroy_workspace`); k8s eviction leaves no residue

### Resilience

* [ ] Redis unavailable: `create` still succeeds via cold path
* [ ] Corrupt record: skipped with warning; claim falls back
* [ ] Maintainer failures never affect request-path creation

### Isolation / Audit

* [ ] Pooled sandbox manifest/security posture identical to a cold
      sandbox of the same spec (labels, netpol, security context)
* [ ] Claim and eviction produce audit events and metrics
* [ ] No cross-session reuse of any sandbox (one-shot enforced)

---

## 14. V1 Non-Goals

* Adaptive pool sizing / demand forecasting
* Return-to-pool or reuse of claimed sandboxes
* Near-match allocation (resource superset, env merge)
* Pooling persistent-workspace specs (pre-mounted PVCs)
* Prefetching workspace content into warm sandboxes
* Per-tenant pools / tenant-scoped pool namespaces
* Kubernetes label patching at claim (identity in infra labels)
* GPU pools, cross-cluster / multi-region pools
