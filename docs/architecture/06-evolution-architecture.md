# Evolution Platform Architecture

## 1. Overview

Evolution Platform is the controlled evolution layer of the AI Agent & Workflow Platform.

Its responsibility is not to directly "let an LLM modify prompts", but to provide a controlled mechanism for:

* discovering optimization opportunities
* defining evolution targets
* generating candidate changes
* running experiments
* evaluating candidates
* applying quality gates
* selecting and releasing new versions
* retaining decision evidence
* supporting rollback and future continuous evolution

The core principle is:

> **Evolution = Search + Experiment + Evaluation + Selection + Governance**

The platform does not define what "good" means. Evaluation owns quality definitions, evaluators, rubrics, datasets and quality gates.

Evolution determines:

> **What should we try, how should we try it, and which candidate should be accepted?**

---

## 2. Design Principles

### 2.1 Evaluation-driven Evolution

Evolution must be constrained by Evaluation.

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
    ↓
Candidate Generation
    ↓
Experiment
    ↓
Evaluation
    ↓
Quality Gate
    ↓
Decision
    ↓
New Version
```

Evolution must never treat its own optimizer output as the final quality signal.

---

### 2.2 Diagnosis-driven Target Selection

Evolution should not blindly modify prompts or skills.

Diagnosis determines the likely evolution target.

```text
Diagnosis
    │
    ├── Prompt Failure
    │       → Prompt Optimizer
    │
    ├── Skill Failure
    │       → Skill Optimizer
    │
    ├── Retrieval Recall Failure
    │       → RAG Optimizer
    │
    ├── Tool Selection Failure
    │       → Tool / Skill / Policy Optimizer
    │
    ├── Tool Argument Failure
    │       → Tool Schema / Prompt Optimizer
    │
    ├── Context Failure
    │       → Context Optimizer
    │
    ├── Model Capability Failure
    │       → Model Selector
    │
    ├── Cost Excessive
    │       → Model / Context / Tool Optimizer
    │
    └── Latency Excessive
            → Model / Parallelism / Tool Optimizer
```

The mapping is explicit and versioned.

---

### 2.3 Candidate-based Evolution

An optimizer should not directly overwrite production configuration.

It generates an `EvolutionCandidate`.

```text
Base Version
    ↓
Optimizer
    ↓
Candidate Patch
    ↓
Experiment
    ↓
Evaluation
    ↓
Gate
    ↓
New Version
```

A candidate represents a proposed change rather than a released version.

---

### 2.4 Bounded Modification

Evolution should prefer small, explainable modifications over unrestricted rewriting.

Supported operations should include:

* ADD
* INSERT
* REPLACE
* DELETE

Each evolution strategy can define:

* maximum number of edits
* maximum token increase
* maximum token decrease
* editable sections
* allowed operations
* forbidden sections

Example:

```yaml
edit_budget:
  max_edits: 3
  max_tokens_added: 200
  max_tokens_removed: 100

allowed_operations:
  - replace
  - insert

allowed_sections:
  - tool_selection
  - tool_usage
```

This reduces destructive changes and makes regression analysis easier.

---

## 3. High-level Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                    Evolution Platform                       │
│                                                             │
│  ┌─────────────────┐      ┌─────────────────────────────┐  │
│  │ Evolution       │      │ Evolution Planner           │  │
│  │ Manager         │─────▶│ Diagnosis → Target →       │  │
│  │                 │      │ Objective → Strategy       │  │
│  └─────────────────┘      └──────────────┬──────────────┘  │
│                                          │                 │
│                                          ▼                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                    Optimizer Layer                    │  │
│  │                                                      │  │
│  │ Prompt │ Skill │ RAG │ Tool │ Policy │ Model         │  │
│  └──────────────────────────┬───────────────────────────┘  │
│                             │                              │
│                             ▼                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                 Experiment Engine                    │  │
│  │                                                      │  │
│  │ Candidate → Rollout → Trial → Evaluation → Selection│  │
│  └──────────────────────────┬───────────────────────────┘  │
│                             │                              │
│                             ▼                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Evolution Governance                    │  │
│  │                                                      │  │
│  │ Constraint │ Holdout │ Gate │ Approval │ Rollback    │  │
│  │ Version    │ Evidence│ Audit│ Policy   │             │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────┘
                               │
             ┌─────────────────┼──────────────────┐
             ▼                 ▼                  ▼
       Observability       Evaluation         Runtime
             │                 │                  │
             │                 │                  │
        Trace / Case       Rubric / Eval      Agent / Workflow
        Diagnosis          Dataset / Gate     / Tool / Skill
```

---

## 4. Relationship with Evaluation

Evolution and Evaluation are separate systems with a strict dependency boundary.

### Evaluation owns

* Task
* Expected Behavior
* Evaluation Asset
* Rubric
* Evaluator
* Evaluation Policy
* Trial Evaluation
* E2E Evaluation
* Process Evaluation
* Case
* Diagnosis
* Quality Gate
* Evaluation Result

