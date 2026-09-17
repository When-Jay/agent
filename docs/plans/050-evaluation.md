# Evaluation Platform Implementation Plan

## 1. Implementation Goal

目标不是一次性实现完整 Evaluation Platform，而是逐步建立：

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
```

的最小闭环。

总体采用：

```text
Foundation
    ↓
Assets
    ↓
Harness
    ↓
E2E
    ↓
Process
    ↓
Evaluator
    ↓
Reliability
    ↓
Online
    ↓
Case
    ↓
Diagnosis
    ↓
Evolution
```

---

# 2. Phase Overview

```text
Phase 0   Foundation
Phase 1   Evaluation Assets
Phase 2   Evaluation Environment & Harness
Phase 3   E2E Evaluation
Phase 4   Process Evaluation
Phase 5   Evaluator Runtime
Phase 6   Judge Reliability
Phase 7   Online Evaluation
Phase 8   Case Mining & Diagnosis
Phase 9   Evaluation Asset Evolution
Phase 10  Quality Gate
Phase 11  Agent Evolution Integration
Phase 12  Coverage & Drift
Phase 13  Business Metric Bridge
Phase 14  Evaluation Agent
```

不是所有 Phase 都需要一次实现。

---

# 3. Phase 0 — Foundation

## Goal

建立 Evaluation Module 基础结构。

```text
evaluation/
├── domain/
├── application/
├── infrastructure/
├── evaluators/
├── harness/
├── cases/
├── diagnosis/
├── reliability/
└── api/
```

建立：

```text
Task
EvaluationRun
Trial
EvaluationResult
```

基础模型。

### Dependencies

```text
PostgreSQL
Langfuse
Observability
Agent Runtime
```

### Acceptance

能够：

```text
创建 Task
创建 Evaluation Run
执行 Trial
记录 Trace ID
保存 Evaluation Result
```

---

# 4. Phase 1 — Evaluation Assets

## Goal

建立评测资产模型。

实现：

```text
EvaluationAsset
Task
ExpectedBehavior
EvaluationSuite
Rubric
```

Asset Type：

```text
GOLDEN
REGRESSION
CHALLENGE
CALIBRATION
INSPECTION
```

### Deliverables

```text
Task CRUD
Suite CRUD
Rubric CRUD
Asset CRUD
Versioning
```

### Acceptance

能够：

```text
创建一个 Task
 ↓
绑定 Rubric
 ↓
加入 Evaluation Suite
 ↓
保存为 Regression / Golden Set
```

---

# 5. Phase 2 — Evaluation Environment & Harness

## Goal

解决：

> 同一个 Agent Version 在相同环境中进行可重复测试。

实现：

```text
EvaluationEnvironment
EnvironmentSnapshot
TaskRunner
TrialRunner
```

Environment：

```text
Sandbox
Workspace
User Context
KB Version
Skill Version
MCP
External Service
Network Policy
```

### Integration

```text
Evaluation Harness
       ↓
Sandbox Manager
       ↓
K8s / Docker Sandbox
```

### Acceptance

同一个：

```text
Task
Agent Version
Environment Version
```

能够重复执行。

---

# 6. Phase 3 — E2E Evaluation

## Goal

先解决最重要的问题：

> Agent 到底有没有完成任务？

实现：

```text
E2E Suite
E2E Evaluator
Outcome Collector
Task Success
```

支持：

```text
Task Success
Required Field
Artifact
Business Result
```

### Acceptance

能够输出：

```text
Task Success Rate
Pass Rate
Failure Cases
```

---

# 7. Phase 4 — Process Evaluation

## Goal

解决：

> Agent 为什么失败？

第一阶段只做核心 Process：

```text
Tool Calling
Skill Calling
RAG
Context
Data Processing
```

暂时不拆：

```text
几十种细粒度 Process
```

### 原则

只有当：

```text
E2E Failure
+
不知道原因
```

时才增加新的 Process Evaluator。

### Acceptance

能够从：

```text
E2E Failure
```

进一步定位：

```text
Skill
Tool
RAG
Context
```

---

# 8. Phase 5 — Evaluator Runtime

## Goal

统一执行不同 Evaluator。

实现：

```text
Evaluator
EvaluatorRegistry
EvaluationPolicy
EvaluatorOrchestrator
ResultAggregator
```

支持：

```text
Rule
Schema
Exact Match
Retrieval
LLM Judge
```

### Evaluator Cascade

```text
Rule
 ↓
