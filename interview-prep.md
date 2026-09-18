# 面试问答文档：AI Runtime Platform

> 基于项目真实实现与 docs/ 设计文档整理。所有技术细节均可在代码中指认，可放心被追问。
> 标注说明：✅ 已实现（有代码有测试）｜📐 架构预留（接口/规范已定，未实现）——面试时务必区分，不夸大是可信度的来源。
> 使用方式：一~五部分是"项目介绍"的完整弹药；六是故事库；七是高频问答；八是防深挖速查。

---

## 一、项目背景与动机（为什么需要这个项目）

### 1.1 背景故事（开场可用）

最初有两类场景：

- 一类是**固定流程**：合同解析、文档审核——步骤确定、分支可枚举
- 一类是**开放式 Agent**：Web Agent、工具调用、知识库问答——路径由模型实时决策

两类场景各写一套就是两套重复的基础设施（会话、恢复、审计、观测全部×2）。所以把两套能力**统一成一个 Runtime**：上层统一管理 Agent 和 Workflow 的执行，下面提供 Context、Memory、Skill、MCP、RAG、Sandbox、Model 等基础能力，同时接入 Observability 和 Evaluation，并建立 Evolution 闭环。

整个系统的核心链路：

> **执行 → 观测 → 评测 → 诊断 → 进化。**

最终目标不是做一个 Agent Demo，而是解决 Agent 在企业生产环境中的**可靠性、安全性、可观测性、成本控制和持续优化**问题。

### 1.2 Demo 与生产的差距（面试官问"为什么需要这个平台"的展开）

传统 Agent Demo 是：

```text
User → LLM → Tool → LLM → Answer
```

真正进入生产后会连环撞上十个问题：

| # | 生产问题 | 平台方案 | 状态 |
|---|---|---|---|
| 1 | Agent 跑到一半挂了怎么办？ | Durable checkpoint + 跨进程 resume | ✅ |
| 2 | 用户中途如何确认/审批？ | 双模 HITL（Lifecycle State 而非输出一句话） | ✅ |
| 3 | Tool/MCP 不可信怎么办？ | MCP Gateway：权限/审计/幂等/沙箱化隔离 | ✅ |
| 4 | Agent 能不能访问宿主机？ | Sandbox 执行，限制犯错后的影响半径 | ✅ |
| 5 | 上下文太长怎么办？ | 大输出截断落 Artifact；预算三段相位 | ✅ |
| 6 | 成本失控怎么办？ | 多维预算（time/token/turn）+ graceful finishing | ✅ |
| 7 | 怎么知道 Agent 到底哪里出错？ | Runtime Events 全链路 + Langfuse Trace | ✅ |
| 8 | Prompt 改了怎么证明变好了？ | 离线评测 + A/B 粘性分流对比 | ✅ |
| 9 | 失败 case 怎么沉淀？ | 在线 Case 挖掘 → 确定性归因 → 回归集 | ✅ |
| 10 | Memory 如何跨会话管理？ | Working/Long-term 分离设计 | 📐 预留 |

一句话总结（金句）：

> **把 Agent 从一个 Loop，变成一个可运行、可恢复、可观测、可评测、可进化的生产系统。**

### 1.3 面试话术：为什么需要这个项目？

**标准回答（60 秒）**：

> Agent demo 和生产系统之间有一条巨大的鸿沟。Demo 就是 User→LLM→Tool→LLM→Answer 一个循环，但企业落地时十个问题连环出现：跑到一半挂了怎么办、用户怎么中途审批、第三方工具不可信怎么办、上下文爆炸、成本失控、改了 prompt 怎么证明变好了。这些问题没有任何一个单独的框架能全包。我做的这个平台就是把 Agent 从一个 Loop 变成可运行、可恢复、可观测、可评测、可进化的生产系统——五个"可"字正好对应五大模块：durable execution、HITL、MCP 治理、预算控制、评测闭环。另外我们有两类场景——固定流程的合同审核和开放式的工具调用 Agent——与其各建一套，不如统一成一个 Runtime，Agent 和 Workflow 双引擎共享同一套状态模型。

---

## 二、总体架构

### 2.1 架构图（白板可画）

```text
┌──────────────────────────────────────────────┐
│              API / Management                │
│      FastAPI: App/Session/Run/SSE/ARTIFACT    │
└──────────────────┬───────────────────────────┘
                   ▼
┌──────────────────────────────────────────────┐
│             AI Runtime Platform             │
│                                             │
│   Run Orchestrator（路由/幂等守卫/恢复入口）    │
│        │                                    │
│        ├── Agent Runtime                    │
│        │    Agent Loop / Context / Tool     │
│        │    Calling / Budget / HITL /       │
│        │    Checkpoint / Recovery            │
│        │                                    │
│        └── Workflow Runtime                 │
│             Graph / Node / State /          │
│             Branching / HITL / Resume       │
│                                             │
│   Runtime Core（共享概念：                    │
│     Application/Session/Run/Event/          │
│     State/Checkpoint/Artifact）              │
│                                             │
│   Runtime Capabilities（接口化能力）          │
│     Model/Tool/MCP/Memory/Sandbox/          │
│     Policy/Budget/HITL/...                  │
└──────┬──────────────┬───────────────────────┘
       ▼              ▼
  MCP Gateway     Sandbox(Docker→K8s)
       │
  第三方 MCP Server（沙箱化 bridge）

  横切（消费 Runtime 事件，不进执行链）：
  Observability（Trace/Metrics/Prometheus/Langfuse）
  Evaluation（离线评测/在线 Case 挖掘/归因/A/B）
  Evolution（Candidate/Gate/Versioning）
```

