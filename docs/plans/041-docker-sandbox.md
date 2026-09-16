# Plan 041 — Docker Sandbox

## 1. Objective

Implement Docker as the first concrete Sandbox provider.

Docker is intended for:

* Local development
* CI
* Integration testing
* Small deployments

---

## 2. Architecture

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
      |
      +-- /workspace
```

The Docker API is accessed only by the trusted platform service.

The Agent never receives Docker API access.

---

## 3. Container Lifecycle

```text
create
  |
  v
create container
  |
  v
configure security
  |
  v
mount workspace
  |
  v
start
  |
  v
health check
  |
  v
READY
```

---

## 4. Container Configuration

Default security posture:

```text
runAsNonRoot
privileged=false
```

Apply:

* CPU limit
* Memory limit
* PID limit
* Disk limit
* seccomp
* AppArmor where available
* Drop unnecessary capabilities
* Read-only root filesystem where possible

---

## 5. Filesystem

Mount only the required Workspace.

Example:

```text
Host / Persistent Storage
        |
        v
Container
/workspace
```

The Sandbox must not mount:

```text
/
/etc
/var/run/docker.sock
platform credentials
cloud credentials
```

Host paths must never be exposed to the Agent.

---

## 6. Command Execution

Use Docker's exec mechanism.

```text
Agent
 |
 v
SandboxManager
 |
 v
DockerSandboxProvider
 |
 v
docker exec
 |
 v
Container Process
```

Execution must support:

* cwd
* timeout
* stdin
* stdout
* stderr
* cancellation
* output limit

---

## 7. Timeout

Provider must terminate commands that exceed timeout.

The implementation must distinguish:

```text
process timeout
container failure
provider failure
```

---

## 8. Network

Default configuration should prevent unnecessary access to internal infrastructure.

Possible configurations:

```text
none
restricted
internet
custom
```

Production deployments should prefer explicit egress policy.

---

## 9. Environment

Do not inherit the host environment.

Only explicitly configured environment variables may be passed.

Never pass:

```text
DOCKER_HOST
KUBECONFIG
cloud credentials
database credentials
platform secrets
```

unless explicitly required and policy-approved.

---

## 10. Workspace

The container should expose:

```text
/workspace
```

The Agent's cwd defaults to:

```text
/workspace
```

---

## 11. File Transfer

Implement:

```text
upload_files()
download_files()
```

Large files should preferably use the persistent Workspace rather than repeatedly transferring through the Sandbox API.

---

## 12. Health Check

The provider should check:

```text
container exists
container running
workspace mounted
execution capability available
```

---

## 13. Cleanup

Destroy:

* container
* temporary resources
* execution processes

Do not destroy persistent Workspace data.

---

## 14. Tests

Provider contract tests:

* create
* execute
* timeout
* cancellation
* upload
* download
* workspace persistence
* health check
* destroy

Security tests:

* host filesystem access
* Docker socket access
* privileged operations
* resource exhaustion
* network restrictions
* unauthorized path access

---

## 15. Acceptance

Docker provider passes the common `SandboxProvider` contract tests.

The same Agent Runtime code must work with:

```text
provider=docker
```

without provider-specific changes in Agent Runtime.

---

## 16. Out of Scope

Do not implement:

* Kubernetes
* Sandbox pooling
* VM isolation
* GPU
* image building service
* advanced network proxy
