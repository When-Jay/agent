# Evolution Platform Specification

## 1. Purpose

This specification defines the functional and non-functional requirements of the Evolution Platform.

The platform provides controlled optimization of Agent and Workflow configuration based on production evidence and evaluation results.

---

# 2. Scope

## 2.1 In Scope

* Evolution Task management
* Evolution Target management
* Evolution Strategy
* Candidate generation
* Candidate validation
* Experiment execution
* Evaluation integration
* Candidate selection
* Quality gates
* Human approval
* Version management
* Rollback
* Evolution evidence
* Agent Evolution
* Evaluation Evolution
* Evolution history

## 2.2 Out of Scope

V1 does not implement:

* Agent Runtime
* Workflow Runtime
* Sandbox
* Trace storage
* Full Evaluation engine
* Automatic unrestricted production modification
* Autonomous model training
* Reinforcement learning
* Parameter-level LLM fine-tuning

---

# 3. Domain Model

## 3.1 EvolutionTask

```text
EvolutionTask
├── id
├── name
├── description
├── trigger
├── diagnosis
├── target
├── objective
├── constraints
├── evaluation_assets
├── strategy
├── risk_level
├── status
├── created_at
└── created_by
```

### Status

```text
DRAFT
READY
RUNNING
COMPLETED
FAILED
CANCELLED
```

---

# 4. EvolutionTarget

```text
EvolutionTarget
├── type
├── resource_id
├── base_version
├── editable_scope
└── risk_level
```

### Target Types

```text
PROMPT
SKILL
RAG
TOOL
POLICY
MODEL
CONTEXT
```

The target version must be immutable during an EvolutionRun.

---

# 5. EvolutionObjective

Objectives consist of hard constraints and optimization objectives.

```text
EvolutionObjective
├── hard_constraints[]
└── optimization_objectives[]
```

Example:

```yaml
hard_constraints:
  - metric: safety_pass_rate
    operator: ">="
    threshold: 1.0

  - metric: regression_pass_rate
    operator: ">="
    threshold: 0.99

optimization_objectives:
  - metric: task_success_rate
    direction: maximize

  - metric: cost
    direction: minimize
```

---

# 6. EvolutionStrategy

```text
EvolutionStrategy
├── type
├── optimizer
├── candidate_count
├── max_iterations
├── edit_budget
├── selection_strategy
├── stop_condition
└── generation_model
```

### Strategy Types

```text
SINGLE_CANDIDATE
MULTI_CANDIDATE
ITERATIVE
POPULATION
```

Population-based optimization is not required for V1.

---

# 7. EvolutionCandidate

```text
EvolutionCandidate
├── id
├── task_id
├── base_version
├── patch
├── reason
├── evidence
├── expected_impact
├── edit_summary
├── validation_status
└── created_at
```

## 7.1 Patch

```text
Patch
├── operation
├── path
├── old_value
└── new_value
```

Supported operations:

```text
ADD
INSERT
REPLACE
DELETE
```

---

# 8. Edit Budget

Each optimizer must respect the configured edit budget.

```text
EditBudget
├── max_edits
├── max_tokens_added
├── max_tokens_removed
├── allowed_operations
└── allowed_sections
```

A candidate violating the budget must be rejected before experiment execution.

---

# 9. EvolutionRun

```text
EvolutionRun
├── id
├── task_id
├── target
├── strategy
├── base_version
├── candidates[]
├── experiment_ids[]
├── evaluation_ids[]
├── gate_result
├── decision
├── status
├── started_at
└── completed_at
```

### Run Lifecycle

```text
CREATED
   ↓
PLANNING
   ↓
GENERATING
   ↓
EXPERIMENTING
   ↓
EVALUATING
   ↓
SELECTING
   ↓
GATING
   ↓
DECIDING
   ↓
COMPLETED
```

Failure can occur at any stage.

---

# 10. Experiment

An Experiment executes one candidate against a defined environment and evaluation asset set.

```text
Experiment
├── candidate_id
├── environment_id
├── evaluation_policy_id
├── dataset_ids
├── trial_ids
├── trace_ids
├── outcome_ids
└── result
```

Experiments must capture enough information to reproduce the evaluation.

---

# 11. Evaluation Integration

Evolution invokes the Evaluation Platform through a stable interface.