### 2.2 分层职责与依赖规则

- 依赖方向强制单向：**API → Runtime → Capabilities → Infrastructure Adapters**
- Observability 和 Evaluation 是横切系统：**消费 Runtime 事件/数据，绝不成为执行依赖**（执行代码里不会 import Langfuse 实现）
- Agent 与 Workflow 互相禁止 import——共性必须下沉 Runtime Core
- 禁止的依赖（举例）：Runtime Core → 具体 MCP server / 具体模型厂商 / K8s 实现——一律走接口/适配器
- **架构边界有 CI 测试守护**（`test_architecture_boundaries.py` 静态分析 import 关系）

### 2.3 关键架构立场

**双执行模型分离**：Agent（自主循环，模型决策驱动，路径不可预测）和 Workflow（确定性图，结构已知可静态分析）是本质不同的执行模型，两个引擎；但**分开的是执行语义，共享的是状态语义**——Run/Event/Checkpoint/Artifact 全部统一在 Runtime Core。

**能力可替换**：12 项能力全部接口化（Protocol），框架只是实现组件不是平台边界。

---

## 三、技术选型与理由（面试必问）

### 3.1 核心立场（金句）

> **框架是实现组件，不是平台边界。**

我们不排斥框架，而是区分 Framework 和 Platform Boundary：框架可以被替换，平台的核心概念（Run/Event/Checkpoint）必须是自己的。

### 3.2 选型表

| 能力 | 选择 | 理由 |
|---|---|---|
| Agent Loop | 自研（DeepAgents 作 Adapter） | 自己掌握 Agent Loop，避免 Runtime 被单一 Agent 框架绑定 |
| Workflow 引擎 | LangGraph | 状态机/Checkpoint/HITL/固定流程是它最强项，拿来主义 |
| API | FastAPI | 异步、生态、SSE 原生支持 |
| DB | PostgreSQL + SQLAlchemy | 持久化执行状态与业务数据；双协议实现（InMemory 测试 / SQL 生产） |
| Cache/事件 | Redis | 运行时状态、实时事件扇出、SSE 解耦 |
| Sandbox | Docker（K8s 契约已定📐） | Docker 本地/CI；K8s 多租户/NetworkPolicy/调度 |
| 工具协议 | MCP + 官方 SDK | 工具标准化交给协议，企业治理自己做（Gateway） |
| Observability | OpenTelemetry + Langfuse | Trace 是评测和进化的数据基础，不只是监控 |
| Evaluation | 自研 Domain + Langfuse | 评测对象是 Task+Outcome+Process+Trace，纯工具覆盖不了 |
| 部署 | Kubernetes | 多租户与弹性 |

### 3.3 三个必被追问的选型问题

**Q：为什么 Agent Loop 不直接用 LangGraph？**

> LangGraph 的强项是状态机、Checkpoint、固定流程——这正是 Workflow 需要的。但 Agent Runtime 我要自己掌握 Agent Loop：Agent 的中间件栈（预算、steering、审批、权限）要作为平台能力注入执行循环，而不是散落在图节点里。DeepAgents 作为 Adapter 接入——框架提供能力来源，平台控制装配和生命周期。这样换框架不动架构：LangGraph 的 interrupt/checkpointer 都被我包在平台自己的接口后面。

**Q：为什么不用 Dify / Coze 这类现成平台？**

> 它们解决"单个应用的搭建"（低代码、编排 UI），我解决"企业多应用的统一执行底座"（可靠性、治理、评测闭环）。定位不同。而且那类平台的 Agent Loop 和评测体系是黑盒——我的平台核心诉求恰恰是 Trace 可编程、评测可归因，这些必须自己掌握。

**Q：为什么 Observability 和 Evaluation 不做成执行链路的一环？**

> 依赖方向问题。观测和评测必须**消费** Runtime 事件而不是成为执行依赖——否则评测系统挂了 Agent 就跑不了，或者评测逻辑侵入执行代码。架构上执行层只管发事件（EventBus），观测和评测是事件的订阅方。这样每个横切系统可独立演进、独立扩容。

---

## 四、功能亮点总览

### 4.1 五大难题对照表

| 难题 | 方案 | 状态 |
|---|---|---|
| 长时任务可靠性 | Durable checkpoint，跨进程恢复，内容哈希版本绑定 | ✅ |
| 人机协同 | 双模 HITL：声明式 Human 节点 + 命令式 ask_user/审批中间件 | ✅ |
| 工具生态 | MCP Gateway：命名空间/幂等窗口/副作用重试矩阵/沙箱化 bridge | ✅ |
| 可控性 | 多维预算三段相位 + 运行时转向 Steering | ✅ |
| 质量度量 | 离线评测 + 在线 Case 挖掘归因回归 + A/B 粘性分流 | ✅ |

### 4.2 十句设计理念金句（好记好讲，面试点睛用）

