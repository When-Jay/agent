# Knowledge / RAG 模块使用指南

面向使用者的操作手册。架构设计见 [docs/architecture/08-knowledge-architecture.md](../architecture/08-knowledge-architecture.md)，
行为规格见 [docs/specs/knowledge-rag-spec.md](../specs/knowledge-rag-spec.md)，实施记录见 [docs/plans/070-knowledge-rag.md](../plans/070-knowledge-rag.md)。

## 1. 模块概览

Knowledge 模块提供知识库管理与检索能力（RAG），核心模型是 **锚点 + parent/child 分片**：

```text
Markdown 文档
   │  按 ATX 标题（默认 ## 与 ###）切分
   ▼
Anchor Sections（锚点小节，每个 = parent）
   │  parent = 完整小节全文 → 进入 LLM
   │  child  = 重叠滑窗（overlap）→ 检索单元
   ▼
Embedding → 余弦检索（child 层）
   │  child 命中 → 按 parent 分组取最高分 → 返回 parent 全文
   ▼
Parent Results（small-to-big 检索）
```

关键性质：

- **parent 进 LLM、child 做检索**：命中碎片时返回的是完整小节，模型拿到的是自洽上下文。
- **锚点即增量单元**：文档更新时对比新旧锚点集合（content-hash），只重嵌 added/changed 小节，unchanged 小节零成本短路。
- **确定性 ID**：`parent_id = {document_id}:{anchor_key}`，`child_id = {parent_id}:{index}`，重跑可复现。
- **LangChain 只出现在两个接缝**（chunking/embeddings），检索与存储均为自研实现，可整体替换 provider。

## 2. 快速开始

### 2.1 配置

环境变量（见 [.env.example](../../.env.example)）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `KNOWLEDGE_EMBEDDING_PROVIDER` | `openai` | `openai` 或 `fake`（测试/开发，无外部依赖） |
| `KNOWLEDGE_OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embedding 模型名 |
| `KNOWLEDGE_TOOL_ENABLED` | `0` | `1`=worker 给 agent 注册 `retrieve_knowledge` 工具 |

Embedding provider 是**惰性构造**的：API 启动不依赖 `OPENAI_API_KEY`，
密钥缺失只影响 ingest/retrieve（返回 503），知识库管理路由始终可用。

### 2.2 数据库

- **PostgreSQL（生产/compose）**：迁移由 api 进程自动执行（`alembic upgrade head`，含 `0002_knowledge_schema`），无需手工操作。
- **SQLite（本地/测试）**：建表走 `metadata.create_all`，开箱即用。

### 2.3 用 fake provider 本地跑通（无 API Key）

```bash
set KNOWLEDGE_EMBEDDING_PROVIDER=fake   # PowerShell: $env:KNOWLEDGE_EMBEDDING_PROVIDER="fake"
uvicorn agent_platform.api.main:app --port 8000
```

fake provider 是确定性的 hash 向量（dim=64），仅用于验证链路与集成测试，检索质量无意义。

## 3. HTTP API

路由前缀 `/api/v1/knowledge`，共 9 条：

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | `/bases` | 创建知识库（201） |
| GET | `/bases` | 列出知识库 |
| GET | `/bases/{kb_id}` | 知识库详情（含 embedding 维度信息） |
| DELETE | `/bases/{kb_id}` | 删除知识库（级联删除文档与索引，204） |
| POST | `/bases/{kb_id}/documents` | 上传/增量更新文档（**upsert，返回 200**） |
| GET | `/bases/{kb_id}/documents` | 文档清单（状态/版本/分片计数） |
| GET | `/bases/{kb_id}/documents/{document_id}` | 文档详情 |
| DELETE | `/bases/{kb_id}/documents/{document_id}` | 删除文档及其索引（204） |
| POST | `/bases/{kb_id}/retrieve` | 检索 |

### 3.1 创建知识库

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/bases \
  -H "Content-Type: application/json" \
  -d '{"name": "product-docs"}'
```

可选参数（不传即用默认值）：

| 字段 | 默认 | 约束 |
|---|---|---|
| `anchor_levels` | `[2, 3]` | 1..6 的非空去重升序列表 |
| `child_chunk_size` | `800` | 100..8000 字符 |
| `child_overlap` | `150` | 必须满足 `0 < overlap < chunk_size` |

`anchor_levels` 决定哪些标题级别切出锚点：`[2,3]` 表示 `##` 和 `###` 各自成节；
写 `[2]` 则 `###` 及更深的标题只是所属小节的正文。

### 3.2 上传文档（ingest）

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/bases/{kb_id}/documents \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "handbook-v2",
    "title": "产品手册",
    "source_uri": "s3://docs/handbook.md",
    "content": "# 手册\n\n## 快速开始\n\n正文……\n\n### 安装\n\n安装正文……"
  }'
```

- 同一 `external_id` 重复上传 = 增量更新（upsert），不是重复创建。
- 内容需为 Markdown ATX 标题格式；围栏代码块（``` / ~~~）内的 `#` 不会误判为标题。
- 无任何锚点标题的文档只有 preamble 一节，仍可正常索引。

响应（ingest 报告）：

```json
{
  "document_id": "doc_...",
  "external_id": "handbook-v2",
  "doc_version": 0,
  "content_hash": "…",
  "anchors_added": 4, "anchors_changed": 0, "anchors_removed": 0,
  "children_indexed": 23, "children_deleted": 0,
  "unchanged": false
}
```

