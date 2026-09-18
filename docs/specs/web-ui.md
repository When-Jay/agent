# AI Agent & Workflow Platform UI Development Spec

## 1. 项目目标

为现有的 AI Agent & Workflow Platform 开发一套完整的 Web UI。

平台定位：

> 企业级 AI Agent & Workflow Runtime Platform

平台核心能力：

* Agent Runtime
* Workflow Runtime
* Context Management
* Memory
* Knowledge / RAG
* Skill
* MCP / Tool
* Sandbox
* Observability
* Evaluation
* Evolution
* Run / Checkpoint / HITL
* Budget / Policy

本次任务重点是完成 **管理端 Admin Portal + 用户端 User Portal** 的 UI。

目标不是制作一个单纯的 Dashboard，而是通过 UI 将平台核心运行链路完整体现出来：

```text
Agent / Workflow Configuration
        ↓
User Task
        ↓
Runtime Execution
        ↓
Run / Trace
        ↓
Evaluation
        ↓
Diagnosis
        ↓
Evolution
        ↓
New Version
```

---

# 2. 开发原则

## 2.1 首先检查现有项目

开始开发前：

1. 检查现有前端项目结构。
2. 检查 package.json。
3. 检查已有 UI 组件库。
4. 检查已有路由。
5. 检查现有 API Client。
6. 检查后端 API。
7. 检查已有数据模型。
8. 尽量复用已有组件和接口。

不要因为本 Spec 重新创建一套前端技术栈。

如果项目已有 UI Framework / Component Library，优先复用。

---

## 2.2 不要过度实现后端

本次主要目标是 UI。

如果后端接口已经存在：

> 直接接真实 API。

如果后端接口暂时不存在：

> 使用 Mock Service / Mock Data，保证 UI 可以完整运行。

不要为了 UI 修改 Runtime、Agent Loop、Evaluation、Evolution 等核心后端架构。

---

# 3. UI 总体结构

整个产品分成两个 Portal：

```text
AI Agent Platform
│
├── Admin Portal
│   ├── 工作台
│   ├── Agent 管理
│   ├── Workflow 管理
│   ├── 知识库管理
│   ├── 运行管理
│   ├── 评测管理
│   ├── 进化管理
│   ├── MCP / Tool 管理
│   └── 系统设置
│
└── User Portal
    ├── Agent 对话
    ├── 知识库
    ├── 我的任务
    ├── 运行历史
    └── 个人中心
```

---

# 4. 全局 Layout

## 4.1 Admin Layout

左侧 Sidebar：

```text
AI Agent Platform

工作台

Agent 管理
Workflow 管理
知识库管理

运行管理

评测管理
进化管理

MCP / Tools

系统设置
```

顶部：

```text
页面标题
Breadcrumb
搜索
通知
当前用户
```

---

## 4.2 User Layout

左侧 Sidebar：

```text
AI Agent Platform

对话
知识库
我的任务
运行历史
个人中心
```

用户端视觉上应该明显比 Admin 简洁。

---

# 5. Admin Portal

---

# 5.1 工作台 Dashboard

Route：

```text
/admin
```

目标：

展示平台整体运行情况。

## 顶部指标

显示：

```text
Agent 数量
Workflow 数量
Knowledge Base 数量
今日 Run 数量
```

第二组：

```text
Task Success Rate
平均 Latency
Token Usage
Cost
```

## 运行趋势

折线图：

```text
过去 7 天 Run 数量
过去 7 天成功率
```

## Run 状态分布

```text
Running
Completed
Failed
Waiting Human
Cancelled
```

## Failure Distribution

```text
Generation Failure
Retrieval Failure
Tool Failure
Agent Failure
System Failure
Safety Failure
```

## 最近运行

表格：

```text
Run ID
Task
Agent / Workflow
Status
Duration
Token
Cost
Created At
Action
```

点击进入 Run Detail。

---

# 6. Agent 管理

Route：

```text
/admin/agents
```

## Agent List

支持：

* 搜索
* 类型过滤
* 状态过滤
* Version
* 创建时间
* 分页

表格：

```text
Name
Type
Version
Model
Knowledge
Tools
Status
Updated At
Actions
```

Actions：

```text
查看
编辑
复制
发布
停用
运行
查看运行记录
```

---

# 7. Agent Detail

Route：

```text
/admin/agents/:id
```

使用 Tab：

```text
基本信息
Prompt
Model
Knowledge
Skills
Tools
Memory
Policy
Runtime
Evaluation
Versions
```