1. **把 Agent 从一个 Loop，变成可运行、可恢复、可观测、可评测、可进化的生产系统**（项目总纲）
2. **框架是实现组件，不是平台边界**（选型立场）
3. **分开的是执行语义，共享的是状态语义**（双引擎架构）
4. **MCP 解决工具标准化，Gateway 解决企业治理**（工具层定位）
5. **Sandbox 不是防止 Agent 犯错，而是限制 Agent 犯错之后能造成的影响**（安全观）
6. **HITL 不是输出一句"请人工确认"，而是 Runtime 的 Lifecycle State**（人机协同观）
7. **预算不是熔断器，是收尾协议**（预算设计，最"Agent 味"）
8. **Trace 不只是为了监控，是 Evaluation 和 Evolution 的数据基础**（可观测性定位）
9. **归因体系自己不能用不确定的工具——LLM 归因 LLM 的失败是二级不确定性**（评测观）
10. **Evolution 不是让 Agent 自己改 Prompt，而是 Evaluation 驱动的受控搜索**（进化观）

### 4.3 数字口径（全篇一致）

**五大难题 / 337 个测试 / 32 个测试文件 / 30 个 commit / 109 个源码文件约 1.3 万行 / 31 篇架构与规格文档 / 12 项能力接口化**

---

## 五、开场陈述

### 30 秒版（电话初筛）

> 我从零设计实现了一个企业级 AI Runtime 平台，为 Agent 和 Workflow 两类应用提供统一执行底座。因为企业里既有固定流程的合同审核类场景，也有开放式工具调用 Agent，统一 Runtime 避免重复建设。核心解决 Agent 生产化五个问题：长时任务的持久化恢复、人机协同、MCP 工具生态治理、预算与转向控制、评测质量闭环。架构上双执行引擎分离、能力全部接口化、Spec 驱动渐进交付。

### 2 分钟版（现场开场）

> 我从零设计实现了一个企业级 AI Runtime 平台，解决 Agent 从 demo 到生产的问题。企业里有两类场景——固定流程的合同解析、文档审核，和开放式的 Web Agent、工具调用——各建一套就是两套重复基础设施，所以我把它们统一成一个 Runtime，核心链路是执行、观测、评测、诊断、进化。
>
> 生产化要过五关。
>
> 第一，**长时任务可靠性**。Agent 任务跑几分钟到几小时，进程重启、发版、等人类审批都是常态。我把 LangGraph checkpoint 持久化到平台数据库，thread_id 就是 run_id，实现跨进程恢复：进程崩溃后 Run 从断点继续，HITL 任务可以等任意久再 resume。
>
> 第二，**人机协同**。高风险决策不能全交给模型。我设计了双模 HITL：Workflow 侧是声明式 Human 节点，Agent 侧是命令式 ask_user 工具和工具审批中间件，两者统一收敛到一个 respond API。
>
> 第三，**工具生态**。MCP 标准化了接入，但生产还要治理：跨 server 工具名冲突、重复调用副作用、stdio 进程安全。我的 Gateway 用命名空间、幂等窗口、副作用分类重试矩阵解决，stdio server 拉起为沙箱化 bridge 工作负载做传输隔离。
>
> 第四，**可控性**。多维预算不是到顶就 kill，而是三段相位：软限告警、临近耗尽注入收尾指令让模型产出最终交付物、硬限才强制终止。另外支持运行时转向：用户中途修正方向，不杀 Run。
>
> 第五，**质量度量**。改动一个 prompt 可能三个场景变好五个变坏，没有度量就是盲改。我建了离线评测加在线闭环：生产失败信号自动挖掘 Case、确定性规则归因、晋升回归集，加上无状态粘性分流的 A/B 对比。
>
> 整个平台双执行引擎分离但共享 Run/Event/Checkpoint 核心模型，Spec 驱动、30 个里程碑渐进交付，架构边界有 CI 测试守护。

---

## 六、STAR 故事库

### 故事 1：Agent 重试的消息累积竞态 ⭐ 必备（"遇到的最难问题"首选）

**S（情境）**：平台用 LangGraph durable checkpoint 持久化 Agent 执行状态，`thread_id == run_id`。测试跨实例恢复时发现：Run 失败自动重试后，Agent 上下文混入上一轮失败的完整对话，模型被旧消息污染，重试成功率反而更低。

**T（任务）**：定位并修复竞态，且不能破坏"跨进程恢复"这个核心能力。

**A（行动）**：
- 从现象反推：重试和 resume 走同一个 durable thread。LangGraph 在已有 thread 上追加消息，但重试语义应是"从头再来"，resume 语义才是"从断点继续"——两个语义对同一存储机制要求相反
- 方案：在 `adapter.run()` 每次启动时（含重试）先调用 `adelete_thread` 清理旧状态再全新执行；resume 路径不清理
- Workflow 侧对称修复：`start()` 时清理旧状态，避免重试时合并过期的 channel values

**R（结果）**：重试不再被历史污染，跨实例 HITL 恢复、失败恢复、重试清理三类测试全绿。

**升华**：durable 状态是把双刃剑——它让恢复成为可能，也让"本不该恢复的东西"悄悄复活。这不是编码 bug，是 **durable 语义与 retry 语义的概念冲突**，必须显式区分"要恢复"和"要重来"。

**追问预案**：
- *怎么发现的？* → 写跨实例 HITL 恢复测试时，重试路径的消息序列断言失败，顺藤摸瓜
- *为什么不在写入侧去重？* → 需要定义"哪些消息属于同一次尝试"，逻辑侵入 LangGraph 内部；删 thread 是更干净的边界——一次尝试 = 一个干净 thread
- *删 thread 会不会删掉有用的东西？* → 重试意味着上次尝试已终态失败，其 checkpoint 只有污染价值没有恢复价值

