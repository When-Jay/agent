# Knowledge / RAG Architecture

## 1. Overview

Knowledge provides knowledge base management and retrieval (RAG) for
the platform.

The Knowledge system is responsible for:

* Knowledge base lifecycle and configuration
* Document ingestion (parse → anchor → chunk → embed → index)
* Anchor-based incremental document updates
* Parent/child (small-to-big) retrieval
* Retrieval API, Runtime Capability, and Agent tool surface

The Knowledge system is **not responsible for**:

* Deciding when to retrieve (Agent Runtime / Context Manager decides)
* LLM reasoning over retrieved content
* Memory (cross-session user/agent state is the Memory capability)
* Retrieval quality evaluation (Evaluation system consumes retrieval
  data; Recall@K evaluation is reserved there)
* Authentication / authorization (injected by the operator's own
  auth system, as with the rest of the API)
* File/blob storage (documents are ingested by content; source URIs
  are stored as references only)

---

## 2. Architectural Position

Knowledge is a Runtime Capability reserved by the system overview
(00-system-overview.md sections 2 and 4: "Knowledge / RAG" capability,
"Knowledge Service" infrastructure service, peer of MCP Gateway and
Sandbox Service). It is therefore implemented as:

```text
                         API Layer
                             |
              (knowledge management + retrieve routes)
                             |
                             v
                    Knowledge Service
                    (agent_platform.knowledge)
                             ^
                             |
                    Runtime Capabilities
                    (KnowledgeCapability interface)
                             ^
                             |
                 +-----------+-----------+
                 |                       |
          Agent Runtime           Workflow Runtime
                 |
          retrieve_knowledge tool
```

```text
Knowledge Service
        |
        +-- KnowledgeStore port ----> SQLAlchemy store (PostgreSQL/SQLite)
        |                                (pgvector / ES adapters reserved)
        |
        +-- EmbeddingProvider port --> LangChain Embeddings adapter
                                         (OpenAI embeddings; fake for tests)
```

Two placements, following existing house patterns:

* **Interface**: `runtime/capabilities/knowledge.py` — the
  `KnowledgeCapability` reserved in runtime-capabilities-spec.md
  section 4. Runtimes depend on this abstraction only.
* **Implementation**: top-level module `agent_platform.knowledge` —
  the Knowledge Service. This is not a new architectural module: it is
  pre-declared by the system overview (00-system-overview.md section 2)
  the same way `agent_platform.sandbox` and `agent_platform.mcp`
  realize their reserved services.

Dependency direction follows the house rule:

```text
API -> knowledge (service) -> runtime.capabilities (interface) / store ports
Infrastructure adapters implement store ports at composition roots.
```

---

## 3. Design Principles

### 3.1 Framework as Implementation Component

LangChain is the implementation base (per technology direction), but
the platform's knowledge domain stays framework-free. LangChain is
confined to two declared seams:

| Seam | LangChain usage |
| --- | --- |
| `knowledge/embeddings.py` | wraps any `langchain_core.embeddings.Embeddings` behind the platform `EmbeddingProvider` port (OpenAI adapter via `langchain-openai`) |
| `knowledge/chunking.py` | child splitting delegates to `langchain_text_splitters.RecursiveCharacterTextSplitter` |

The anchor parser, incremental diff engine, domain models, and stores
never import LangChain. Architecture boundary tests enforce this
(mirroring how Evolution confines frameworks). DeepAgents, LangGraph,
and Celery remain forbidden inside the module.

### 3.2 Retrieval Unit ≠ Context Unit

The retrieval unit and the LLM context unit are different sizes, and
the system keeps them explicitly separated:

```text
child chunk  (small, overlapping)  -> embedded, searched
parent chunk (whole anchor section) -> fetched, sent to the LLM
```

A small child matches precisely (dense embedding favors short focused
text); the parent supplies the LLM with complete, coherent context
(whole section under one anchor heading). This is the standard
parent-child / small-to-big retrieval pattern, expressed with anchors
as the parent boundary.

### 3.3 Anchor as Structural Identity

A document is not a byte stream; it is a tree of **anchors** (heading
positions, by default level-2/3 headings). Anchors give the system:

* stable section identity (`anchor key` derived from the heading path)
* change detection (`content hash` per anchor)
* incremental updates at section granularity

Re-ingesting a document diffs the new anchor tree against the stored
one and touches only added/changed/removed anchors. Embedding cost is
proportional to the diff, not to the document.

### 3.4 Storage Portability

The vector index lives behind the `KnowledgeStore` port. The V1
implementation stores embeddings as JSON payload columns in
PostgreSQL/SQLite and scores with numpy cosine in-process — portable,
zero extra infrastructure, and adequate for the documented V1 scale
(~≤100k child chunks per knowledge base). The pgvector / Elasticsearch
adapters are reserved extension points that swap the store without
touching the service or runtime.

### 3.5 Ingestion is Idempotent and Diff-Based

* Same content hash → no-op.
* Per-document writes are transactional: an ingest either fully
  replaces the affected anchors or leaves the previous version
  queryable.
* External side effects (embedding calls) happen before the write
  transaction; orphan vectors cannot exist because vectors live in the
  child rows written by that same transaction.

---

## 4. Core Concepts

```text
KnowledgeBase
├── id, name, created_at, metadata
├── anchor_levels        # heading levels treated as anchors, default [2, 3]
├── child_chunk_size     # child window size in characters, default 800
├── child_overlap        # child overlap in characters, default 150 (> 0)
├── embedding_model      # captured at first ingest
└── embedding_dimension  # captured at first ingest

Document
├── id, knowledge_base_id, external_id   # stable doc identity per KB
├── title, source_uri, metadata
├── status               # READY | FAILED (async states reserved)
├── content_hash         # whole-document fast path
└── doc_version          # incremented per changed ingest

Anchor (derived, not stored as a tree)
├── path                 # chain of anchor heading texts, e.g. ["Setup", "Docker"]
├── key                  # sha256 of the display path (+ occurrence suffix)
├── level                # heading level; 0 for the preamble
├── content_hash         # sha256 of the section content
└── span                 # char offsets in the source document

ParentChunk              # one per anchor (V1: parent == anchor section)
├── id, document_id, knowledge_base_id
├── anchor_key, anchor_path, anchor_level, heading
├── content              # full section text — the LLM context unit
└── content_hash, child_count

ChildChunk               # retrieval unit
├── id, parent_id, document_id, knowledge_base_id, child_index
├── content              # overlapping window of the parent
├── embedding            # float vector (JSON column in V1)
└── span, anchor_path    # provenance for results and debugging
```

---

## 5. Anchor Model

### 5.1 Anchors

An anchor is a heading whose level is in the knowledge base's
`anchor_levels` (default `[2, 3]`: level-2 and level-3 headings).
Anchors partition the document: an anchor's section runs from its
heading line to the next heading of **any** anchor level (higher or
lower). Non-anchor headings (e.g. h1, h4–h6 when anchors are [2, 3])
are ordinary body text inside the enclosing section.

Content before the first anchor is a synthetic **preamble** anchor
(path `[]`, level 0). A whitespace-only preamble produces no parent.

ATX headings only (`#` … `######`); headings inside fenced code blocks
are text.

### 5.2 Anchor Identity

* `path` = chain of anchor-level heading texts from document root to
  the anchor. Non-anchor headings are excluded from identity —
  renaming a non-anchor heading never invalidates its descendants'
  keys.
* `key` = sha256 of the display path (`" > "`-joined, with an
  occurrence suffix `" [n]"` for duplicate paths, disambiguated in
  document order).
* Heading rename ⇒ path change ⇒ old anchor removed + new anchor
  added (the section re-indexes). This is the specified, intended
  semantics: identity follows structure.

### 5.3 Anchor Content Hash

`content_hash` = sha256 of the exact section text (heading line
included, trailing whitespace stripped). Any edit to a section — even
whitespace — makes that anchor "changed" and re-chunks/re-embeds it.
Correctness over cleverness: no fuzzy "equivalent content" matching.

---

## 6. Chunking Model

```text
document
   |
   v parse (anchor_levels)
anchor sections = parents
   |
   v RecursiveCharacterTextSplitter(chunk_size, chunk_overlap, add_start_index)
children (overlapping windows, per parent)
   |
   v embed children only
vector index (children)
```

