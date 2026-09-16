# Workflow Runtime Specification

## 1. Objective

Workflow Runtime 提供确定性、可恢复、可观察的 Workflow Execution。

---

## 2. Core Concepts

```text
Workflow
Graph
Node
Edge
State
NodeExecution
Checkpoint
```

---

## 3. Execution

```text
Load Workflow
      ↓
Load State
      ↓
Resolve Node
      ↓
Execute Node
      ↓
Update State
      ↓
Emit Event
      ↓
Checkpoint
      ↓
Resolve Next Node
      ↓
Repeat
```

---

## 4. Node Types

预留：

```text
LLM Node
Tool Node
Agent Node
Code Node
Condition Node
Parallel Node
Human Node
Knowledge Node
Transform Node
```

---

## 5. Reliability

每个 Node 支持：

* Timeout
* Retry
* Failure
* Checkpoint
* Resume

---

## 6. Versioning

Workflow 必须支持版本概念：

```text
Workflow
  ├── v1
  ├── v2
  └── v3
```

Run 必须绑定具体 Workflow Version。

---

## 7. Framework

V1 可以使用 LangGraph 实现。

LangGraph 只属于 Workflow Runtime 实现层。

Platform API 不直接依赖 LangGraph。
