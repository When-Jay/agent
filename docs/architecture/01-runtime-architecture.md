# Runtime Architecture

## 1. Purpose

AI Runtime 是整个 AI Application Platform 的核心执行层。

Runtime 负责统一承载两类执行模式：

* Agent Runtime：面向动态决策和工具调用的 Agent 执行
* Workflow Runtime：面向确定性流程和节点编排的 Workflow 执行

Runtime 不负责：

* HTTP API 接入
* MCP Server 实现
* 具体模型实现
* 具体知识库实现
* 具体 Sandbox 实现
* Evaluation 业务逻辑

Runtime 通过统一的 Runtime Core 和 Capability 接口访问这些能力。

---

## 2. Architecture

```text
                    API Layer
                       |
                       v
             PostgreSQL Run Record
                       |
                       v
             Celery Task Queue (Redis)
                       |
                       v
                Runtime Worker
                       |
                       v
                +--------------+
                | Run          |
                | Orchestrator |
                +------+-------+
                       |
             +---------+---------+
             |                   |
             v                   v
      +-------------+     +-------------+
      | Agent       |     | Workflow    |
      | Runtime     |     | Runtime     |
      +------+------+     +------+------+
             |                   |
             +---------+---------+
                       |
                       v
                +-------------+
                | Runtime Core|
                +------+------+
                       |
          +------------+-------------+
          |            |             |
          v            v             v
       Model         Tool          Memory
      Capability   Capability    Capability
          |
      +---+----+----+----+----+
     |        |    |    |    |
     RAG    Skill Sandbox Policy Budget
```

API 与 Runtime Worker 之间必须通过 Celery 异步隔离。

* PostgreSQL 是 Run / Session / Event / Checkpoint / Artifact / Audit 的事实存储。
* Redis 是 Celery broker/result backend 以及可选短期事件流，不是长期事实存储。
* Runtime Worker 负责执行 Run，不应运行在 API request handler 内。

---

## 3. Runtime Components

### 3.1 Run Orchestrator

负责 Run 生命周期管理。

Responsibilities:

* 创建 Run
* 启动 Run
* 调度 Run
* 取消 Run
* 暂停 Run
* 恢复 Run
* Retry
* Timeout
* Failure handling
* Resource routing
* Budget coordination

Run Orchestrator 不负责：

* Agent Loop
* Workflow Graph execution
* LLM Prompt 构建
* Tool 具体执行

---

### 3.2 Agent Runtime

负责将平台 Run 适配到 LangChain / DeepAgents 执行。

核心能力：

* DeepAgents / LangChain agent assembly
* Middleware stack assembly
* Platform tool -> LangChain tool adaptation
* Platform sandbox/workspace -> DeepAgents backend adaptation
* LangGraph checkpoint integration
* Runtime Event mapping
* Run output / artifact mapping
* Failure / cancellation mapping

Agent Runtime MUST NOT implement its own long-term Agent Loop, Decision Engine, Tool Executor, Context Manager, Memory Manager, Skill Manager, Sub-Agent Manager, or middleware mechanism.

Agent execution belongs to:

```text
DeepAgents create_deep_agent()
  ↓
LangChain create_agent()
  ↓
LangGraph runtime
```

Platform-specific Policy, Budget, HITL, Observability, Memory, Skill, and Tool filtering should be implemented as LangChain / DeepAgents middleware.

Agent Runtime 必须依赖 Runtime Core 的统一对象和事件系统，但不得将 LangChain / DeepAgents 类型泄漏到 Runtime Core。

---

### 3.3 Workflow Runtime

负责确定性流程执行。

核心能力：

* Graph
* Node
* Node Execution
* State
* Conditional Branch
* Parallel Execution
* Retry
* Timeout
* HITL
* Checkpoint
* Resume
* Versioning

LangGraph 可以作为 Workflow Runtime 的实现组件。

LangGraph 不应成为整个 Platform 的架构边界。

---

### 3.4 Runtime Core