Small LLM
 ↓
Strong LLM
 ↓
Human
```

### Acceptance

Evaluator 可以通过 Registry 插拔。

---

# 9. Phase 6 — Judge Reliability

## Goal

解决：

> LLM Judge 到底可不可信？

实现：

```text
Calibration Dataset
Human Label
Judge Label
Agreement
Disagreement
Unknown Rate
Judge Regression
```

### Flow

```text
Human Calibration
      ↓
Judge
      ↓
Compare
      ↓
Quality Gate
```

### Acceptance

能够回答：

```text
Judge 和人工的一致率是多少？
哪些 Criterion 最容易判断错误？
Judge 是否发生版本回归？
```

---

# 10. Phase 7 — Online Evaluation

## Goal

让 Evaluation 从离线进入真实生产。

实现：

```text
AB
Shadow
Inspection
Production Sampling
```

### Inspection

从：

```text
Golden Set
Regression Set
Representative Task
```

抽取线上巡检 Task。

### Acceptance

能够：

```text
周期执行 Inspection
 ↓
产生 Evaluation Result
 ↓
发现质量下降
```

---

# 11. Phase 8 — Case Mining & Diagnosis

## Goal

建立：

> Online → Case → Diagnosis

闭环。

实现：

```text
Case
CaseMiner
CaseClassifier
Diagnosis
```

Case 来源：

```text
User Feedback
Production Sampling
Monitoring
Random Sampling
Evaluation Failure
```

Case 类型：

```text
GOOD
BAD
UNCERTAIN
UNCOVERED
```

### Diagnosis

```text
AGENT_FAILURE
EVALUATION_FAILURE
COVERAGE_GAP
```

### Acceptance

一个线上 Bad Case 能够：

```text
Trace
 ↓
Case
 ↓
Diagnosis
 ↓
Recommended Action
```

---

# 12. Phase 9 — Evaluation Asset Evolution

## Goal

让线上问题反向进入 Evaluation。

核心流程：

```text
Bad Case
 ↓
Diagnosis
 ↓
Regression Set
```

或者：

```text
Uncovered Case
 ↓
New Task
 ↓
New Rubric
 ↓
Evaluation Set
```

或者：

```text
Evaluation Failure
 ↓
Rubric Update
 ↓
Evaluator Update
 ↓
Calibration
```

### Acceptance

线上问题可以自动/半自动进入：

```text
Golden Set
Regression Set
Challenge Set
```

---

# 13. Phase 10 — Quality Gate

## Goal

Evaluation 接入 Agent 发布流程。

实现：

```text
QualityGate
GateRule
ReleaseCheck
```

### Gate

```text
Must Pass
Threshold
Warning
Informational
```

### Pipeline

```text
Code / Prompt / Skill Change
          ↓
Evaluation
          ↓
Quality Gate
          ↓
Pass / Block
```

### Acceptance

Agent 发布前能够自动执行：

```text
Regression Set
Core E2E
Core Process
Safety
Data Accuracy
```

---

# 14. Phase 11 — Agent Evolution Integration

## Goal

让 Evaluation 真正成为 Agent Evolution 的入口。

```text
Case
 ↓
Diagnosis
 ↓
Agent Change
 ↓
Regression
 ↓
Gate
 ↓
Release
 ↓
AB
```

Evaluation Platform 不直接修改 Agent。

提供：

```text
Diagnosis Result
Regression Cases
Evaluation Report
Quality Gate
```

---

# 15. Phase 12 — Coverage & Drift

## Goal

发现：

> 我们是不是正在测试一个已经过时的 Agent？

实现：

```text
Coverage Analyzer
Distribution Analyzer
Drift Detector
```

Coverage：

```text
Task
Scenario
Skill
Tool
Failure
Process
User Distribution
```

Drift：

```text
Production Distribution
vs
Evaluation Distribution
```

### Acceptance

系统能够提示：

```text
线上出现新任务类型
 ↓
