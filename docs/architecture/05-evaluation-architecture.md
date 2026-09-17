# Evaluation Platform Architecture

## 1. Overview

Evaluation Platform 是 AI Agent & Workflow Platform 的质量控制与持续改进层。

它不只是回答：

> “这个 Agent 得了多少分？”

而是完整回答：

1. Agent 是否完成了任务？
2. Agent 的行为是否符合预期？
3. Agent 哪个环节出现了问题？
4. 这是 Agent 问题、评测问题，还是评测覆盖不足？
5. 这个问题是否应该进入评测资产？
6. Agent 修改后是否发生回归？
7. 线上真实效果是否得到改善？
8. 评测体系自身是否需要演进？

因此 Evaluation Platform 的核心不是一个“Evaluator Service”，而是一套围绕 Agent Quality 建立的质量闭环。

整体形成：

```text
                         ┌──────────────────────┐
                         │     Agent Runtime    │
                         │  Agent / Workflow    │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │    Observability     │
                         │ Trace / Metrics / Log │
                         └──────────┬───────────┘
                                    │
                   ┌────────────────┴────────────────┐
                   │                                 │
                   ▼                                 ▼
          ┌─────────────────┐               ┌─────────────────┐
          │ Online Evaluation│               │    Monitoring   │
          │ AB / Shadow /   │               │ Stability / Cost│
          │ Inspection      │               │ Behavior        │
          └────────┬────────┘               └────────┬────────┘
                   │                                 │
                   └────────────────┬────────────────┘
                                    ▼
                         ┌──────────────────────┐
                         │     Case Mining      │
                         │ Good / Bad / Unknown │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │      Diagnosis       │
                         │ Agent / Eval / Gap   │
                         └──────────┬───────────┘
                                    │
                       ┌────────────┴────────────┐
                       │                         │
                       ▼                         ▼
              Agent Evolution             Eval Evolution
              Prompt / Skill              Rubric / Dataset
              Model / RAG                 Evaluator / Policy
              Context / Runtime           Coverage
                       │                         │
                       └────────────┬────────────┘
                                    ▼
                         ┌──────────────────────┐
                         │ Offline Evaluation   │
                         │ Regression / Gate    │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │     Release Gate     │
                         └──────────┬───────────┘
                                    │
                                    └──────────────► Agent Runtime
```

Evaluation Platform 因此存在两条相互耦合的 Loop：

```text
Agent Evolution Loop
Online Case
    ↓
Diagnosis
    ↓
Agent Change
    ↓
Offline Regression
    ↓
Release
    ↓
Online Observation
```

```text
Evaluation Evolution Loop
Online Case
    ↓
Diagnosis
    ↓
Coverage / Rubric / Evaluator Problem
    ↓
Evaluation Asset Evolution
    ↓
Offline Regression
    ↓
Online Observation
```

两条 Loop 共享：

* Online Trace
* Case
* Evaluation Dataset
* Rubric
* Evaluation Result

---

# 2. Architectural Position

Evaluation Platform 位于 Runtime 与 Observability 之上。

```text
┌─────────────────────────────────────────────────────────┐
│                    Application Layer                    │
│             Agent Application / Workflow                │
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│                     AI Runtime                          │
│ Agent Runtime │ Workflow Runtime │ Run Orchestrator     │
└───────────────────────────┬─────────────────────────────┘
                            │
                            │ Trace / Event / Outcome
                            ▼
┌─────────────────────────────────────────────────────────┐
│                  Observability Layer                    │
│ Trace │ Metrics │ Logs │ Token │ Cost │ Tool │ Skill    │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                  Evaluation Platform                    │
│                                                         │
│  Assets      Harness       Runtime       Diagnosis       │
│  Case        E2E           Evaluator      Evolution      │
│  Rubric      Process       Judge          Quality Gate   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

Evaluation 不负责：

* Agent Loop
* Workflow Graph
* Tool 执行
* Sandbox 执行
* Trace 采集基础设施
* Metrics 基础设施
* Model Serving

Evaluation 消费这些系统产生的数据，并建立质量判断与演进闭环。

---

# 3. Evaluation Target

Agent Evaluation 的对象不是单一 LLM。

完整 Evaluation Target：

```text
Agent Quality
├── Model
├── Prompt
├── Skill
├── Context Management
├── Memory
├── RAG
├── Tool / MCP
├── Agent Runtime
├── Workflow
├── Sandbox / Environment
├── Execution Process
└── Final Outcome
```

因此 Evaluation 应同时观察：

```text
Input
  ↓
