# Plan 042 — Kubernetes Sandbox

> **Status: IMPLEMENTED** (commit b0264ae). Implementation:
> `src/agent_platform/sandbox/providers/kubernetes.py`, tests:
> `tests/test_k8s_sandbox.py` (40 pure-helper unit tests pass locally;
> the cluster contract suite and timeout test are gated on cluster
> availability — integration verification pending).
>
> Deviations from the plan text below (documented in
> 06-sandbox-architecture.md section 11.1):
>
> * **PID limits**: not settable per-pod in upstream Kubernetes;
>   `SandboxResources.pids` maps to kubelet-level `PodPidsLimit`
>   (cluster configuration), not container resources.
> * **Recovery (section 4/13)**: pod failure is normalized as
>   `SandboxUnavailable`/`ProviderError` and surfaced via health
>   status; automatic pod replacement is not implemented (manager
>   recovery engine is future work).
> * **NetworkPolicy granularity**: policies are per-sandbox (named
>   `<pod>-netpol`, selected by sandbox-id label) rather than
>   namespace-wide; `INTERNET_ONLY` requires `cluster_cidrs` at
>   provider construction to deny private ranges, otherwise
>   unrestricted (documented limitation).
> * **Workspace**: `emptyDir` by default; persistence requires
>   spec metadata `workspace_pvc` (pre-created PVC). PVC lifecycle is
>   NOT owned by the provider (section 14 respected).
> * **Upload/download**: exec-stdin tar and `tar|base64` (no archive
>   API in k8s exec); stdin EOF relies on the empty-stdin-frame
>   behavior of the apiserver websocket protocol — verify on a real
>   cluster before production use.

## 1. Objective

Implement Kubernetes as the production Sandbox provider.

The Kubernetes provider provides:

* Pod-level isolation
* Resource isolation
* NetworkPolicy integration
* Workspace PVC mounting
* Kubernetes exec
* Sandbox recovery

---

## 2. Architecture

```text
SandboxManager
      |
      v
KubernetesSandboxProvider
      |
      v
Kubernetes API
      |
      +----------------+
      |                |
      v                v
   Sandbox Pod     Workspace PVC
      |
      +-- /workspace
```

---

## 3. Sandbox Unit

V1 uses:

```text
1 Sandbox = 1 Pod
```

A Pod can execute multiple commands during its lifetime.

The provider must not create a new Pod for every command.

```text
Session
   |
   v
Sandbox Pod
   |
   +-- command 1
   +-- command 2
   +-- command 3
```

This reduces lifecycle overhead.

---

## 4. Pod Lifecycle

```text
create Pod
    |
    v
Pending
    |
    v
Running
    |
    v
Readiness Check
    |
    v
READY
```

On failure:

```text
Pod Failed
    |
    v
Sandbox UNHEALTHY
    |
    v
Create replacement Pod
    |
    v
Mount same Workspace
    |
    v
READY
```

---

## 5. Pod Security

Pod configuration should use a restrictive security posture.

Recommended:

```text
runAsNonRoot
allowPrivilegeEscalation=false
readOnlyRootFilesystem=true where possible
drop ALL capabilities
seccompProfile=RuntimeDefault
```

Only required capabilities should be explicitly added.

---

## 6. Service Account

Sandbox Pods should not receive unnecessary Kubernetes permissions.

Prefer:

```text
automountServiceAccountToken=false
```

unless the sandbox explicitly requires Kubernetes API access.

The Agent must never receive the platform's Kubernetes credentials.

---

## 7. Resource Limits

Every Pod must specify:

```yaml
resources:
  requests:
    cpu: ...
    memory: ...
  limits:
    cpu: ...
    memory: ...
```

Also consider:

```text
ephemeral-storage
```

and PID limits.

---

## 8. Workspace PVC

Workspace is mounted into the Pod.

```text
PVC
 |
 +-- /workspace
```

Sandbox recreation should preserve the PVC.

```text
Sandbox A
   |
   X
destroyed
   |
   v
Sandbox B
   |
   +-- same PVC
```

This separates execution lifecycle from persistent data.

---

## 9. Kubernetes Exec

Command execution:

```text
Agent
 |
 v
SandboxManager
 |
 v
KubernetesSandboxProvider
 |
 v
Kubernetes Exec API
 |
 v
Sandbox Pod
 |
 v
Process
```

The provider must normalize Kubernetes exec behavior into:

```text
ExecutionResult
```

Agent Runtime must not know Kubernetes exec semantics.

---

## 10. NetworkPolicy

Each Sandbox namespace should have an explicit network policy.

Example conceptual policy:

```text
Sandbox
 |
 +-- DNS: allowed
 |
 +-- Internet: allowed
 |
 +-- Platform private network: denied
 |
 +-- Database: denied
 |
 +-- Kubernetes API: denied
```

Specific requirements depend on deployment architecture.

The default should be deny-by-default for private network access.

---

## 11. Namespace Strategy

Possible strategies:

### V1

Shared namespace + strong Pod isolation.

```text
sandbox
├── pod-a
├── pod-b
└── pod-c
```

### Future

Tenant-specific namespace.

```text
tenant-a
├── sandbox-a
└── sandbox-b

tenant-b
├── sandbox-c
└── sandbox-d
```

Tenant-specific namespaces should only be introduced when operational requirements justify the additional complexity.

---

## 12. Image Strategy

Sandbox images should be predefined and controlled.

Example:

```text
python-sandbox
node-sandbox
java-sandbox
general-sandbox
```

Do not allow arbitrary user-provided images in V1.

Image selection must be validated by Policy.

---

## 13. Recovery

The provider must distinguish:

```text
command failure
Pod failure
Kubernetes API failure
Workspace failure
```

Pod failure should not automatically be interpreted as Agent failure.

Example:

```text
Run
 |
 +-- Sandbox Pod failure
        |
        v
   recreate Pod
        |
        v
  restore Workspace
        |
        v
      resume
```

---

## 14. Workspace Consistency

The provider must ensure that the replacement Pod sees the same Workspace state.

Potential storage options:

```text
NAS / PVC
```

The Sandbox provider should not own the persistent storage lifecycle.

---

## 15. Cleanup

On Sandbox destruction:

```text
Pod -> deleted
```

But:

```text
PVC -> retained
```

unless the caller explicitly requests Workspace deletion.

---

## 16. Observability

Record:

```text
sandbox_id
pod_name
namespace
node
container
command
duration
exit_code
pod_status
```

Do not automatically expose infrastructure metadata to the Agent.

---

## 17. Tests

Provider contract tests:

* Pod creation
* Readiness
* Exec
* Timeout
* Cancellation
* Upload
* Download
* Workspace mount
* Pod failure
* Recovery
* Destroy

Security tests:

* Private network access
* Kubernetes API access
* ServiceAccount access
* Host filesystem access
* Privileged operations
* Resource exhaustion
* Cross-Sandbox filesystem access

---

## 18. Acceptance

The Kubernetes provider must satisfy the same:

```text
SandboxProvider
```

contract as Docker.

Agent Runtime must be unaware of whether execution happens in:

```text
Docker
```

or:

```text
Kubernetes
```

---

## 19. Out of Scope

V1 does not implement:

* Sandbox pool
* Warm Pod pool
* GPU scheduling
* Cross-cluster scheduling
* Multi-region scheduling
* Firecracker
* Arbitrary user images
* Advanced egress proxy