---

### 故事 2：Durable Execution——长任务活过进程重启 ⭐ 必备

**S**：内存态执行的 Agent 进程一死，几小时进度归零；发版、崩溃、等待人类输入都会打断执行。

**T**：任意 Run 任意时刻可被打断，之后从断点继续。

**A**：
- 用 LangGraph checkpointer 但不依赖默认存储——自实现 `StoreCheckpointSaver`，checkpoint 落平台数据库，与 Run 元数据同库（复用 store 自己的 engine）
- `thread_id == run_id`，一个 Run 的执行历史天然可查
- 放共享包 `runtime/checkpointing`，Agent 和 Workflow 引擎复用同一实现
- 存储组合策略：SQLAlchemy store 走 durable checkpointer；in-memory store 退化为 per-run MemorySaver（仅进程内 resume），测试环境与生产行为分级
- 调度器幂等守卫：只有 `CREATED/QUEUED` 状态的 Run 可执行，终态 Run 被 worker 重投递时直接跳过（重试任务不重复执行），`WAITING_FOR_HUMAN` 的 Run 绝不在调用方背后偷偷执行

**R**：跨进程 resume 全链路打通；两种恢复入口——FAILED 恢复（无 response，从 checkpoint 续）和 HITL 恢复（带 response，折叠进暂停节点）。

**追问预案**：
- *checkpoint 里存什么？* → LangGraph 的 channel values + 消息序列；平台层另有 Run 状态机（WAITING_FOR_HUMAN 等）配合
- *代价？* → 每步多一次持久化写。用吞吐换可靠性，Agent 任务价值高、频率低，划算
- *resume 失败怎么处理？* → 区分"重启前失败"和"重启后失败"：Run 还在 FAILED 时 resume 失败只记 warning 不再 fail（FAILED→FAILED 不是合法状态转换，必须保住原始错误）

---

### 故事 3：内容哈希版本绑定——拒绝"旧数据跑新流程"

**S**：Workflow 定义会迭代。用户 HITL 暂停三天，期间定义更新了，resume 时旧 checkpoint 遇新定义，channel 结构对不上，轻则报错重则静默错乱。

**T**：保证 resume 一致性安全。

**A**：
- 放弃版本标签绑定（标签可覆盖可复用），给 `WorkflowDefinition` 计算结构内容哈希（节点/边/配置的结构化指纹）
- Run 创建时固化哈希；resume 时校验当前定义哈希，不匹配直接拒绝
- `RunStarted`/`RunCompleted` 事件携带 `definition_hash`，事后可审计"当时跑的是哪份定义"

**R**：消灭一类静默数据错乱，审计链路完整。

**升华**：**持久化系统里，凡会被时间穿越的状态，绑定的都应是内容而不是标签。**

**追问预案**：
- *为什么不用版本号？* → 版本号是可变人类标签，同版本号下偷偷改内容防不住；内容哈希是客观指纹
- *定义改了想续跑怎么办？* → 明确拒绝。走迁移工具或重新执行，宁可多一次确认不做隐式兼容

---

### 故事 4：双模 HITL——两种执行模型，一个恢复入口

**S**：企业高风险决策（审批、确认、补信息）不能全交给模型，但 Agent 和 Workflow 的人机协同形态本质不同。

**T**：统一但不失各自语义的 HITL。

**A**：
- **Workflow 声明式**：Human 节点进流程图，执行到该节点用 LangGraph `interrupt` 暂停，Run 状态机进入 `WAITING_FOR_HUMAN`
- **Agent 命令式**（两套）：
  - `ask_user` 工具：模型不确定时主动征询
  - `HumanApprovalMiddleware`：继承 LangChain `HumanInTheLoopMiddleware`，`after_model` hook 在任何工具执行前 interrupt，**replay 永不重跑已批准的工具**；只适配平台审批策略形状 `{tool: [decisions] | True}`
- 两者统一收敛到 `POST /runs/{id}/respond`

**R**：审批型 Workflow 和开放型 Agent 都有人机协同，API 调用方无感知差别。

**设计立场**：**HITL 不是 Agent 输出一句"请人工确认"，而是 Runtime 的 Lifecycle State**——RUNNING → WAITING_FOR_HUMAN → (User Response) → RESUME，有状态机、有事件、有 checkpoint 语义。

**追问预案**：
- *审批粒度？* → 按工具配置允许的决策类型（approve / reject+message）
- *reject 之后呢？* → reject 的 message 作为工具结果回给模型，模型自主调整——人类管边界，模型管路径
- *怎么防止恢复时重复执行已批准工具？* → interrupt 发生在工具执行前的 after_model hook，批准后从 pending tool call 处继续而不是从头重放
- *哪些场景触发审批？* → 架构上是 Policy 驱动：高风险工具/敏感数据/外部发送/资金操作/权限变化/不确定性高

---

### 故事 5：预算是收尾协议，不是熔断器 ⭐ 最"Agent 味"的设计

**S**：朴素预算控制是到顶 kill——半途 kill 意味着前面投入全作废，用户拿到 nothing。

**T**：既守住硬顶，又尽量留下交付物。