### 3.3 检索

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/bases/{kb_id}/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "如何安装", "top_k": 8, "max_parents": 4, "document_ids": []}'
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `top_k` | `8` | 取多少个 child 窗口参与分组 |
| `max_parents` | `4` | 最终返回的 parent（小节）数量上限 |
| `document_ids` | `[]` | 非空时限定检索范围 |

检索流程：child 余弦相似度 → 按 parent 分组取组内最高分 → 降序 → 截断 `max_parents` → 返回 parent 全文。
每条结果含 `content / score / display_path / anchor_level / heading / document_id / external_id / document_metadata`，
`display_path` 由**锚点层级标题栈**拼接（`" > "` 连接，与 `anchor_levels` 对应；
默认 `[2,3]` 时 `### 安装` 位于 `## 快速开始` 下则显示 `快速开始 > 安装`，文档 `# 一级标题` 不入栈；
重复标题自动加 ` [n]` 后缀），可直接作为引用来源展示。

### 3.4 错误码

| 状态码 | 触发条件 |
|---|---|
| 404 | 知识库/文档不存在 |
| 422 | 参数校验失败（anchor_levels、chunk 参数、空 query 等） |
| 409 | 知识库已用模型 A 索引过，现请求模型 B（维度/模型一致性保护，见 §6.3） |
| 503 | embedding provider 不可用（如 `OPENAI_API_KEY` 缺失、网络失败） |

## 4. Python 直接调用

不经过 HTTP 的场景（脚本、测试、其他 service）：

```python
from agent_platform.config import Settings
from agent_platform.knowledge.application import KnowledgeService
from agent_platform.knowledge.embeddings import create_embedding_provider
from agent_platform.infrastructure.knowledge_sqlalchemy_store import create_knowledge_store

settings = Settings()
service = KnowledgeService(
    create_knowledge_store(settings.database_url),
    create_embedding_provider(settings),
)

kb = service.create_knowledge_base("docs")
report = service.ingest(kb.id, "handbook-v2", markdown_content)

# 结构化结果（含 score/display_path 等）
results = service.retrieve(kb.id, "如何安装", top_k=8, max_parents=4)

# 直接拼装成 LLM 可注入的字符串（R5 格式）
context = service.get_context(kb.id, "如何安装")
# ## Source: 快速开始 > 安装 (handbook-v2)
# <parent 全文>
```

`get_context` 的输出格式（每段一个来源头 + 全文 + 空行）是平台约定的 R5 格式，
工具调用与 API 消费方保持一致。

## 5. 增量更新语义

ingest 对同一 `external_id` 的行为由内容 hash 与锚点 diff 决定：

1. **内容完全未变** 且文档处于 READY：整体短路，`unchanged=true`，不触碰 provider，`doc_version` 不变。
2. **内容变化**：解析新锚点并与旧锚点 diff（added/changed/removed），
   仅对 added∪changed 小节的 children 做一次批量 embedding，removed 小节的索引行随事务删除。
3. **版本号**：`doc_version` 只在 READY 前驱上 +1（首次成功索引为 0）。
4. **失败恢复**：provider 失败时文档标记 `FAILED`，**旧索引完整保留**（检索不受影响），
   `content_hash` 保留上次成功索引的值——修复 provider 后重新上传即可续跑。
   失败原因记录在文档的 `error` 字段（见文档清单接口）。

## 6. 与 Agent 集成

### 6.1 retrieve_knowledge 工具（worker 侧）

`.env` 开启：

```bash
KNOWLEDGE_TOOL_ENABLED=1
```

worker 会向 agent 的原生工具注册表注册 `retrieve_knowledge`：

```json
{
  "knowledge_base_id": "kb_...",
  "query": "如何安装",
  "top_k": 8
}
```

工具返回 R5 格式字符串；错误经工具结果通道呈现（registry 统一捕获异常）。
默认关闭——开启前已存在的 agent 工具面不受影响。

### 6.2 平台能力接口

其他模块（如未来 workflow 节点、evaluation）应通过能力接口消费，而非直接 import knowledge 模块：

```python
from agent_platform.runtime.capabilities.knowledge import KnowledgeCapability

class KnowledgeCapability(ABC):
    def retrieve(self, kb_id, query, *, top_k=..., max_parents=..., document_ids=...) -> list[RetrievedContext]: ...
    def get_document(self, kb_id, document_id) -> dict | None: ...
    def get_context(self, kb_id, query, **kwargs) -> str: ...
    # rerank 为保留扩展点
```

`agent_platform.knowledge.capability.PlatformKnowledgeCapability` 是其默认实现（委托 KnowledgeService）。

### 6.3 模型一致性（重要）

知识库**首次成功 ingest 时**捕获 embedding 模型名与维度并固化到 KB。
此后若配置切换到不同模型/维度：

- ingest/retrieve 返回 **409**（`EmbeddingModelChangedError`）；
- 管理路由不受影响；
- 需重建：新建 KB 重新 ingest，或删除 KB 后重建（暂不支持全量重嵌）。

## 7. 依赖与边界速查

- 依赖方向：`api → knowledge → runtime.capabilities → infrastructure`；
  knowledge 模块禁止 import api/observability/evaluation/sandbox 等（边界测试 `tests/test_architecture_boundaries.py` 强制）。
- LangChain 仅允许出现在 `knowledge/chunking.py`（text splitter）与 `knowledge/embeddings.py`（embedding provider）两个接缝。
- 存储为 4 表：`knowledge_bases / documents / parents / children`；锚点清单从 parents 行派生，无独立锚点表。
- 新增依赖：`langchain-text-splitters`、`langchain-openai`、`numpy`（见 [pyproject.toml](../../pyproject.toml)）。
