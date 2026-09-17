# Evaluation Platform Specification

## 1. Purpose

Evaluation Platform 为 AI Agent & Workflow Platform 提供统一的质量评估、回归验证、线上质量分析和持续演进能力。

系统必须支持：

```text
Task
 → Execution
 → Trace
 → Outcome
 → Evaluation
 → Diagnosis
 → Evolution
```

同时支持：

```text
Offline Evaluation
Online Evaluation
Case Mining
Evaluation Governance
```

---

# 2. Functional Requirements

## 2.1 Task

Task 是 Evaluation 的基本执行单元。

```yaml
Task:
  id: string
  name: string
  input: object
  user_context: object
  expected_behavior: object
  success_criteria: object
  process_assertions: []
  outcome_assertions: []
  environment_id: string
  suite_id: string
```

### Requirements

* Task 必须可以独立执行。
* Task 必须具备明确的输入。
* Task 必须具备可验证的成功标准。
* Task 可以定义 Process Assertion。
* Task 可以绑定 Evaluation Environment。
* Task 必须可版本化。

---

# 3. Expected Behavior

Expected Behavior 描述 Agent 完成任务时应该达到的行为和结果。

```yaml
ExpectedBehavior:
  id: string
  task_id: string
  description: string
  required_actions: []
  forbidden_actions: []
  expected_outcome: object
  version: string
```

必须避免仅依赖：

```text
Question + Reference Answer
```

对于长程 Agent，应支持：

```text
Input
+
Expected Behavior
+
Expected Outcome
+
Process Assertions
```

---

# 4. Evaluation Asset

```yaml
EvaluationAsset:
  id: string
  name: string
  type: enum
  task_ids: []
  suite_id: string
  version: string
  source: string
  metadata: object
```

Type：

```text
GOLDEN
REGRESSION
CHALLENGE
CALIBRATION
INSPECTION
```

---

# 5. Evaluation Suite

Evaluation Suite 用于组织 Task。

```yaml
EvaluationSuite:
  id: string
  name: string
  type: enum
  tasks: []
  rubric_ids: []
  environment_id: string
  version: string
```

Type：

```text
E2E
PROCESS
```

可以进一步通过 metadata 描述：

```text
E2E:
  contract-analysis

PROCESS:
  rag-retrieval
  skill-routing
  tool-calling
  context-management
```

---

# 6. Rubric

```yaml
Rubric:
  id: string
  name: string
  dimension: string
  criteria: []
  aggregation: object
  version: string
```

Criterion：

```yaml
Criterion:
  id: string
  description: string
  type: enum
  expected_evidence: []
  positive_examples: []
  negative_examples: []
  score_rule: object
```

Type：

```text
BINARY
CATEGORICAL
NUMERIC
SCALAR
```

优先：

```text
BINARY
```

---

# 7. Evaluator

```yaml
Evaluator:
  id: string
  type: enum
  rubric_id: string
  config: object
  version: string
```

Type：

```text
RULE
SCHEMA
EXACT_MATCH
RETRIEVAL
LLM_JUDGE
HUMAN
```

---

# 8. Evaluation Policy

```yaml
EvaluationPolicy:
  id: string
  name: string
  trigger: enum
  sampling: object
  suite_id: string
  evaluator_ids: []
  cascade: object
  action: object
```

Trigger：

```text
MANUAL
SCHEDULED
PRODUCTION_SAMPLE
USER_FEEDBACK
ERROR
REGRESSION
RELEASE
DATASET_CHANGE
```

---

# 9. Evaluation Environment

```yaml
EvaluationEnvironment:
  id: string
  name: string
  sandbox_profile: object
  workspace_snapshot: object
  user_context: object
  dataset_version: string
  knowledge_base_version: string
  skill_versions: {}
  mcp_config: object
  external_services: []
  network_policy: object
  version: string
```

要求：

* Environment 必须可版本化。
* Task 必须可以绑定 Environment。
* Trial 必须记录 Environment Version。
* Agent Version 与 Environment Version 必须同时进入 Evaluation Result。

V1 实现边界（对齐 050-evaluation.md Phase 2）：

* V1 的 Environment 是**版本化描述符**：`id / name / version / config`（其余字段
  收敛进 `config` 记录，不做预置）。
* Sandbox 预置、workspace 快照恢复、KB/MCP 版本固定、网络策略执行均为保留能力，
  按后续 Phase 实现；在实现前这些字段仅作为元数据记录，不产生执行语义。

---

# 10. Trial

