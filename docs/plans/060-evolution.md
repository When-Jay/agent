# Evolution Platform Implementation Plan

## 1. Goal

Implement the Evolution Platform as a controlled optimization layer on top of the existing:

* Runtime
* Observability
* Evaluation
* Versioning
* Configuration management

The implementation prioritizes a reliable offline Evolution loop before introducing automatic production evolution.

---

# 2. Implementation Principles

1. Evaluation is reused rather than reimplemented.
2. Runtime is reused rather than modified for Evolution-specific logic.
3. Candidate changes are immutable.
4. Experiments are isolated from production.
5. Optimization and validation datasets are separated.
6. Human approval is required for initial production release.
7. Every accepted change must have evidence.
8. Evolution must remain target-agnostic at the orchestration layer.
9. Target-specific optimization logic lives in optimizer plugins.

---

# 3. Phase 0 — Foundation

## Goal

Establish the Evolution domain and dependency boundaries.

### Deliverables

```text
EvolutionTask
EvolutionTarget
EvolutionStrategy
EvolutionCandidate
EvolutionRun
EvolutionDecision
```

### Work

* create domain models
* create repositories
* create service interfaces
* define lifecycle states
* define error model
* define version references
* define event model

### Acceptance

* domain models can be created and persisted
* lifecycle transitions are validated
* Evolution cannot directly mutate production configuration

---

# 4. Phase 1 — Evolution Target and Versioning

## Goal

Provide a stable versioned configuration abstraction.

### Initial Targets

* Prompt
* Skill
* RAG configuration

Tool, Policy and Model targets can follow.

### Work

* target registry
* version registry
* immutable version storage
* parent version relationship
* diff generation
* rollback metadata

### Acceptance

```text
skill:v12
   ↓
candidate
   ↓
skill:v13
```

must be fully traceable.

---

# 5. Phase 2 — Strategy Framework

## Goal

Introduce pluggable evolution strategies.

### Strategy V1

```text
SingleCandidate
MultiCandidate
```

### Strategy Interface

```python
class EvolutionStrategy:
    async def generate(
        self,
        task,
        target,
        context,
    ) -> list[Candidate]:
        ...
```

### Strategy Configuration

```yaml
candidate_count: 4
max_iterations: 2

edit_budget:
  max_edits: 3
```

### Acceptance

A single EvolutionTask can run different strategies without changing the Evolution Manager.

---

# 6. Phase 3 — Optimizer Framework

## Goal

Create target-specific optimizer plugins.

### Optimizer Interface

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

### First Optimizer

Implement:

```text
SkillOptimizer
```

because Skill optimization has a clear bounded-text-edit model.

### Next

```text
PromptOptimizer
RAGOptimizer
ToolOptimizer
ModelSelector
PolicyOptimizer
```

---

# 7. Phase 4 — Candidate Patch Engine

## Goal

Prevent unrestricted rewriting.

### Patch Operations

```text
ADD
INSERT
REPLACE
DELETE
```

### Validation

Before execution:

```text
Patch
 ↓
Syntax Validation
 ↓
Schema Validation
 ↓
Edit Budget Validation
 ↓
Permission Validation
 ↓
Candidate
```

### Acceptance

Invalid or oversized candidates are rejected before Runtime execution.

---

# 8. Phase 5 — Experiment Engine

## Goal

Execute candidates in isolated evaluation environments.

### Flow

```text
Candidate
    ↓
Environment Snapshot
    ↓
Runtime Execution
    ↓
Trace
    ↓
Outcome
    ↓
Evaluation
```

### Work

* experiment creation
* environment snapshot
* candidate materialization
* Runtime invocation
* trace association
* outcome collection
* cleanup

### Acceptance

The same candidate can be reproduced using the same environment and evaluation assets.

---

# 9. Phase 6 — Evaluation Integration

## Goal

Connect Evolution with the existing Evaluation Platform.

### Evaluation Input

```text
candidate_version
base_version
evaluation_assets
evaluation_policy
environment
```

### Evaluation Output

```text
E2E Result
Process Result
Evaluator Results
Metrics
Failures
Evidence
```

### Important Boundary

Evolution does not implement:

* Rubric
* Evaluator
* E2E evaluation
* Process evaluation
* Dataset execution
* Quality Gate logic

It calls Evaluation APIs.

---

# 10. Phase 7 — Candidate Selection

## Goal

Implement constrained candidate selection.

### Selection Pipeline

```text
Candidates
   ↓
Hard Constraint Filter
   ↓
Regression Filter
   ↓
Challenge Filter
   ↓
Objective Comparison
   ↓
Best Valid Candidate
```

### Initial Selection

Support:

* threshold filtering
* maximize
* minimize
* lexicographic objectives

Avoid a single opaque weighted score in V1.

---

# 11. Phase 8 — Evolution Gate

## Goal

Prevent unsafe or regressive changes.