Rules:

* Parent = the whole anchor section, stored once, never embedded.
* Children = overlapping character windows of the parent content,
  produced by LangChain's `RecursiveCharacterTextSplitter` with
  `child_chunk_size` / `child_overlap` (both per-KB; overlap MUST be
  > 0 — the overlap exists so retrieval-critical sentences straddling
  a boundary survive in at least one child).
* Splitting is deterministic: same content + same parameters ⇒ same
  children (no timestamps, no randomness).
* Coverage: children in `child_index` order reconstruct the parent
  content (overlap regions duplicated by design).
* Children carry provenance: parent id, anchor path, index, char span.

---

## 7. Ingestion and Incremental Update

```text
ingest(kb, external_id, content)
   |
   v
document = get_or_create by (kb, external_id)
   |
   v
content_hash == stored?  -- yes --> no-op report (version unchanged)
   |
   no
   v
new_tree = parse(content, kb.anchor_levels)
old_tree = stored anchors of the document
   |
   v diff by anchor key
   ├── added   anchors: in new, not in old
   ├── changed anchors: in both, content_hash differs
   └── removed anchors: in old, not in new
   |
   v
children(added ∪ changed) -> EmbeddingProvider   (external, before writes)
   |
   v single transaction
delete children+parents of removed
delete children of changed, upsert their parents
insert new children
update document (content_hash, version+1, status READY)
```

* **Embedding cost ∝ diff**: only children of added/changed anchors
  are embedded.
* Document identity is `(knowledge_base_id, external_id)`; re-uploading
  the same `external_id` is an incremental update of that document.
* On embedding-provider failure the document is marked `FAILED` with
  the error recorded, and the previous version remains queryable.
* Deleting a document deletes its parents and children. Deleting a KB
  cascades to all documents.

---

## 8. Retrieval Pipeline

```text
query
  |
  v embed query (dimension checked against KB)
  |
  v KnowledgeStore.search_children(kb, vector, top_k)     cosine, numpy
  |
  v group by parent, parent score = max child score
  |
  v order parents desc, cap at max_parents
  |
  v fetch parent contents
  |
  v RetrievedContext items
```

Each result carries the **parent content** (LLM-ready), the anchor
path/level/heading, document provenance, and the score. `get_context`
formats the items into one context string for direct prompt
injection. Reranking is a reserved extension (runtime-capabilities
spec section 4) — V1 returns vector-scored parents only.

---

## 9. Knowledge Service and API

`KnowledgeService` is the application layer (house pattern: service +
store port + attach-routes):

```text
POST   /api/v1/knowledge/bases                          create KB
GET    /api/v1/knowledge/bases                          list
GET    /api/v1/knowledge/bases/{kb_id}                  detail + stats
DELETE /api/v1/knowledge/bases/{kb_id}                  delete (cascade)
POST   /api/v1/knowledge/bases/{kb_id}/documents        upload / incremental update
GET    /api/v1/knowledge/bases/{kb_id}/documents        list
GET    /api/v1/knowledge/bases/{kb_id}/documents/{doc}  detail + anchor inventory
DELETE /api/v1/knowledge/bases/{kb_id}/documents/{doc}  delete
POST   /api/v1/knowledge/bases/{kb_id}/retrieve         query
```

Document size is bounded by the platform request-body limit; ingestion
is synchronous in V1 (async Celery ingestion reserved). KB
configuration (anchor/chunk parameters) is immutable after creation —
changing it invalidates every anchor key, so a re-index operation is
the only correct migration path and is reserved.

---

## 10. Runtime Integration

* **Capability**: `runtime/capabilities/knowledge.py` defines
  `KnowledgeCapability` (`retrieve`, `get_document`, `get_context`;
  `rerank` reserved). `knowledge/capability.py` implements it over the
  service. Agent/Workflow runtimes depend on the capability only.
* **Agent tool**: `knowledge/tool.py` exposes a `retrieve_knowledge`
  `ToolSpec` + handler (arguments: knowledge base id, query, top_k).
  The existing `LangChainToolAdapter` turns the ToolSpec into a native
  LangChain tool for the DeepAgents loop. The tool registers on the
  worker only when `KNOWLEDGE_TOOL_ENABLED=true` (default off — the
  agent tool surface must not change for existing deployments).