---

## 7.1 Basic

显示：

```text
Name
Description
Type
Owner
Status
Current Version
Created At
Updated At
```

---

## 7.2 Prompt

显示：

```text
System Prompt
User Prompt Template
Variables
```

支持编辑。

显示：

```text
Current Version
Last Modified
Change History
```

---

## 7.3 Model

配置：

```text
Provider
Model
Temperature
Max Tokens
```

---

## 7.4 Knowledge

这是 Agent 使用 RAG 的配置入口。

显示：

```text
Knowledge Scope

☑ 公司制度知识库
☑ 合同知识库
☑ 产品知识库
```

Retrieval Configuration：

```text
Strategy:
Hybrid

Top K:
10

Reranker:
Qwen3-Reranker

Enable Query Rewrite:
ON

Citation:
ON
```

注意：

Agent 这里只配置 **Knowledge Scope 和 Retrieval Policy**。

知识库本身的生命周期管理放在 Knowledge Management。

---

## 7.5 Skills

显示 Agent 当前绑定的 Skill：

```text
Contract Review Skill
Document Analysis Skill
Risk Detection Skill
```

支持：

```text
添加
删除
查看版本
```

---

## 7.6 Tools / MCP

显示：

```text
Tool
Type
Permission
Risk Level
Status
```

例如：

```text
knowledge_search    Built-in
sandbox_exec        Built-in
github              MCP
web_search          MCP
```

---

## 7.7 Memory

配置：

```text
Enable Memory
Memory Scope
Write Policy
Read Policy
```

例如：

```text
Memory Scope:
User

Write:
After Session

Read:
Before Turn
```

---

## 7.8 Runtime

配置：

```text
Max Turns
Max Tokens
Max Cost
Max Tool Calls
Max Execution Time

Checkpoint
HITL
Sandbox
```

---

## 7.9 Evaluation

显示当前 Agent 的 Evaluation 配置：

```text
Evaluation Policy
Dataset
Rubric
Evaluator
Quality Gate
```

显示最近评测结果：

```text
Task Success
Retrieval Recall
Tool Success
Safety
Latency
Cost
```

---

## 7.10 Versions

版本列表：

```text
Version
Status
Created By
Created At
Evaluation
Release
Actions
```

支持：

```text
查看
Compare
Rollback
Publish
```

---

# 8. Workflow 管理

Route：

```text
/admin/workflows
```

Workflow List：

```text
Name
Version
Status
Run Count
Success Rate
Updated At
Actions
```

---

# 9. Workflow Editor

Route：

```text
/admin/workflows/:id/editor
```

提供简单 DAG 编辑器。

节点：

```text
Start
LLM
Agent
Knowledge Retrieval
Tool
Condition
Parallel
Human Approval
End
```

示例：

```text
Start
 ↓
Parse Document
 ↓
Knowledge Retrieval
 ↓
Extract Fields
 ↓
Risk Check
 ↓
Human Approval
 ↓
Generate Report
 ↓
End
```

V1 不需要实现复杂的 Workflow Builder。

重点是能够：

* 查看节点
* 查看节点配置
* 查看连线
* 编辑基础参数
* 保存版本

---

# 10. Knowledge Management

Route：

```text
/admin/knowledge
```

这是 Knowledge/RAG 平台的管理入口。

## Knowledge Base List

显示：

```text
Name
Description
Document Count
Chunk Count
Status
Permission
Updated At
Actions
```

Actions：

```text
查看
编辑
删除
检索测试
权限
```

---

# 11. Knowledge Base Detail

Route：

```text
/admin/knowledge/:id
```

Tabs：

```text
概览
文档
数据源
检索
权限
版本
统计
```

---

## 11.1 Documents

支持：

```text
上传
删除
重新解析
重新索引
查看详情
```

显示：

```text
Document
Size
Type
Version
Processing Status
Chunk Count
Updated At
```

状态：

```text
Pending
Processing
Completed
Failed
```

---

## 11.2 Document Detail

显示：

```text
Original File
Parsing Result
Metadata
Chunks
Index Status
Version
Processing Logs
```

Chunk 查看：

```text
Chunk ID
Content
Parent
Anchor
Metadata
Embedding Status
```

---

# 12. Knowledge Retrieval Test

Route：

```text
/admin/knowledge/:id/retrieval
```

提供一个检索调试页面。

输入：

```text
Query
```

配置：

