# Agent Runtime Control 功能修改 Spec

## 1. 修改目标

当前 Agent Runtime 基于 **LangGraph DeepAgents + Middleware** 实现，需要补齐以下 Runtime Control 能力：

1. **Steering**

   * Agent 执行过程中允许用户发送新的 steering message。
   * 不直接终止当前正在执行的 Tool。
   * 在下一次 Model Decision 前，通过 Middleware 将 steering message 注入 Agent Context。
   * Agent 根据 steering message 调整后续执行方向。

2. **Multi-dimensional Budget**

   * Budget 不再只有单一的 timeout。
   * 至少支持：

     * `max_time`
     * `max_total_tokens`
     * `max_turns`
   * 设计成可扩展模型，后续可以增加：

     * `max_tool_calls`
     * `max_cost`
   * Budget 分为：

     * Soft Limit：进入 graceful finishing。
     * Hard Limit：强制终止。
   * Soft Limit 不应该立即 kill Agent，而是通过 Middleware 向 Agent 注入系统提示，让 Agent 尽快完成当前任务。

3. **AskUserMiddleware 接入**

   * 项目中已经存在 `AskUserMiddleware`。
   * 本次不重新实现 AskUser。
   * 只需要将已有 `AskUserMiddleware` 正确加入 DeepAgents Middleware Stack。
   * 确保 AskUser 使用现有的 LangGraph interrupt/checkpoint/resume 机制正常工作。

4. **Hard Timeout**

   * Middleware 不负责解决挂死的 LLM/Tool。
   * 保留现有 Watchdog/Runtime Supervisor。
   * Watchdog 负责最终 Hard Timeout。
   * Middleware 负责正常情况下的 graceful finishing。

---

# 2. 非目标

本次修改不做以下工作：

* 不修改 DeepAgents 核心 Agent Loop。
* 不重新实现 `AskUserMiddleware`。
* 不把 Steering 实现成 Tool。
* 不重构整个 Runtime。
* 不重新设计 Context / Memory / Skill / MCP。
* 不要求第一版支持 Tool Call Budget / Cost Budget。
* 不要求 Steering 能够中断正在执行的 Tool。
* 不要求修改 LangGraph 核心状态结构，只在现有 State / Runtime Context 上扩展必要字段。

---

# 3. 修改后的 Runtime Control 架构

```text
                    ┌─────────────────────────┐
                    │       User / Client     │
                    └────────────┬────────────┘
                                 │
                    normal message / steering
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │       API Server        │
                    └────────────┬────────────┘
                                 │
                    Redis / Runtime Event
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────┐
│                       Agent Runtime                          │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                 DeepAgents Middleware                  │  │
│  │                                                        │  │
│  │ Context                                                │  │
│  │ Memory                                                 │  │
│  │ Skills                                                 │  │
│  │ Sandbox                                                │  │
│  │                                                        │  │
│  │ SteeringMiddleware       ← 本次新增                    │  │
│  │ BudgetMiddleware         ← 本次新增                    │  │
│  │ AskUserMiddleware        ← 已有，仅接入                │  │
│  │                                                        │  │
│  │ Evaluation                                               │
│  │ Observability                                            │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│                      Agent Loop                              │
│              Model → Tool → Result → Model                  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                                 │
                                 │ hard timeout
                                 ▼
                    ┌─────────────────────────┐
                    │       Watchdog          │
                    │    Runtime Supervisor   │
                    └─────────────────────────┘
```

---

# 4. Steering 功能

## 4.1 设计原则

Steering 是 Runtime Control，而不是 Agent Tool。

用户在 Agent 执行过程中发送：

```text
不要继续搜索了，直接基于目前的信息给出结论。
```

Agent 当前可能正在：

```text
Model
  ↓
Tool Call
  ↓
Tool Running
  ↓
Tool Result
  ↓
Model
```

Steering 到达后：

```text
Tool Running
     │
     │ steering arrived
     │
     ▼
Tool 正常执行完成
     │
     ▼
before_model
     │
     ▼
注入 steering message
     │
     ▼
Model 根据 steering 调整行为
```

**不要因为 Steering 到达而直接 kill 当前 Tool。**

---

# 5. Steering 数据模型

建议增加 Runtime Steering Event：

```python
@dataclass
class SteeringMessage:
    id: str
    run_id: str
    message: str
    created_at: datetime
```

至少包含：

