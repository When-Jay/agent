# 评测（Evaluation）与进化（Evolution）专题：面试深度准备

> 本文是 interview-prep.md 的专题深化篇。评测与进化是本项目**差异化最强的部分**——多数候选人只做过离线评测，完整"在线闭环 + 受控进化"链路很少见。遇到懂行的面试官，这里是拉区分度的主力。
> 标注：✅ 已实现｜📐 架构预留（设计已定，实现保留）

---

## 一、定位：为什么评测和进化是 Agent 平台的核心模块

### 1.1 核心矛盾

Agent 的行为是**不确定**的：改一个 prompt，可能三个场景变好、五个场景变坏。传统软件有单测守护回归，Agent 没有断言可以知道"这次改动整体上是变好了还是变坏了"。

没有度量体系的 Agent 迭代 = **盲改**。

### 1.2 两句定位金句

> **Trace 不只是为了监控，是 Evaluation 和 Evolution 的数据基础。**

> **Evolution 不是"让 Agent 自己改 Prompt"，而是 Evaluation 驱动的受控搜索。**

第一句决定了架构：观测系统产出的 Runtime Events / Trace 是评测的输入，所以观测和评测是数据上下游，不是两个孤立系统。第二句决定了进化的边界：进化不是全自动魔法，每一步都在门禁和约束下。

### 1.3 完整链路（白板可画）

```text
Production（生产流量）
    ↓ 失败信号
Case Mining（案例挖掘）✅
    ↓
Diagnosis（确定性归因）✅
    ↓
Regression Set（回归集沉淀）✅
    ↓
Evolution Task（进化任务：改什么、约束是什么）✅
    ↓
Candidate Generation（候选生成，bounded edit）✅
    ↓
Experiment（实验：跑评测资产）✅
    ↓
Selection（硬约束过滤 → 多目标比较）✅
    ↓
Gate（质量门禁 + 自动发布七条件）✅
    ↓ ACCEPT / HUMAN_REVIEW
New Version（版本发布）✅
```

架构关系：**Evolution 通过稳定接口（EvaluationGateway）消费 Evaluation 的能力，自己不实现评测引擎、不执行 Runtime**——依赖方向是单向的。

---

## 二、评测体系详解

### 2.1 评测对象：四层而非一层

传统 Agent 评测只看最终答案。本平台把评测对象扩展为 **Task + Outcome + Process + Trace**，对应四层：

| 层 | 问题 | 示例 | 状态 |
|---|---|---|---|
| **E2E** | 任务最终完成了吗？ | 合同字段是否全部正确、任务是否完成 | ✅ |
| **Process** | Agent 是怎么做的？ | 工具选择、工具参数、Workflow 分支是否合理 | ✅（rubric 设计）/ 部分 📐 |
| **Efficiency** | 代价如何？ | Token、成本、时延、轮次、工具调用数 | ✅ |
| **Risk** | 有没有越界？ | 安全、数据泄露、策略违规、未授权工具 | 📐 |

只评 E2E 的盲区：一个"答对了但调了 50 次工具"的 Agent 和一个"两步答对"的 Agent，E2E 分数一样，但显然不该同分。

### 2.2 评测资产

```text
Evaluation Asset
├── Golden        # 黄金标准集
├── Bad Case      # 坏案例（从生产挖掘）
├── Good Case     # 好案例
├── Challenge     # 挑战集（防过拟合的 held-out）
├── Calibration   # 校准集（校准 LLM Judge 用）📐
└── Inspection    # 巡检集（定期巡检）📐
```

每个资产带 Purpose 标签：Optimization / Validation / Regression / Challenge / Calibration / Inspection。**资产的 Purpose 决定它在进化流程里什么时候被使用**——优化阶段只碰 Optimization 集，验收看 Validation 集，最终门禁跑 Regression 集。

### 2.3 Rubric 与 Evaluator

Rubric 是评分的核心抽象：

```text
Rubric
├── Dimension   # 维度，如 Tool Selection
├── Criterion   # 判据，如"是否选择正确工具"、"是否存在不必要调用"
├── Evidence    # 证据：从 Trace 哪里取
├── Score       # 评分方式
└── Threshold   # 阈值
```

Evaluator 实现（✅）：
- **Rule-based**：exact match、JSON diff、结构化断言——确定性，优先用
- **LLM Judge**：已实现 evaluator 类型，可进 rubric

