# Sandbox Architecture

## 1. Overview

Sandbox provides an isolated execution environment for AI Agents and Workflows.

The Sandbox is responsible for:

* Command execution
* File operations
* Process isolation
* Filesystem isolation
* Resource isolation
* Network isolation
* Workspace mounting
* Sandbox lifecycle

The Sandbox is **not responsible for**:

* Agent Loop
* LLM reasoning
* Prompt Injection detection
* Tool selection
* MCP registry
* Agent orchestration
* Knowledge retrieval
* Long-term memory

The Sandbox is a Runtime Capability used by Agent Runtime and Workflow Runtime.

---

## 2. Architectural Position

```text
                         API Layer
                             |
                             v
                       Run Orchestrator
                             |
                 +-----------+-----------+
                 |                       |
                 v                       v
          Agent Runtime           Workflow Runtime
                 |                       |
                 +-----------+-----------+
                             |
                             v
                    Runtime Capabilities
                             |
                     +-------+-------+
                     |               |
                     v               v
                Workspace         Sandbox
                     |               |
                     |        +------+------+
                     |        |             |
                     v        v             v
                Storage    Docker       Kubernetes
                     |      Provider       Provider
                     |
                  PVC/NAS/S3
```

Sandbox is an independent capability.

Workspace and Sandbox are related but have different responsibilities.

```text
Workspace
├── Persistent storage
├── Files
├── Artifacts
└── Session data

Sandbox
├── Process isolation
├── Command execution
├── Runtime environment
├── Resource isolation
└── Network isolation
```

A Sandbox may mount a Workspace, but the Workspace lifecycle must not depend on the Sandbox lifecycle.

---

## 3. Design Principles

### 3.1 Provider Independence

The Runtime must not depend directly on Docker or Kubernetes.

```text
Agent Runtime
      |
      v
SandboxCapability
      |
      v
SandboxBackend
      |
 +----+----+
 |         |
Docker    Kubernetes
```

Docker and Kubernetes are infrastructure implementations.

---

### 3.2 Framework Independence

The platform Sandbox interface must not depend on DeepAgents.

DeepAgents can be integrated through an adapter.

```text
Platform Sandbox Interface
          |
          v
DeepAgents Adapter
          |
          v
DeepAgents Sandbox Contract
```

This prevents the Agent Runtime from becoming tightly coupled to a specific Agent framework.

---

### 3.3 Workspace/Sandbox Separation

Workspace represents persistent user data.

Sandbox represents an isolated execution environment.

Example:

```text
User
 |
 +-- Workspace
 |     |
 |     +-- /documents
 |     +-- /artifacts
 |     +-- /skills
 |
 +-- Session
       |
       +-- Sandbox
              |
              +-- mounted workspace
```

A Sandbox can be destroyed while the Workspace remains.

A Sandbox can also be recreated and reattached to the same Workspace.

---

### 3.4 Security by Isolation

Sandbox security consists of multiple independent layers.

```text
Sandbox Security
├── Process Isolation
├── Filesystem Isolation
├── Resource Isolation
├── Network Isolation
├── Identity Isolation
└── Capability Isolation
```

Sandbox itself does not solve prompt injection.

For example:

```text
User Prompt
     |
     v
Agent
     |
     v
Tool Policy / Risk Control
     |
     v
Sandbox
     |
     v
Command
```

Prompt injection and malicious tool instructions must be handled by Agent Runtime / Policy / MCP Gateway.

The Sandbox provides the final execution boundary.

---

## 4. Sandbox Interface

The platform defines a minimal provider-independent interface.

```python
class SandboxBackend(Protocol):

    async def execute(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        ...

    async def upload_files(
        self,
        files: list[FileUpload],
    ) -> list[FileUploadResult]:
        ...

    async def download_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadResult]:
        ...

    async def health_check(self) -> HealthStatus:
        ...

    async def close(self) -> None:
        ...
```

The exact Python types are implementation details and may evolve.

---

## 5. Execution Model

Every command execution produces a normalized result.

```text
ExecutionRequest
├── command
├── cwd
├── timeout
├── environment
├── stdin
└── output limits

        |
        v

SandboxBackend.execute()

        |
        v

ExecutionResult
├── exit_code
├── stdout
├── stderr
├── duration
├── truncated
└── metadata
```

The execution API must enforce:

* Timeout
* Output size limit
* Working directory restriction
* Environment variable policy
* Resource limits
* Cancellation
* Audit information

---

## 6. Path Model

The Agent must only interact with virtual sandbox paths.

Example:

```text
/workspace
├── input
├── output
├── tmp
└── skills
```

The Agent must never receive host filesystem paths.

For Docker:

```text
Agent:
/workspace/project/a.py

        |
        v

Container:
/workspace/project/a.py
```

For Kubernetes:

```text
Agent:
/workspace/project/a.py

        |
        v

Pod:
/workspace/project/a.py
```

Host paths and infrastructure-specific paths remain internal to the provider.

---

## 7. Sandbox Lifecycle

Sandbox lifecycle is managed by `SandboxManager`.

```text
create
  |
  v
initializing
  |
  v
ready
  |
  +----> executing
  |          |
  |          v
  |        ready
  |
  +----> unhealthy
             |
             v
          recovering
             |
             v
           ready
  |
  v
released
  |
  v
destroyed
```

