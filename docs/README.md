
# 一、项目整体介绍



> 一个企业级 AI Agent & Workflow Runtime Platform。
>
> 最初我们有两类场景，一类是固定流程，比如合同解析、文档审核；另一类是开放式 Agent，比如 Web Agent、工具调用、知识库问答。后来我们把两套能力统一成一个 Runtime。
>
> Runtime 上层统一管理 Agent 和 Workflow 的执行，下面提供 Context、Memory、Skill、MCP、RAG、Sandbox、Model 等基础能力，同时接入 Observability 和 Evaluation，并进一步建立 Evolution 闭环。
>
> 所以整个系统的核心链路可以概括为：
>
> **执行 → 观测 → 评测 → 诊断 → 进化。**
>
> 最终目标不是做一个 Agent Demo，而是解决 Agent 在企业生产环境中的**可靠性、安全性、可观测、成本控制和持续优化**问题。

---

# 二、整体架构

```text
┌──────────────────────────────────────────────────────┐
│                    API / Management                  │
└──────────────────────────┬───────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────┐
│                  AI Runtime Platform                 │
│                                                      │
│  Run Orchestrator                                    │
│       │                                              │
│       ├── Agent Runtime                              │
│       │     ├── Agent Loop                           │
│       │     ├── Context                              │
│       │     ├── Memory                               │
│       │     ├── Skill                                │
│       │     ├── Tool Calling                         │
│       │     ├── Sub-Agent                            │
│       │     ├── HITL                                 │
│       │     └── Checkpoint / Recovery                │
│       │                                              │
│       └── Workflow Runtime                           │
│             ├── Graph                                │
│             ├── Node                                 │
│             ├── State                                │
│             └── Checkpoint                            │
│                                                      │
│  Runtime Capabilities                                │
│  ├── RAG ├── Model ├── Skill ├── Memory              │
│  ├── MCP └── Sandbox └── Policy / Budget              │
└──────────────┬──────────────────────┬────────────────┘
               │                      │
               ▼                      ▼
       ┌──────────────┐       ┌──────────────┐
       │ Observability│       │  Evaluation  │
       │              │       │              │
       │ Trace/Metric │       │ E2E/Process  │
       │ Log/Cost     │       │ Rubric/Judge  │
       └──────────────┘       │ Case/Diagnosis│
                              └──────┬───────┘
                                     │
                                     ▼
                               Evolution
                                     │
                         ┌───────────┴──────────┐
                         │ Candidate / Experiment│
                         │ Gate / Version       │
                         └──────────────────────┘
```

---

# 1. 项目介绍

**为什么做这个平台**

### 背景

传统 Agent Demo 往往是：

```text
User
 ↓
LLM
 ↓
Tool
 ↓
LLM
 ↓
Answer
```

真正进入生产后会出现：

* Agent 跑到一半挂了怎么办？
* 上下文太长怎么办？
* Memory 如何管理？
* Tool/MCP 不可信怎么办？
* Agent 能不能访问宿主机？
* 多用户怎么隔离？
* 用户中途如何确认？
* 怎么知道 Agent 到底哪里出错？
* Prompt 改了以后怎么证明变好了？
* Agent 能不能持续优化？

所以平台的目标就是：

> **把 Agent 从一个 Loop，变成一个可运行、可恢复、可观测、可评测、可进化的生产系统。**

---

# 2. 项目选型考虑

## 为什么不是直接使用一个 Agent Framework？

> 我们不是完全排斥框架，而是区分 Framework 和 Platform Boundary。

例如：

| 能力            | 选择                       |
| ------------- | ------------------------ |
| Agent Loop    | 自研                       |
| Workflow      | LangGraph                |
| API           | FastAPI                  |
| DB            | PostgreSQL               |
| Cache/State   | Redis                    |
| Sandbox       | Docker/K8s               |
| MCP           | MCP Protocol             |
| Observability | OpenTelemetry + Langfuse |
| Evaluation    | 自研 Domain + Langfuse     |
| Vector        | pgvector/ES              |
| Deployment    | Kubernetes               |

核心考虑：

### LangGraph

适合：

* Workflow
* 状态机
* Checkpoint
* HITL
* 固定流程