**A**：三段相位预算：
- **软限**：事件告警，继续跑
- **Graceful Finishing**：临近耗尽注入 Runtime Notice（System message，仅当前 model call 生效）："预算即将耗尽，停止新的探索/验证工作，基于已有状态产出最终交付物"——给模型收尾指令而非 kill
- **硬限**：抛 `BudgetExceededError` 强制终止
- 工程细节：token 只能调用后得知，所以 before hook 做 turn 计数与相位检查、after hook 记录 usage 再复查，允许少量 overshoot（设计内不是 bug）；单一 `awrap_model_call` hook 承担前后两个职责

**R**：超预算的 Run 大概率带着可用的部分产出结束而非全损。

**升华**：**把预算执行从进程信号变成对模型的沟通。**

**追问预案**：
- *notice 会被当历史消息吗？* → 不会，Runtime Notice 只对当前 model call 生效，不伪装成历史用户消息
- *token 预算怎么算准？* → 算不准。精确预算在 streaming 下是伪命题，工程上做的是相位管理：软限+收尾+硬顶
- *多维预算优先级？* → time/token/turn 独立计数，任一硬超限即终止，软限独立告警

---

### 故事 6：Steering——用户中途改方向，不杀 Run

**S**：长任务跑偏，用户唯一选项是取消重跑，几十分钟投入清零。

**T**：中途可下指令修正方向，且不引入新的一致性问题。

**A**：三个关键决策：
- **一次性合并注入**：所有 pending steering 在下一次 model call 前合并成一条 notice，不产生额外 model turn
- **不打断正在执行的工具**：只影响下一次模型决策——半途改主意比跑完更危险
- **consume-after-successful-injection**：模型调用成功才消费（acknowledge），失败保持 pending 下次重注入，配 steering_id 幂等
- 注入全程有 `STEERING_INJECTED`/`STEERING_CONSUMED` 事件可审计

**R**：Run 中途可修正方向；注入/消费事件可审计。

**追问预案**：
- *和 interrupt 的区别？* → interrupt 是硬暂停（状态机层面 WAITING），steering 是软转向（不动状态机，只影响下一次决策）。两者并存语义不同
- *为什么 System message 不是 user message？* → 避免污染对话历史语义——steering 是运行时控制指令不是对话内容

---

### 故事 7：MCP Gateway——从协议到生产能力

**S**：MCP 标准化了工具接入，但生产还有一堆协议不管的事：多 server 工具重名、重试导致重复副作用、stdio server 直接跑宿主机的安全风险、有状态工具的会话管理。

**T**：把 MCP 变成带治理能力的工具层。

**A**：两阶段：
- **V1**：工具注册、权限、超时、重试、审计
- **V2** 逐个击破：
  - `{server}__{tool}` 命名空间消除跨 server 重名
  - **副作用三分类重试矩阵**：readonly / idempotent / mutating——前两类可重试，mutating 不重试（防重复副作用）
  - **幂等窗口**：幂等键放请求 `_meta` 的 `platform.idempotency_key` 命名空间，TTL 60 秒，in-flight 检测（同 key 并发调用直接拒绝而非排队）
  - 凭据链路（环境变量 resolver）与会话持有者管理有状态工具
  - **stdio server 拉起为沙箱化 bridge 工作负载**：宿主只跟沙箱内进程通信，第三方代码不可逃逸宿主；runner 按 server 名幂等，工作负载死了自动重启
- 接官方 MCP SDK，用真实集成测试固化 bridge 行为假设（不 mock SDK 测自己的想象）

**R**：外部工具接入变成"注册即治理"，安全边界明确。

**定位金句**：**MCP 解决工具标准化，Gateway 解决企业治理。**

**追问预案**：
- *为什么 mutating 不重试？* → mutating 重试 = 重复副作用风险，宁可让上层决定；readonly/idempotent 重试安全
- *in-flight 检测和 TTL 缓存什么区别？* → in-flight 挡住并发重复（立刻拒绝），TTL 记录"最近调用过"用于审计与防抖；一个管并发一个管时间窗
- *为什么 stdio 必须沙箱化？* → stdio server 是任意第三方代码以平台权限跑在宿主机，等于交出安全边界；bridge 化后宿主只与沙箱内进程通信

---

### 故事 8：评测闭环——让 Agent 变更可度量 ⭐ 差异化亮点

**S**：Agent 行为不确定：改一个 prompt，三个场景变好五个变坏，没人说得清。传统单测无能为力，没有度量体系的迭代是盲改。

**T**：建"改动→评测→灰度→上线→回归"的完整质量循环。

**A**：三层建设：
- **离线评测**：评测资产（数据集/评判器/质量阈值）+ 执行 harness + quality gate；评测 Run 派发 worker 执行，评分导出 Langfuse
- **在线闭环**：生产失败信号 + 评测失败 Trial → 按 `(source, trace_id)` 幂等挖掘 Case → 归因 → Case 晋升为回归集资产
- **A/B**：粘性分流 + 按变体独立度量

**评测对象是四层而非一层**（与传统"只看最终答案"的区别）：E2E（任务完成了吗）/ Process（怎么做的：工具选择、参数、分支）/ Efficiency（token、成本、时延、轮次）/ Risk（安全、越权、数据泄露）。