```text
Retrieval Strategy
Top K
Reranker
Filters
```

点击：

```text
[Search]
```

结果：

```text
Rank
Document
Chunk
Retrieval Score
Rerank Score
Metadata
```

同时支持切换：

```text
BM25
Vector
Hybrid
Hybrid + Rerank
```

这是一个重点页面。

---

# 13. Knowledge Permission

显示：

```text
Tenant
Department
User
Role
Knowledge Scope
```

示例：

```text
合同知识库

产品部 ✓
法务部 ✓
财务部 ✕

User A ✓
User B ✓
```

---

# 14. Run Management

Route：

```text
/admin/runs
```

显示所有 Agent / Workflow Run。

Filter：

```text
Agent
Workflow
Status
User
Time
Failure Type
```

状态：

```text
Running
Completed
Failed
Waiting Human
Cancelled
```

---

# 15. Run Detail / Trace

Route：

```text
/admin/runs/:id
```

这是平台最重要的调试页面之一。

页面结构：

```text
Run Overview
Timeline
Context
LLM Calls
Tool Calls
Retrieval
Events
Checkpoint
Artifacts
Evaluation
```

Timeline：

```text
Run Started
 ↓
Turn Started
 ↓
LLM Call
 ↓
Tool Call: knowledge_search
 ↓
Retrieval
 ↓
LLM Call
 ↓
Tool Call: sandbox.exec
 ↓
HITL
 ↓
Human Approval
 ↓
LLM Call
 ↓
Run Completed
```

点击每一个节点显示详细信息。

---

# 16. Evaluation Management

Route：

```text
/admin/evaluation
```

Tabs：

```text
评测任务
数据集
Rubric
Evaluator
Experiments
Cases
Online Evaluation
Quality Gate
```

---

# 17. Evaluation Task

列表：

```text
Task
Target
Dataset
Rubric
Status
Pass Rate
Created At
```

创建 Evaluation Task：

```text
Target
Dataset
Evaluation Type

☑ E2E
☑ Process

Rubric
Evaluator
Sampling
```

---

# 18. Evaluation Result

显示：

```text
Task Success
Retrieval Recall
Generation Quality
Tool Success
Safety
Latency
Cost
```

支持：

```text
Case Detail
Trace
Failure Diagnosis
Compare Version
```

---

# 19. Rubric Management

Rubric：

```text
Rubric
 ├── Dimension
 │    ├── Criterion
 │    └── Evaluator
 │
 └── Dimension
```

示例：

```text
Contract Review Rubric

1. Field Accuracy
2. Risk Detection
3. Citation Correctness
4. Output Format
```

每个 Criterion：

```text
Definition
Positive Example
Negative Example
Scoring Rule
Evidence Requirement
Threshold
```

---

# 20. Evaluation Cases

Case 类型：

```text
Good
Bad
Golden
Challenge
Calibration
Inspection
```

支持：

```text
查看 Trace
查看 Evaluation
标记 Failure Type
添加到 Dataset
```

---

# 21. Evolution Management

Route：

```text
/admin/evolution
```

Tabs：

```text
Evolution Tasks
Candidates
Experiments
Decisions
History
```

---

# 22. Evolution Task

列表：

```text
Task
Target
Diagnosis
Optimizer
Status
Best Candidate
Created At
```

例如：

```text
合同审核 Agent
Diagnosis:
Retrieval Failure

Target:
RAG Configuration

Strategy:
RAG Optimizer
```

---

# 23. Evolution Detail

展示完整 Evolution Pipeline：

```text
Trigger
 ↓
Diagnosis
 ↓
Evolution Target
 ↓
Strategy
 ↓
Candidates
 ↓
Experiment
 ↓
Evaluation
 ↓
Gate
 ↓
Decision
```

---

# 24. Candidate Comparison

Candidate A / B / C 横向比较：

```text
                    A        B        C

Validation         92%      95%      93%
Regression          97%      97%      96%
Challenge           89%      93%      91%
Cost                ↓5%      ↑2%      ↓8%
Latency             ↓3%      ↑5%      ↓4%

Edit Count           2        3        1
```

注意：

这里不要只显示一个 Overall Score。

需要展示：

* Validation
* Regression
* Challenge
* Cost
* Latency
* Safety
* Edit Scope

---

# 25. Evolution Decision

显示：

