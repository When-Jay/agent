# Plan 070 — Knowledge / RAG Module

> **Status: PLANNED** (not implemented). Specification:
> `docs/specs/knowledge-rag-spec.md`. Architecture:
> `docs/architecture/08-knowledge-architecture.md`.
> Default-neutral: management routes attach like Evaluation (no
> external dependency at import); the agent tool registers only when
> `KNOWLEDGE_TOOL_ENABLED=true`.

## 1. Objective

Implement the Knowledge module per the specification:

* Knowledge base + document management over the house store pattern
* Anchor-structured markdown parsing (default levels 2/3)
* Parent/child chunking — parents are LLM context, overlapping
  children are the retrieval unit
* Anchor-granular incremental updates (re-embed only the diff)
* Vector retrieval with parent dedupe, exposed as REST API,
  `KnowledgeCapability`, and a default-off agent tool

---

## 2. Architecture

```text
API (app.py composition root)
  |-- attach_knowledge_routes(app, service)
  v
KnowledgeService (application.py)
  |-- ingest: anchors.parse -> chunking.split -> incremental.diff
  |            -> EmbeddingProvider.embed_documents -> store (txn)
  |-- retrieve: embed_query -> store.search_children
  |              -> parent dedupe/cap -> parent content
  v
KnowledgeStore (port)                    EmbeddingProvider (port)
  |-- InMemoryKnowledgeStore             |-- LangChainEmbeddingProvider
  |-- SQLAlchemyKnowledgeStore           |      (openai, lazy; fake)
  |        (PostgreSQL / SQLite)         v
  v                                 langchain_openai.OpenAIEmbeddings
numpy cosine over child rows           [LangChain seam #1]
(chunking.py uses langchain_text_splitters  [LangChain seam #2])

Runtime side (worker, default-off):
_build_tool_capability -> knowledge retrieve_knowledge ToolSpec
                                   -> LangChainToolAdapter (existing)
```

Cross-process constraint: Celery workers rebuild the orchestrator per
task and the API runs multiple uvicorn workers. All knowledge state
therefore lives in the database (no in-process caches); the tool
handler holds only (store URL, provider) and constructs the service
per call, mirroring the per-task orchestrator rebuild.

---

## 3. Module Layout

```text
src/agent_platform/
├── knowledge/
│   ├── __init__.py            # public exports
│   ├── domain.py              # NEW: dataclasses (KB/Document/Anchor/
│   │                          #      Parent/Child/IngestReport/Retrieved...)
│   ├── anchors.py             # NEW: markdown -> AnchorSection list (pure)
│   ├── chunking.py            # NEW: parent -> overlapping children
│   │                          #      (langchain_text_splitters seam)
│   ├── incremental.py         # NEW: old/new anchor trees -> diff (pure)
│   ├── storage.py             # NEW: KnowledgeStore Protocol + InMemory
│   ├── embeddings.py          # NEW: EmbeddingProvider port + adapters
│   │                          #      (langchain_openai seam)
│   ├── application.py         # NEW: KnowledgeService
│   ├── capability.py          # NEW: PlatformKnowledgeCapability
│   ├── tool.py                # NEW: retrieve_knowledge ToolSpec + handler
│   └── api.py                 # NEW: attach_knowledge_routes
├── runtime/capabilities/
│   └── knowledge.py           # NEW: KnowledgeCapability ABC +
│                              #      RetrievedContext (spec section 11)
├── infrastructure/
│   └── knowledge_sqlalchemy_store.py   # NEW: durable store + tables
├── config.py                  # EDIT: 3 knowledge settings
├── api/app.py                 # EDIT: compose service, attach routes
└── runtime/dispatch/orchestrator.py    # EDIT: optional tool registration

alembic/versions/0002_knowledge_schema.py   # NEW: create_all mirror of 0001
tests/
├── test_knowledge_anchors.py      # NEW: parse + chunk + diff (pure)
├── test_knowledge_service.py      # NEW: service + fake provider + memory store
├── test_knowledge_sqlalchemy_store.py  # NEW: store contract on sqlite
├── test_knowledge_api.py          # NEW: HTTP contract
└── test_architecture_boundaries.py    # EDIT: knowledge rules
```

Pure-part rule (boundary-enforced): `domain.py`, `anchors.py`,
`incremental.py`, `storage.py`, `application.py`, `capability.py`,
`tool.py`, `api.py` import no LangChain; only `chunking.py` and
`embeddings.py` touch LangChain.

---

## 4. Pure Components

### 4.1 domain.py

Frozen dataclasses exactly as spec section 3; `DocumentStatus`
enum (`READY`, `FAILED`; `PENDING`/`INDEXING` reserved comments).
Validation helpers used by both service and API:
`validate_anchor_levels`, `validate_chunk_params` (spec A1, C3, C4).

### 4.2 anchors.py

```python
def parse_anchors(content: str, anchor_levels: list[int]) -> list[AnchorSection]
```