**归因刻意用确定性规则而非 LLM 裁判**，按证据强度排序：
1. trace 缺失 → 证据不足转人工（`HUMAN_REVIEW`，confidence 0.0）
2. Run 终态 FAILED → `AGENT_FAILURE/runtime`（confidence 0.9）
3. 有 ToolCallFailed / LLMFailed / NodeFailed 事件 → 归因对应组件 tool/model/workflow（0.8）
4. trace 完整且过程干净但 E2E 失败 → 评测覆盖缺口，推荐补用例（`ADD_COVERAGE`，0.4）

归因历史 append-only，Case 保留最近一次结果；已 PROMOTED 的 Case 不因重新归因状态回退。

**R**：失败信号自动沉淀为回归资产，每次变更有量化对比。

**升华**：**归因体系自己不能用不确定的工具——用 LLM 归因 LLM 的失败是二级不确定性，结论无法复现。**

**追问预案**：
- *为什么区分 confidence？* → 决定下游处理：0.9 可自动开修复任务，0.4 走人工补用例，0.0 必须人工
- *为什么 (source, trace_id) 去重？* → 同一生产失败可能被多个信号源捕获，不去重会重复建 Case 污染统计
- *LLM Judge 在体系里的位置？* → 设计上是分层置信链：Deterministic → Small Judge → Strong Judge → Human；Judge 不能当 Ground Truth，要靠 Human Calibration Dataset 校准。V1 落地了 Deterministic 层
- *什么时候才允许用 LLM 归因？* → 需要 judge 校准证据之后；EVALUATION_FAILURE 归因（评测自身的错误）就是这个原因才暂不实现

---

### 故事 9：A/B 粘性分流——用计算代替存储

**S**：对比新旧 Agent 版本真实效果需要线上分流。传统 A/B 要维护"用户→分组"分配表；Agent 场景还要保证同一会话多次 Run 落同一组，否则体验分裂。

**T**：不引入分配表基础设施实现一致分流。

**A**：
- `sha256(ab_test_id + session_id)` 派生摘要：**前 8 字节**归一化为 [0,1) 均匀随机数，与 sampling_rate 比较决定是否入选；**后 8 字节**归一化后按 variant 权重做累积分布映射选组
- **无状态粘性**：同一 session 在同一实验下分配结果永远一致，纯函数计算零外部存储
- `assign_run` 幂等（已有 Assignment 直接返回）；强制同一 application + runtime_type 同时只允许一个 RUNNING 实验，防交叉污染
- 度量按变体聚合：完成率/失败率/时延 avg/max，从 Assignment 关联的 Run 终态计算；变体被修改过的历史 Assignment 容忍跳过

**R**：A/B 基础设施零额外存储依赖。

**升华**：**用确定性哈希替代分配表——"计算代替存储"解决分配一致性。**

**追问预案**：
- *流量不均怎么办？* → 哈希均匀性依赖 session_id 分布，极端情况在 report 层按样本量解读
- *variant 怎么真正影响执行？* → V1 边界：Assignment 是度量事实（run↔variant 绑定），执行侧按 agent_version 切换是下一步——主动说明边界比被问出来强
- *为什么同一 app 同一 runtime_type 只允许一个 RUNNING 实验？* → 两个实验同时分流的交叉效应无法归因，宁可串行

---

### 故事 10：架构守护测试——用 CI 强制模块边界

**S**：分层架构（API→Runtime→Capabilities→Adapters）最大的敌人不是初版设计，是三个月后的渐进腐化：总有人图方便跨层 import。

**T**：让架构约束可执行，不躺在文档里。

**A**：
- `test_architecture_boundaries.py` 静态分析 import：违反分层依赖方向、Agent 与 Workflow 互相 import、执行层 import API 层——全部测试失败进 CI
- Spec 驱动流程：每模块先写规格文档与 ADR，实现与文档冲突先改文档再改代码

**R**：30 个 commit 迭代后依赖方向依然干净；架构文档是活的事实源。

**追问预案**：
- *为什么 Agent/Workflow 不能互相 import？* → 平行执行模型，共性必须下沉 Runtime Core；互相 import 说明概念放错了位置
- *测试怎么实现？* → 遍历模块 import 关系断言允许的依赖方向集合

---

## 七、高频通用问答

### 7.1 动机与架构类

**Q1：遇到的最难的技术问题？**
故事 1。升华点：不是编码 bug，是 durable 语义与 retry 语义的**概念冲突**——同一存储机制对两种场景给出相反的正确答案，必须显式区分"要恢复"和"要重来"。

**Q2：为什么 Agent 和 Workflow 分成两个引擎？（考架构观）**
本质不同的执行模型：Agent 是自主循环（模型决策驱动、路径不可预测），Workflow 是确定性图（结构已知、可静态分析）。硬塞一个引擎两边都削足适履。但共享 Run/Event/Checkpoint/Artifact——**分开的是执行语义，共享的是状态语义**。这是"什么时候抽象、什么时候分离"的典型判断。

**Q3：为什么不用 LangGraph Platform / Dify / 现成平台？**
见 3.3。核心：定位不同（单应用搭建 vs 企业统一执行底座）+ 核心概念必须自己掌握（Run/Event/Checkpoint 可编程、Trace 可归因）。

**Q4：这个项目最大的 trade-off？**
Durable execution 的吞吐代价：每步持久化写。Agent 场景任务价值高、频率低，可靠性 > 吞吐，划算。如果换高频短任务场景，需要按 Run 级别配置 checkpoint 策略（能力接口的扩展点上）。

### 7.2 设计深挖类