**Judge 的原则（金句）**：

> **LLM Judge 不能当 Ground Truth。**

分层置信链（设计立场，V1 落地了 Deterministic 层）：

```text
Deterministic（规则/精确匹配）→ Small LLM Judge → Strong Judge → Human
```

控制 Judge 可靠性的设计（📐 校准闭环）：Human Calibration Dataset → Judge Agreement（与人的一致率）→ Judge Regression（judge 自身的回归测试）→ Confidence Calibration。**Judge 本身也需要回归测试**——这是多数团队想不到的一层。

### 2.4 离线评测执行 ✅

- 评测以 **Evaluation Run** 为单位：一个 suite（资产 + 评判器 + 阈值）+ 目标版本 → 派发 worker（Celery）执行 Trial
- Trial Runner 执行单条 case：构造输入 → 跑目标（Agent/Workflow）→ 收集输出与 Trace → 跑 evaluator → 记分
- **Quality Gate**：按 suite 配置的阈值出 PASS/BLOCK 决策，是进化自动发布的输入之一
- 评分导出 Langfuse（score 链路），与 Trace 关联可回看

### 2.5 在线闭环 ✅（差异化核心）

三个环节：

**① Case 挖掘**——信号源：
- 生产 Run 失败信号（trace 里有失败的 Run）
- 评测失败的 Trial（离线评测没过的 case 也是线索）

挖掘幂等键：`(source, trace_id)`——**同一生产失败可能被多个信号源捕获，不去重会重复建 Case 污染统计**。

**② 确定性归因**（详见难点 1）：

| 证据 | 归因 | 置信度 | 下游动作 |
|---|---|---|---|
| trace 缺失 | `HUMAN_REVIEW` | 0.0 | 必须人工看 |
| Run 终态 FAILED | `AGENT_FAILURE/runtime` | 0.9 | 可自动开修复任务 |
| ToolCallFailed 事件 | 组件失败 `tool` | 0.8 | 定位工具问题 |
| LLMFailed 事件 | 组件失败 `model` | 0.8 | 定位模型问题 |
| NodeFailed 事件 | 组件失败 `workflow` | 0.8 | 定义/节点问题 |
| trace 干净但 E2E 失败 | `COVERAGE_GAP/ADD_COVERAGE` | 0.4 | 评测没覆盖到，补用例 |

**③ 回归集沉淀**：Case 状态机（OPEN → … → PROMOTED），PROMOTED 的 Case 挂入 REGRESSION 资产——从此每次变更的 quality gate 都会跑它，**生产失败永久变成回归防线**。

### 2.6 A/B 实验 ✅

线上分流对比（详见难点 4、5）：
- 无状态粘性分流（sha256 派生双随机数）
- Assignment = run↔variant 绑定事实（幂等创建）
- 按变体聚合报表：runs / completion_rate / failure_rate / latency avg&max
- 守卫：同一 application + runtime_type 同时只允许一个 RUNNING 实验

边界（主动说明）：V1 的 Assignment 是度量事实，执行侧按 variant 切换 agent_version 是下一步；Shadow/巡检调度 📐。

---

## 三、Evolution 体系详解

### 3.1 领域模型 ✅

```text
EvolutionTask     # 进化任务：优化目标 + 硬约束 + 编辑预算 + 风险等级
EvolutionTarget   # 目标：PROMPT / SKILL / RAG 文档（V1 文本类）
EvolutionCandidate# 候选：基线内容 + 补丁 + 版本
EvolutionRun      # 进化执行：任务 → 候选 → 实验 → 决策
EvolutionVersion  # 发布产物：可追溯的版本记录
```

### 3.2 Patch 引擎：bounded edit ✅

**候选不是"重新生成一份新内容"，而是对基线的一组有界补丁**：

```text
操作集（封闭枚举）：
ADD      # 新增一个小节
REPLACE  # 替换指定小节
DELETE   # 删除指定小节
INSERT   # 在指定小节前插入

寻址方式：
内容按 markdown 小节（"## 标题"）寻址，首段标题为 ""
```

补丁进入实验前过**四层校验管线**：

```text
PATCH → Syntax Validation → Schema Validation → Edit Budget Validation
      → Permission Validation → Candidate（合格才能进实验）
```

细节：编辑预算的 token 估算 V1 用空白分词（避免引入 tokenizer 依赖的明确取舍）；DELETE 不允许删除 preamble 首段。

