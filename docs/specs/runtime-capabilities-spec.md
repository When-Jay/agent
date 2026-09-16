# Runtime Capabilities Specification

## 1. Principle

Capability 是 Runtime 对外部基础设施的抽象。

Runtime 依赖 Interface。

Infrastructure 实现 Interface。

---

## 2. Model

```text
ModelCapability
├── chat
├── stream
├── structured_output
├── embeddings
└── metadata
```

预留：

* Provider Routing
* Fallback
* Model Selection
* Cost Tracking

---

## 3. Tool

```text
ToolCapability
├── list
├── describe
├── authorize
├── invoke
└── cancel
```

实现：

* MCP
* Native Tool
* Internal Service

---

## 4. Knowledge

预留：

```text
KnowledgeCapability
├── retrieve
├── rerank
├── get_document
└── get_context
```

---

## 5. Memory

预留：

```text
MemoryCapability
├── recall
├── write
├── update
└── delete
```

分：

* Short-term Memory
* Long-term Memory
* User Memory
* Agent Memory

---

## 6. Skill

预留：

```text
SkillCapability
├── discover
├── load
├── validate
└── execute
```

---

## 7. Workspace

预留：

```text
WorkspaceCapability
├── files
├── artifacts
├── command
├── sandbox
└── session isolation
```

---

## 8. Policy

预留：

```text
PolicyCapability
├── authorization
├── tool policy
├── data policy
├── network policy
└── security policy
```

---

## 9. Budget

预留：

```text
BudgetCapability
├── token
├── cost
├── time
├── tool_calls
└── concurrency
```

---