```text
id
run_id
message
created_at
```

如果当前项目已经存在 Runtime Event / Message 模型，可以直接复用，不要重复建立数据结构。

---

# 6. Steering 存储

第一版继续使用 Redis。

建议：

```text
runtime:steering:{run_id}
```

可以使用 Redis List / Stream。

如果项目已经使用 Redis Stream 保存 Runtime Event，则优先统一为 Runtime Event：

```text
runtime:event:{run_id}
```

事件：

```json
{
  "type": "steering",
  "run_id": "...",
  "message": "不要继续搜索了，直接总结",
  "created_at": "..."
}
```

### 要求

Steering 必须具备：

* run_id 隔离
* 顺序性
* consume-once
* 不影响其他 Run

---

# 7. SteeringMiddleware

新增：

```python
class SteeringMiddleware(...):
    ...
```

核心职责：

```text
before_model
    ↓
检查是否存在新的 steering
    ↓
读取 pending steering
    ↓
注入当前 Model Context
    ↓
consume steering
    ↓
继续调用 Model
```

第一版只需要在：

```text
before_model
```

实现。

不要为了 Steering 修改多个 Agent Loop Hook。

---

# 8. Steering 注入方式

Steering 不应该伪装成历史 User Message。

推荐作为 Runtime System Notice / Runtime Control Message 注入。

例如：

```text
[SYSTEM NOTICE — USER STEERING]

The user has provided a runtime steering instruction:

"不要继续搜索了，直接基于目前的信息给出结论。"

Adjust your next actions according to this instruction.
```

如果项目现有 Context/Middleware 有统一的 runtime message 构造机制，应复用。

---

# 9. Steering 消费策略

一个 Steering Message：

```text
只消费一次。
```

例如：

```text
Redis:
    steering-1
    steering-2
    steering-3
```

下一次：

```text
before_model
```

全部取出：

```text
steering-1
steering-2
steering-3
```

然后一次性注入。

不要出现：

```text
Model
 ↓
steering-1

Model
 ↓
steering-2

Model
 ↓
steering-3
```

这种无意义的额外 Model Turn。

---

# 10. Steering 与 Tool 的关系

第一版明确：

```text
Steering 不取消正在执行的 Tool。
```

例如：

```text
Tool A running
       │
       │ user steering
       ▼
Tool A complete
       │
       ▼
before_model
       │
       ▼
Steering injected
       │
       ▼
Model changes plan
```

后续如果需要，可以增加：

```text
interruptible tool
```

但不属于本次范围。

---

# 11. Multi-dimensional Budget

Budget 从单一 timeout 修改为统一 Resource Budget。

第一版至少支持：

```text
Time
Token
Turn
```

结构建议：

```python
@dataclass
class BudgetConfig:
    max_time_ms: int | None = None

    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_total_tokens: int | None = None

    max_turns: int | None = None

    soft_ratio: float = 0.8

    grace_time_ms: int = 60_000

    max_final_turns: int = 1
```

未来可扩展：

```python
max_tool_calls: int | None
max_cost: float | None
```

---

# 12. Budget State

Runtime 中维护：

```python
@dataclass
class BudgetState:
    elapsed_ms: int

    input_tokens: int
    output_tokens: int
    total_tokens: int

    turns: int

    tool_calls: int
    cost: float

    phase: str

    triggered_limits: list[str]
```

其中：

```text
phase =
    RUNNING
    FINISHING
    EXHAUSTED
```

第一版即使还没有：

```text
tool_calls
cost
```

也可以保留字段，方便未来扩展。

---

# 13. Budget Decision

Budget Checker 不直接控制 Agent。

它只负责产生 Decision。

例如：

```python
@dataclass
class BudgetDecision:
    exceeded: bool
    entering_finishing: bool
    hard_exceeded: bool

    triggered_dimensions: list[str]

    remaining: dict[str, int | float | None]
```

例如：

```json
{
  "exceeded": false,
  "entering_finishing": true,
  "hard_exceeded": false,
  "triggered_dimensions": [
    "time"
  ]
}
```

---

# 14. Soft Limit

每一个 Budget Dimension 都支持 Soft Limit。

例如：

```text
max_time = 10 min
soft_ratio = 0.8
```

则：

```text
8 min  → FINISHING
10 min → HARD EXHAUSTED
```

Token：

```text
max_total_tokens = 100000
soft threshold = 80000
hard threshold = 100000
```