### 3.3 候选选择：三段式 ✅

```text
Stage 1  硬约束过滤（Hard Constraints）
         ↓ 指标缺失 = 未通过（不强行判定）
Stage 2  多目标比较（Optimization Objectives）
         ↓ 字典序比较，不合成单一加权分
Stage 3  选择
         ↓ 无有效目标时按 pass_rate 兜底
```

两个关键设计立场：
- **字典序多目标比较，不合成单一加权分**：加权分会掩盖"某维度塌了但被总分摊平"的问题，字典序保证优先级高的维度先比
- **指标缺失 = 未通过**：与 Evaluation 的 UNKNOWN 语义一致——数据缺失时不下赌注

### 3.4 治理与自动发布 ✅

自动发布（ACCEPT）必须**七条件全部满足**，否则转 HUMAN_REVIEW：

```text
HARD_CONSTRAINTS    # 硬约束通过（选择阶段已过滤）
REGRESSION          # 回归集通过
CHALLENGE           # 挑战集通过
EDIT_BUDGET         # 编辑预算内
RISK_POLICY         # V1：仅 LOW 风险可自动发布，MEDIUM/HIGH 必须人工
QUALITY_GATE        # 质量门禁非 FAIL
AUTO_RELEASE_ENABLED# 任务显式开启自动发布
```

一个容易被质疑的设计（准备好答辩）：**未配置的评测资产（regression/challenge/gate）视为该条件通过**——理由：约束来自任务配置而非隐式失败，没配置回归集不应该变相禁止发布；想收紧就把资产配上。门禁配置属于 Evaluation Governance，Evolution 只封装"调用 + 结果解读"，**质量门禁的所有权在评测侧，进化侧只消费**——职责分离。

### 3.5 防过拟合：数据分层

进化最大的风险是对优化集过拟合——候选在优化数据上分高，实际没变好。设计上资产按用途分层：

```text
Optimization Set（优化用）
    ↓ 候选只在优化集上迭代
Validation Set（验收用，held-out）
    ↓ 优化过程看不到
Regression Set（回归防线，来自生产 Case 晋升）
    ↓ 保证旧失败不复发
Challenge Set（挑战集，held-out）
    ↓ 最难的 case，防"刷分"
Production / Shadow（最终验证）📐
```

（思想来源可提：借鉴 SkillOpt 的 bounded edit、多轮优化、held-out validation，但扩展成整个 Agent Runtime 的进化。）

---

## 四、难点与解决方案（本文核心）

### 难点 1：归因的不确定性——用 LLM 归因 LLM 的失败？

**问题**：拿到一个失败 Case，问"为什么失败"。直觉方案是让 LLM 读 trace 写归因报告。但 LLM 归因本身不可复现——同一 trace 跑两次归因可能不同。**归因是整个质量体系的地基，地基不确定，上面的回归集、进化决策全部悬空**。这叫二级不确定性：用不确定的工具去解释不确定的系统，误差乘法放大。

**方案**：归因退回**确定性规则 + 证据强度分级**。规则只看客观事实（Run 终态、事件序列），输出带 confidence；证据不足（trace 缺失）不猜，直接 HUMAN_REVIEW + confidence 0.0。LLM Judge 作为 evaluator 保留在打分层（那里容忍噪声），**不进归因层**。

**取舍**：确定性规则能归因的是"直接死因"（runtime/工具/模型/节点），归因不了"为什么模型选错工具"这类深层原因——后者留给人工或未来的校准过的 judge（这正是 EVALUATION_FAILURE 归因暂缓实现的原因：需要 judge 校准数据先行）。

### 难点 2：Case 重复挖掘

**问题**：同一生产失败会被多个信号源捕获（生产信号 + 多次评测失败），每次都建 Case 会重复计数，统计"本周新增 N 个问题"全是水分。

**方案**：`(source, trace_id)` 作为幂等键。同一信号源对同一 trace 只产一个 Case；不同信号源对同一 trace 各建一个（视角不同，信息不冗余）。

### 难点 3：归因结果会变，Case 状态不能跟着抖

**问题**：Case 晋升为回归资产后，如果重新归因得出不同结论，状态怎么处理？回退 PROMOTED 会让回归防线出现漏洞（时序上：A 归因 → 晋升 → B 重新归因不同 → 回退 → 窗口期内回归集少了这条防线）。