Agent
  ↓
Execution
  ├── LLM Calls
  ├── Tool Calls
  ├── Skill Calls
  ├── Retrieval
  ├── Context Changes
  ├── State Changes
  └── Environment Interaction
  ↓
Outcome
```

---

# 4. Core Evaluation Model

Evaluation 的基本模型：

```text
Task
  +
Expected Behavior
  +
Evaluation Environment
  +
Trace / Trajectory
  +
Outcome
  ↓
Evaluation
  ↓
Diagnosis
```

其中：

### Task

定义要测试什么。

```text
Task
├── task_id
├── input
├── user_context
├── expected_behavior
├── success_criteria
├── process_assertions
├── outcome_assertions
├── environment
└── evaluation_suite
```

### Expected Behavior

描述 Agent 应该如何完成任务，而不是只提供一个固定答案。

例如：

```text
Expected Behavior:

1. 必须查询门店经营数据
2. 必须使用指定数据源
3. 不得编造不存在的数据
4. 必须生成经营分析结果
5. 最终结果必须包含指定字段
```

### Outcome

描述任务最终是否达到业务目标。

```text
Outcome
├── task_completed
├── business_result
├── artifacts
├── environment_state
└── user_feedback
```

---

# 5. Evaluation Asset

Evaluation Asset 是整个 Evaluation Platform 的长期资产。

```text
Evaluation Assets
├── Golden Set
├── Must-Pass / Regression Set
├── Bad Case Set
├── Challenge Set
├── Calibration Set
├── Inspection Set
└── Evaluation Rubrics
```

## 5.1 Golden Set

用于定义：

> 什么样的结果可以被认为是合格的。

Golden Set 不一定覆盖所有问题，而是提供稳定的核心质量基线。

---

## 5.2 Must-Pass / Regression Set

记录：

> 曾经出现过的问题，之后不能再次出现。

典型来源：

```text
Production Bad Case
        ↓
Diagnosis
        ↓
修复
        ↓
加入 Regression Set
```

这是 Agent 持续演进过程中最重要的资产之一。

---

## 5.3 Challenge Set

用于定义：

> Agent 能力边界在哪里。

Challenge Set 可以包含：

* 长程任务
* 边界任务
* 复杂工具组合
* 大规模上下文
* 异常环境
* 新用户场景

Challenge Set 不应该直接作为唯一发布门禁。

---

## 5.4 Calibration Set

用于校准：

* LLM Judge
* Rubric
* Human Judge
* Evaluator

核心目标：

```text
Human Judgment
      ↕
Machine Judgment
```

---

## 5.5 Inspection Set

用于线上巡检。

它可以从：

* Golden Set
* Regression Set
* 生产代表性 Task

中抽取。

---

# 6. E2E Evaluation

E2E Evaluation 回答：

> “事情到底有没有办成？”

```text
Task
 ↓
Agent Execution
 ↓
Outcome
 ↓
E2E Evaluation
```

典型指标：

* Task Success Rate
* Business Success Rate
* Artifact Validity
* Required Output Completeness
* Business Metric

E2E Evaluation 是用户视角的评价。

例如：

```text
任务：
生成某门店经营分析报告

E2E：
报告是否最终生成？
数据是否正确？
报告是否满足业务要求？
```

---

# 7. Process Evaluation

Process Evaluation 回答：

> “Agent 到底在哪一步出了问题？”

```text
Task
 ↓
Execution Trace
 ├── Context
 ├── Retrieval
 ├── Skill
 ├── Tool
 ├── LLM
 └── State
 ↓
Process Evaluation
```

可以按照真实问题逐步拆分：

```text
Process Evaluation
├── Context Evaluation
├── RAG Evaluation
├── Skill Evaluation
├── Tool Evaluation
├── Planning Evaluation
├── Data Processing Evaluation
└── Safety Evaluation
```

但初期不应该过度拆分。

推荐：

```text
V1
├── E2E
└── Core Process

出现：
“知道坏了，但不知道为什么坏”
        ↓
