# Agent Runtime Implementation Plan

## Goal

Build an Agent Runtime adapter on top of LangChain / DeepAgents.

The goal is not to implement a new Agent Loop. The platform should delegate agent execution to `create_deep_agent()` / LangChain `create_agent()` and focus on enterprise runtime governance.

---

## Dependencies

Required:

```text
Runtime Core
Celery worker runtime
PostgreSQL persistence
LangChain
DeepAgents
LangGraph
Langfuse adapter
Platform Sandbox / Workspace abstraction
```

Optional:

```text
Custom model routing
Custom skill registry
Custom memory providers
Human approval middleware
```

---

## V1

Implement:

```text
DeepAgentsRuntimeAdapter
LangChainToolAdapter
PlatformBackendAdapter
PlatformMiddlewareStack
RuntimeEventCallbackHandler
RuntimeCheckpointBridge
Celery run task
Langfuse trace adapter
```

Do not implement:

```text
Custom AgentLoop
Custom DecisionEngine
Custom ContextManager
Custom ToolExecutor
Custom middleware framework
```

---

## Acceptance Criteria

Given:

```text
User Input
+
Model
+
Tool
```

Agent must be able to:

```text
API creates Run
 → PostgreSQL records Run(status=queued)
 → Celery dispatches run_id through Redis
 → Runtime Worker loads Run
 → DeepAgentsRuntimeAdapter invokes create_deep_agent()
 → LangChain / LangGraph execute model + tools + middleware
 → Runtime events are persisted and streamed
 → Langfuse receives trace data
 → Run completes with output / artifacts
```

And every major execution step produces Event.

Existing custom AgentLoop code should be deleted, quarantined as experimental, or replaced by adapter tests that assert DeepAgents / LangChain integration behavior.

---

## Out of Scope

* Multi-Agent
* A2A
* Agent Evolution
* Automatic Skill Generation
* Custom planning framework
* Reimplementing LangChain middleware