```text
Evolution
    ↓
Evaluation Request
    ↓
Evaluation Platform
    ↓
Evaluation Result
```

The request should contain:

```text
EvaluationRequest
├── candidate_version
├── base_version
├── task_ids / dataset_ids
├── evaluation_policy
├── environment
└── execution_context
```

The result should contain:

```text
EvaluationResult
├── e2e_result
├── process_result
├── evaluator_results
├── metrics
├── failures
└── evidence
```

---

# 12. Evaluation Set Separation

Evolution must support separate asset purposes:

```text
OPTIMIZATION
VALIDATION
REGRESSION
CHALLENGE
```

Recommended behavior:

* Optimization: candidate generation feedback
* Validation: candidate selection
* Regression: prevent known behavior degradation
* Challenge: test capability boundaries

The same case may be assigned to different purposes through versioned asset configuration.

---

# 13. Candidate Selection

Selection occurs in three stages.

## Stage 1 — Hard Constraint Filter

Reject candidates that violate:

* safety
* must-pass cases
* regression thresholds
* mandatory business constraints

## Stage 2 — Objective Comparison

Compare valid candidates against optimization objectives.

## Stage 3 — Selection

Select:

```text
BEST_VALID_CANDIDATE
```

or:

```text
NO_ACCEPTABLE_CANDIDATE
```

The platform must not select an invalid candidate merely because it has the highest aggregate score.

---

# 14. EvolutionDecision

```text
EvolutionDecision
├── decision
├── selected_candidate_id
├── rejected_candidate_ids
├── gate_result
├── evaluation_summary
├── evidence
├── approval
└── created_at
```

### Decisions

```text
ACCEPT
REJECT
HUMAN_REVIEW
```

---

# 15. Quality Gate

Evolution uses Evaluation Quality Gates.

Example:

```yaml
gate:
  required:
    safety_pass_rate >= 1.0
    regression_pass_rate >= 0.99
    challenge_pass_rate >= 0.95

  optimization:
    task_success_rate:
      improvement: ">0"

    cost:
      direction: minimize
```

Gate configuration belongs to Evaluation Governance.

Evolution only invokes the gate and consumes the result.

---

# 16. Human Approval

High-risk evolution requires approval.

```text
Candidate
    ↓
Evaluation
    ↓
Gate
    ↓
HUMAN_REVIEW
    ↓
Approve / Reject
```

Approval must record:

```text
Approval
├── approver
├── decision
├── reason
└── timestamp
```

---

# 17. Automatic Release Policy

A candidate can be automatically released only if all conditions are satisfied:

```text
Hard Constraints PASS
AND
Regression PASS
AND
Challenge PASS
AND
Edit Budget PASS
AND
Risk Policy PASS
AND
Auto Release Enabled
```

Otherwise:

```text
HUMAN_REVIEW
```

---

# 18. Version Model

Every accepted candidate creates a new immutable version.

```text
Version
├── version_id
├── parent_version
├── candidate_id
├── evolution_run_id
├── evaluation_result
├── approval
├── deployment_status
└── created_at
```

Example:

```text
skill:v12
   │
   └── EvolutionRun: EV-1024
            │
            └── Candidate:C2
                    │
                    ▼
                skill:v13
```

---

# 19. Rollback

Rollback restores a previous known-good version.

Rollback must not:

* regenerate a candidate
* rerun evaluation
* mutate historical versions

Rollback creates a deployment event referencing the selected historical version.

---

# 20. Evidence

Each EvolutionRun must retain:

```text
Evidence
├── Trigger Cases
├── Diagnosis
├── Base Version
├── Candidate Patch
├── Evaluation Assets
├── Evaluation Results
├── Regression Results
├── Challenge Results
├── Gate Result
└── Final Decision
```

Evidence must be queryable by:

* EvolutionTask
* EvolutionRun
* Version
* Candidate
* Case
* Evaluation Result

---

# 21. Rejected Candidates

Rejected candidates should be retained.

```text
RejectedCandidate
├── candidate
├── rejection_reason
├── evaluation_result
├── gate_result
└── timestamp
```

This provides historical knowledge and may support future optimizer strategies.

---

# 22. Agent Evolution

V1 supports the following target types:

```text
PROMPT
SKILL
RAG
TOOL
MODEL
POLICY
```

Each target type has its own optimizer.