但 Agent Runtime 我们希望：

> **自己掌握 Agent Loop。**

避免 Runtime 被某一个 Agent Framework 绑定。

### DeepAgents / OpenClaw / Claude Agent SDK

这些可以作为：

* 参考实现
* Runtime 能力来源
* Adapter
* 特定场景实现

但不作为整个平台的架构边界。

> **框架是实现组件，不是平台边界。**

这是一个比较好的架构观点。

---

# 3. 上下文管理

核心问题：

> Agent 不是简单地把历史消息全部塞给 LLM。

Context Manager 可以拆成：

```text
Context
├── System Context
├── User Context
├── Conversation
├── Memory
├── Skills
├── Tool Definitions
├── Retrieved Context
├── Current Task
└── Runtime State
```

核心流程：

```text
User Request
    ↓
Context Manager
    ├── Load Conversation
    ├── Load Memory
    ├── Load Skill
    ├── Retrieve RAG
    ├── Select Tools
    └── Build Context
             ↓
            LLM
```

### 上下文优化

主要考虑：

1. **Token Budget**
2. **历史消息裁剪**
3. **Summary**
4. **重要信息保留**
5. **RAG 按需注入**
6. **Tool Schema 按需加载**
7. **Memory 与 Conversation 分离**

例如：

```text
最近消息
    +
Summary
    +
Relevant Memory
    +
Relevant RAG
    +
Current Task
    ↓
LLM Context
```

而不是：

```text
全部历史消息
+
全部工具
+
全部知识库
```

---

# 4. Memory 管理

> **Context 是当前一次执行需要什么；Memory 是跨 Session 什么值得留下。**

架构：

```text
                Memory
                   │
        ┌──────────┴──────────┐
        │                     │
   Working Memory        Long-term Memory
        │                     │
   Current Session       User / Agent
                              │
                         Semantic Memory
                         Episodic Memory
                         Preference
```

### Write

不是每一轮都把全部上下文直接扔给 Memory。

应该：

```text
Conversation / Trace
       ↓
Memory Extraction
       ↓
Candidate Memory
       ↓
Triage
       ↓
Dedup / Update
       ↓
Long-term Memory
```

### Read

```text
New User Task
      ↓
Memory Retrieval
      ↓
Relevant Memory
      ↓
Context Manager
      ↓
Prompt
```

---

# 5. 异常恢复

> Agent 的失败不是一种失败，所以我们做了分层恢复。

```text
Failure
│
├── LLM Failure
│      └── Retry / Fallback
│
├── Tool Failure
│      └── Retry / Alternative Tool
│
├── Sandbox Failure
│      └── Recreate Sandbox
│
├── Agent Process Crash
│      └── Checkpoint Resume
│
├── Worker Crash
│      └── Watchdog Recovery
│
├── Network Failure
│      └── Retry / Idempotency
│
└── Context Overflow
       └── Summarization / Compaction
```

尤其：

```text
Run
 ↓
Turn
 ↓
Checkpoint
 ↓
Worker Crash
 ↓
Watchdog
 ↓
Load Checkpoint
 ↓
Resume
```

### Redis + DB

可以说：

> Redis 负责运行时状态和实时事件，PostgreSQL 保存持久化执行状态和业务数据。

例如：

```text
Redis
├── Run State
├── Lock
└── Stream

PostgreSQL
├── Run
├── Message
├── Checkpoint
└── Execution
```

### 幂等

尤其 Tool/MCP：

```text
request_id = tool_call_id
```

重复恢复时：

```text
same request_id
      ↓
dedup
      ↓
avoid duplicate side effect
```

---

# 6. HITL 机制

HITL 不应该是：

> Agent 输出一句“请人工确认”。

而应该是 Runtime 的一种 **Lifecycle State**。

例如：

```text
RUNNING
   ↓
WAITING_FOR_HUMAN
   ↓
User Response
   ↓
RESUME
```

Checkpoint：

```text
Agent
 ↓
Tool Call
 ↓
Sensitive Operation
 ↓
ApprovalRequired
 ↓
Checkpoint
 ↓
WAITING
```

用户批准以后：