**Q5：上下文怎么管理？**
分层回答：
- 架构上 Context Manager 职责拆解：System/User Context、Conversation、Memory、Skills、Tool Definitions、Retrieved Context、Current Task、Runtime State——上下文不是把历史全部塞给 LLM，而是**按需组装**：最近消息 + Summary + 相关 Memory + 相关 RAG + 当前任务
- V1 已落地的部分：预算中间件记录 token 用量并做相位管理；大输出截断、完整内容落 Artifact（防单次工具输出击穿上下文）；Runtime Notice / steering 是"运行时向上下文注入控制信息"的两种形态
- 预留：Summary 压缩、Tool Schema 按需加载、Memory 按需检索——都在能力接口上留了扩展点
- 关键立场：**Context 是当前一次执行需要什么；Memory 是跨 Session 什么值得留下**——两者必须分离，否则上下文会无限膨胀

**Q6：Memory 怎么设计的？**
- 架构：Working Memory（当前 Session）与 Long-term Memory（User/Agent 级：语义记忆、情景记忆、偏好）分离
- Write 不是每轮全量写：Conversation/Trace → Memory Extraction → Candidate → Triage → Dedup/Update → Long-term
- Read：新任务 → Memory Retrieval → 相关记忆进 Context Manager
- 诚实边界：📐 V1 是预留接口未实现——但设计区分了"该记什么"（Write 链路的 Triage）和"怎么取"（Read 链路的 Retrieval），这两个环节是 Memory 泛滥/失效的根源

**Q7：异常恢复怎么做的？（分层恢复，好答案）**
> Agent 的失败不是一种失败，所以做了分层恢复：

| 失败类型 | 恢复策略 | 状态 |
|---|---|---|
| LLM Failure | Retry / Fallback | ✅ 调用层重试 |
| Tool Failure | Retry / 替代工具 | ✅ MCP 副作用重试矩阵 |
| Sandbox Failure | 重建 Sandbox | ✅ runner 幂等 + 死了自动重启 |
| Agent Process Crash | Checkpoint Resume | ✅ 跨进程恢复（核心） |
| Worker Crash | Watchdog | ✅ 部分（orchestrator 幂等守卫：重投递不重复执行） |
| Network Failure | Retry + 幂等 | ✅ MCP 幂等键 |
| Context Overflow | 截断落盘 / Summary | ✅ Artifact（Summary 📐） |

配套存储分工：**Redis 负责运行时状态和实时事件（fanout/SSE），PostgreSQL 保存持久化执行状态（Run/Message/Checkpoint）**。

**Q8：评测体系怎么设计的？**
故事 8 的展开版：评测对象四层（E2E/Process/Efficiency/Risk）+ 评测资产分类（Golden/Bad Case/Challenge/Calibration，带 Purpose 标签：Optimization/Validation/Regression/...）+ Rubric 作为核心（Dimension/Criterion/Evidence/Score/Threshold）+ Judge 分层置信链（Deterministic → Small Judge → Strong Judge → Human，Judge 不能当 Ground Truth，要校准）。

**Q9：Evolution 是什么？**
> **Evolution 不是"让 Agent 自己改 Prompt"，而是 Evaluation 驱动的受控搜索。**
链路：Production → Case Mining → Diagnosis → Evolution Task → Candidate Generation → Experiment → Evaluation → Gate → Decision → New Version。两个关键约束：① Candidate 用 bounded edit（ADD/REPLACE/DELETE/INSERT），不是自由改写；② 数据集分层防过拟合（Optimization/Validation/Regression/Challenge/Production 五层 held-out）。V1 落地了 targets/candidates/gate/versioning。

### 7.3 开放与防御类

**Q10：项目的不足/重来会怎么做？（诚实清单，主动暴露是加分项）**
1. Memory / Knowledge / RAG 还是预留接口未实现
2. A/B 的 variant 尚未真正切换执行行为，目前只做度量绑定
3. Agent 侧 resume 对 checkpoint payload 兼容性依赖强，定义变更比 Workflow 侧更复杂
4. EVALUATION_FAILURE 归因（评测自身错误）需要 judge 校准数据，未做
5. K8s 沙箱只有契约规范，实现是 Docker
6. 多 Agent 编排（Supervisor/Sub-Agent 平台级支持）是扩展点——DeepAgents Adapter 层有 sub-agent 能力，平台级编排未做

**Q11：如果流量涨 100 倍怎么办？**
- 执行侧：worker 水平扩容（Run 已是持久化实体，天然可分发，Celery/Redis stream 派发）
- 事件侧：Redis fanout 已把事件流与执行解耦，SSE 网关可独立扩容
- 存储侧：checkpoint 写是热点，可按 Run 分片或引入分层存储（热 checkpoint 在 Redis，冷归档）
- 这是架构分层的直接收益：每一层可独立扩容

**Q12：SSE 事件流怎么实现的？**
Redis 事件扇出：执行进程发布事件到 Redis，API 层订阅后 SSE 推送给客户端。执行与 API 层通过事件总线解耦——API 进程挂了执行照常，重连后补拉事件历史（事件持久化在 store）。

**Q13：沙箱怎么设计的？**
- 核心安全观：**Sandbox 不是防止 Agent 犯错，而是限制 Agent 犯错之后能造成的影响**——进程/文件系统/资源/网络/身份五重隔离
- Workspace 与 Sandbox 分离：Sandbox 是计算环境，Workspace 是持久数据；Sandbox 挂掉 → Workspace 保留 → 新 Sandbox → reattach
- 实现走 SandboxCapability 接口，**契约测试**保证 Docker/K8s 可替换——同一套契约测试跑不同实现
- Docker（本地/CI/小规模）→ K8s（多租户/ResourceLimit/NetworkPolicy）的演进路径

