# Agent Runtime Specification

## 1. Objective

Agent Runtime 提供平台级 Agent 执行入口，但不自研 Agent Loop。

Agent Runtime 的核心职责：

> 将平台 Run、Policy、Tool、Workspace、Sandbox、Budget、Observability 适配到 LangChain / DeepAgents，并将执行结果映射回 Runtime Core。

---

## 2. Execution Model

```text
Load Run
   ↓
Load Application / Agent configuration
   ↓
Build DeepAgents backend from Workspace / Sandbox
   ↓
Build LangChain tools from platform tool registry
   ↓
Build middleware stack
   ↓
create_deep_agent(...)
   ↓
Invoke / stream LangGraph execution
   ↓
Map callbacks/events to RuntimeEvent
   ↓
Persist checkpoints / artifacts / final output
```

---

## 3. Required Components

```text
DeepAgentsRuntimeAdapter
LangChainToolAdapter
PlatformMiddlewareStack
PlatformBackendAdapter
RuntimeEventCallbackHandler
RuntimeCheckpointBridge
RunResultMapper
```

这些组件必须保持职责独立。

The following components are NOT platform-owned implementation targets:

```text
AgentLoop
DecisionEngine
ContextManager
ToolExecutor
MemoryManager
SkillManager
SubAgentManager
StopController
```

If these names exist in code, they should be treated as temporary experimental code and replaced with LangChain / DeepAgents adapters.

---

## 4. Agent State

Platform state stores durable metadata and references:

```text
run_id
thread_id / checkpoint_id
agent_config_id
status
input
output
artifacts
runtime_events
metadata
error
```

LangChain / LangGraph owns message state and internal agent graph state.

State 不应直接暴露底层数据库模型，也不应要求 Runtime Core import LangChain / DeepAgents concrete types.

---

## 5. Middleware Responsibilities

Platform-specific behavior should be middleware:

```text
Budget / token / cost control
Tool permission filtering
Human approval
Tenant policy enforcement
Sensitive data filtering
Runtime event emission
Langfuse trace enrichment
Model routing / fallback
Memory injection
Skill discovery / prompt injection
```

---

## 6. Framework Boundary

Agent Runtime explicitly depends on LangChain / DeepAgents at the adapter layer.

Allowed:

```text
Agent Runtime Adapter -> DeepAgents / LangChain
Middleware -> Runtime Core interfaces
Backend Adapter -> Sandbox / Workspace interfaces
```

Forbidden:

```text
Runtime Core -> LangChain / DeepAgents
API -> DeepAgents / LangChain direct execution
Custom platform AgentLoop replacing create_deep_agent()
Custom platform middleware system duplicating LangChain middleware
```

---

## 7. Acceptance Criteria

* Agent 可以启动 Run
* Agent 执行通过 DeepAgents / LangChain 完成
* Agent 可以通过 LangChain Tool 调用平台 Tool
* Agent 可以产生标准 Event
* Agent 可以通过 LangGraph / platform bridge Checkpoint
* Agent 可以 Resume
* Agent 可以通过 middleware 触发 Budget / Stop / HITL
* Agent 可以返回 Final Answer
* Runtime Core 不依赖 LangChain / DeepAgents concrete types
* Existing custom AgentLoop implementation is removed or no longer used by production code



