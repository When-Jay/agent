# Runtime Core Implementation Plan

## Goal

Implement the minimum Runtime Core required by Agent Runtime and Workflow Runtime.

---

## Scope

Implement:

```text
Application
Session
Run
Execution
State
Event
Checkpoint
Artifact
```

---

## Required Interfaces

```text
RunManager
SessionManager
StateManager
EventBus
CheckpointStore
ArtifactStore
```

---

## Execution Order

```text
1. Domain Models
2. Repository Interfaces
3. Persistence
4. Run Lifecycle
5. Event System
6. Checkpoint
7. Artifact
8. Tests
```

---

## Acceptance Criteria

### Run

Must support:

```text
create
start
complete
fail
cancel
```

### Event

Must support:

```text
publish
subscribe
persist
```

### Checkpoint

Must support:

```text
create
get
resume
```

### Artifact

Must support:

```text
create
get
metadata
```

---

## Out of Scope

* Agent Loop
* Workflow Graph
* MCP
* RAG
* Memory
* Skill
