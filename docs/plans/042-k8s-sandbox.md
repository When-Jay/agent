# Plan 042 — Kubernetes Sandbox

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