再增加 Process Evaluation
```

---

# 8. Rubric

Rubric 是 Evaluation 的最小评价标准。

```text
Rubric
├── Definition
├── Dimension
├── Criterion
├── Evaluation Type
├── Expected Evidence
├── Positive Examples
├── Negative Examples
├── Score Rule
└── Threshold
```

推荐优先使用可执行的二元 Criterion：

```text
Criterion:

是否使用真实经营数据？
YES / NO

是否引用不存在的数据？
YES / NO

是否完成指定报告？
YES / NO
```

而不是：

```text
报告质量：
0 ~ 100
```

复杂评分可以由多个二元 Criterion 聚合得到。

---

# 9. Evaluator

Rubric 定义“评价什么”。

Evaluator 定义“如何执行评价”。

```text
Evaluator
├── Rule Evaluator
├── Schema Evaluator
├── Exact Match
├── Retrieval Evaluator
├── LLM Judge
└── Human Evaluator
```

因此：

```text
Rubric ≠ LLM Judge
```

同一个 Rubric 可以被：

```text
Rule Judge
LLM Judge
Human Judge
```

共同执行。

---

# 10. Evaluation Runtime

Evaluation Runtime 负责：

```text
Evaluation Runtime
├── Evaluation Policy
├── Evaluator Orchestrator
├── Evaluation Cascade
├── Trial Manager
├── Result Aggregator
└── Evaluation Context
```

---

# 11. Trial

Agent 存在随机性。

同一个 Task：

```text
Trial 1 → Pass
Trial 2 → Pass
Trial 3 → Fail
Trial 4 → Pass
Trial 5 → Pass
```

因此不能只使用：

```text
Single Run → Pass / Fail
```

而应该支持：

```text
Task
 ↓
N Trials
 ↓
Pass Rate
```

例如：

```text
Pass Rate = 4 / 5 = 80%
```

需要区分：

* Pass Rate
* Pass@K
* Fail Rate
* Variance

---

# 12. Evaluation Harness

Evaluation Harness 负责让 Agent 在可控环境中执行。

```text
Evaluation Harness
├── Task Runner
├── Trial Runner
├── Environment Manager
├── Sandbox
├── Workspace
├── Mock Service
├── MCP
├── Trace Collector
└── Outcome Collector
```

Harness 的核心目标：

> 控制变量，让不同 Agent Version 在相同环境中进行可重复比较。

---

# 13. Evaluation Environment

Evaluation Environment 是 V2.1 的重要新增概念。

离线评测最大的风险之一是：

```text
Offline Environment
        ≠
Production Environment
```

例如：

* 测试数据库数据不同
* 用户历史文件不存在
* KB 版本不同
* MCP 返回数据不同
* Skill 配置不同
* Sandbox 文件不同
* 网络策略不同

最终可能出现：

```text
Offline Pass
     ↓
Production Fail
```

因此：

```text
Evaluation Environment
├── Sandbox Profile
├── Workspace Snapshot
├── User Context
├── Dataset Version
├── Knowledge Base Version
├── Skill Version
├── MCP Mock / Service
├── External Service
└── Network Policy
```

需要支持：

```text
Agent Version A + Environment E1
Agent Version B + Environment E1
```

从而真正做到控制变量。

---

# 14. Online Evaluation

Online Evaluation 负责真实环境中的质量判断。

支持：

```text
Online Evaluation
├── AB Test
├── Shadow Evaluation
└── Inspection
```

### AB

真实用户流量验证业务效果。

### Shadow

真实流量进入新版本，但不直接影响用户。

### Inspection

周期性运行代表性 Task。

---

# 15. Monitoring Boundary

Monitoring 和 Evaluation 有明确边界。

### Monitoring

回答：

> 系统有没有正常工作？

关注：

```text
Tool Success Rate
Tool Failure Rate
Timeout
Token
Cost
Latency
Error
Skill Distribution
Task Distribution
```

### Evaluation

回答：

> 系统工作得好不好？

例如：

```text
Tool Success = 100%
```

但 Tool 返回的数据全部错误：

```text
Monitoring = Green
Evaluation = Red
```

因此 Monitoring 属于 Observability / Operations。

Evaluation 消费 Monitoring 产生的信号。

---

# 16. Case Mining

Case 是 Online 与 Offline 之间的桥梁。

```text
Online Signal
      ↓