```yaml
Trial:
  id: string
  task_id: string
  evaluation_run_id: string
  agent_version: string
  environment_version: string
  attempt: int
  status: enum
  trace_id: string
  outcome_id: string
  started_at: datetime
  finished_at: datetime
```

Status：

```text
RUNNING
PASSED
FAILED
TIMEOUT
ERROR
CANCELLED
```

语义约定（V1 实现口径）：

* `PASSED / FAILED` 是**评估裁决**：Runtime Run 达到终态并完成评估后得出；
  FAILED Run 仍会执行评估以保留诊断证据（process assertions）。
* `TIMEOUT / ERROR / CANCELLED` 是 **harness 级状态**：等待 Run 终态超时、
  harness 派发异常、取消。
* V1 中 Outcome 直接内嵌于 Trial（`outcome` 字段）；独立 Outcome 实体与
  `outcome_id` 引用在结果规模需要时再拆分（reserved）。
* `agent_version` 由调用方提供（平台尚无 Agent 版本化概念）；引入版本化后
  改为自动记录。

---

# 11. Evaluation Run

```yaml
EvaluationRun:
  id: string
  suite_id: string
  agent_version: string
  environment_version: string
  trial_count: int
  status: enum
  started_at: datetime
  finished_at: datetime
```

Result：

```yaml
EvaluationSummary:
  pass_rate: float
  pass_at_k: float
  fail_rate: float
  latency: object
  cost: object
  token_usage: object
```

---

# 12. Outcome

Outcome 用于描述任务最终状态。

```yaml
Outcome:
  id: string
  task_completed: boolean
  business_result: object
  artifacts: []
  environment_state: object
  user_feedback: object
```

Evaluation 不应只依赖 final answer。

---

# 13. E2E Evaluation

E2E Evaluation 必须回答：

```text
Did the task succeed?
```

输出：

```yaml
E2EResult:
  task_success: boolean
  criteria_results: []
  business_metrics: {}
  score: float
```

---

# 14. Process Evaluation

Process Evaluation 必须回答：

```text
Where did the execution fail?
```

输入：

```text
Trace
Trajectory
Tool Calls
Skill Calls
Retrieval
Context
State
```

输出：

```yaml
ProcessResult:
  criteria_results: []
  failed_stage: string
  evidence: []
```

---

# 15. Case

```yaml
Case:
  id: string
  source: enum
  type: enum
  task_id: string
  trace_id: string
  input: object
  output: object
  expected_behavior: object
  evidence: []
  attribution: object
  status: string
```

Source：

```text
USER_FEEDBACK
PRODUCTION_SAMPLE
MONITORING
EVALUATION
RANDOM_SAMPLE
```

Type：

```text
GOOD
BAD
UNCERTAIN
UNCOVERED
```

---

# 16. Case Attribution

```yaml
CaseAttribution:
  category: enum
  component: string
  confidence: float
  evidence: []
  evaluator_id: string
```

Category：

```text
AGENT_FAILURE
EVALUATION_FAILURE
COVERAGE_GAP
```

Agent Failure：

```text
PROMPT
MODEL
SKILL
RAG
MEMORY
CONTEXT
TOOL
PLANNING
RUNTIME
WORKFLOW
```

Evaluation Failure：

```text
RUBRIC
EVALUATOR
JUDGE
METRIC
THRESHOLD
```

Coverage Gap：

```text
TASK
SCENARIO
PROCESS
USER_DISTRIBUTION
FAILURE_MODE
```

---

# 17. Evaluation Result

```yaml
EvaluationResult:
  id: string
  task_id: string
  run_id: string
  trial_id: string
  evaluator_id: string
  rubric_id: string
  result: enum
  score: float
  evidence: []
  confidence: float
  created_at: datetime
```

Result：

```text
PASS
FAIL
UNKNOWN
ERROR
```

---

# 18. UNKNOWN

UNKNOWN 是合法的一等结果。

出现以下情况时不得强行判定：

* 信息不足
* Judge 无法判断
* Trace 不完整
* Evidence 不足
* Environment 不一致
* Evaluator 异常

```text
Unknown
 ↓
Escalation
 ↓
Strong Judge
 ↓
Human
```

---

# 19. Judge Reliability

必须支持：

```yaml
JudgeCalibration:
  judge_id: string
  calibration_dataset_id: string
  human_labels: []
  machine_labels: []
  agreement: float
  disagreement: float
  unknown_rate: float
  version: string
```

必须支持 Judge Regression。

新 Judge：