### Evolution owns

* Evolution Task
* Evolution Target
* Evolution Strategy
* Evolution Candidate
* Evolution Run
* Evolution Decision
* Optimizer
* Experiment orchestration
* Candidate search
* Evolution evidence
* Evolution lifecycle

The dependency is:

```text
Evolution
    │
    ├── consumes Evaluation
    │      ├── Evaluation Policy
    │      ├── Evaluation Asset
    │      ├── Evaluation Result
    │      └── Quality Gate
    │
    └── produces
           ├── Candidate
           ├── Evolution Result
           └── New Version
```

Evolution must not reimplement the Evaluation engine.

---

## 5. Evolution Domain Model

### 5.1 EvolutionTask

`EvolutionTask` describes the problem to be solved.

It answers:

> Why should the system evolve?

Example:

```yaml
id: EV-TASK-1024

trigger:
  type: production_bad_case

diagnosis:
  type: TOOL_SELECTION_FAILURE

target:
  type: SKILL
  resource_id: search_skill

objective:
  primary:
    - tool_success_rate

constraints:
  - safety_pass_rate >= 100%
  - regression_pass_rate >= 99%
  - max_edit_count <= 3
```

EvolutionTask is a problem definition, not an experiment.

---

### 5.2 EvolutionTarget

`EvolutionTarget` identifies the resource that can be changed.

Supported target types:

* Prompt
* Skill
* RAG Configuration
* Tool Configuration
* Agent Policy
* Model Configuration
* Context Configuration

Each target must be versioned.

```text
search_skill:v12
       │
       ▼
Evolution Candidate
       │
       ▼
search_skill:v13
```

The base version must be immutable during an EvolutionRun.

---

### 5.3 EvolutionStrategy

`EvolutionStrategy` defines how candidates are searched.

Examples:

* Single Candidate
* Multi Candidate
* Iterative Search
* Population-based Search

A strategy defines:

* optimizer
* candidate count
* generation model
* edit budget
* iteration count
* selection strategy
* stop condition

Example:

```yaml
strategy:
  type: MULTI_CANDIDATE
  candidate_count: 4
  optimizer: skill_optimizer

  edit_budget:
    max_edits: 3

  iterations: 2
```

---

### 5.4 EvolutionCandidate

A candidate is a proposed modification.

```text
EvolutionCandidate
├── base_version
├── patch
├── reason
├── evidence
├── expected_impact
├── constraints
└── metadata
```

A candidate is not a production version.

It becomes a version only after successful evaluation and gate approval.

---

### 5.5 EvolutionRun

`EvolutionRun` represents one concrete evolution experiment.

```text
EvolutionRun
├── task
├── target
├── strategy
├── candidates
├── experiments
├── evaluations
├── gate
├── decision
└── evidence
```

A single EvolutionTask may result in multiple EvolutionRuns.

This allows repeated experiments using different strategies or updated evaluation assets.

---

### 5.6 EvolutionDecision

`EvolutionDecision` records the final decision.

Supported decisions:

* ACCEPT
* REJECT
* HUMAN_REVIEW

The decision must include:

* selected candidate
* rejected candidates
* gate result
* evaluation summary
* decision evidence
* approval information when required

The system must be able to answer:

> Why was this candidate accepted?

---

## 6. Evolution Objectives

Evolution objectives are divided into hard constraints and optimization objectives.

### Hard Constraints

Examples:

```text
Safety Pass Rate = 100%
Must-Pass Set = 100%
Regression Pass Rate >= 99%
```

Candidates violating hard constraints must be rejected regardless of other improvements.

### Optimization Objectives

Examples:

```text
Task Success ↑
Tool Success Rate ↑
Cost ↓
Latency ↓
Token Usage ↓
Tool Calls ↓
```

The platform should not reduce all objectives to a single opaque score.

The recommended selection process is:

```text
Candidate
    ↓
Hard Constraint Filter
    ↓
Multi-objective Comparison
    ↓
Selection
```

---

## 7. Evaluation Asset Usage

Evolution requires different evaluation assets for different stages.

```text
Evaluation Asset
│
├── Purpose
│   ├── Optimization
│   ├── Validation
│   ├── Regression
│   ├── Challenge
│   ├── Calibration
│   └── Inspection
│
└── Quality
    ├── Golden
    ├── Good
    ├── Bad
    ├── Uncertain
    └── Uncovered
```

Recommended evolution flow:

```text
Optimization Set
      ↓
Candidate Generation
      ↓
Validation Set
      ↓
Regression Set
      ↓
Challenge Set
      ↓
Production / Shadow
```

Optimization data must not automatically become the final acceptance set.

---

## 8. Experiment Engine

The Experiment Engine executes candidates under controlled conditions.

```text
Candidate
    ↓
Environment Snapshot
    ↓
Trial / Rollout
    ↓
Trace
    ↓
Evaluation
    ↓
Result
```