```text
Optimizer Registry
├── PromptOptimizer
├── SkillOptimizer
├── RAGOptimizer
├── ToolOptimizer
├── ModelSelector
└── PolicyOptimizer
```

The optimizer interface should be target-independent.

```python
class EvolutionOptimizer:
    async def generate_candidates(
        self,
        task,
        target,
        strategy,
        context,
    ) -> list[Candidate]:
        ...
```

---

# 23. Evaluation Evolution

Evaluation itself can be evolved.

Supported targets:

```text
RUBRIC
EVALUATOR
DATASET
THRESHOLD
EVALUATION_POLICY
```

Examples:

* excessive UNKNOWN rate
* low human-machine agreement
* missing production failure coverage
* evaluator disagreement
* business metric mismatch

Evaluation Evolution must use the same Evolution governance mechanism.

---

# 24. Diagnosis Mapping

The initial mapping should be explicit.

| Diagnosis                   | Target                 | Optimizer             |
| --------------------------- | ---------------------- | --------------------- |
| Prompt Failure              | Prompt                 | Prompt Optimizer      |
| Skill Failure               | Skill                  | Skill Optimizer       |
| Retrieval Recall Failure    | RAG                    | RAG Optimizer         |
| Retrieval Precision Failure | RAG                    | RAG Optimizer         |
| Tool Selection Failure      | Tool / Skill / Policy  | Tool Optimizer        |
| Tool Argument Failure       | Tool / Prompt          | Tool/Prompt Optimizer |
| Context Overflow            | Context                | Context Optimizer     |
| Model Capability Failure    | Model                  | Model Selector        |
| Excessive Cost              | Model / Context / Tool | Cost Optimizer        |
| Excessive Latency           | Model / Tool / Runtime | Performance Optimizer |

The mapping should be configurable and versioned.

---

# 25. API Requirements

## Create Evolution Task

```http
POST /api/evolution/tasks
```

## Start Evolution Run

```http
POST /api/evolution/tasks/{task_id}/runs
```

## List Candidates

```http
GET /api/evolution/runs/{run_id}/candidates
```

## Get Evolution Run

```http
GET /api/evolution/runs/{run_id}
```

## Approve Decision

```http
POST /api/evolution/runs/{run_id}/approve
```

## Reject Decision

```http
POST /api/evolution/runs/{run_id}/reject
```

## Rollback Version

```http
POST /api/evolution/versions/{version_id}/rollback
```

---

# 26. Persistence

Recommended entities:

```text
evolution_tasks
evolution_targets
evolution_strategies
evolution_runs
evolution_candidates
evolution_experiments
evolution_decisions
evolution_versions
evolution_approvals
evolution_evidence
```

Evaluation-specific entities remain owned by Evaluation.

Runtime execution records remain owned by Runtime.

---

# 27. Observability

Every EvolutionRun must emit standard events.

```text
EvolutionStarted
CandidateGenerated
ExperimentStarted
ExperimentCompleted
EvaluationStarted
EvaluationCompleted
GateEvaluated
DecisionCreated
VersionCreated
ApprovalRequired
EvolutionCompleted
EvolutionFailed
```

These events should integrate with the platform's existing Observability system.

---

# 28. Security

Evolution must enforce:

* tenant isolation
* authorization
* target-level permissions
* sensitive configuration protection
* immutable version history
* approval policy
* audit trail
* prompt/skill content access control

Evolution must never bypass Runtime, Tool, Sandbox or Policy security boundaries.

---

# 29. Non-functional Requirements

### Reproducibility

Every experiment must identify its:

* target version
* model version
* prompt version
* skill version
* RAG configuration
* tool configuration
* environment
* evaluation assets

### Idempotency

Starting the same EvolutionRun request repeatedly must not create uncontrolled duplicate releases.

### Traceability

Every released version must be traceable to:

```text
Version
→ Candidate
→ EvolutionRun
→ EvolutionTask
→ Cases
→ Evaluation
```

### Failure Isolation

A failed experiment must not affect production.

---

# 30. Final Contract

Evolution consumes:

```text
Case
Diagnosis
Evaluation Assets
Evaluation Policy
Quality Gate
Runtime Version
```

Evolution produces:

```text
Candidate
Experiment
Decision
Version
Evidence
```

The core contract is:

> **Evolution proposes changes; Evaluation determines quality; Gate determines acceptability; Runtime determines execution.**