```text
Human Response
      ↓
Load Checkpoint
      ↓
Resume
```

### 哪些场景触发 HITL？

```text
高风险 Tool
敏感数据
外部发送
资金操作
权限变化
不确定性高
```

所以可以设计：

```text
Policy
   ↓
Risk Assessment
   ↓
Need Approval?
   ├── No → Continue
   └── Yes
         ↓
    HITL Checkpoint
```

---

# 7. Sandbox

核心观点：

> **Sandbox 不是为了防止 Agent 犯错，而是为了限制 Agent 犯错以后能造成的影响。**

安全边界：

```text
Sandbox
├── Process Isolation
├── Filesystem Isolation
├── Resource Isolation
├── Network Isolation
└── Identity Isolation
```

架构：

```text
Agent Runtime
      │
      ▼
SandboxManager
      │
      ├── Docker Backend
      │
      └── K8s Backend
```

接口统一：

```python
class SandboxBackend:
    execute()
    upload_files()
    download_files()
    close()
```

### 为什么 Docker → K8s？

Docker：

* 本地开发
* CI
* 小规模

K8s：

* 多租户
* Resource Limit
* NetworkPolicy
* Pod Isolation
* PVC
* 调度能力

### Workspace 和 Sandbox 分离

```text
Workspace
   │
   ├── Persistent Storage
   │
   └── Sandbox
```

Sandbox 挂掉：

```text
Sandbox Destroy
      ↓
Workspace 保留
      ↓
New Sandbox
      ↓
Reattach Workspace
```

所以 Sandbox 是计算环境，而 Workspace 是持久数据。

---

# 8. 评测体系

一句话：

> **传统 Agent 评测只看最终答案，我们把评测对象扩展成 Task + Outcome + Process + Trace。**

整体：

```text
Task
 ↓
Expected Behavior
 ↓
Agent Execution
 ↓
Trace
 ↓
Outcome
 ↓
Evaluation
 ↓
Diagnosis
```

---

## Evaluation 四层

### ① E2E

> 任务最终完成了吗？

例如：

```text
合同字段是否全部正确
用户问题是否解决
任务是否完成
```

### ② Process

> Agent 是怎么做的？

例如：

```text
Tool Selection
Tool Args
RAG Retrieval
Skill Usage
Workflow Branch
```

### ③ Efficiency

```text
Token
Cost
Latency
Tool Calls
Turn Count
```

### ④ Risk

```text
Safety
Data Leakage
Policy Violation
Unauthorized Tool
```

---

# 评测资产

```text
Evaluation Asset
├── Golden
├── Bad Case
├── Good Case
├── Challenge
├── Calibration
└── Inspection
```

并且增加 Purpose：

```text
Optimization
Validation
Regression
Challenge
Calibration
Inspection
```

---

# Rubric

Rubric 作为核心。

```text
Rubric
├── Dimension
├── Criterion
├── Evidence
├── Score
└── Threshold
```

例如：

```text
Tool Selection
  ├── 是否选择正确工具
  ├── 是否存在不必要调用
  └── 参数是否正确
```

### LLM Judge

> LLM Judge 不能当 Ground Truth。

体系：

```text
Deterministic
      ↓
Small LLM Judge
      ↓
Strong Judge
      ↓
Human
```

并通过：

```text
Human Calibration Dataset
        ↓
Judge Agreement
        ↓
Judge Regression
        ↓
Confidence Calibration
```

控制 Judge 的可靠性。

---

# 9. Evolution 体系

这是 Evaluation 后面的闭环。

核心：

> **Evolution 不是“让 Agent 自己改 Prompt”，而是 Evaluation 驱动的受控搜索。**

完整链路：

```text
Production
    ↓
Case Mining
    ↓
Diagnosis
    ↓
Evolution Task
    ↓
Candidate Generation
    ↓
Experiment
    ↓
Evaluation
    ↓
Gate
    ↓
Decision
    ↓
New Version
```

---

## Evolution Target

```text
Prompt
Skill
RAG
Tool
Model
Policy
Context
```

但是不是一个万能 Optimizer。