The Sandbox lifecycle must be independent from the Run lifecycle.

A Run may be retried using the same Sandbox.

A Sandbox may also be recreated while preserving the Workspace.

---

## 8. Sandbox Manager

```python
class SandboxManager:

    async def create(
        self,
        spec: SandboxSpec,
    ) -> Sandbox:
        ...

    async def get(
        self,
        sandbox_id: str,
    ) -> Sandbox:
        ...

    async def acquire(
        self,
        sandbox_id: str,
    ) -> Sandbox:
        ...

    async def release(
        self,
        sandbox_id: str,
    ) -> None:
        ...

    async def destroy(
        self,
        sandbox_id: str,
    ) -> None:
        ...
```

Responsibilities:

* Provider selection
* Sandbox creation
* Lifecycle management
* Resource tracking
* Health checking
* Recovery
* Cleanup
* Provider-specific error normalization

`SandboxManager` must not implement command execution itself.

---

## 9. Sandbox Specification

A Sandbox is described by a declarative specification.

```python
class SandboxSpec:
    sandbox_id: str
    tenant_id: str
    user_id: str
    session_id: str

    provider: str

    image: str

    workspace: WorkspaceMount

    cpu_limit: str
    memory_limit: str
    disk_limit: str

    network_policy: NetworkPolicy

    environment: dict[str, str]

    ports: list[PortSpec]
```

The specification is provider-independent.

Docker and Kubernetes translate this specification into their own runtime configuration.

`ports` declares inbound service ports (deny-by-default; see
sandbox-spec.md sections 9.1-9.3). Providers publish declared ports to
the platform network only and report resolved addresses on the Sandbox
instance (`endpoints`); the motivating consumer is the MCP Gateway
runner, which reaches a stdio server bridged to HTTP inside the sandbox.

---

## 10. Docker Provider

Docker is primarily intended for:

* Local development
* Integration testing
* CI
* Small-scale deployments
* Fast sandbox creation

```text
SandboxManager
      |
      v
DockerSandboxProvider
      |
      v
Docker Engine
      |
      v
Container
```

Docker Sandbox must use restricted containers.

Recommended controls:

* Non-root user
* Drop Linux capabilities
* seccomp
* AppArmor where available
* No privileged mode
* Read-only root filesystem where possible
* CPU limit
* Memory limit
* PID limit
* Disk limit
* Network restriction
* No Docker socket exposed to the sandbox

The Agent must never receive Docker Engine access.

---

## 11. Kubernetes Provider

Kubernetes is the production-oriented provider.

```text
SandboxManager
      |
      v
KubernetesSandboxProvider
      |
      v
Kubernetes API
      |
      v
Sandbox Pod
      |
      +-- Workspace PVC
      +-- Resource Limits
      +-- NetworkPolicy
      +-- SecurityContext
```

The provider is responsible for:

* Pod lifecycle
* Pod readiness
* Kubernetes exec
* Workspace mounting
* Resource limits
* SecurityContext
* NetworkPolicy integration
* Cleanup
* Recovery

The Agent does not directly access the Kubernetes API.

---

## 12. Sandbox and DeepAgents

DeepAgents Sandbox provides an abstraction around isolated execution environments.

The platform can implement a compatibility adapter:

```text
                 Platform
                    |
             SandboxBackend
                    |
                    v
        DeepAgentsSandboxAdapter
                    |
                    v
          DeepAgents Sandbox API
```

The adapter translates:

```text
Platform execute()
        ->
DeepAgents execute()

Platform upload_files()
        ->
DeepAgents upload_files()
```

DeepAgents-specific types must not leak into the platform domain model.

---

## 13. Observability

Every Sandbox execution must generate observability information.

```text
sandbox.execute
├── sandbox.id
├── tenant.id
├── user.id
├── session.id
├── command hash
├── provider
├── duration
├── exit code
├── timeout
├── output size
└── error
```

The raw command may be subject to data masking.

Sandbox events should be correlated with:

```text
Run
  |
  +-- Agent Turn
       |
       +-- Tool Call
            |
            +-- Sandbox Execution
```

This allows debugging:

```text
User Request
    |
Agent Decision
    |
Tool Call
    |
Sandbox Command
    |
Execution Result
```

---

## 14. Audit

Every command execution should produce an audit record.

```text
SandboxAudit
├── request_id
├── tenant_id
├── user_id
├── session_id
├── sandbox_id
├── command
├── cwd
├── provider
├── started_at
├── completed_at
├── exit_code
└── result_summary
```

Sensitive command content may require masking or hashing.

Audit storage must be independent from the Sandbox lifecycle.

---

## 15. V1 Boundary

V1 includes:

* Sandbox abstraction
* SandboxManager
* Docker provider
* Kubernetes provider
* Execute
* Upload
* Download
* Workspace mounting
* Resource limits
* Basic network isolation
* Health check
* Lifecycle management
* Observability
* Audit

V1 does not include:

* Sandbox pooling
* Warm sandbox scheduling
* Advanced VM isolation
* GPU sandbox scheduling
* Multi-region scheduling
* Automatic image building
* Arbitrary network proxying
* Full malware detection

These can be introduced later.