Turn：

```text
max_turns = 30
soft threshold = 24
hard threshold = 30
```

**任何一个维度进入 Soft Limit，都可以触发 FINISHING。**

---

# 15. Graceful Finishing

进入：

```text
FINISHING
```

之后，不要直接停止 Agent。

通过 `BudgetMiddleware.before_model` 注入 Runtime Notice。

推荐使用：

```text
[SYSTEM NOTICE — run time budget nearly exhausted]

Run time budget nearly exhausted.

Stop new discovery/verification work now.
Produce the required final deliverable (answer/JSON/summary)
from the state you already have, completing only mandatory writes.

Do not start new non-essential tool calls.
Prioritize completing the final response.
```

如果不是 Time，而是 Token / Turn 触发，可以适当修改 Notice。

例如：

```text
[SYSTEM NOTICE — execution budget nearly exhausted]

Execution budget is nearly exhausted.

Stop new discovery/verification work now.
Produce the required final deliverable from the state you already have.

Do not start new non-essential tool calls.
Prioritize completing the final response.
```

第一版允许统一 Notice，不需要为每个 Dimension 设计复杂 Prompt。

---

# 16. Graceful Finishing 状态

进入：

```text
FINISHING
```

后：

```text
禁止新的 Discovery / Verification
```

但允许：

```text
mandatory writes
final formatting
final answer
```

例如：

```text
读取已经得到的数据       ❌ 尽量避免新的 Discovery
重新搜索互联网            ❌
重新检索大量知识库        ❌
重新规划复杂任务           ❌

写入必须保存的文件         ✅
完成已经开始的必要操作     ✅
整理最终结果               ✅
输出最终 Answer             ✅
```

注意：

**不要在 Middleware 中简单粗暴地禁止所有 Tool。**

因为某些任务的最终结果可能依赖一个 Mandatory Write。

---

# 17. Final Turn 控制

建议：

```python
max_final_turns = 1
```

进入 FINISHING 后：

```text
允许最多 1 个 Final Model Turn
```

如果 Agent 已经能够生成最终答案，则直接结束。

如果仍然没有结束，则进入 Hard Limit 流程。

---

# 18. Time Budget

Time Budget 计算：

```text
elapsed = now - run_started_at
```

不要依赖某个 Model Call 的 timeout 来计算整个 Agent Runtime。

例如：

```text
max_time = 10 min

Agent:
  Model 1: 20s
  Tool 1: 2min
  Model 2: 30s
  Tool 2: 3min
  Model 3: 20s

total elapsed ≈ 6m10s
```

Budget 应该基于整个 Run 生命周期。

---

# 19. Token Budget

Token Budget 使用 Model 返回的 Usage。

统一抽象：

```python
@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
```

每次 Model Call 完成后：

```text
after_model
    ↓
读取 usage
    ↓
更新 BudgetState
    ↓
检查 token budget
```

---

# 20. Token Hard Limit 的特殊处理

Token 使用量通常在 Model Call 完成之后才能准确获得。

因此：

```text
Token Hard Limit
```

允许存在少量 overshoot。

例如：

```text
max_total_tokens = 100000

当前：
99000

下一次 Model Call：
实际消耗 5000

最终：
104000
```

这是正常现象。

不要为了避免 overshoot 在第一版实现复杂的 Token Prediction。

如果底层 Model API 支持：

```text
max_tokens
```

可以用于限制单次 Model Output。

但 Runtime Budget 仍然以实际 Usage 为准。

---

# 21. Turn 定义

Turn 不等于 Tool Call。

定义：

```text
一个 Agent Model Decision Cycle = 一个 Turn
```

例如：

```text
Turn 1:
Model → Tool A → Tool B → Tool Result

Turn 2:
Model → Tool C → Tool Result

Turn 3:
Model → Final Answer
```

这里：

```text
turns = 3
tool_calls = 3
```

第一版只统计：

```text
turns
```

---

# 22. Turn Budget 检查

Turn Budget 最适合在：

```text
before_model
```

检查。

例如：

```text
max_turns = 30

turn = 24
→ FINISHING

turn = 30
→ HARD EXHAUSTED
```

需要明确：

```text
Final Turn 是否计入 max_turns
```

推荐：

**计入。**

因此：

```text
max_turns = 30
```

意味着整个 Agent 最多进行 30 次 Model Decision。

