# Sandbox Specification

## 1. Purpose

Sandbox provides isolated execution environments for Agent and Workflow execution.

The Sandbox must provide a consistent execution API independent of the underlying infrastructure.

Supported providers:

* Docker
* Kubernetes

Future providers may include:

* Firecracker
* Cloud sandbox
* VM
* Remote execution service

---

## 2. Responsibilities

### 2.1 Required

Sandbox must provide:

1. Command execution
2. File upload
3. File download
4. Workspace mounting
5. Process isolation
6. Resource isolation
7. Network policy
8. Lifecycle management
9. Health checking
10. Execution observability

### 2.2 Out of Scope

Sandbox does not decide:

* Which command the Agent should execute
* Whether a tool should be called
* Whether a prompt is malicious
* Whether a file contains prompt injection
* Which knowledge base should be queried
* Which Agent should run
* How Agent reasoning works

These belong to Agent Runtime, Policy, MCP Gateway, or Evaluation.

---

## 3. Core Entities

### 3.1 Sandbox

```text
Sandbox
├── sandbox_id
├── tenant_id
├── user_id
├── session_id
├── provider
├── status
├── image
├── workspace
├── resources
├── network_policy
├── created_at
└── last_heartbeat
```

---

### 3.2 SandboxStatus

```text
CREATING
READY
BUSY
UNHEALTHY
RECOVERING
RELEASING
DESTROYED
```

---

### 3.3 ExecutionRequest

```text
ExecutionRequest
├── request_id
├── command
├── cwd
├── timeout
├── env
├── stdin
└── output_limit
```

`request_id` must be idempotent.

A repeated request with the same `request_id` must not unintentionally execute the command twice.

---

### 3.4 ExecutionResult

```text
ExecutionResult
├── request_id
├── exit_code
├── stdout
├── stderr
├── duration_ms
├── timed_out
├── truncated
└── metadata
```

---

## 4. Execution Contract

### 4.1 Command

The command is executed inside the Sandbox.

The platform must never construct commands by concatenating untrusted parameters without appropriate escaping.

The command execution implementation must support:

* cwd
* timeout
* environment
* stdin
* cancellation
* output limit

---

## 5. Timeout

Timeout must exist at multiple levels.

```text
Run Timeout
     |
     v
Tool Timeout
     |
     v
Sandbox Execution Timeout
```

Sandbox execution timeout is the lowest-level enforcement mechanism.

When timeout occurs:

1. Send termination signal.
2. Wait for process termination.
3. Force kill if necessary.
4. Return `timed_out=true`.
5. Emit audit event.

---

## 6. Output Limits

Sandbox must limit:

* stdout bytes
* stderr bytes
* combined output
* execution duration

If output exceeds the limit:

```text
truncated = true
```

The full output must not automatically be persisted.

Large outputs should be written to an Artifact and referenced by ID.

```text
ExecutionResult
    |
    +-- small output -> inline
    |
    +-- large output -> Artifact
```

---

## 7. Working Directory

The Agent-visible working directory is:

```text
/workspace
```

The following paths may be exposed:

```text
/workspace
/workspace/input
/workspace/output
/workspace/tmp
/workspace/skills
```

Access outside the allowed workspace must be denied where policy requires it.

The provider must not expose host paths.

---

## 8. Environment Variables

Environment variables must be controlled by Policy.

By default:

* Do not expose host environment variables.
* Do not expose cloud credentials.
* Do not expose database credentials.
* Do not expose Kubernetes credentials.
* Do not expose platform secrets.

Explicitly allowed variables may be injected through a secret manager.

---

## 9. Network Policy

Network access is configurable.

Possible modes:

```text
NONE
INTERNET_ONLY
ALLOWLIST
INTERNAL
FULL
```

Default production mode:

```text
INTERNET_ONLY
```

The Sandbox must not automatically inherit access to the platform's private network.

Network controls are enforced outside the Agent's reasoning layer.

---

## 10. Resource Limits

Every Sandbox must have resource limits.

```text
CPU
Memory
PID
Disk
Ephemeral Storage
Execution Time
```

Example:

```yaml
resources:
  cpu: "1"
  memory: "2Gi"
  ephemeral_storage: "5Gi"
  pids: 256
```

Actual values are deployment configuration rather than fixed platform constants.

---

## 11. Workspace Mount

Workspace storage is persistent.

Sandbox storage is ephemeral.