Implementation: single line scan; state = fence tracking (```/~~~,
3+, same-char close) + heading-level stack of anchor headings;
`re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)` outside fences.
Heading at anchor level → close current section, push, open new.
Non-anchor heading lines remain body text. Preamble per A5 (skip if
`content.strip()` of the region is empty). Duplicate display paths get
` [n]` suffixes by document order; `key = sha256(display_path)`
(preamble: `sha256("(preamble)")`). Sections carry exact content
(trailing whitespace stripped) + `[start, end)` char spans.

### 4.3 chunking.py

```python
def split_children(parent_content: str, chunk_size: int, overlap: int) -> list[ChildSplit]
# ChildSplit(content, start, end); deterministic
```

`RecursiveCharacterTextSplitter(chunk_size=..., chunk_overlap=...,
add_start_index=True, separators=["\n\n", "\n", "。", "！", "？",
". ", " ", ""]).split_text(parent_content)`; start indices taken from
splitter documents metadata. Pure function of (content, params).

### 4.4 incremental.py

```python
@dataclass(frozen=True)
class AnchorDiff:
    added: list[AnchorSection]
    changed: list[AnchorSection]      # new versions
    removed: list[str]                # old anchor keys

def diff_anchors(old: dict[str, StoredAnchor], new: list[AnchorSection]) -> AnchorDiff
```

Pure set logic per spec I3.

---

## 5. Storage

### 5.1 Tables (infrastructure/knowledge_sqlalchemy_store.py)

Exactly spec S3: `knowledge_bases`, `knowledge_documents`,
`knowledge_parents`, `knowledge_children`; queryable columns + JSON
payload; UNIQUE (kb_id, external_id) and UNIQUE (document_id,
anchor_key); house helpers `create_sqlalchemy_engine` /
`ensure_schema`; per-URL in-memory SQLite singleton (mirror
evaluation store). `search_children` loads child rows of the KB
(optional `document_ids` IN filter), scores with numpy cosine
(`embedding` lists → matrix), returns top-k `(child, score)`.

### 5.2 In-memory store (storage.py)

Same contract; used by service unit tests and injectable into the
API for tests.

### 5.3 Migration

`0002_knowledge_schema.py`: mirror of `0001` —
`knowledge_sqlalchemy_store.metadata.create_all(bind=op.get_bind())`;
downgrade drops. Follows the existing baseline strategy (idempotent
on DBs that already used create_all).

---

## 6. Embedding Providers (embeddings.py)

```python
class EmbeddingProvider(Protocol):
    name: str
    def embed_documents(self, texts: list[str]) -> list[list[float]]
    def embed_query(self, text: str) -> list[float]

class LangChainEmbeddingProvider:          # wraps any langchain Embeddings
    def __init__(self, embeddings, name): ...

def create_embedding_provider(settings) -> EmbeddingProvider
    # "openai" -> lazy OpenAI: import langchain_openai and construct
    #   on first call (key from host env), cached; failures raise
    #   ProviderUnavailableError (-> 503)
    # "fake"  -> FakeEmbeddingProvider

class FakeEmbeddingProvider:               # tests/dev
    # dim=64; token -> hashed bucket index; +count; L2 normalize.
    # Deterministic; cosine ~ token overlap (functional ranking tests)
```

The lazy OpenAI construction is what keeps `create_app()` booting in
CI without keys (spec E3).

---

## 7. Service (application.py)

```python
class KnowledgeService:
    def __init__(self, store, embedding_provider): ...
    # KB: create / list / get / delete (cascade)
    # Documents: ingest(kb_id, external_id, content, ...) -> IngestReport
    #            list / get (with anchor inventory) / delete
    # Retrieval: retrieve(...) / get_context(...)
```

Ingest per spec section 6: resolve-or-create document (status
`READY` only after first success) → content-hash short-circuit (I2)
→ parse → diff → embed children of added∪changed (batch, once) →
single transaction (deletes, upserts, inserts, document update) →
report. Dimension capture per E4 (first successful ingest writes
model+dimension onto KB; mismatch → `EmbeddingModelChangedError` →
409). Provider failures → document marked FAILED (I7) → 503.

Retrieve per spec section 7: embed query → dimension check →
`search_children` → parent grouping (`max child score`), cap
`max_parents` → fetch parents + document info → `RetrievedContext`.

---

## 8. API Wiring (api.py + app.py)

`attach_knowledge_routes(app, service)` implements spec section 10
verbatim (route table, error mapping 404/422/409/503, ingest returns
200 upsert semantics). `create_app` gains a `knowledge_service=None`
injection parameter (house pattern, mirrors `judge_model`): default
builds `KnowledgeService(create_knowledge_store(database_url),
create_embedding_provider(settings))` and attaches routes. No
injection-point changes for existing tests.

---

## 9. Capability and Tool

* `runtime/capabilities/knowledge.py`: `RetrievedContext` dataclass +
  `KnowledgeCapability` ABC (spec section 11) — registered in
  capabilities `__init__` exports.
* `knowledge/capability.py`: `PlatformKnowledgeCapability(service)`.
* `knowledge/tool.py`: `retrieve_knowledge` ToolSpec (JSON-schema
  params: `knowledge_base_id` str required, `query` str required,
  `top_k` int optional) + handler returning R5-formatted context.
* `orchestrator.py` `_build_tool_capability`: when
  `settings.knowledge_tool_enabled`, add the knowledge ToolSpec +
  handler to the native `InMemoryToolCapability` registrations
  (default off; no behavior change otherwise).

---

## 10. Config, Dependencies, Deployment Hygiene

config.py (house conventions; raw strings only in Settings):

```text
knowledge_embedding_provider        default "openai"
knowledge_openai_embedding_model   default "text-embedding-3-small"
knowledge_tool_enabled              default False
```

pyproject dependencies: add `langchain-openai`, `numpy`
(`langchain-text-splitters` already ships with `langchain`).
`.env.example`: commented-out knowledge block (provider, model, tool
flag). No compose change (no new services; Postgres/Redis reused).

---

## 11. Boundary Tests (test_architecture_boundaries.py)

```python
def test_knowledge_module_boundary():
    # forbid: api, infrastructure, mcp, observability, evaluation,
    #         evolution, sandbox, runtime.agent, runtime.workflow,
    #         runtime.dispatch, celery, langgraph, deepagents
    # (langchain allowed ONLY in declared seams, next test)