### Required Gates

```text
Safety
Regression
Challenge
Edit Budget
Risk Policy
```

### Example

```yaml
required:
  safety_pass_rate: 1.0
  regression_pass_rate: 0.99
  challenge_pass_rate: 0.95
```

### Acceptance

Any failed mandatory gate prevents automatic acceptance.

---

# 12. Phase 9 — Human Approval

## Goal

Introduce controlled release.

### Flow

```text
Evolution
    ↓
Evaluation
    ↓
Gate
    ↓
Human Review
    ↓
Approve
    ↓
Create Version
```

### UI Information

The reviewer should see:

* base version
* candidate patch
* changed sections
* triggering cases
* diagnosis
* evaluation result
* regression result
* challenge result
* gate result
* expected impact

This allows approval based on evidence rather than only a score.

---

# 13. Phase 10 — Version Release and Rollback

## Goal

Create production-ready versions.

### Work

* version creation
* version lineage
* deployment integration
* deployment status
* rollback
* audit event

### Acceptance

```text
v12 → v13
```

can be reverted to:

```text
v12
```

without re-running Evolution.

---

# 14. Phase 11 — Evolution Evidence

## Goal

Make Evolution explainable and auditable.

### Evidence

```text
Trigger Case
Diagnosis
Base Version
Candidate Patch
Evaluation Assets
Evaluation Results
Gate Result
Decision
```

### Query Paths

Support:

```text
Version → EvolutionRun
EvolutionRun → Cases
Case → EvolutionTask
Candidate → Evaluation
Decision → Gate
```

---

# 15. Phase 12 — Rejected Candidate Buffer

## Goal

Retain rejected optimization attempts.

### Work

* store rejected candidates
* store rejection reason
* store evaluation result
* store gate result
* expose historical candidates to optimizers

Potential future use:

```text
Previous Rejected Candidate
        ↓
New Evolution Task
        ↓
Optimizer learns what not to repeat
```

This should remain a data source in V1 rather than an automatic learning mechanism.

---

# 16. Phase 13 — Iterative Evolution

## Goal

Support multi-round optimization.

```text
Iteration 1
Generate → Evaluate → Select

Iteration 2
Generate → Evaluate → Select

Iteration 3
...
```

### Stop Conditions

* no improvement
* objective threshold reached
* maximum iterations
* budget exhausted
* regression failure
* safety failure

### Initial Limit

Recommended:

```text
max_iterations = 2~3
```

Avoid uncontrolled optimizer loops.

---

# 17. Phase 14 — Agent Evolution

## Goal

Expand from Skill optimization to Agent configuration.

### Target Order

```text
1. Skill
2. Prompt
3. RAG
4. Tool
5. Model
6. Policy
```

### Reason

Start with text/configuration targets that are:

* easier to diff
* easier to validate
* easier to rollback
* lower operational risk

---

# 18. Phase 15 — Evaluation Evolution

## Goal

Allow the evaluation system itself to evolve.

### Targets

```text
Rubric
Evaluator
Dataset
Threshold
Evaluation Policy
```

### Trigger Examples

```text
High UNKNOWN Rate
Low Human Agreement
Production Failure Not Covered
Evaluator Disagreement
Business Metric Mismatch
```

### Flow

```text
Evaluation Failure
       ↓
Evaluation Diagnosis
       ↓
Evaluation Evolution Task
       ↓
Candidate
       ↓
Validation
       ↓
Evaluation Version
```

Evaluation Evolution must use the same governance framework as Agent Evolution.

---

# 19. Phase 16 — Online Evolution Integration

## Goal

Connect production cases to Evolution.

```text
Production
   ↓
Observability
   ↓
Case Mining
   ↓
Diagnosis
   ↓
Evolution Task
```

Candidate evaluation initially remains offline.

Later:

```text
Offline
   ↓
Shadow
   ↓
A/B
   ↓
Production
```

---

# 20. Phase 17 — Automatic Low-risk Release

## Goal

Enable controlled automatic deployment.

Only allow auto-release when:

```text
Risk = LOW
AND
Safety = PASS
AND
Regression = PASS
AND
Challenge = PASS
AND
Edit Budget = PASS
AND
Quality Gate = PASS
```

High-risk targets continue to require human approval.

---

# 21. Phase 18 — Evolution Agent

## Goal

Automate the Evolution orchestration layer.

Potential capabilities:

```text
Case Selection
Diagnosis
Task Generation
Target Selection
Strategy Selection
Candidate Generation
Evaluation
Failure Analysis
Recommendation
```

The Evolution Agent must use platform APIs and cannot bypass:

* Candidate validation
* Evaluation
* Quality Gate
* Approval
* Versioning

---

# 22. Recommended Repository Structure