**方案**：归因历史 **append-only**（每次诊断追加记录，可回溯归因演变），但 Case 状态单向流转——**已 PROMOTED 不因重新归因回退**。宁可回归集里多一条"可能不再必要"的 case（成本是误报），不能少一条防线（成本是漏报）。安全系统的默认方向是保守。

### 难点 4：A/B 分配一致性——不要分配表

**问题**：传统 A/B 要维护"用户→分组"分配表。Agent 场景要求更强：同一 session 多次 Run 必须同组（否则用户上一条消息是 A 版回复、下一条是 B 版，体验分裂）。分配表 = 新增基础设施 + 一致性负担。

**方案**：**用计算代替存储**。`sha256(ab_test_id + session_id)` 的摘要切两段用：**前 8 字节**归一化成 [0,1) 均匀数与采样率比较（是否入选），**后 8 字节**归一化后按 variant 权重做累积分布映射（选哪组）。纯函数：同一 session 同一实验永远同一结果，零存储、零一致性维护、可离线重放验证。

**代价（主动说）**：均匀性依赖 session_id 分布，极端情况在 report 层按样本量解读。

### 难点 5：实验交叉污染

**问题**：如果同一应用同时跑两个 A/B 实验，一个 Run 同时被两个实验分组，效果变化无法归因给谁。

**方案**：强制**同一 application + runtime_type 同时只允许一个 RUNNING 实验**——宁可实验串行，不可交叉。这条守卫在 store 层实现（创建实验时校验）。

### 难点 6：多目标选择不可比

**问题**：候选 A pass_rate 高但 token 消耗高，候选 B 反之。直觉做法是加权合成单一分数——但权重是拍脑袋的，且**加分会掩盖"某维度塌了被总分摊平"**。

**方案**：三段式选择。硬约束先过滤（不及格的直接出局）；然后**字典序多目标比较**——按目标优先级逐维比，先比完第一维才看第二维。不合成加权分。没有有效优化目标时按 pass_rate 兜底。

**配套语义决策**：**指标缺失 = 未通过**（不是 0 分也不是跳过）——评测 summary 里找不到这个指标，说明没评到，不下赌注。与 Evaluation 的 UNKNOWN 语义对齐。

### 难点 7：LLM 生成补丁的不可控

**问题**：进化里候选由 LLM 生成（optimizer 产出 patch）。让 LLM 自由改写目标内容（prompt/skill 文档），一次"重写"可能夹带无关改动、格式破坏、越权修改。

**方案**：三层防线：
1. **表达层收紧**：候选的表达是封闭操作集（ADD/REPLACE/DELETE/INSERT）+ markdown 小节寻址——LLM 只能说"替换哪个小节"，说不出"重写整个文件"
2. **校验管线**：Syntax → Schema → **Edit Budget**（改动量上限）→ Permission（允许动哪些目标）四层校验，过了才是 Candidate
3. **治理层兜底**：自动发布七条件，任一不满足转人工

金句化：**LLM 负责提案，系统负责约束，人类保留否决权。**

### 难点 8：自动发布的信任边界

**问题**：什么情况下敢不让人看就发布新版本？

**方案**：不做单一判断，做成七条件合取（hard constraints / regression / challenge / edit budget / risk policy / quality gate / auto_release_enabled），且 **V1 只有 LOW 风险任务允许自动发布**。每个条件都有证据记录（evidence 呈现），blocked 时明确列出被谁拦下。人工不是被移除，是被精确到"什么时候必须出现"。

---

## 五、面试问答

**Q1：介绍一下你的评测体系？（总起题，必考）**

> 传统 Agent 评测只看最终答案对不对，我把评测对象扩展成四层：E2E 任务完成度、Process 过程质量（工具选择、参数）、Efficiency 代价（token/时延/轮次）、Risk 越界检测。落到实现是三层：离线评测——评测资产加评判器组成 suite，派发 worker 跑 Trial，规则类 evaluator 优先、LLM Judge 可用但不当 Ground Truth；在线闭环——生产失败信号自动挖掘 Case，确定性规则归因到 runtime/工具/模型/覆盖缺口或转人工，Case 晋升为回归资产，永久进入后续所有变更的质量门禁；再加 A/B——无状态粘性分流，按变体独立度量。评测之上是 Evolution：候选生成走 bounded edit，选择走硬约束过滤加字典序多目标比较，自动发布要过七条件门禁。一句话：**从"改了 prompt 不知道变好没"到"每次变更有量化对比、失败自动沉淀为回归防线"**。