def test_knowledge_langchain_confined_to_seams():
    # every file in knowledge/ EXCEPT chunking.py and embeddings.py
    # must not import langchain / langchain_text_splitters / langchain_openai
```

---

## 12. Tests

### test_knowledge_anchors.py (pure, no I/O)

1. **Parsing**: levels [2,3] boundaries (h2/h3 close, h1/h4-h6 body);
   fence suppression; preamble (present / whitespace-only absent);
   duplicate path suffixes; path excludes non-anchor headings;
   spans + content hashes; custom levels ([1], [3])
2. **Chunking**: coverage (C6), overlap existence between adjacent
   windows (C3), determinism across calls, spans inside parent,
   heading-only parent
3. **Diff**: added/changed/removed classification; unchanged
   sections absent from diff; rename = remove+add; empty new tree =
   all removed

### test_knowledge_service.py (in-memory store + fake provider)

4. **KB CRUD**: create validation (A1/C3/C4 → ValueError/422 mapping),
   list/get/delete cascade
5. **Ingest**: first ingest sets embedding model+dimension on KB;
   content-hash no-op (zero provider calls, version unchanged);
   single-section edit → provider called once with exactly that
   section's new children; added/removed sections; version bump;
   FAILED marking on provider error with previous version retrievable;
   dimension mismatch → EmbeddingModelChangedError
6. **Retrieve**: parent dedupe (multiple children of one parent →
   one result, score = max), ordering, `max_parents` cap,
   `document_ids` filter, empty KB → []
7. **get_context / capability / tool**: formatting per R5;
   capability delegates; tool handler formats

### test_knowledge_sqlalchemy_store.py

8. Store contract on SQLite (file + in-memory singleton): CRUD
   round-trips, unique constraints (duplicate external_id /
   duplicate anchor_key), cascade deletes, `search_children` scoring
   with known vectors, document_ids filter

### test_knowledge_api.py (httpx against create_app)

9. Route contract: KB/document lifecycle, ingest upsert semantics
   (200), anchor inventory in document detail, retrieve endpoint,
   404/422/409/503 mapping; app boots without embedding key and KB
   management works (ingest → 503 until provider available — tested
   via a failing fake provider injected through `knowledge_service`)

### test_architecture_boundaries.py (edit)

10. The two rules from section 11

Deferred integration (mirroring 042/043): OpenAI-provider round-trip
and pgvector-scale behavior.

---

## 13. Implementation Order

1. `domain.py` + `anchors.py` + `chunking.py` + `incremental.py`
   (pure) with tests 1-3
2. `storage.py` (port + in-memory) + `application.py` service with
   fake provider, tests 4-7
3. `infrastructure/knowledge_sqlalchemy_store.py` + Alembic `0002`,
   tests 8
4. `embeddings.py` (lazy openai + fake) — no new tests beyond
   service-level fakes; manual provider check deferred
5. `api.py` + `app.py` wiring, tests 9
6. `runtime/capabilities/knowledge.py` + `capability.py` + `tool.py`
   + orchestrator wiring (default-off) + boundary tests + config +
   pyproject + `.env.example`
7. Full suite: `uv pip install -e . --python .venv/Scripts/python.exe`
   (pulls langchain-openai, numpy); full pytest — existing 443 passed
   + 5 skipped baseline must hold, new tests green

---

## 14. Acceptance

* All spec acceptance criteria (knowledge-rag-spec.md section 15)
  checked
* Full existing test suite passes unmodified
* Incrementality proven: single-section edit triggers embedding calls
  only for that section's children (asserted by fake-provider call
  counting)
* Default-neutral proven: no `KNOWLEDGE_*` env set → routes attach
  (DB-only), tool absent, no new external connections

---

## 15. Out of Scope

Everything in knowledge-rag-spec.md section 16: rerankers, hybrid
search, query rewriting, pgvector/ES adapters, async ingestion,
re-index API, KB config updates, PDF/HTML/DOCX parsing, ACLs,
cross-KB retrieval, contextual-retrieval enrichment, Recall@K
evaluation integration.