```text
Prompt Optimizer
Skill Optimizer
RAG Optimizer
Tool Optimizer
Model Selector
Policy Optimizer
```

---

## Candidate

不是直接修改线上配置。

```text
v12
 ↓
Candidate A
Candidate B
Candidate C
 ↓
Evaluation
 ↓
Gate
 ↓
v13
```

并且使用：

```text
ADD
REPLACE
DELETE
INSERT
```

这种 bounded edit。

---

## 防止过拟合

```text
Optimization Set
      ↓
Validation Set
      ↓
Regression Set
      ↓
Challenge Set
      ↓
Production / Shadow
```

这个点可以直接联系 SkillOpt：

> 我们借鉴了 SkillOpt 的 bounded edit、multi-round optimization、held-out validation 思路，但把它扩展成整个 Agent Runtime 的 Evolution。

---

# 10. 其他：你还应该主动讲的几个模块

---

## 10.1 MCP / Tool Governance

```text
Agent
 ↓
MCP Gateway
 ↓
Tool Registry
 ↓
Policy
 ↓
Tool
```

重点：

* Tool Isolation
* Tool Metadata
* Permission
* Timeout
* Rate Limit
* Audit
* Tool Result 不可信
* Prompt Injection
* Tool Call Idempotency

一句话：

> **MCP 解决工具标准化，Gateway 解决企业治理。**

---

## 10.2 Observability

Agent Trace：

```text
Run
 ├── Turn
 │    ├── LLM
 │    ├── Retrieval
 │    ├── Skill
 │    └── Tool
 │
 └── Outcome
```

核心指标：

```text
Latency
Token
Cost
Tool Success
Error
Turn Count
Task Success
```

并且 Trace 不只是为了监控：

> **Trace 是 Evaluation 和 Evolution 的数据基础。**

---

## 10.3 Budget / Stop Mechanism

Agent 必须有多个预算：

```text
max_turns
max_tokens
max_cost
max_tool_calls
max_execution_time
```

例如：

```text
Turn Budget
Cost Budget
Time Budget
Tool Budget
```

达到 Budget：

```text
Graceful Stop
```

而不是简单：

```text
while True:
    agent()
```

---

## 10.4 Multi-Agent / Sub-Agent

不是所有任务都应该一个 Agent 从头做到尾。

例如：

```text
Supervisor
   │
   ├── Research Agent
   ├── Coding Agent
   ├── Retrieval Agent
   └── Review Agent
```

Supervisor 负责：

* Task Decomposition
* Routing
* Aggregation

Sub-Agent：

* 独立 Context
* 独立 Tool
* 独立 Budget

这样可以减少主 Agent Context 膨胀。

---

# 整个项目浓缩成这张图

```text
                 ┌────────────────────┐
                 │      User/API      │
                 └─────────┬──────────┘
                           ↓
                 ┌────────────────────┐
                 │    Run Orchestrator│
                 └─────────┬──────────┘
                           ↓
        ┌────────────────────────────────────┐
        │             AI Runtime             │
        │                                    │
        │ Agent Runtime    Workflow Runtime  │
        │      │                 │            │
        │      └───────┬─────────┘            │
        │              ↓                      │
        │ Context / Memory / Skill / RAG      │
        │ MCP / Tool / Model / Budget         │
        │ HITL / Checkpoint / Recovery        │
        └──────────────┬─────────────────────┘
                       ↓
              ┌──────────────────┐
              │     Sandbox      │
              └──────────────────┘

                       │
             ┌─────────┴─────────┐
             ↓                   ↓
      ┌──────────────┐    ┌──────────────┐
      │ Observability│    │  Evaluation  │
      │              │    │              │
      │ Trace        │    │ E2E          │
      │ Metrics      │    │ Process      │
      │ Cost         │    │ Rubric       │
      └──────┬───────┘    │ Judge        │
             │            │ Case         │
             │            └──────┬───────┘
             │                   ↓
             │             ┌──────────────┐
             └────────────→│  Evolution   │
                           │              │
                           │ Diagnosis    │
                           │ Candidate    │
                           │ Experiment   │
                           │ Gate         │
                           │ Version      │
                           └──────┬───────┘
                                  │
                                  └────→ Runtime
```