**Q14：Multi-Agent / Sub-Agent 怎么考虑的？**
架构设计：Supervisor 负责任务分解/路由/聚合，Sub-Agent 有独立 Context/Tool/Budget——核心收益是**减少主 Agent Context 膨胀**。V1 通过 DeepAgents Adapter 获得子代理能力，平台级编排（Agent Registry、A2A）是预留扩展点。

**Q15：team 规模和你的角色？**
（按实际情况填写。若是独立项目：独立完成从架构设计、规格文档到实现测试的全部工作，30 个里程碑渐进交付。）

---

## 八、硬核细节速查（防三层追问）

| 主题 | 硬细节 |
|---|---|
| Checkpoint | `StoreCheckpointSaver`，`thread_id == run_id`，SQLAlchemy store 复用自身 engine；in-memory store 退化 per-run MemorySaver（仅进程内 resume） |
| 可执行状态 | Orchestrator 只执行 `CREATED/QUEUED`；终态跳过（worker 重投幂等）；`WAITING_FOR_HUMAN` 不在背后执行 |
| 恢复入口 | FAILED + 无 response = 断点续跑；WAITING_FOR_HUMAN + response = 折叠进暂停节点 |
| 状态转换守卫 | FAILED→FAILED 非法：resume 重启前失败只记 warning，保住原始错误 |
| 版本绑定 | 结构内容哈希；RunStarted/RunCompleted 事件携带 definition_hash |
| 重试清理 | Agent: 每次启动 `adelete_thread`；Workflow: `start()` 清理旧状态 |
| 预算相位 | soft → finishing（注入固定文案收尾指令）→ hard（BudgetExceededError）；token 事后记录允许 overshoot |
| Steering | 一次性合并注入、不打断在途工具、consume-after-successful-injection、steering_id 幂等 |
| HITL 审批 | `HumanApprovalMiddleware` 继承 `HumanInTheLoopMiddleware`，after_model hook 在工具执行前 interrupt，replay 不重跑已批准工具 |
| MCP 命名空间 | `{server}__{tool}` |
| 副作用分类 | readonly / idempotent / mutating；前两类重试，mutating 不重试 |
| MCP 幂等 | `_meta["platform.idempotency_key"]`，TTL 60s，in-flight 拒绝并发同 key |
| stdio 隔离 | stdio server 拉起为沙箱化 bridge 工作负载；runner 按 server 名幂等、死了自动重启 |
| Case 去重 | `(source, trace_id)` 幂等；BAD case 来自生产 Run 失败信号或评测失败 Trial |
| 归因 confidence | runtime 失败 0.9 / 组件失败 0.8 / coverage gap 0.4 / trace 缺失 0.0（转人工） |
| Case 晋升 | PROMOTED 后挂 REGRESSION 资产；重新归因不回退状态 |
| A/B 分流 | sha256(test_id:session_id) 前 8 字节定入选、后 8 字节按权重定组；无状态粘性 |
| A/B 守卫 | 同一 application + runtime_type 仅一个 RUNNING 实验；assign 幂等 |
| A/B 报表 | runs / completed / failed / cancelled / completion_rate / failure_rate / latency avg&max |
| Prometheus | 零依赖 exposition v0.0.4 格式导出；MetricsCollector 订阅 RuntimeEvents 聚合 |
| 存储分工 | Redis：运行时状态/实时事件/SSE；PostgreSQL：Run/Message/Checkpoint 持久化 |
| Artifact | 沙箱上传登记 `sandbox://` URI；truncated 输出全量落盘 `.platform/outputs/{request_id}.txt` |
| 测试规模 | 337 个用例、32 个测试文件；Docker/Redis 环境门控跳过 |

---

## 九、反问面试官建议

1. 团队现在的 Agent 生产化卡在五关（可靠/协同/工具/可控/度量）的哪一关？
2. Agent 行为变更目前怎么做回归验证？（如果他们没做，可自然接故事 8）
3. 团队对 MCP 生态的接入策略是什么？自建工具还是 MCP 优先？
4. 评测体系里 LLM Judge 的校准是怎么做的？（如有评测团队）

---

## 十、面试当天 checklist

- [ ] 30 秒版 + 2 分钟版开场陈述背熟
- [ ] 背景故事先讲"两类场景统一"再讲"五难题"——有业务动机再讲技术，比上来堆技术更可信
- [ ] 故事 1（竞态）、5（预算收尾）、8（评测闭环）为第一梯队，优先使用
- [ ] 数字口径一致：五难题 / 337 测试 / 30 commit / 双执行模型
- [ ] 主动说明设计边界（A/B 只度量、Memory 未实现）——"知道自己没做什么"是高级工程师信号
- [ ] 金句按需投放：总纲（把 Loop 变生产系统）、选型（框架是组件不是边界）、预算（收尾协议）、评测（二级不确定性）各场景一句
- [ ] steering / durable execution / 评测闭环是当前 Agent 工程热点（Claude Code、Manus 公开分享都在讲），遇懂行面试官用故事 5/6/8 拉区分度
- [ ] 每个 STAR 先说结论再展开