```text
src/
├── evolution/
│   ├── domain/
│   │   ├── task.py
│   │   ├── target.py
│   │   ├── strategy.py
│   │   ├── candidate.py
│   │   ├── run.py
│   │   └── decision.py
│   │
│   ├── application/
│   │   ├── task_service.py
│   │   ├── evolution_service.py
│   │   ├── candidate_service.py
│   │   └── release_service.py
│   │
│   ├── strategy/
│   │   ├── base.py
│   │   ├── single_candidate.py
│   │   └── multi_candidate.py
│   │
│   ├── optimizer/
│   │   ├── base.py
│   │   ├── skill_optimizer.py
│   │   └── prompt_optimizer.py
│   │
│   ├── experiment/
│   │   ├── runner.py
│   │   └── environment.py
│   │
│   ├── governance/
│   │   ├── edit_budget.py
│   │   ├── gate.py
│   │   ├── approval.py
│   │   └── policy.py
│   │
│   └── repository/
│       ├── task_repository.py
│       ├── run_repository.py
│       ├── candidate_repository.py
│       └── version_repository.py
```

---

# 23. Testing Strategy

## Unit Tests

Test:

* patch validation
* edit budget
* objective comparison
* gate evaluation
* lifecycle transitions
* strategy behavior

## Integration Tests

Test:

```text
Evolution
 → Runtime
 → Observability
 → Evaluation
 → Gate
```

## Regression Tests

Maintain a fixed Evolution benchmark containing:

* known failures
* known good cases
* regression cases
* challenge cases

## Safety Tests

Verify that:

* invalid patches are rejected
* unauthorized targets cannot be modified
* failed gates cannot release
* failed experiments cannot affect production
* rollback works

---

# 24. MVP

The first usable Evolution MVP should contain only:

```text
EvolutionTask
EvolutionTarget
EvolutionStrategy
EvolutionCandidate
EvolutionRun
EvolutionDecision

SkillOptimizer
Patch Engine
Experiment Engine
Evaluation Integration
Quality Gate
Human Approval
Versioning
Rollback
Evidence
```

End-to-end flow:

```text
Bad Case
   ↓
Diagnosis
   ↓
Evolution Task
   ↓
Skill Optimizer
   ↓
3 Candidates
   ↓
Offline Experiment
   ↓
Evaluation
   ↓
Regression + Challenge
   ↓
Human Approval
   ↓
New Skill Version
```

This is sufficient to demonstrate the complete Evolution loop.

---

# 25. Second Stage

Add:

```text
Prompt Optimizer
RAG Optimizer
Iterative Search
Rejected Candidate Buffer
Online Case Mining
Shadow Evaluation
Automatic Low-risk Release
```

---

# 26. Third Stage

Add:

```text
Evaluation Evolution
Model Selection
Tool Optimization
Policy Optimization
A/B Experiment
Continuous Evolution
Evolution Agent
```

---

# 27. Implementation Dependency

Recommended dependency order:

```text
Foundation
    ↓
Versioning
    ↓
Target
    ↓
Strategy
    ↓
Optimizer
    ↓
Patch Engine
    ↓
Experiment
    ↓
Evaluation Integration
    ↓
Selection
    ↓
Gate
    ↓
Approval
    ↓
Release / Rollback
    ↓
Evidence
    ↓
Iterative Evolution
    ↓
Online Evolution
```

---

# 28. Architecture Validation

After implementation, validate the following boundaries.

### Runtime

> Does Runtime execute the candidate?

### Observability

> Does Observability record the experiment?

### Evaluation

> Does Evaluation determine whether the candidate is good?

### Evolution

> Does Evolution determine what to try and which candidate to select?

### Governance

> Does Governance determine whether the change may be released?

If any component violates these boundaries, the implementation should be reconsidered.

---

# 29. Final MVP Acceptance Criteria

The MVP is complete when the following scenario works end-to-end:

```text
Production Bad Case
        ↓
Diagnosis
        ↓
Evolution Task
        ↓
Target: Skill v12
        ↓
Multi Candidate Strategy
        ↓
C1 / C2 / C3
        ↓
Sandboxed Experiment
        ↓
Evaluation
        ↓
Hard Constraint Filter
        ↓
Regression / Challenge
        ↓
Candidate Selection
        ↓
Human Approval
        ↓
Skill v13
        ↓
Deployment
        ↓
Evidence + Audit
```

And:

```text
Skill v13
    ↓
Rollback
    ↓
Skill v12
```

must work without regenerating or reevaluating the candidate.

---

# 30. Engineering Principle

The first version should not attempt to build an autonomous self-improving Agent.

The correct implementation sequence is:

> **Controlled Evolution → Measurable Evolution → Governed Evolution → Partially Automated Evolution → Continuous Evolution**

The most important MVP is therefore not the optimizer itself.

It is the complete and trustworthy loop:

> **Case → Diagnosis → Candidate → Experiment → Evaluation → Gate → Decision → Version → Evidence**
