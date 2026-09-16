# AI Runtime Platform - System Architecture

## 1. Purpose

The system is an enterprise-oriented AI application runtime platform.

It provides a unified infrastructure for running:

* Agent applications
* Workflow applications

The platform separates:

* API/control
* Runtime execution
* Tool infrastructure
* Observability
* Evaluation

---

# 2. High-Level Architecture

```text
                         ┌────────────────────┐
                         │     API Layer      │
                         │                    │
                         │ Auth / Application │
                         │ Run / Session      │
                         └─────────┬──────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────┐
│                        AI Runtime                           │
│                                                             │
│  ┌─────────────────────┐   ┌─────────────────────────────┐ │
│  │   Agent Runtime     │   │     Workflow Runtime        │ │
│  │                     │   │                             │ │
│  │ Agent Loop          │   │ Graph Execution             │ │
│  │ Context             │   │ Node Execution               │ │
│  │ Tool Calling        │   │ State                        │ │
│  │ Memory              │   │ Branching                    │ │
│  │ Skill               │   │ Parallel Execution            │ │
│  │ Planning             │   │ HITL                         │ │
│  │ Budget               │   │ Checkpoint                   │ │
│  │ Recovery             │   │ Resume                       │ │
│  └──────────┬──────────┘   └──────────────┬──────────────┘ │
│             │                             │                │
│             └──────────────┬──────────────┘                │
│                            ▼                               │
│                 ┌──────────────────────┐                   │
│                 │    Runtime Core      │                   │
│                 │                      │                   │
│                 │ Application          │                   │
│                 │ Session              │                   │
│                 │ Run                  │                   │
│                 │ State                │                   │
│                 │ Context              │                   │
│                 │ Execution            │                   │
│                 │ Event                │                   │
│                 │ Checkpoint           │                   │
│                 │ Artifact             │                   │
│                 └──────────┬───────────┘                   │
│                            │                               │
│                 ┌──────────▼───────────┐                   │
│                 │ Runtime Capabilities │                   │
│                 │                      │                   │
│                 │ Model                │                   │
│                 │ Tool / MCP           │                   │
│                 │ Knowledge / RAG      │                   │
│                 │ Memory               │                   │
│                 │ Skill                │                   │
│                 │ Workspace / Sandbox  │                   │
│                 │ Policy               │                   │
│                 │ Budget               │                   │
│                 │ HITL                 │                   │
│                 └──────────────────────┘                   │
└─────────────────────────────────────────────────────────────┘

             │                 │                 │
             ▼                 ▼                 ▼
      MCP Gateway         Sandbox Service    Knowledge Service


        ┌─────────────────────┐    ┌─────────────────────┐
        │   Observability     │    │     Evaluation      │
        │                     │    │                     │
        │ Trace               │    │ Dataset             │
        │ Metrics             │    │ Evaluator           │
        │ Logs                │    │ LLM Judge           │
        │ Runtime Events      │    │ Rubric              │
        └─────────────────────┘    └─────────────────────┘
```

---

# 3. Top-Level Components

## 3.1 API Layer

Responsibilities:

* Authentication
* Authorization
* Application management
* Session management
* Run creation
* Run query
* Run cancellation
* Run resume
* Streaming output
* Artifact access

The API Layer MUST NOT implement Agent Loop or Workflow execution logic.

---

## 3.2 AI Runtime

The Runtime is the core execution layer.

It consists of:

```text
AI Runtime
├── Run Orchestrator
├── Agent Runtime
├── Workflow Runtime
├── Runtime Core
└── Runtime Capabilities
```

---

## 3.3 MCP Gateway

MCP Gateway provides centralized tool infrastructure.

Reserved responsibilities:

* MCP Server Registry
* Tool Registry
* Tool Discovery
* Tool Permission
* Tool Routing
* Tool Invocation
* Timeout
* Retry
* Error Handling
* Risk Control
* Audit
* Tool-level Observability

The Agent Runtime MUST NOT directly manage MCP server lifecycle.

---

## 3.4 Observability

Observability is a cross-cutting system.

It consumes Runtime events and records:

* LLM calls
* Tool calls
* Workflow node execution
* Agent turns
* Latency
* Token usage
* Cost
* Errors
* Runtime state transitions

Initial technology direction:

* OpenTelemetry
* Langfuse

Observability MUST NOT contain execution logic.

---

## 3.5 Evaluation

Evaluation is an offline/online analysis system.

It evaluates:

* Agent output
* Workflow output
* Agent trajectory
* Tool usage
* RAG retrieval
* Model quality
* End-to-end task quality

Reserved evaluation methods:

* Exact Match
* JSON Diff
* Recall@K
* LLM Judge
* Rubric
* Human Evaluation
* Regression Evaluation

Evaluation MUST consume execution data rather than modifying Runtime execution logic.

---

# 4. Runtime Capability Boundaries

Runtime capabilities are extensible services/interfaces.

Reserved capabilities:

```text
Model
Tool
MCP
Knowledge
RAG
Memory
Skill
Workspace
Sandbox
Policy
Budget
HITL
Artifact
Scheduler
Agent Registry
```

Not all capabilities must be implemented in the initial version.

---

# 5. Execution Models

## Agent

```text
Input
  ↓
Agent Runtime
  ↓
LLM
  ↓
Decision
  ↓
Tool / Capability
  ↓
Observation
  ↓
State Update
  ↓
LLM
  ↓
...
  ↓
Final Output
```

## Workflow

```text
Input
  ↓
Workflow Runtime
  ↓
Node
  ↓
State Update
  ↓
Next Node
  ↓
...
  ↓
Final Output
```

Both execution models produce common Runtime artifacts:

```text
Run
State
Execution
Event
Checkpoint
Artifact
Output
```

---

# 6. Dependency Rules

```text
API
 ↓
Runtime
 ↓
Capabilities
 ↓
Infrastructure
```

Observability and Evaluation consume Runtime data/events.

The following dependencies are prohibited:

```text
Agent Runtime → Evaluation implementation
Agent Runtime → Langfuse implementation
Workflow Runtime → API implementation
Runtime Core → specific MCP server
Runtime Core → specific model vendor
Runtime Core → Kubernetes implementation
```

Use interfaces/adapters instead.

---

# 7. Architecture Extension Points

The architecture reserves extension points for:

* Multi-Agent
* A2A
* Agent Registry
* Agent-to-Agent invocation
* Scheduler
* Long-running tasks
* Human approval
* Model routing
* Cost governance
* Policy engine
* Skill marketplace
* Plugin system

These are architectural extension points, not mandatory V1 features.

---

# 8. V1 Scope

V1 should focus on:

1. API Layer
2. Runtime Core
3. Run Orchestrator
4. Agent Runtime
5. Workflow Runtime
6. Basic Model capability
7. Basic Tool capability
8. Event system
9. Checkpoint foundation
10. Basic Observability
11. Basic Evaluation integration

Advanced capabilities remain extensibility points.
