# ADR-0001: Unified Runtime Boundary

## Status

Accepted

## Decision

Platform 使用统一 AI Runtime 作为执行层。

Runtime 内部包含：

```text
Run Orchestrator
Agent Runtime
Workflow Runtime
Runtime Core
Runtime Capabilities
```

---

## Context

Agent 和 Workflow 都需要：

* Run Lifecycle
* State
* Event
* Checkpoint
* Artifact
* Observability
* Evaluation

但两者的执行模型不同。

因此不直接强行统一 Agent Loop 和 Workflow Graph。

---

## Decision Details

统一：

```text
Run
State
Event
Checkpoint
Artifact
Lifecycle
```

分离：

```text
Agent Execution
Workflow Execution
```

---

## Consequences

### Positive

* Agent / Workflow 可以共享基础设施
* Evaluation 可以统一
* Observability 可以统一
* API 可以统一
* Runtime Framework 可以替换

### Negative

* Runtime Core 需要设计稳定抽象
* 初期代码量高于直接使用单一 Agent Framework

---

## Rejected Alternatives

### Only Agent Runtime

无法自然承载确定性 Workflow。

### Only Workflow Runtime

无法自然承载动态 Agent Loop。

### Everything directly based on LangGraph

会导致 Platform Architecture 与具体 Framework 强耦合。