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

## Expected Structure

```text
src/
├── api/
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