Runtime Core 提供 Agent 和 Workflow 共享的基础抽象。

核心对象：

```text
Application
Session
Run
Execution
State
Context
Event
Checkpoint
Artifact
```

核心能力：

* Run Lifecycle
* Session Management
* State Management
* Context Management
* Event System
* Checkpoint
* Execution History
* Artifact Management

---

## 4. Capability Model

Runtime 不直接绑定具体基础设施。

通过 Capability 接口提供：

```text
ModelCapability
ToolCapability
KnowledgeCapability
MemoryCapability
SkillCapability
WorkspaceCapability
PolicyCapability
BudgetCapability
HumanCapability
```

例如：

```text
Agent Runtime
      |
      v
ModelCapability
      |
      +-- LiteLLM
      +-- OpenAI
      +-- Anthropic
      +-- Qwen
      +-- Other Provider
```

Runtime 只依赖 Capability Interface。

具体实现可以替换。

---

## 5. Execution Model

### Agent

```text
Run
 |
 +-- DeepAgentsRuntimeAdapter
      |
      +-- create_deep_agent(...)
      +-- middleware stack
      +-- DeepAgents backend
      +-- LangChain create_agent(...)
      +-- LangGraph execution/checkpoint
      +-- RuntimeEvent mapping
```

### Workflow

```text
Run
 |
 +-- Node Execution
      |
      +-- Node
      +-- State Read
      +-- Node Execution
      +-- State Update
      +-- Checkpoint
      |
      +-- Next Node
```

两者共享：

* Run
* State
* Event
* Checkpoint
* Artifact
* Lifecycle

但不共享具体执行逻辑。

---

## 6. Event Boundary

Runtime 必须产生标准化 Event。

至少预留：

```text
RunStarted
RunCompleted
RunFailed
RunCancelled

TurnStarted
TurnCompleted

NodeStarted
NodeCompleted
NodeFailed

LLMStarted
LLMCompleted
LLMFailed

ToolCallStarted
ToolCallCompleted
ToolCallFailed

CheckpointCreated

ApprovalRequired
UserInputRequired
HumanResponseReceived
```

Event 是 Runtime 与 Observability / Evaluation 之间的重要解耦接口。

---

## 7. Checkpoint Boundary

Checkpoint 用于：

* Failure Recovery
* Resume
* Pause / Resume
* HITL
* Time Travel
* Debugging

Checkpoint 不绑定具体存储实现。

初始实现可以使用 PostgreSQL。

---

## 8. Dependency Rules

允许：

```text
API
 ↓
Run Orchestrator
 ↓
Agent / Workflow Runtime
 ↓
Runtime Core
 ↓
Capabilities
```

禁止：

```text
Agent Runtime -> API Layer
Runtime Core -> Agent Runtime
Runtime Core -> Workflow Runtime
Evaluation -> Agent Runtime
Observability -> Agent Runtime
API request handler -> direct Agent/Workflow execution
Runtime Core -> LangChain / DeepAgents concrete types
```

Observability 和 Evaluation 应通过 Event / Run Data 获取 Runtime 信息。

Langfuse is the preferred V1 observability backend. Runtime code emits normalized events and traces; the Langfuse adapter consumes those events and must not become an execution dependency.

---

## 9. Extension Points

预留：

```text
Multi-Agent
A2A
Agent Registry
Scheduler
Plugin
External Memory
External Knowledge
External Sandbox
Custom Model Provider
Custom Tool Provider
```

V1 不要求实现。

---

## 10. V1 Boundary

V1 只保证：

* Unified Run Model
* Agent Runtime
* Workflow Runtime
* Runtime Core
* Event
* Checkpoint
* Capability Interface
* Model Capability
* Tool Capability
* Basic Budget
* Basic Stop Control

以下暂不作为 V1 核心实现：

* 完整 Multi-Agent
* A2A
* Agent Marketplace
  -复杂 Scheduler
* 自动 Agent Evolution
* 自动 Skill Evolution