当前 Evaluation 没有覆盖
 ↓
Coverage Gap
```

---

# 16. Phase 13 — Business Metric Bridge

## Goal

建立：

```text
Evaluation Metric
        ↓
Business Metric
```

实现：

```text
BusinessMetric
BusinessMetricMapping
BusinessExperiment
```

例如：

```text
Task Success
      ↓
Business Completion Rate
```

```text
Agent Automation Rate
      ↓
Manual Cost Reduction
```

### Acceptance

Evaluation Report 不再只有：

```text
Score = 0.87
```

而是能够展示：

```text
Agent Quality
+
Business Impact
```

---

# 17. Phase 14 — Evaluation Agent

## Goal

最后再实现 Agent 化的 Evaluation。

不要在 V1 就实现。

Evaluation Agent：

```text
Online Trace
 ↓
Select Cases
 ↓
Select Rubric
 ↓
Run Evaluation
 ↓
Analyze
 ↓
Diagnose
 ↓
Generate Report
 ↓
Recommend Evolution
```

它可以承担：

```text
Case Selection
Evaluator Selection
Failure Analysis
Coverage Analysis
Rubric Recommendation
```

但最终修改仍需要经过：

```text
Human / CI / Quality Gate
```

---

# 18. Recommended MVP

如果只有有限开发资源，第一阶段只实现：

```text
1. Task
2. Expected Behavior
3. Rubric
4. Evaluation Suite
5. E2E Evaluation
6. Core Process Evaluation
7. Deterministic Evaluator
8. LLM Judge
9. Trial
10. Evaluation Environment
11. Regression Set
12. Quality Gate
13. Langfuse Integration
```

形成：

```text
Agent Change
     ↓
Offline Evaluation
     ↓
Regression
     ↓
Quality Gate
     ↓
Release
```

这已经足够形成一个可工作的 Evaluation MVP。

---

# 19. Second Stage

随后增加：

```text
Online Evaluation
      ↓
Case Mining
      ↓
Diagnosis
      ↓
Regression Set
```

形成：

```text
Online
  ↓
Case
  ↓
Offline Regression
  ↓
Release
```

---

# 20. Third Stage

再增加：

```text
Evaluation Evolution
```

形成：

```text
                ┌───────────────┐
                │ Agent Runtime │
                └───────┬───────┘
                        ↓
                  Observability
                        ↓
                   Online Eval
                        ↓
                    Case Mining
                        ↓
                    Diagnosis
                   /         \
                  /           \
        Agent Evolution   Eval Evolution
                \             /
                 \           /
                  Offline Eval
                       ↓
                  Quality Gate
                       ↓
                    Release
                       ↓
                 Agent Runtime
```

---

# 21. Suggested Repository Structure

```text
evaluation/
├── domain/
│   ├── task.py
│   ├── expected_behavior.py
│   ├── suite.py
│   ├── asset.py
│   ├── rubric.py
│   ├── evaluator.py
│   ├── trial.py
│   ├── outcome.py
│   ├── result.py
│   ├── case.py
│   └── diagnosis.py
│
├── application/
│   ├── task_service.py
│   ├── evaluation_service.py
│   ├── case_service.py
│   ├── diagnosis_service.py
│   └── gate_service.py
│
├── harness/
│   ├── task_runner.py
│   ├── trial_runner.py
│   ├── environment.py
│   └── trace_collector.py
│
├── evaluators/
│   ├── base.py
│   ├── rule.py
│   ├── schema.py
│   ├── exact_match.py
│   ├── retrieval.py
│   ├── llm_judge.py
│   └── human.py
│
├── reliability/
│   ├── calibration.py
│   ├── agreement.py
│   ├── regression.py
│   └── confidence.py
│
├── cases/
│   ├── miner.py
│   ├── classifier.py
│   └── repository.py
│
├── diagnosis/
│   ├── diagnosis.py
│   ├── agent_failure.py
│   ├── evaluation_failure.py
│   └── coverage_gap.py
│
├── coverage/
│   ├── coverage.py
│   └── drift.py
│
├── gates/
│   ├── gate.py
│   └── rules.py
│
└── api/
    ├── tasks.py
    ├── suites.py
    ├── runs.py
    ├── results.py
    ├── cases.py
    ├── diagnosis.py
    └── gates.py