Case Mining
      ↓
Case
```

Case 来源：

```text
Case Source
├── User Feedback
├── Production Sampling
├── Monitoring Rule
├── Evaluation Failure
└── Random Sampling
```

Random Sampling 非常重要，因为它能够发现：

> 当前监控和评测体系都没有定义的问题。

---

# 17. Case Model

```text
Case
├── Good
├── Bad
├── Uncertain
└── Uncovered
```

### Good Case

用于：

* Golden Set
* Challenge Set
* 正向案例

### Bad Case

用于：

* Regression Set
* Failure Analysis
* Agent Evolution

### Uncertain Case

需要人工或者更强 Judge 判断。

### Uncovered Case

意味着：

> 现有 Evaluation 没有覆盖该问题。

它应该进入 Evaluation Evolution。

---

# 18. Diagnosis

Diagnosis 是 Evaluation Platform 的核心价值之一。

```text
Case
 ↓
Trace
 ↓
Diagnosis
```

推荐一级分类：

```text
Diagnosis
├── Agent Failure
├── Evaluation Failure
└── Coverage Gap
```

进一步：

```text
Agent Failure
├── Prompt
├── Skill
├── Context
├── Memory
├── RAG
├── Tool
├── Model
├── Planning
└── Runtime
```

```text
Evaluation Failure
├── Rubric
├── Evaluator
├── Judge
├── Metric
└── Threshold
```

```text
Coverage Gap
├── Missing Task
├── Missing Scenario
├── Missing Process Criterion
└── Distribution Shift
```

---

# 19. Agent Evolution Loop

```text
Production
 ↓
Case
 ↓
Diagnosis
 ↓
Agent Problem
 ↓
Agent Change
 ↓
Offline Regression
 ↓
Quality Gate
 ↓
Release
 ↓
AB / Online Evaluation
```

可能发生的变化：

```text
Prompt
Skill
RAG
Memory
Context
Model
Tool
Runtime
Workflow
```

---

# 20. Evaluation Evolution Loop

Evaluation 本身也需要被评估。

```text
Production
 ↓
Case
 ↓
Diagnosis
 ↓
Evaluation Problem
 ↓
Evaluation Asset Change
 ↓
Regression
 ↓
Release
```

可能发生：

```text
Rubric Change
Metric Change
Evaluator Change
Judge Prompt Change
Dataset Change
Threshold Change
Coverage Expansion
```

这是避免：

> Agent 为了迎合错误评测标准而优化

的重要机制。

---

# 21. Evaluation Reliability

LLM Judge 不是 Ground Truth。

Evaluation Reliability：

```text
Judge Reliability
├── Calibration Dataset
├── Human Agreement
├── Machine Agreement
├── Evaluator Agreement
├── Confidence Calibration
├── Regression Test
├── Version Management
└── Quality Gate
```

评价 Judge 时可以使用：

* Accuracy
* Precision
* Recall
* F1
* MAE
* Correlation
* Agreement Rate

具体指标根据任务类型决定。

---

# 22. Evaluation Quality Gate

Quality Gate 支持分层门禁。

```text
Quality Gate
├── Must Pass
├── Threshold
└── Informational
```

例如：

```text
Safety              → Must Pass
Data Accuracy       → Must Pass
Core Task Success   → Threshold
Experience Score    → Threshold
Challenge Set       → Informational
```

不要要求所有指标都必须超过上一版本。

Agent 的最终业务效果仍然应该通过线上真实业务指标验证。

---

# 23. Coverage & Drift

Evaluation Coverage：

```text
Coverage
├── Task Coverage
├── Scenario Coverage
├── Skill Coverage
├── Tool Coverage
├── Failure Coverage
├── User Distribution Coverage
└── Process Coverage
```

Evaluation Drift：

```text
Production Distribution
        ↓
Evaluation Distribution
        ↓
Compare
```

发现：

```text
Production New Scenario
        ↓
Evaluation Missing
        ↓
Coverage Gap
        ↓
Create New Case
        ↓
Update Evaluation Set
```

---

# 24. Business Metric Bridge

Agent 的最终价值不能只用 Evaluation Score 表达。

```text
Evaluation Metric
        ↓
Business Metric
```

例如：

```text
Task Success Rate
        ↓
业务完成率