```text
New Judge
   ↓
Calibration Set
   ↓
Compare Human Labels
   ↓
Quality Gate
```

---

# 20. Evaluation Cascade

推荐：

```text
Production Trace
       ↓
Deterministic Evaluator
       ↓
Small LLM Judge
       ↓
Uncertain?
       ↓
Strong LLM Judge
       ↓
Still Uncertain?
       ↓
Human
```

原则：

> 能确定性判断的问题，不使用 LLM。

---

# 21. Online Evaluation

支持：

```yaml
OnlineEvaluation:
  mode: enum
  task_source: string
  sampling_rate: float
  evaluation_policy_id: string
```

Mode：

```text
AB
SHADOW
INSPECTION
```

---

# 22. Monitoring Integration

Evaluation Platform 不负责基础 Monitoring。

Observability 提供：

```text
Trace
Metrics
Logs
Tool Metrics
Skill Metrics
Token
Cost
Latency
Errors
```

Evaluation Platform 消费：

```text
Monitoring Signal
       ↓
Case Mining
```

---

# 23. Case Mining

Case Mining 支持：

```text
User Feedback
Production Sampling
Random Sampling
Rule Trigger
Evaluation Failure
```

推荐流程：

```text
Signal
 ↓
Candidate Case
 ↓
Deduplication
 ↓
Trace Enrichment
 ↓
Case Classification
 ↓
Attribution
```

---

# 24. Diagnosis

Diagnosis API：

```text
diagnose(case_id)
```

输出：

```yaml
DiagnosisResult:
  category: string
  component: string
  evidence: []
  confidence: float
  recommended_action: string
```

Recommended Action：

```text
AGENT_FIX
EVAL_FIX
ADD_COVERAGE
HUMAN_REVIEW
```

---

# 25. Evaluation Evolution

当：

```text
Case → COVERAGE_GAP
```

执行：

```text
Create Task
 ↓
Add Evaluation Asset
 ↓
Add Rubric
 ↓
Add Process Criterion
 ↓
Run Regression
```

当：

```text
Case → EVALUATION_FAILURE
```

执行：

```text
Update Rubric / Evaluator
 ↓
Calibration
 ↓
Regression
 ↓
Release
```

---

# 26. Agent Evolution Integration

当：

```text
Case → AGENT_FAILURE
```

进入：

```text
Agent Change
 ↓
Offline Evaluation
 ↓
Regression Set
 ↓
Quality Gate
 ↓
Release
 ↓
AB
```

Evaluation Platform 不直接修改 Agent。

它提供：

```text
Evaluation Result
Diagnosis
Regression Cases
Quality Gate
```

---

# 27. Quality Gate

```yaml
QualityGate:
  id: string
  rules: []
  action: enum
```

Rule：

```yaml
GateRule:
  rubric_id: string
  type: enum
  threshold: object
  severity: enum
```

Severity：

```text
BLOCK
WARN
INFO
```

例如：

```text
Safety Violation → BLOCK
Data Accuracy    → BLOCK
Core Success     → BLOCK / THRESHOLD
Experience       → WARN
Challenge        → INFO
```

---

# 28. Coverage

系统需要支持：

```text
Task Coverage
Scenario Coverage
Rubric Coverage
Skill Coverage
Tool Coverage
Failure Coverage
Process Coverage
User Distribution Coverage
```

Coverage Gap 必须能够形成新的 Evaluation Asset。

---

# 29. Drift

支持：

```yaml
EvaluationDrift:
  dimension: string
  production_distribution: object
  evaluation_distribution: object
  divergence: float
  detected_at: datetime
```

重点检测：

* 新任务类型
* 新用户群
* 新 Skill
* 新 Tool
* 新失败模式
* 新业务目标

---

# 30. Business Metric Bridge

支持：

```yaml
BusinessMetricMapping:
  evaluation_metric: string
  business_metric: string
  relationship: string
  measurement_method: string
```

例如：

```text
Task Success
      ↓
Business Completion

Automation Rate
      ↓
Manual Work Reduction

Data Accuracy
      ↓
Decision Accuracy
```

---

# 31. Versioning

以下对象必须支持 Version：

```text
Task
Expected Behavior
Rubric
Evaluator
Judge Prompt
Evaluation Suite
Evaluation Asset
Evaluation Environment
Evaluation Policy
Quality Gate
```

Evaluation Result 必须记录：

```text
Agent Version
Environment Version
Task Version
Rubric Version
Evaluator Version
Judge Version
```

---

# 32. API