* **Context Manager**: retrieved context joins "Retrieved Context" in
  the context composition (00-system-overview README section 3); the
  pull is runtime-side, Knowledge only supplies retrieve/get_context.

---

## 11. Storage Model

```text
knowledge_bases      id PK, name, created_at, payload JSON
knowledge_documents  id PK, kb_id (idx), external_id, status,
                     content_hash, doc_version, created_at, updated_at, payload JSON
                     UNIQUE (kb_id, external_id)
knowledge_parents    id PK, document_id (idx), kb_id (idx), anchor_key,
                     anchor_level, content_hash, payload JSON
                     UNIQUE (document_id, anchor_key)
knowledge_children   id PK, parent_id (idx), document_id (idx), kb_id (idx),
                     child_index, payload JSON   (content + embedding + span)
```

House conventions: queryable columns for filtering, JSON payload for
aggregate shape; in-memory SQLite URLs share per-URL singletons;
production schema delivered through Alembic migration `0002`
(create_all semantics mirroring `0001`).

Scale note (V1): search loads the KB's child rows and scores with numpy
cosine — comfortably ≤ ~100k children per KB. Beyond that, the
pgvector adapter (same port, `embedding` VECTOR column) is the
production path.

---

## 12. Embeddings

```text
EmbeddingProvider port (platform)
   ├── LangChainEmbeddingProvider   wraps langchain Embeddings  [seam]
   │      └── OpenAIEmbeddings (langchain-openai), lazy, key via host env
   └── FakeEmbeddingProvider        deterministic token-hash vectors (tests/dev)
```

* Provider selection is configuration (`KNOWLEDGE_EMBEDDING_PROVIDER`:
  `openai` | `fake`); model keys follow the platform rule (host env,
  never in settings).
* Construction is lazy: the API boots without embedding keys; only
  ingest/retrieve that need the provider fail, with a clear 503.
* Dimension consistency: the first successful ingest captures
  `embedding_model` + `embedding_dimension` on the KB; later ingests or
  queries with a different dimension are rejected (409) until the KB is
  re-indexed (reserved op).

---

## 13. Observability

V1 emits structured logs (ingest diff summary, retrieve latency and
result counts). The platform `MetricsCollector` is RuntimeEvent-scoped
(run-correlated); knowledge management operations happen outside runs,
so metric/event integration is reserved until knowledge retrieval is
traceable within run spans (tool-call correlation). Evaluation of
retrieval quality (Recall@K over anchor-labeled data) is reserved in
the Evaluation system.

---

## 14. Security

* Documents and chunks are scoped by knowledge base id; every store
  query is KB-filtered (no cross-KB leakage).
* Retrieved document content is **untrusted input** to the LLM.
  Knowledge performs no prompt-injection filtering; that risk is owned
  by Agent Runtime / Policy / MCP Gateway governance, as with sandbox
  output.
* Embedding keys are host environment variables, never stored in KB
  metadata.

---

## 15. V1 Boundary

V1 includes:

* Knowledge base CRUD + document lifecycle
* Markdown anchor parsing (configurable anchor levels, default 2/3)
* Parent/child chunking with overlapping children
* Anchor-based incremental updates (diff + selective re-embed)
* Vector retrieval (parent-dedup, top-k, max-parents, document filter)
* `KnowledgeCapability` (retrieve / get_document / get_context)
* `retrieve_knowledge` agent tool (default-off registration)
* SQLAlchemy store + Alembic migration + REST API

V1 does not include (reserved):

* Reranking (cross-encoder, LLM rerank)
* Hybrid search (BM25/keyword), query rewriting, multi-query
* pgvector / Elasticsearch adapters
* Async (Celery) ingestion; re-index API; KB config updates
* PDF/HTML/DOCX parsers (markdown + plain text only)
* Per-tenant ACL on knowledge bases
* Cross-KB federated retrieval; contextual-retrieval enrichment
* Retrieval-quality evaluation integration (Recall@K)