**Q2：为什么归因不用 LLM？这不是更适合 LLM 的场景吗？（必被追问）**

> 恰恰相反。LLM 归因不可复现——同一份 trace 跑两次结论可能不同，这叫二级不确定性：用不确定的工具解释不确定的系统。归因是质量体系的地基：归因结果决定 Case 路由（0.9 的 runtime 失败可自动开修复任务、0.4 的覆盖缺口走补用例、0.0 必须人工），地基抖了整个下游都抖。所以我用确定性规则只看客观事实——Run 终态、事件序列——输出带 confidence 分级；trace 缺失证据不足时宁可转人工也不猜。LLM Judge 我保留在打分层，那里容忍噪声。什么时候才敢让 LLM 进归因？Judge 先过校准——用 Human Calibration Dataset 测 Judge Agreement、给 Judge 自己建回归测试。这也是 EVALUATION_FAILURE 归因我暂缓实现的原因：它是"评测自身的失败"，没有校准过的 judge 没资格判。

**Q3：归因错了怎么办？你的规则覆盖不了深层原因吧？**

> 覆盖不了，这是明确接受的范围。确定性规则归因的是"直接死因"（runtime 崩了/工具失败了/模型报错了/评测没覆盖到），归因不了"为什么模型选错工具"。两个缓解：第一，归因历史 append-only，人工推翻后重新归因是追加不是覆盖，可以回溯归因演变；第二，Case 晋升后状态单向——重新归因不会让它掉出回归集。宁多一条防线不少一条，安全系统默认保守。深层归因是 judge 校准之后的路线图。

**Q4：A/B 分流怎么做的？为什么不用分配表？**

> 用计算代替存储：sha256(实验 id + session id) 的摘要切两段，前 8 字节归一化后和采样率比较决定是否入选，后 8 字节按变体权重做累积分布映射选组。同一 session 同一实验永远同一组——纯函数，零存储、零一致性维护、可离线重放验证。Agent 场景这个性质特别重要：一个会话里多次 Run 必须同组，否则用户上一条消息是 A 版回复下一条是 B 版，体验分裂。守卫上，同一 application 同时只允许一个 RUNNING 实验——两个实验交叉分组，效果变化无法归因。代价是哈希均匀性依赖 session 分布，极端情况在报表层按样本量解读。

**Q5：Evolution 为什么不直接让 LLM 优化 prompt？现在 AutoPrompt 那类方案很多。**

> 那是"让 Agent 自己改 Prompt"，我做的是 **Evaluation 驱动的受控搜索**。三层防线：表达层——候选是封闭操作集 ADD/REPLACE/DELETE/INSERT 加 markdown 小节寻址，LLM 只能说"替换哪个小节"，说不出"重写整个文件"；校验层——语法、schema、编辑预算、权限四层校验，过了才是候选；治理层——自动发布七条件，V1 只有 LOW 风险能自动发，其余人工。另外防过拟合靠数据分层：优化集上迭代，验证集 held-out，回归集守旧失败，挑战集防刷分。一句话总结这个设计立场：**LLM 负责提案，系统负责约束，人类保留否决权**。

**Q6：候选之间怎么选择？多个指标怎么权衡？**

> 三段式：硬约束过滤、多目标比较、选择。最关键的设计是不合成单一加权分——加权分有两个问题：权重是拍的，而且会掩盖"某维度塌了被总分摊平"。我用字典序多目标比较：按优先级逐维比，第一维分出胜负就不看第二维。配套一个语义决策：指标缺失等于未通过，不是零分也不是跳过——评测 summary 里没这个指标说明没评到，不下赌注，和评测侧 UNKNOWN 语义对齐。

**Q7：什么情况下允许自动发布？**

> 七条件合取：硬约束、回归集、挑战集、编辑预算、风险策略、质量门禁、任务显式开启自动发布。V1 只有 LOW 风险可自动发，MEDIUM/HIGH 必须人工。有个设计点被质疑过：未配置的评测资产视为通过——回归集没配就不阻塞发布。理由是约束来自任务配置而非隐式失败：没配回归集应该去配，而不是变相禁止所有发布。想收紧随时配上资产。另外门禁实现属于 Evaluation 治理，Evolution 只调用和解读——质量门禁的所有权在评测侧，职责分离。