---

# 23. BudgetMiddleware

新增：

```python
class BudgetMiddleware(...):
    ...
```

至少实现：

```text
before_model
after_model
```

### before_model

职责：

```text
1. 获取 BudgetState
2. 检查 Time
3. 检查 Turn
4. 检查 Token
5. 判断是否进入 FINISHING
6. 判断是否 HARD EXHAUSTED
7. 必要时注入 Budget Notice
```

### after_model

职责：

```text
1. 获取本次 Model Usage
2. 更新 Token Usage
3. 更新 Turn
4. 重新计算 Budget State
5. 判断是否进入 FINISHING
```

---

# 24. Budget Manager

建议将计算逻辑从 Middleware 中抽离。

例如：

```python
class BudgetManager:

    def check_before_model(
        self,
        state,
        config,
    ) -> BudgetDecision:
        ...

    def update_after_model(
        self,
        state,
        usage,
    ) -> BudgetDecision:
        ...

    def should_finish(
        self,
        state,
    ) -> bool:
        ...

    def should_abort(
        self,
        state,
    ) -> bool:
        ...
```

Middleware 只负责：

```text
Hook
 ↓
BudgetManager
 ↓
Decision
 ↓
修改 Runtime Context / Message
```

不要把所有 Budget 计算逻辑堆在 Middleware。

---

# 25. Hard Limit

Hard Limit 与 Graceful Finishing 必须区分。

```text
Soft Limit
    ↓
FINISHING
    ↓
允许 Agent 自己完成
    ↓
Grace Period
    ↓
仍未完成
    ↓
Hard Stop
```

Hard Stop 最终由：

```text
Watchdog / Runtime Supervisor
```

执行。

---

# 26. 为什么 Hard Timeout 不完全交给 Middleware

Middleware 无法保证：

```text
LLM Request Hang
Tool Process Hang
Network Request Hang
```

例如：

```text
before_model
 ↓
LLM API
 ↓
一直没有返回
```

此时：

```text
after_model
```

根本不会执行。

因此必须保留外部 Watchdog。

架构：

```text
BudgetMiddleware
    │
    ├── Soft Limit
    ├── Graceful Finishing
    └── Normal Hard Limit Detection
             │
             ▼
        Runtime Supervisor

Watchdog
    │
    └── 最终 Hard Timeout / Kill
```

---

# 27. AskUserMiddleware 接入

项目已经存在：

```text
AskUserMiddleware
```

本次不要重新实现。

只需要：

```text
将 AskUserMiddleware 加入 DeepAgents Middleware Stack
```

例如：

```python
middleware = [
    ContextMiddleware(...),
    MemoryMiddleware(...),
    SkillsMiddleware(...),
    SandboxMiddleware(...),

    SteeringMiddleware(...),
    BudgetMiddleware(...),
    AskUserMiddleware(...),

    EvaluationMiddleware(...),
    ObservabilityMiddleware(...),
]
```

实际顺序根据项目现有 Middleware API 调整。

---

# 28. AskUser Runtime 要求

确认已有：

```text
interrupt
checkpoint
resume
```

链路能够工作：

```text
Agent
 ↓
AskUser
 ↓
interrupt
 ↓
persist checkpoint
 ↓
等待用户
 ↓
用户回答
 ↓
resume
 ↓
Agent 继续执行
```

本次不新增：

```text
AskUser Tool
AskUser State
AskUser API
```

如果现有 AskUserMiddleware 已经完整实现，则只做 wiring。

---

# 29. Middleware 推荐顺序

推荐初始顺序：

```text
ContextMiddleware
        ↓
MemoryMiddleware
        ↓
SkillsMiddleware
        ↓
SandboxMiddleware
        ↓
SteeringMiddleware
        ↓
BudgetMiddleware
        ↓
AskUserMiddleware
        ↓
EvaluationMiddleware
        ↓
ObservabilityMiddleware
```

但不要为了严格匹配该顺序而大规模修改现有 Middleware。

**优先遵循当前 DeepAgents Middleware 的实际 hook 执行语义。**

本次核心要求：

```text
Steering → before_model
Budget   → before_model + after_model
AskUser  → 已有 Middleware 正确接入
```

---

# 30. Runtime State 建议

如果当前 Runtime 已有 State，不要重新建立完整 State。

只增加必要字段，例如：

