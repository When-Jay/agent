# Project Foundation Plan

## Goal

建立 Platform 基础工程结构，并确保后续 Coding Agent 按 Architecture / Spec 实现。

---

## Scope

### Include

* Project structure
* Configuration
* Logging
* Error model
* Basic dependency injection
* Database abstraction
* Redis abstraction
* API skeleton
* Runtime skeleton
* Test framework

### Exclude

* Complete Agent Loop
* Complete Workflow
* MCP Gateway implementation
* Evaluation implementation
* Production deployment

---

## Engineering Defaults

Initial implementation defaults:

* Package metadata: `pyproject.toml`
* Runtime package: `agent_platform`
* API framework: FastAPI
* Test framework: pytest
* Configuration: environment-backed `Settings` object with local defaults
* Logging: Python standard library logging with centralized configuration
* Database direction: SQLAlchemy-compatible URL, with an in-memory local default until durable persistence is implemented
* Redis direction: Redis URL configuration, without requiring Redis for local health checks or unit tests

---

## Expected Structure

```text
src/
└── agent_platform/
    ├── api/
    ├── config.py
    ├── errors.py
    ├── logging.py
    ├── runtime/
    │   ├── core/
    │   ├── agent/
    │   ├── workflow/
    │   └── capabilities/
    ├── mcp/
    ├── observability/
    ├── evaluation/
    └── infrastructure/

tests/
docs/
```

---

## Initial API Boundary

The initial API only exposes:

```text
GET /api/v1/health
```

The health endpoint must not require database, Redis, model, tool, MCP, Agent Runtime, or Workflow Runtime availability.

FastAPI application creation should happen through a factory function so tests and future dependency injection can construct isolated app instances.

---

## Initial Runtime Boundary

The foundation phase may define package skeletons for:

```text
runtime.core
runtime.agent
runtime.workflow
runtime.capabilities
mcp
observability
evaluation
infrastructure
```

Only `runtime.core` should contain behavior during this phase.

`runtime.agent`, `runtime.workflow`, MCP, Observability, Evaluation, and Infrastructure packages should remain placeholders until their dedicated implementation plans are executed.

---

## Initial Persistence Strategy

Runtime Core should start with in-memory implementations for tests and local development.

This allows:

* Run lifecycle tests without PostgreSQL
* Event tests without Redis
* Checkpoint and Artifact interface validation before durable storage

PostgreSQL and Redis adapters should be added later behind the same Runtime Core interfaces.

---

## Architecture Guardrails

Add tests that protect dependency direction:

* API must not import Agent or Workflow execution modules.
* Runtime Core must not import Agent Runtime, Workflow Runtime, MCP, or Infrastructure adapters.
* Concrete infrastructure dependencies must remain outside Runtime Core.

These tests are part of the foundation acceptance criteria.

---

## Acceptance Criteria

* Project can start locally
* API health endpoint works
* Runtime package can be imported
* Database connection abstraction works
* Redis abstraction works
* Basic unit test works
* Architecture dependency direction is valid

---

## Out of Scope

Do not implement business features during this phase.
