# DeepAgents Runtime Specification

## 1. Purpose

This specification defines how the platform Agent Runtime integrates with LangChain and DeepAgents.

The platform must use DeepAgents / LangChain as the Agent execution engine and must not maintain a parallel custom Agent Loop.

---

## 2. Layer Ownership

### Platform Runtime Core Owns

```text
Application
Session
Run
RuntimeEvent
Checkpoint reference
Artifact reference
Tenant / user scope
Policy metadata
Audit metadata
Evaluation metadata
```

### DeepAgents / LangChain Own

```text
Agent loop
Message state
Model call loop
Tool execution loop
Middleware lifecycle
Subagent orchestration
Context engineering
Summarization
HITL mechanics
LangGraph execution
LangGraph checkpointing
```

### Platform Agent Adapter Owns

```text
Run loading
Agent config loading
DeepAgents construction
Tool adaptation
Middleware assembly
Backend adaptation
Event mapping
Checkpoint bridge
Output mapping
Error mapping
```

---

## 3. Agent Runtime Entry Point

Production Agent Runtime should expose one platform-facing adapter:

```python
class DeepAgentsRuntimeAdapter:
    async def run(self, run_id: str) -> AgentRunResult:
        ...
```

`run_id` is the only required task input. The adapter loads durable state from PostgreSQL through Runtime Core repositories.

---

## 4. DeepAgents Construction

The adapter constructs a DeepAgents agent from platform configuration:

```text
model -> LangChain chat model
tools -> LangChain tools
system_prompt -> configured agent prompt
middleware -> platform + application middleware stack
backend -> platform workspace/sandbox backend
checkpointer -> LangGraph-compatible checkpoint bridge
store -> optional LangGraph store bridge
```

The adapter delegates execution to DeepAgents / LangChain. It does not parse model decisions or manually dispatch tools.

---

## 5. Middleware Mapping

Platform features should be implemented as LangChain / DeepAgents middleware:

```text
PolicyMiddleware
BudgetMiddleware
RuntimeEventMiddleware
LangfuseMiddleware
HumanApprovalMiddleware
ToolPermissionMiddleware
ModelRoutingMiddleware
MemoryMiddleware
SkillMiddleware
```

Middleware may read platform configuration and write RuntimeEvents, but must not import API request handlers.

---

## 6. Tool Mapping

Platform tool registry entries are adapted into LangChain tools.

Tool permission and risk decisions should happen before exposing tools to the model or inside tool-call middleware.

MCP remains a tool integration mechanism behind the platform tool registry.

Agent Runtime must not directly manage MCP server lifecycle.

---

## 7. Backend Mapping

DeepAgents filesystem and command execution should use a platform backend adapter.

The backend adapter maps:

```text
DeepAgents file operations -> Workspace capability
DeepAgents command execution -> Sandbox capability
DeepAgents artifact creation -> Artifact store
```

The adapter must not expose host paths to the model.

---

## 8. Checkpoint and Resume

LangGraph checkpointing is the execution checkpoint mechanism.

Platform checkpoints store references and metadata:

```text
run_id
thread_id
checkpoint_id
created_at
status
metadata
```

Durable checkpoint payloads should use a LangGraph-compatible PostgreSQL checkpointer or a platform bridge that persists equivalent data.

---

## 9. Event Mapping

The adapter converts LangChain / LangGraph callbacks and stream events into RuntimeEvents:

```text
RunStarted
RunCompleted
RunFailed
LLMStarted
LLMCompleted
LLMFailed
ToolCallStarted
ToolCallCompleted
ToolCallFailed
CheckpointCreated
ApprovalRequired
HumanResponseReceived
```

RuntimeEvent remains the platform event contract for API streaming, observability, audit, and evaluation.

---

## 10. Observability

Langfuse is the preferred V1 backend for agent traces.

Trace correlation must include:

```text
tenant_id
user_id
application_id
session_id
run_id
celery_task_id
langgraph_thread_id
```

Langfuse must consume callbacks/events and must not become required for successful execution.

---

## 11. Migration From Custom Agent Loop

Existing custom Agent Loop code should be removed from the production path.

Allowed migration options:

1. Delete the custom Agent Loop and its tests.
2. Move it under an explicit experimental package.
3. Convert tests to assert the DeepAgents adapter behavior instead of custom loop internals.

The preferred option is deletion unless the code is needed as a temporary comparison fixture.

---

## 12. Acceptance Criteria

* Production Agent Runtime invokes DeepAgents / LangChain.
* No production code path uses a custom `AgentLoop`.
* Platform tools are exposed as LangChain tools.
* Platform policy/budget/HITL behavior is implemented as middleware.
* Platform sandbox/workspace is exposed through a DeepAgents backend adapter.
* RuntimeEvents are emitted from LangChain / LangGraph callbacks.
* PostgreSQL stores durable Run and checkpoint metadata.
* Langfuse receives trace data when configured.
* Runtime Core does not import LangChain or DeepAgents.