```python
runtime = {
    "run_id": "...",

    "budget": {
        "phase": "RUNNING",
        "elapsed_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "turns": 0,
        "triggered_limits": [],
    },

    "steering": {
        "last_consumed_id": None,
    },
}
```

如果已有 Redis/DB Runtime State，则优先复用。

---

# 31. Runtime 状态机

最终建议：

```text
                 ┌───────────────┐
                 │    RUNNING    │
                 └───────┬───────┘
                         │
              soft budget reached
                         │
                         ▼
                 ┌───────────────┐
                 │   FINISHING   │
                 └───────┬───────┘
                         │
              final answer completed
                         │
                         ▼
                    COMPLETED


FINISHING
    │
    │ grace timeout
    ▼
EXHAUSTED


RUNNING / FINISHING
    │
    │ hard timeout
    ▼
ABORTED
```

---

# 32. Budget 配置示例

一个普通 Agent：

```yaml
budget:
  max_time_ms: 600000
  max_total_tokens: 100000
  max_turns: 30

  soft_ratio: 0.8

  grace_time_ms: 60000
  max_final_turns: 1
```

含义：

```text
Time:
10 min hard limit
8 min soft limit

Token:
100k hard limit
80k soft limit

Turn:
30 hard limit
24 soft limit

Grace:
60 seconds

Final Turn:
最多额外/允许 1 个 final model decision
```

注意：如果实现采用“max_turns 内包含 final turn”的语义，则不要额外增加 turn，只使用剩余 turn 完成最终输出。

---

# 33. Steering + Budget 同时发生

这是本次实现需要重点处理的情况。

例如：

```text
Agent:
    运行到 8 min

Budget:
    FINISHING

User:
    "不要再搜索了，直接输出结论"
```

下一次：

```text
before_model
    ↓
BudgetMiddleware
    ↓
SteeringMiddleware
    ↓
Model
```

Model 应同时看到：

```text
Budget Notice
+
User Steering
```

最终：

```text
停止 Discovery
停止新的 Verification
根据现有信息完成结果
```

不要因为两个 Runtime Control Event 而产生两个额外 Model Turn。

---

# 34. 推荐统一 Runtime Notice

后续可以统一：

```python
RuntimeNotice
```

例如：

```python
@dataclass
class RuntimeNotice:
    type: str
    message: str
    priority: int
```

类型：

```text
steering
budget
system
```

但如果当前项目规模较小，本次可以暂时不抽象，直接在两个 Middleware 内实现。

**不要为了抽象而抽象。**

---

# 35. 日志与 Observability

本次修改必须增加关键 Runtime Event。

至少记录：

### Steering

```text
steering.received
steering.injected
steering.consumed
```

### Budget

```text
budget.soft_limit
budget.finishing
budget.hard_limit
budget.exhausted
```

Event metadata：

```json
{
  "run_id": "...",
  "dimension": "time",
  "current": 480000,
  "limit": 600000,
  "phase": "FINISHING"
}
```

Token：

```json
{
  "dimension": "total_tokens",
  "current": 80000,
  "limit": 100000
}
```

---

# 36. Idempotency

Steering 必须防止重复消费。

例如：

```text
steering_id = abc123
```

同一个 steering：

```text
只能被注入一次。
```

如果 Agent Worker 因故重启：

```text
before_model
 ↓
读取 steering
 ↓
注入
 ↓
Worker crash
```

恢复后不能因为没有 commit 而造成：

```text
重复 steering
```

具体实现可以复用当前 Runtime Event 的消费机制。

如果当前没有成熟机制，第一版至少保证：

```text
consume-after-successful-injection
```

并通过：

```text
steering_id
```

做幂等。

---

# 37. 并发要求

必须保证：

```text
Run A steering
```

不会进入：

```text
Run B
```

所有 Redis Key / Event 都必须基于：

```text
run_id
```

隔离。

同时：

```text
user_id
tenant_id
```

如果当前 Runtime 已有多租户隔离，也应保留现有权限校验。

---

# 38. 测试要求

## 38.1 Steering

### Case 1：正常 Steering

```text
启动 Agent
发送 steering
Agent 完成当前 Tool
下一次 before_model
收到 steering
```

验证：

```text
steering 被注入
```

---

### Case 2：多个 Steering

```text
steering A
steering B
steering C
```

下一次 Model：

```text
一次性收到 A/B/C
```

而不是产生三个 Model Turn。

