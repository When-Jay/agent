# Agent Runtime Specification

## 1. Objective

Agent Runtime 提供统一的 Agent 执行能力。

Agent Runtime 的核心职责：

> 根据当前 State 和 Context，由 Model 产生下一步 Decision，并驱动 Tool / Sub-Agent / Final Response，直到满足终止条件。

---

## 2. Execution Loop

```text
Load State
   ↓
Build Context
   ↓
Invoke Model
   ↓
Parse Decision
   ↓
Execute Action
   ↓
Update State
   ↓
Emit Event
   ↓
Checkpoint
   ↓
Stop?
 ┌─┴─┐
Yes No
 |   |
End  Next Turn
```

---

## 3. Reserved Components

```text
AgentLoop
ContextManager
DecisionEngine
ToolExecutor
MemoryManager
SkillManager
SubAgentManager
BudgetController
StopController
CheckpointManager
```

这些组件必须保持职责独立。

---

## 4. Agent State

至少预留：

```text
messages
context
tool_calls
artifacts
metadata
variables
budget
execution_status
stop_reason
```

State 不应直接暴露底层数据库模型。

---

## 5. Stop Conditions

预留：

```text
FinalAnswer
MaxTurns
MaxTokens
MaxCost
MaxToolCalls
Timeout
UserCancelled
Error
PolicyViolation
```

---

## 6. Framework Boundary

Agent Runtime 不依赖特定 Agent Framework。

可以适配：

```text
Claude Agent SDK
DeepAgents
OpenClaw
Pi Agent
Custom Agent Loop
```

Framework Adapter 属于实现层。

不得反向污染 Runtime Core。

---

## 7. Acceptance Criteria

* Agent 可以启动 Run
* Agent 可以执行多轮 Loop
* Agent 可以调用 Tool
* Agent 可以产生标准 Event
* Agent 可以 Checkpoint
* Agent 可以 Resume
* Agent 可以触发 Budget / Stop
* Agent 可以返回 Final Answer
* Agent Framework 可以替换而不修改 Runtime Core