```text
Workspace
    |
    v
Persistent Storage
    |
    +----------------+
    |                |
    v                v
Sandbox A        Sandbox B
```

This allows Sandbox recreation without losing user data.

---

## 12. Lifecycle

### Create

```text
CREATE
  |
  v
PROVISION
  |
  v
MOUNT WORKSPACE
  |
  v
HEALTH CHECK
  |
  v
READY
```

### Destroy

```text
READY
  |
  v
RELEASE
  |
  v
UNMOUNT
  |
  v
DESTROY
```

Workspace data must not be deleted automatically when the Sandbox is destroyed.

---

## 13. Recovery

Sandbox failure must be distinguishable from Run failure.

```text
Agent Run
   |
   +-- Sandbox unhealthy
          |
          v
     Sandbox Recovery
          |
          v
     Reattach Workspace
          |
          v
        Resume
```

Recovery may create a new Sandbox using the same Workspace.

---

## 14. Provider Contract

```python
class SandboxProvider(Protocol):

    async def create(
        self,
        spec: SandboxSpec,
    ) -> Sandbox:
        ...

    async def execute(
        self,
        sandbox: Sandbox,
        request: ExecutionRequest,
    ) -> ExecutionResult:
        ...

    async def upload_files(
        self,
        sandbox: Sandbox,
        files: list[FileUpload],
    ) -> list[FileUploadResult]:
        ...

    async def download_files(
        self,
        sandbox: Sandbox,
        paths: list[str],
    ) -> list[FileDownloadResult]:
        ...

    async def health_check(
        self,
        sandbox: Sandbox,
    ) -> HealthStatus:
        ...

    async def destroy(
        self,
        sandbox: Sandbox,
    ) -> None:
        ...
```

---

## 15. Provider Selection

Provider selection is performed by `SandboxManager`.

Example:

```text
SandboxSpec
   |
   +-- environment=dev
   |       -> Docker
   |
   +-- environment=prod
           -> Kubernetes
```

Provider selection must not be performed inside Agent Runtime.

---

## 16. DeepAgents Compatibility

The platform may provide:

```text
DeepAgentsSandboxAdapter
```

The adapter maps platform Sandbox operations to the DeepAgents Sandbox contract.

The platform must remain independent of DeepAgents.

This enables:

```text
Agent Runtime
   |
   +-- Native Agent Loop
   |
   +-- DeepAgents Agent
           |
           v
      Sandbox Adapter
```

The Sandbox implementation can therefore be reused by both.

---

## 17. Security Requirements

### Container

* Non-root
* No privileged mode
* Drop unnecessary capabilities
* seccomp
* AppArmor where available
* Resource limits
* PID limits
* Network isolation
* No host Docker socket

### Kubernetes

* Restricted Pod Security configuration
* Non-root
* Read-only root filesystem where possible
* Drop capabilities
* seccomp
* Minimal ServiceAccount
* Avoid unnecessary API permissions
* NetworkPolicy
* CPU/memory/ephemeral-storage limits

---

## 18. Audit Requirements

Each execution must be traceable to:

```text
tenant
  |
user
  |
session
  |
run
  |
turn
  |
tool_call
  |
sandbox_execution
```

The platform should support querying:

```text
Who executed it?
When?
In which Sandbox?
Which command?
What was the result?
How much time?
How much output?
```

---

## 19. Acceptance Criteria

### Core

* [ ] Create Sandbox
* [ ] Execute command
* [ ] Upload file
* [ ] Download file
* [ ] Destroy Sandbox
* [ ] Health check

### Isolation

* [ ] Cannot access host filesystem
* [ ] Cannot access Docker socket
* [ ] Cannot access unauthorized network
* [ ] Cannot exceed resource limits
* [ ] Cannot escape workspace boundary

### Reliability

* [ ] Timeout works
* [ ] Cancellation works
* [ ] Sandbox failure can be detected
* [ ] Sandbox can be recreated
* [ ] Workspace survives Sandbox recreation

### Observability

* [ ] Execution trace exists
* [ ] Execution duration recorded
* [ ] Exit code recorded
* [ ] Timeout recorded
* [ ] Audit record exists

---

## 20. V1 Non-Goals

The first version does not implement:

* Sandbox pooling
* Warm containers
* GPU scheduling
* VM-level isolation
* Multi-region scheduling
* Automatic image construction
* Advanced egress proxy
* Malware scanning

These are future extensions.