---

### Case 3：Steering Consume Once

执行：

```text
before_model
```

后再次：

```text
before_model
```

验证：

```text
同一 steering 不重复注入
```

---

### Case 4：Steering + Tool

```text
Tool running
 ↓
Steering
 ↓
Tool complete
 ↓
before_model
 ↓
Steering injected
```

验证：

```text
Tool 没有被强制 kill
```

---

# 39. Budget 测试

## Time

```text
max_time = 100s
soft_ratio = 0.8
```

模拟：

```text
80s
```

验证：

```text
phase = FINISHING
```

模拟：

```text
100s+
```

验证：

```text
hard_exceeded = true
```

---

## Token

```text
max_total_tokens = 10000
soft_ratio = 0.8
```

模拟：

```text
8000
```

验证：

```text
FINISHING
```

模拟：

```text
10000+
```

验证：

```text
HARD EXHAUSTED
```

---

## Turn

```text
max_turns = 10
```

模拟：

```text
8
```

验证：

```text
FINISHING
```

模拟：

```text
10
```

验证：

```text
HARD EXHAUSTED
```

---

# 40. Graceful Finish 测试

模拟：

```text
Budget:
    进入 FINISHING
```

验证 Model Context 包含：

```text
[SYSTEM NOTICE — run time budget nearly exhausted]
```

同时验证：

```text
Agent 不会继续进行新的 discovery
```

并能够：

```text
完成 final answer
```

---

# 41. Graceful Finish + Mandatory Tool

模拟：

```text
Agent:
    已进入 FINISHING

下一步：
    必须写文件才能完成任务
```

验证：

```text
mandatory write 可以执行
```

不能简单实现：

```python
if finishing:
    disable_all_tools()
```

---

# 42. Hard Timeout 测试

模拟：

```text
LLM request hang
```

验证：

```text
Middleware 无法返回
```

但：

```text
Watchdog
```

能够最终：

```text
terminate run
```

验证：

```text
Agent Worker 不会无限占用资源
```

---

# 43. AskUser 测试

验证已有：

```text
AskUserMiddleware
```

被正确加载。

测试：

```text
Agent
 ↓
AskUser
 ↓
interrupt
 ↓
checkpoint
 ↓
user response
 ↓
resume
 ↓
Agent continue
```

本次不需要修改 AskUser 内部逻辑，除非发现 Middleware 接入后存在 API/状态兼容问题。

---

# 44. 配置兼容

如果项目当前已经有：

```text
timeout
max_iterations
```

等配置，不要直接删除。

应该进行兼容迁移。

例如：

```yaml
budget:
  max_time_ms: 600000
```

可以从现有：

```yaml
timeout: 600
```

迁移。

最终 Runtime 内部统一使用：

```text
BudgetConfig
```

避免出现：

```text
timeout
agent_timeout
max_runtime
max_time
run_timeout
```

多个不同概念。

---

# 45. 错误处理

Budget 错误不能导致 Runtime 崩溃。

例如：

```text
Model 没有返回 token usage
```

应该：

```text
记录 warning
```

而不是：

```text
Agent crash
```

可以：

```text
usage unavailable
```

并保留：

```text
time / turn
```

Budget 继续工作。

---

# 46. 实现优先级

按以下顺序实现：

### P0

```text
1. BudgetConfig
2. BudgetState
3. BudgetManager
4. BudgetMiddleware
5. SteeringMiddleware
6. AskUserMiddleware 接入
7. Watchdog Hard Timeout
```

### P1

```text
8. Observability Event
9. Steering Idempotency
10. 完整测试
```

### P2

未来：

```text
11. max_tool_calls
12. max_cost
13. Tool-level interrupt
14. Runtime Event abstraction
15. 更细粒度 Budget Policy
```

---

# 47. 最终验收标准

功能完成后，必须满足：

### Steering

* [ ] 用户可以在 Agent Run 中发送 Steering。
* [ ] Steering 使用 Middleware 实现。
* [ ] Steering 在 `before_model` 注入。
* [ ] 不直接终止正在执行的 Tool。
* [ ] Steering consume-once。
* [ ] 不同 Run 之间完全隔离。
* [ ] 多个 Steering 可以合并后一次注入。
* [ ] Steering 有 Observability Event。

### Budget