API 挂载在平台统一前缀下：`/api/v1/evaluation/...`。

V1 已实现（050-evaluation.md Phase 0-3/5/10）：

```text
POST   /api/v1/evaluation/tasks
GET    /api/v1/evaluation/tasks
GET    /api/v1/evaluation/tasks/{id}

POST   /api/v1/evaluation/suites
GET    /api/v1/evaluation/suites/{id}

POST   /api/v1/evaluation/rubrics
GET    /api/v1/evaluation/rubrics/{id}

POST   /api/v1/evaluation/assets
GET    /api/v1/evaluation/assets

POST   /api/v1/evaluation/environments
GET    /api/v1/evaluation/environments/{id}

POST   /api/v1/evaluation/runs          # 创建并派发（dispatch worker 执行）；
                                        # eager/dev 模式下内联执行完成
GET    /api/v1/evaluation/runs
GET    /api/v1/evaluation/runs/{id}
GET    /api/v1/evaluation/runs/{id}/results
POST   /api/v1/evaluation/runs/{id}/cancel

POST   /api/v1/evaluation/gates
POST   /api/v1/evaluation/gates/check
```

后续 Phase（reserved，spec 目标形态）：

```text
GET    /api/v1/evaluation/results/{id}

POST   /api/v1/evaluation/cases
GET    /api/v1/evaluation/cases/{id}
POST   /api/v1/evaluation/cases/{id}/diagnose

POST   /api/v1/evaluation/evaluators
POST   /api/v1/evaluation/calibration
GET    /api/v1/evaluation/coverage
GET    /api/v1/evaluation/drift
```

---

# 33. Persistence

推荐：

```text
PostgreSQL
├── task
├── expected_behavior
├── evaluation_suite
├── evaluation_asset
├── rubric
├── evaluator
├── evaluation_policy
├── evaluation_run
├── trial
├── outcome
├── evaluation_result
├── case
├── diagnosis
├── quality_gate
└── version
```

Langfuse：

```text
Trace
Observation
Dataset
Experiment
Score
Annotation
```

对象之间通过：

```text
trace_id
dataset_id
experiment_id
```

关联。

V1 集成边界：

* Trace 关联已建立：`Trial.trace_id = Trial.run_id`（Runtime Run id），Langfuse
  trace id = `UUID(run_id).hex`（deepagents-runtime-spec.md section 10），
  Evaluation Result 可回查对应 Trace。
* Score 导出已实现（Phase 2）：`LangfuseScoreExporter`（observability 适配器）
  在 evaluation run 结束后由 dispatch worker 调用，把 EvaluationResult 写为
  对应 Trial trace 上的 Score；不进入 evaluation 执行路径。UNKNOWN/ERROR
  结果不导出数值（section 18）。Dataset / Experiment 的写入导出仍为保留
  能力（TODO）。

异步派发（Phase 2）：

* `POST /evaluation/runs` 只创建并派发；worker 侧 `execute_evaluation_run`
  task 驱动 trial 循环（`EvaluationService.run_evaluation_run`），每个
  Trial 的 Runtime Run 仍走标准 dispatch 路径。
* eager/dev 模式下派发内联执行，API 返回终态 run；broker 模式下返回
  RUNNING，summary 由 worker 完成后写入。

---

# 34. Non-Functional Requirements

## Reproducibility

相同：

```text
Task
Agent Version
Environment Version
```

应该尽可能得到可比较的结果。

## Traceability

每一个 Evaluation Result 必须能够追溯：

```text
Result
 ↓
Trial
 ↓
Trace
 ↓
Agent Execution
```

## Auditability

必须知道：

```text
谁创建了 Rubric
谁修改了 Rubric
谁执行了 Evaluation
谁修改了 Gate
```

## Extensibility

Evaluator 必须 Plugin 化。

---

# 35. Out of Scope

V1 不负责：

* 自研 Trace Storage
* 自研 Metrics Storage
* 自研 LLM Serving
* 自动修改 Prompt
* 自动修改 Skill
* 自动修改 Agent
* 全自动 Evaluation Agent
* 全自动业务指标归因

这些属于：

```text
Observability
Model Platform
Agent Platform
Evolution Platform
```

---

# 36. Final Contract

Evaluation Platform 对外提供的核心能力：

```text
Evaluate
Diagnose
Regress
Gate
Evolve Evaluation Assets
```

而不是：

```text
Build Agent
Run Tool
Store Trace
Serve Model
```

最终形成：

```text
Agent Runtime
      ↓
Observability
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
```
