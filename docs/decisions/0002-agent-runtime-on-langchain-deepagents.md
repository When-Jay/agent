# ADR-0002: Agent Runtime Built on LangChain and DeepAgents

## Status

Accepted

---

## Decision

Agent Runtime MUST use the LangChain / LangGraph / DeepAgents ecosystem as the execution foundation.

The platform MUST NOT grow a separate long-term Agent Loop, Decision Engine, Tool Executor, Context Manager, Memory Manager, Skill Manager, or middleware mechanism that duplicates LangChain or DeepAgents.

The platform Agent Runtime is an adapter and governance layer around DeepAgents / LangChain:

```text
Platform Runtime Core
  |
  v
Agent Runtime Adapter
  |
  v
DeepAgents create_deep_agent()
  |
  v
LangChain create_agent()
  |
  v
LangGraph runtime
```

---

## Context

The platform needs enterprise runtime responsibilities:

* Run lifecycle
* Tenant / user ownership
* API and worker separation
* Durable state
* Event normalization
* Observability
* Audit
* Evaluation data capture
* Sandbox and workspace governance

LangChain and DeepAgents already provide the Agent execution model:

* Agent loop
* Model invocation
* Tool calling
* Middleware hooks
* Context engineering
* Summarization
* Human-in-the-loop
* Model / tool call limits
* Deep agent filesystem, subagent, skill, and backend integration patterns

Reimplementing these inside the platform would create a parallel agent framework and make the platform harder to maintain.

---

## Decision Details

### Platform Owns

```text
Application
Session
Run
State metadata
RuntimeEvent
Checkpoint reference
Artifact reference
Tenant / user scope
Policy decisions
Audit records
Evaluation records
Worker dispatch
```

### LangChain / DeepAgents Own

```text
Agent loop
Model/tool iteration
Message state
Tool call execution flow
Context engineering
Middleware lifecycle
Subagent orchestration
Deep agent filesystem and backend behavior
LangGraph execution graph
LangGraph checkpoint semantics
```

### Adapter Owns

```text
Platform Run -> DeepAgents invocation
Platform tools -> LangChain tools
Platform policy/budget/observability -> LangChain middleware
Platform sandbox/workspace -> DeepAgents backend
LangChain/LangGraph events -> RuntimeEvent
LangGraph checkpoint metadata -> platform checkpoint reference
Final agent output -> Run output / Artifact
```

---

## Consequences

### Positive

* Avoids duplicated Agent Loop implementation.
* Lets the platform inherit LangChain middleware improvements.
* Keeps DeepAgents backend and middleware as first-class extension points.
* Makes custom platform behavior injectable through middleware instead of framework forks.
* Keeps Runtime Core independent from LangChain-specific types.

### Negative

* Agent Runtime depends on LangChain / DeepAgents APIs at the adapter boundary.
* Runtime events must be mapped from LangGraph / LangChain execution data.
* Checkpoint and resume need a deliberate bridge between platform persistence and LangGraph checkpointers.

---

## Implementation Direction

Existing or future code named like the following should be removed, renamed as experimental, or converted into adapters/tests around LangChain behavior:

```text
AgentLoop
DecisionEngine
ContextManager
ToolExecutor
StopController
BudgetController
```

Platform-specific budget, policy, human approval, memory, observability, and tool filtering should be implemented as LangChain / DeepAgents middleware.

The only acceptable long-term Agent Runtime entrypoint is an adapter such as:

```text
DeepAgentsRuntimeAdapter
LangChainAgentRuntimeAdapter
```

These adapters may expose platform-facing methods, but must delegate execution to LangChain / DeepAgents.