```

---

# 22. Dependency Order

必须遵守：

```text
Observability
      ↓
Task / Asset
      ↓
Harness
      ↓
Evaluator
      ↓
Evaluation Runtime
      ↓
Offline Evaluation
      ↓
Quality Gate
      ↓
Online Evaluation
      ↓
Case Mining
      ↓
Diagnosis
      ↓
Evolution
```

不要反过来先做：

```text
Evaluation Agent
自动 Rubric
自动 Diagnosis
自动 Evolution
```

因为没有 Evaluation Asset、Trace 和可靠 Evaluator，这些自动化能力没有稳定的数据基础。

---

# 23. Testing Strategy

每个阶段必须同时建设：

```text
Unit Test
Integration Test
Evaluation Test
Regression Test
```

重点测试：

### Evaluator

```text
Input
→ Expected Result
```

### Judge

```text
Calibration Set
→ Agreement
```

### Harness

```text
Same Environment
→ Comparable Result
```

### Quality Gate

```text
Threshold
→ Pass / Block
```

### Case Mining

```text
Signal
→ Correct Case
```

### Diagnosis

```text
Known Failure
→ Correct Attribution
```

---

# 24. Architecture Validation

每个 Phase 完成后执行：

```text
Implementation
      ↓
Architecture Check
```

检查：

```text
1. 是否突破 Runtime Boundary？
2. 是否把 Monitoring 逻辑放进 Evaluation？
3. 是否把 Agent Loop 放进 Evaluation？
4. 是否绕过 Langfuse 重复实现 Trace？
5. 是否存在 Evaluation → Agent 的强耦合？
6. 是否记录 Version？
7. 是否可追溯 Trace？
8. 是否可以回归？
```

---

# 25. Final Implementation Milestones

## Milestone 1 — Evaluation MVP

```text
Task
Rubric
E2E
Process
Trial
Environment
Langfuse
Regression
Gate
```

目标：

> 能够可靠地判断一次 Agent 变更有没有回归。

---

## Milestone 2 — Production Evaluation

```text
Online
Inspection
Sampling
Case
Diagnosis
```

目标：

> 能够发现离线评测没有覆盖的真实问题。

---

## Milestone 3 — Evaluation Flywheel

```text
Online
 ↓
Case
 ↓
Diagnosis
 ↓
Regression
 ↓
Evaluation Asset
```

目标：

> 线上问题能够持续沉淀为评测资产。

---

## Milestone 4 — Agent Evolution

```text
Case
 ↓
Agent Fix
 ↓
Regression
 ↓
Gate
 ↓
AB
```

目标：

> Agent 修改能够被量化验证。

---

## Milestone 5 — Evaluation Evolution

```text
Case
 ↓
Evaluation Failure / Coverage Gap
 ↓
Rubric / Dataset / Evaluator Evolution
 ↓
Regression
```

目标：

> 评测体系自身能够持续校准。

---

# 26. Final Target

最终 Evaluation Platform 不应该被描述为：

> “一个可以跑评测的服务。”

而应该是：

```text
                 ┌─────────────────────┐
                 │    Agent Runtime    │
                 └──────────┬──────────┘
                            ↓
                    Observability
                            ↓
                  Online Evaluation
                            ↓
                       Case Mining
                            ↓
                        Diagnosis
                     ┌──────┴──────┐
                     ↓             ↓
              Agent Evolution  Eval Evolution
                     └──────┬──────┘
                            ↓
                    Offline Evaluation
                            ↓
                       Quality Gate
                            ↓
                         Release
                            ↓
                    Agent Runtime
```

即：

> **Evaluation = Agent Quality Control Plane**

它负责：

```text
发现问题
定位问题
验证修改
沉淀资产
校准评测
驱动演进
```

最终形成：

```text
Observation
    ↓
Evaluation
    ↓
Diagnosis
    ↓
Evolution
    ↓
Regression
    ↓
Release
    ↓
Observation
```

这才是 AI Agent Platform 中 Evaluation 的长期定位。