**Q8：回归集怎么来的？和普通测试集什么区别？**

> 普通测试集是写用例的时候想出来的；回归集是**生产失败自动沉淀的**。链路：生产 Run 失败信号或评测失败 Trial → 按 (source, trace_id) 幂等挖掘成 Case → 确定性归因 → OPEN 一路流转到 PROMOTED 挂入 REGRESSION 资产。从此每次变更的质量门禁都会跑它。本质区别：普通测试集测"我想象中的失败模式"，回归集测"真实发生过的失败模式"——后者才是真正的防线。幂等键的设计是因为同一失败会被多个信号源捕获，不去重统计全是水分。

**Q9：LLM Judge 在你体系里的位置？怎么保证它可靠？**

> 位置：evaluator 的一种，进 rubric 打分，和规则类 evaluator 并存——规则的优先，规则评不了的（开放性、过程质量）才给 judge。可靠性设计是分层置信链：Deterministic → Small Judge → Strong Judge → Human，Judge 不能当 Ground Truth。校准闭环是四步：Human Calibration Dataset、Judge Agreement（与人一致率）、Judge Regression（judge 自己的回归测试——多数团队想不到 judge 也需要回归测试）、Confidence Calibration。诚实边界：校准闭环目前是设计预留，V1 落地的是 Deterministic 层和 judge evaluator 本身。

**Q10：这部分有什么没做的？（主动亮边界）**

> 四项明确保留：① Shadow / 巡检调度——A/B 只做了分流度量，shadow 流量回放没做；② RANDOM_SAMPLE / MONITORING 信号源——Case 挖掘目前只从失败信号挖，随机抽样挖掘（发现"成功了但很差"的 case）没做；③ EVALUATION_FAILURE 归因——评测自身的失败需要 judge 校准数据先行；④ 执行侧 variant 切换——A/B 的 Assignment 是度量事实，按变体真正切换 agent_version 执行是下一步。每个不做都有明确理由，不是遗漏。

---

## 六、速查表

| 主题 | 硬细节 |
|---|---|
| 评测四层 | E2E / Process / Efficiency / Risk |
| 评测资产 | Golden / Bad Case / Good Case / Challenge / Calibration📐 / Inspection📐 |
| 资产 Purpose | Optimization / Validation / Regression / Challenge / Calibration / Inspection |
| Evaluator | rule（exact match 等）✅ + llm_judge ✅；Judge 不当 Ground Truth |
| Judge 置信链 | Deterministic → Small Judge → Strong Judge → Human |
| Case 幂等键 | `(source, trace_id)` |
| 归因规则 | trace 缺失→HUMAN_REVIEW(0.0)；Run FAILED→runtime(0.9)；Tool/LLM/NodeFailed→组件(0.8)；trace 干净→COVERAGE_GAP(0.4) |
| Case 状态 | PROMOTED 挂 REGRESSION 资产；重新归因不回退；归因历史 append-only |
| A/B 分流 | sha256(test_id:session_id)：前 8 字节定入选 / 后 8 字节按权重定组 |
| A/B 守卫 | 同一 application + runtime_type 仅一个 RUNNING 实验；assign 幂等 |
| A/B 报表 | runs / completion_rate / failure_rate / latency avg&max |
| Patch 操作集 | ADD / REPLACE / DELETE / INSERT；markdown 小节寻址；不可删 preamble |
| Patch 校验 | Syntax → Schema → Edit Budget → Permission → Candidate |
| Edit Budget | V1 token 估算 = 空白分词（避免 tokenizer 依赖的取舍） |
| 选择三段式 | 硬约束过滤 → 字典序多目标比较 → 选择；指标缺失=未通过 |
| 自动发布七条件 | HARD_CONSTRAINTS / REGRESSION / CHALLENGE / EDIT_BUDGET / RISK_POLICY / QUALITY_GATE / AUTO_RELEASE_ENABLED |
| 风险策略 | V1 仅 LOW 可自动发布；未配置资产视为条件通过（约束来自任务配置） |
| 架构关系 | Evolution 经 EvaluationGateway 消费评测；不实现评测引擎、不执行 Runtime |
| 保留项 | Shadow/巡检调度、RANDOM_SAMPLE/MONITORING 信号源、EVALUATION_FAILURE 归因、执行侧 variant 切换 |