Data Accuracy
        ↓
业务决策准确率

Agent Automation Rate
        ↓
人工工作量下降

Latency
        ↓
用户转化 / 使用体验
```

Evaluation Platform 负责建立映射，而不是替代业务指标。

---

# 25. Langfuse Boundary

Langfuse 作为 Observability / Evaluation Foundation。

```text
Our Evaluation Platform
        │
        ├── Task
        ├── Expected Behavior
        ├── Suite
        ├── Rubric
        ├── Policy
        ├── Trial
        ├── Outcome
        ├── Case
        ├── Diagnosis
        ├── Eval Result
        └── Quality Gate
        │
        ▼
     Langfuse
        ├── Trace
        ├── Observation
        ├── Dataset
        ├── Experiment
        ├── Score
        └── Annotation
```

原则：

> 不重复建设 Langfuse 已经成熟解决的问题。

我们负责业务语义、评测编排、资产管理和演进闭环。

---

# 26. Overall Architecture

最终 Evaluation Architecture：

```text
                         ┌──────────────────┐
                         │   Agent Runtime  │
                         └────────┬─────────┘
                                  │
                                  ▼
                         ┌──────────────────┐
                         │ Observability    │
                         │ Trace / Metrics  │
                         └────────┬─────────┘
                                  │
                 ┌────────────────┴────────────────┐
                 │                                 │
                 ▼                                 ▼
        ┌─────────────────┐               ┌─────────────────┐
        │ Online Eval     │               │ Monitoring      │
        │ AB / Shadow     │               │ Stability/Cost  │
        └────────┬────────┘               └────────┬────────┘
                 │                                 │
                 └────────────────┬────────────────┘
                                  ▼
                         ┌──────────────────┐
                         │   Case Mining    │
                         └────────┬─────────┘
                                  ▼
                         ┌──────────────────┐
                         │    Diagnosis     │
                         └────────┬─────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    │                           │
                    ▼                           ▼
            Agent Evolution             Eval Evolution
                    │                           │
                    └─────────────┬─────────────┘
                                  ▼
                         ┌──────────────────┐
                         │ Offline Eval     │
                         │ E2E / Process    │
                         └────────┬─────────┘
                                  ▼
                         ┌──────────────────┐
                         │ Quality Gate     │
                         └────────┬─────────┘
                                  │
                                  ▼
                            Agent Release
```

---

# 27. Design Principles

### Principle 1

> Evaluation 不是一个分数系统，而是一个质量闭环。

### Principle 2

> Observability 是 Evaluation 的数据基础。

### Principle 3

> Case 是 Online 与 Offline 之间的桥梁。

### Principle 4

> E2E 负责判断“有没有完成”，Process 负责判断“哪里出问题”。

### Principle 5

> Evaluation 本身也需要持续演进。

### Principle 6

> Rubric 是评价标准，Evaluator 是执行机制。

### Principle 7

> LLM Judge 不是 Ground Truth。

### Principle 8

> 离线评测负责守住已知问题，在线评测负责发现未知问题。

### Principle 9

> Evaluation Asset 是长期资产。

### Principle 10

> 体系的成熟度由最短板决定，而不是由最复杂模块决定。

---

# 28. V1 Scope

第一阶段不追求完整实现所有能力。

建议：

```text
V1
├── Task
├── Expected Behavior
├── Rubric
├── Evaluation Suite
│   ├── E2E
│   └── Core Process
├── Deterministic Evaluator
├── LLM Judge
├── Trial
├── Evaluation Environment
├── Langfuse Integration
├── Offline Evaluation
├── Regression Set
└── Quality Gate
```

第二阶段：

```text
V2
├── Online Evaluation
├── Case Mining
├── Diagnosis
├── Golden / Challenge Set
├── Judge Reliability
├── Evaluation Drift
└── Business Metric Bridge
```

第三阶段：

```text
V3
├── Evaluation Evolution
├── Agent Evolution Integration
├── Automated Diagnosis
└── Evaluation Agent
```

最终目标不是“实现所有 Evaluation 功能”，而是建立：

```text
Observe
  ↓
Evaluate
  ↓
Discover
  ↓
Diagnose
  ↓
Evolve
  ↓
Regression
  ↓
Release
  ↓
Observe
```

的持续质量飞轮。