```text
Selected Candidate

Decision:
ACCEPT / REJECT / HUMAN_REVIEW

Evidence:

✓ Validation Passed
✓ Regression Passed
✓ Challenge Passed
✓ Safety Passed
✓ Edit Budget Passed

Gate Result

[Approve]
[Reject]
[Rollback]
```

---

# 26. MCP / Tool Management

Route：

```text
/admin/tools
```

显示：

```text
Tool Name
Provider
Type
Permission
Risk
Status
Usage
```

MCP Detail：

```text
Server
Tools
Authentication
Permission
Rate Limit
Risk Policy
Usage
Logs
```

---

# 27. System Settings

Route：

```text
/admin/settings
```

Tabs：

```text
Platform
Models
Storage
Sandbox
Permission
Observability
Evaluation
```

只做基础配置 UI。

不要在 V1 实现复杂 IAM。

---

# 28. User Portal

---

# 29. Agent Chat

Route：

```text
/app/chat
```

这是用户最核心的页面。

布局：

```text
┌────────────┬─────────────────────────────┐
│ Agent List │                             │
│            │        Conversation         │
│ 合同审核    │                             │
│ 数据分析    │ User                        │
│ Web Agent  │ 帮我审核这个合同             │
│            │                             │
│            │ Agent                       │
│            │ 正在分析合同...              │
│            │                             │
│            │ 🔎 查询知识库                │
│            │ ✓ 找到 8 个相关文档           │
│            │ 📄 分析合同                  │
│            │ ⏳ 等待人工审批              │
│            │                             │
│            │ 最终结果...                   │
│            │                             │
│            │ [输入消息................] ➤ │
└────────────┴─────────────────────────────┘
```

不要展示内部 Chain-of-Thought。

只展示可解释的 Execution Events：

```text
检索知识
调用工具
分析文件
执行代码
等待审批
完成
```

---

# 30. Agent Execution Detail

用户可以点击：

```text
查看执行过程
```

展示：

```text
Task
Status
Duration
Tools
Knowledge
Artifacts
Human Approval
```

普通用户不显示完整系统 Prompt。

---

# 31. User Knowledge Base

Route：

```text
/app/knowledge
```

用户只能管理自己有权限的知识库。

功能：

```text
创建知识库
上传文件
删除文件
查看文件
搜索
```

不默认展示：

```text
Embedding
Vector Index
Chunk Strategy
Reranker
```

这些属于平台技术细节。

可以提供一个简单：

```text
[测试知识库]
```

入口。

---

# 32. My Tasks

Route：

```text
/app/tasks
```

显示：

```text
Task
Agent / Workflow
Status
Created At
Duration
Action
```

状态：

```text
Running
Completed
Waiting Approval
Failed
Cancelled
```

点击进入 Run Detail。

---

# 33. History

Route：

```text
/app/history
```

显示用户历史 Agent / Workflow Run。

支持：

```text
Search
Filter
Date
Agent
Status
```

---

# 34. Personal Center

Route：

```text
/app/profile
```

显示：

```text
Basic Information

My Agents

My Knowledge Bases

Usage Statistics
```

Usage：

```text
Runs
Token Usage
Execution Time
```

---

# 35. 页面之间的核心关系

整个 UI 必须体现下面的数据流：

```text
Admin

Agent
  │
  ├── Knowledge
  ├── Skill
  ├── Tool
  ├── Memory
  ├── Policy
  └── Runtime
       │
       ↓
    Version
       │
       ↓
     Publish
       │
       ↓
────────────────────────────────

User

Task
 ↓
Agent / Workflow
 ↓
Run
 ↓
Trace
 ↓
Evaluation
 ↓
Case / Diagnosis
 ↓
Evolution
 ↓
New Version
 ↓
Publish
```

---

# 36. Mock Data

如果真实 API 不存在，创建 Mock Service。

至少提供：

```text
agents
workflows
knowledgeBases
documents
runs
traces
evaluations
rubrics
evaluationCases
evolutionTasks
evolutionCandidates
tools
```

Mock 数据不要全部为空。

至少准备：

* 5 个 Agent
* 4 个 Workflow
* 6 个 Knowledge Base
* 20 个 Documents
* 20 个 Runs
* 3 个 Evaluation Tasks
* 3 个 Evolution Tasks
* 6 个 Evolution Candidates
* 8 个 Tools

状态需要覆盖：

```text
Success
Running
Failed
Waiting Human
Processing
Pending
```

这样页面能够体现真实平台运行状态。

---

# 37. 通用 UI 组件

优先抽象：