* [ ] 支持 `max_time`。
* [ ] 支持 `max_total_tokens`。
* [ ] 支持 `max_turns`。
* [ ] Budget Dimension 可扩展。
* [ ] 每个 Dimension 都有 soft/hard threshold。
* [ ] Soft Limit 进入 `FINISHING`。
* [ ] FINISHING 注入 Runtime Notice。
* [ ] FINISHING 不立即 kill Agent。
* [ ] FINISHING 禁止新的 discovery/verification。
* [ ] Mandatory Write 仍然允许。
* [ ] 支持 Grace Period。
* [ ] Hard Limit 最终能够终止 Run。
* [ ] Token Usage 正确累计。
* [ ] Turn 定义清晰且实现一致。

### AskUser

* [ ] 使用现有 `AskUserMiddleware`。
* [ ] 正确加入 DeepAgents Middleware Stack。
* [ ] interrupt 正常工作。
* [ ] checkpoint 正常保存。
* [ ] user response 可以 resume。
* [ ] 不重复实现 AskUser。

### Watchdog

* [ ] Middleware 不承担最终 Hard Timeout。
* [ ] LLM Hang 时 Watchdog 仍能终止 Run。
* [ ] Tool Hang 时 Watchdog 仍能终止 Run。

---

# 48. 推荐最终代码结构

如果当前项目没有更合适的目录结构，可以参考：

```text
runtime/
├── control/
│   ├── __init__.py
│   ├── budget.py
│   ├── budget_manager.py
│   ├── budget_policy.py
│   ├── steering.py
│   └── runtime_notice.py
│
├── middleware/
│   ├── context.py
│   ├── memory.py
│   ├── skills.py
│   ├── sandbox.py
│   ├── steering.py
│   ├── budget.py
│   ├── ask_user.py
│   ├── evaluation.py
│   └── observability.py
│
├── watchdog/
│   └── ...
│
└── ...
```

如果项目已有目录结构，则**优先按照当前项目结构修改，不要为了匹配该目录结构而重构代码**。

---

# 49. Coding Agent 执行要求

执行本 Spec 时遵循：

1. **先扫描现有代码**

   * Agent 创建入口
   * DeepAgents Middleware 注册位置
   * Middleware 基类/接口
   * Runtime State
   * Redis Runtime State/Event
   * Watchdog
   * AskUserMiddleware
   * Model Usage 获取逻辑

2. **尽量复用现有机制**

   * 不重复实现 Redis Runtime Event。
   * 不重复实现 Runtime State。
   * 不重复实现 AskUser。
   * 不修改 DeepAgents Core Loop。

3. **先实现最小闭环**

   ```text
   Steering
       ↓
   before_model
       ↓
   Agent 感知

   Budget
       ↓
   Soft Limit
       ↓
   FINISHING
       ↓
   Final Answer

   AskUserMiddleware
       ↓
   interrupt/resume
   ```

4. **再补充**

   * Observability
   * Idempotency
   * Tests
   * Error handling

5. 不要为了未来扩展过度设计。

---

# 50. 最终目标

本次修改完成后，Agent Runtime 的控制能力从：

```text
Agent
 └── timeout
```

升级为：

```text
Agent Runtime Control
│
├── Steering
│   └── Runtime Middleware Injection
│
├── Budget
│   ├── Time
│   ├── Token
│   └── Turn
│
├── Graceful Finishing
│   └── Runtime Notice
│
├── Hard Stop
│   └── Watchdog
│
└── Human Interaction
    └── AskUserMiddleware
```

最终形成：

```text
User
 │
 ├── normal request
 │
 └── steering
       │
       ▼
┌─────────────────────────────┐
│       Agent Runtime         │
│                             │
│ Context / Memory / Skills   │
│ Sandbox / MCP               │
│                             │
│ Steering Middleware         │
│ Budget Middleware           │
│ AskUser Middleware          │
│                             │
│       Agent Loop            │
└──────────────┬──────────────┘
               │
        ┌──────┴──────┐
        │             │
    graceful       hard stop
        │             │
        ▼             ▼
   final answer    Watchdog
```

核心设计原则：

> **Middleware 负责“引导 Agent 收敛”，Watchdog 负责“保证 Runtime 最终可控”。**

> **Budget 不是简单的 timeout，而是 Time / Token / Turn 等多个资源维度的统一约束。**

> **Steering 不是新的 Tool，而是 Runtime 在下一次 Model Decision 前对 Agent 行为进行动态修正。**
