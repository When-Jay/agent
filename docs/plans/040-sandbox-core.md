# Plan 040 — Sandbox Core

## 1. Objective

Implement the platform-independent Sandbox abstraction and lifecycle manager.

This plan does not implement Docker or Kubernetes-specific logic.

---

## 2. Scope

Implement:

```text
platform/sandbox/
├── interface.py
├── models.py
├── manager.py
├── errors.py
├── registry.py
└── policy.py
```

---

## 3. Models

Implement:

```text
SandboxSpec
Sandbox
SandboxStatus
SandboxResources
NetworkPolicy
WorkspaceMount
ExecutionRequest
ExecutionResult
FileUpload
FileDownload
HealthStatus
```

All models must be provider-independent.

---

## 4. Interface

Define:

```python
class SandboxProvider(Protocol):
    ...
```

Required operations:

```text
create
execute
upload_files
download_files
health_check
destroy
```

---

## 5. Sandbox Manager

Implement:

```python
class SandboxManager:
    create()
    get()
    acquire()
    release()
    execute()
    upload()
    download()
    health_check()
    destroy()
```

Responsibilities:

* Provider selection
* Lifecycle
* State validation
* Error normalization
* Policy enforcement
* Logging
* Trace context propagation

The manager must not contain Docker/Kubernetes implementation details.

---

## 6. Provider Registry

Example:

```python
registry.register("docker", DockerSandboxProvider(...))
registry.register("kubernetes", KubernetesSandboxProvider(...))
```

The core module only depends on the provider interface.

---

## 7. Policy

Implement basic policy validation.

```text
SandboxPolicy
├── allowed_images
├── max_cpu
├── max_memory
├── max_timeout
├── max_output
├── allowed_network_mode
└── allowed_workspace_root
```

Policy must be evaluated before Sandbox creation/execution.

---

## 8. Idempotency

Execution requests must contain:

```text
request_id
```

The manager should prevent accidental duplicate execution.

This is particularly important when:

```text
Agent
  |
Tool Call
  |
MQ Retry
  |
Worker Retry
```

occurs.

---

## 9. Error Model

Normalize provider-specific failures:

```text
SandboxError
├── SandboxNotFound
├── SandboxUnavailable
├── ExecutionTimeout
├── ExecutionCancelled
├── ExecutionFailed
├── ResourceLimitExceeded
├── PermissionDenied
├── NetworkDenied
└── ProviderError
```

Agent Runtime must not need to understand Docker/Kubernetes error types.

---

## 10. DeepAgents Adapter

Implement:

```text
platform/sandbox/adapters/deepagents.py
```

Responsibilities:

* Translate platform request -> DeepAgents request
* Translate DeepAgents result -> platform result
* Preserve platform Sandbox identity
* Prevent framework-specific types from leaking into core

The adapter should be thin.

---

## 11. Tests

Unit tests:

* Provider registry
* Lifecycle transitions
* Invalid state transitions
* Policy validation
* Timeout validation
* Output limit validation
* Idempotency
* Error normalization

Contract tests:

```text
SandboxProvider
      |
      +-- Docker
      |
      +-- Kubernetes
```

Both providers must pass the same provider contract tests.

---

## 12. Acceptance

A fake provider must be able to run the complete Sandbox Manager test suite.

This proves the core does not depend on Docker/Kubernetes.

---

## 13. Out of Scope

Do not implement:

* Docker API
* Kubernetes API
* Pod creation
* Container creation
* Sandbox pooling
* Image building
* Network proxy
* GPU scheduling