The experiment must capture:

* base version
* candidate version
* environment
* model
* prompt
* skill
* RAG configuration
* tool configuration
* evaluation assets
* execution trace
* outcome
* cost
* latency
* evaluation result

This guarantees reproducibility.

---

## 9. Iterative Evolution

Evolution supports iterative optimization.

```text
Evolution Run
│
├── Iteration 1
│    ├── Generate
│    ├── Evaluate
│    └── Select
│
├── Iteration 2
│    ├── Generate
│    ├── Evaluate
│    └── Select
│
└── Final Gate
```

A maximum iteration count and stop condition must be configured.

Possible stop conditions:

* no improvement
* objective threshold reached
* maximum iterations
* budget exhausted
* regression failure
* safety violation
* no valid candidate

---

## 10. Governance

Evolution must be governed by explicit constraints.

### Edit Governance

* edit count
* token budget
* editable sections
* allowed operations

### Evaluation Governance

* required evaluation sets
* minimum sample size
* required evaluators
* holdout policy

### Release Governance

* quality gates
* approval policy
* risk level
* automatic/manual deployment

### Version Governance

* immutable versions
* lineage
* rollback
* audit trail

---

## 11. Automatic Evolution Levels

### V1 — Human Approval

```text
Generate
 → Evaluate
 → Gate
 → Human Approval
 → Deploy
```

### V2 — Low-risk Automatic Release

Candidates can be automatically released when:

* all hard constraints pass
* regression passes
* challenge set passes
* change scope is within limits
* target is low risk

### V3 — Continuous Evolution

```text
Production
 → Case Mining
 → Diagnosis
 → Evolution
 → Offline Evaluation
 → Shadow
 → A/B
 → Production
```

High-risk changes should continue to require explicit approval.

---

## 12. Agent Evolution and Evaluation Evolution

The platform contains two evolution loops.

### Agent Evolution

Changes the Agent itself:

```text
Prompt
Skill
RAG
Tool
Model
Context
Policy
```

### Evaluation Evolution

Changes the evaluation system:

```text
Rubric
Evaluator
Dataset
Threshold
Sampling Policy
Evaluation Policy
```

The two loops share the same Case Mining and Diagnosis infrastructure.

```text
                 Production
                     ↓
                Case Mining
                     ↓
                  Diagnosis
                 /         \
                ↓           ↓
       Agent Evolution   Eval Evolution
                \           /
                 ↓         ↓
                  Evaluation
                       ↓
                      Gate
```

This prevents the system from continuously optimizing an incorrect evaluation objective.

---

## 13. Evolution Evidence

Every accepted or rejected evolution must produce evidence.

```text
EvolutionResult
├── previous_version
├── new_version
├── changed_components
├── trigger_cases
├── diagnosis
├── candidate
├── evaluation_report
├── regression_result
├── challenge_result
├── gate_result
└── decision_evidence
```

Example:

```text
Task Success       72% → 81%
Tool Success       83% → 91%
Regression          99.4%
Safety              100%
Challenge           98.7%
Cost                -8%

Decision:
ACCEPT
```

The evidence becomes part of the version lineage and audit trail.

---

## 14. Version and Rollback

Every evolved artifact must support:

```text
Version
├── parent_version
├── source_candidate
├── evolution_run
├── evaluation_result
├── approval
└── deployment_status
```

Rollback should restore the previous known-good version without rerunning evolution.

---

## 15. Failure Handling

Evolution failures include:

* optimizer failure
* candidate generation failure
* invalid patch
* experiment failure
* evaluation failure
* regression failure
* challenge failure
* gate failure
* deployment failure

A failed EvolutionRun must not modify production state.

Rejected candidates should remain available as historical evidence and may optionally be stored in a rejected-candidate buffer for future analysis.

---

## 16. Architecture Boundary

Evolution is not responsible for:

* executing Agent loops
* executing Workflow graphs
* executing tools
* implementing sandbox
* storing raw traces
* implementing the core evaluation engine
* defining business metrics

Its responsibility ends at:

```text
Candidate
 → Experiment
 → Evaluation
 → Decision
 → Version
```

Runtime executes.

Observability records.

Evaluation judges.

Evolution searches and changes.

---

## 17. Future Evolution Agent

A future Evaluation/Evolution Agent may automate:

```text
Select Cases
    ↓
Diagnose
    ↓
Create Evolution Task
    ↓
Select Target
    ↓
Select Strategy
    ↓
Generate Candidates
    ↓
Run Evaluation
    ↓
Analyze Results
    ↓
Recommend Decision
```

The Evolution Agent should initially operate as an orchestrator over deterministic platform APIs rather than bypassing Evolution governance.

---

## 18. Core Principle

The platform does not pursue:

> Agent → Self Modify

It implements:

> **Observe → Diagnose → Generate Candidate → Experiment → Evaluate → Gate → Evolve**

Evolution is therefore a controlled search problem constrained by Evaluation.
