# AI Runtime Platform - Coding Agent Instructions

## 1. Project Goal

Build an enterprise-oriented AI Runtime Platform that provides a unified execution infrastructure for:

* Agent applications
* Workflow applications

The platform must separate execution logic from platform infrastructure and must remain extensible.

The initial implementation should prioritize:

1. Clear module boundaries
2. Stable interfaces
3. Testability
4. Observability
5. Recoverability
6. Extensibility

Do not optimize for feature quantity.

---

## 2. Architecture Principles

### 2.1 Runtime is the execution core

The Runtime is responsible for executing AI tasks.

It contains:

* Run Orchestrator
* Agent Runtime
* Workflow Runtime
* Runtime Core
* Runtime Capabilities

Do not put API-layer logic, MCP Gateway logic, Evaluation logic, or Observability implementation directly into Agent/Workflow execution code.

---

### 2.2 Agent and Workflow are different execution models

Agent Runtime and Workflow Runtime MUST remain separate modules.

Agent Runtime is responsible for:

* Agent Loop
* Context Management
* Tool Calling
* Memory
* Skill
* Planning/Reasoning
* Budget Control
* Stop Conditions
* Sub-Agent
* Recovery

Workflow Runtime is responsible for:

* Graph Execution
* Node Execution
* State
* Branching
* Parallel Execution
* Retry
* HITL
* Checkpoint
* Resume
* Versioning

Do not merge Agent Loop and Workflow Graph into a single execution implementation.

---

### 2.3 Runtime Core is shared

Agent Runtime and Workflow Runtime SHOULD reuse Runtime Core.

Runtime Core provides common concepts:

* Application
* Session
* Run
* State
* Context
* Execution
* Event
* Checkpoint
* Artifact

Do not create separate incompatible versions of these concepts for Agent and Workflow.

---

### 2.4 Capabilities are replaceable

The following capabilities must be accessed through interfaces:

* Model
* Tool
* MCP
* Memory
* Knowledge/RAG
* Skill
* Workspace
* Sandbox
* Policy
* Budget
* Human-in-the-loop

Do not tightly couple Runtime execution logic to a specific vendor or framework.

---

## 3. Technology Direction

Initial technology choices:

* Python
* FastAPI
* PostgreSQL
* Redis
* SQLAlchemy
* LangGraph for Workflow Runtime
* Kubernetes for Sandbox
* MCP for Tool Integration
* OpenTelemetry/Langfuse for Observability
* Langfuse + custom evaluators for Evaluation

These choices are implementation defaults, not reasons to violate architectural boundaries.

---

## 4. Framework Usage Rules

Frameworks should be treated as implementation components, not as the system architecture.

For example:

* LangGraph = Workflow execution engine
* MCP = Tool protocol/integration mechanism
* Langfuse = Observability/Evaluation infrastructure
* Kubernetes = Sandbox infrastructure

Do not make the entire platform dependent on the internal abstraction model of any single framework.

---

## 5. Implementation Rules

Before implementing a feature:

1. Read the relevant architecture/spec document.
2. Identify the owning module.
3. Check whether an existing interface already represents the capability.
4. Prefer extending an existing abstraction over creating a parallel abstraction.
5. Do not move responsibilities between modules without updating the architecture document.
6. Do not introduce new top-level modules without architectural justification.

---

## 6. Scope Control

The first version should NOT implement all reserved capabilities.

Reserved capabilities may initially contain:

* Interface definitions
* Data models
* Extension points
* Minimal adapters
* TODO markers

A reserved capability is not automatically an implementation requirement.

Avoid speculative implementation.

---

## 7. Dependency Direction

Preferred dependency direction:

API
↓
Runtime
↓
Runtime Capabilities
↓
Infrastructure Adapters

Cross-cutting systems:

Observability
Evaluation

must consume Runtime events/data instead of becoming execution dependencies.

Avoid circular dependencies.

---

## 8. Code Quality

Every major module should have:

* Clear public interfaces
* Unit tests
* Error handling
* Logging
* Type annotations
* Documentation for non-obvious behavior

Do not add complex abstractions unless they solve an identified architectural problem.

---

## 9. Change Policy

When implementation requirements conflict with architecture:

DO NOT silently change the architecture.

Instead:

1. Identify the conflict.
2. Explain the impact.
3. Propose an architecture change.
4. Update the relevant specification.
5. Only then implement the change.

The architecture/specification is the source of truth.