```text
StatusBadge
MetricCard
DataTable
SearchBar
FilterBar
DetailDrawer
DetailPanel
Timeline
EventItem
VersionSelector
Tag
EmptyState
LoadingState
ErrorState
ConfirmDialog
JSONViewer
CodeEditor
MarkdownViewer
```

Run / Evaluation / Evolution 中尽量复用组件。

---

# 38. UI 视觉要求

整体风格：

> Enterprise AI Platform / Modern SaaS

要求：

* 简洁
* 专业
* 信息密度适中
* 不要过度炫技
* 不要大量渐变
* 不要做成游戏化 AI UI
* 不要大量动画
* Dashboard 使用卡片 + 表格 + Timeline
* 状态使用明确的 Badge
* Detail 页面强调信息层级

颜色语义保持统一：

```text
Success
Running
Warning
Failed
Waiting
Disabled
```

---

# 39. 响应式

优先支持：

```text
Desktop
1280px+
```

本项目主要面向企业后台和桌面用户。

移动端不作为 V1 重点。

---

# 40. 开发顺序

不要一次性实现所有页面。

按以下顺序：

### Phase 1 — Shell

实现：

```text
Admin Layout
User Layout
Sidebar
Header
Routing
Theme
Common Components
```

### Phase 2 — Core Runtime UI

实现：

```text
Dashboard
Agent List
Agent Detail
Workflow
Run Management
Run Detail
```

### Phase 3 — Knowledge

实现：

```text
Knowledge List
Knowledge Detail
Documents
Document Detail
Retrieval Test
```

### Phase 4 — Evaluation

实现：

```text
Evaluation Tasks
Dataset
Rubric
Cases
Evaluation Result
```

### Phase 5 — Evolution

实现：

```text
Evolution Task
Candidate Comparison
Experiment
Decision
```

### Phase 6 — User Portal

实现：

```text
Agent Chat
User Knowledge
Tasks
History
Profile
```

---

# 41. V1 不实现

以下内容只做 UI Placeholder 或 Mock：

```text
复杂 IAM
SSO
计费
组织架构
复杂 Workflow Builder
复杂 Prompt IDE
真正的在线 Evolution 自动发布
复杂 Agent Marketplace
多区域部署
复杂 Kubernetes 运维
```

不要为了“看起来功能很多”而扩展范围。

---

# 42. 最重要的验收标准

完成后，用户应该能够在 UI 中完整走通：

## Scenario 1：创建 Agent

```text
Admin
 ↓
Create Agent
 ↓
Configure Model
 ↓
Configure Knowledge
 ↓
Configure Tools
 ↓
Configure Runtime
 ↓
Save Version
```

## Scenario 2：用户运行 Agent

```text
User
 ↓
Select Agent
 ↓
Send Task
 ↓
Agent Running
 ↓
Knowledge Retrieval
 ↓
Tool Calling
 ↓
Final Answer
```

## Scenario 3：查看运行过程

```text
Admin
 ↓
Run Management
 ↓
Run Detail
 ↓
Timeline
 ↓
LLM / Tool / Retrieval / HITL
```

## Scenario 4：评测

```text
Evaluation
 ↓
Select Agent Version
 ↓
Dataset
 ↓
Rubric
 ↓
Run Evaluation
 ↓
Result
 ↓
Failure Case
```

## Scenario 5：进化

```text
Evaluation Failure
 ↓
Diagnosis
 ↓
Evolution Task
 ↓
Candidate A/B/C
 ↓
Experiment
 ↓
Evaluation
 ↓
Gate
 ↓
Decision
 ↓
New Version
```

---

# 43. 最终要求

开发完成后，整个 UI 应该让一个第一次看到项目的人能够理解：

> 这不是一个简单的 ChatGPT Web UI，也不是一个单纯的 Agent CRUD 系统。

而是：

```text
Agent
  +
Workflow
  +
Knowledge
  +
Tool/MCP
  +
Sandbox
  +
Runtime
  +
Observability
  +
Evaluation
  +
Evolution
```

组成的企业级 AI Agent Runtime Platform。

核心产品闭环：

```text
        ┌───────────────┐
        │ Agent/Workflow│
        └───────┬───────┘
                ↓
             Runtime
                ↓
             Run/Trace
                ↓
           Evaluation
                ↓
            Diagnosis
                ↓
            Evolution
                ↓
             Version
                │
                └──────────────→ Runtime
```

优先保证这条链路在 UI 中完整可见，而不是追求页面数量。
