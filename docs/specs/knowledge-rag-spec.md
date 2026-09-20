# Knowledge / RAG Specification

> Status: PLANNED. Implements the Knowledge capability reserved in
> runtime-capabilities-spec.md section 4 and the Knowledge Service
> position in 00-system-overview.md. Architecture:
> docs/architecture/08-knowledge-architecture.md. Implementation plan:
> docs/plans/070-knowledge-rag.md.

## 1. Purpose and Position

The Knowledge module provides knowledge base management and
parent/child retrieval (RAG):

```text
manage  -> knowledge bases, documents, anchor-structured indices
ingest  -> parse anchors -> chunk (parent + overlapping children)
          -> embed children -> index
update  -> anchor-granular incremental diffs, re-embed only the diff
retrieve-> search children, return whole parents (LLM-ready context)
```

Position (not a new architectural module — pre-declared by
00-system-overview.md section 2):

* Interface: `runtime/capabilities/knowledge.py` (`KnowledgeCapability`)
* Implementation: top-level `agent_platform.knowledge` module
* Consumers: API layer (management routes), Agent/Workflow runtimes
  (capability + tool)

LangChain is the implementation base and is confined to two seams:
the embedding adapter (`knowledge/embeddings.py`) and the child
splitter (`knowledge/chunking.py`,
`langchain_text_splitters.RecursiveCharacterTextSplitter`). No other
knowledge module file may import LangChain; boundary tests enforce it.

---

## 2. Responsibilities

### 2.1 Required

1. Knowledge base CRUD (anchor levels, chunk parameters, metadata)
2. Document upload with stable external identity per KB
3. Anchor parsing of markdown content (configurable anchor levels,
   default `[2, 3]`)
4. Parent/child chunking with overlapping children
5. Anchor-based incremental updates (add/change/remove diff;
   re-embed only the diff)
6. Vector retrieval returning parent content (small-to-big)
7. `KnowledgeCapability` (retrieve / get_document / get_context)
8. REST API under `/api/v1/knowledge`
9. Durable storage (SQLAlchemy) + Alembic migration

### 2.2 Out of Scope

* Deciding when/whether to retrieve (runtime's decision)
* Reranking, hybrid search, query rewriting (reserved)
* Async ingestion (Celery), re-index API, KB config mutation
* Non-markdown parsers (PDF/HTML/DOCX)
* AuthN/AuthZ, per-tenant ACL
* Retrieval-quality evaluation (Evaluation system's reserved concern)

---

## 3. Core Concepts

```text
KnowledgeBase
├── id: str                       # platform uuid
├── name: str                     # unique per platform
├── anchor_levels: list[int]      # default [2, 3]; subset of 1..6
├── child_chunk_size: int         # default 800 (chars)
├── child_overlap: int            # default 150 (chars), MUST be > 0
├── embedding_model: str | None   # captured at first successful ingest
├── embedding_dimension: int | None
├── metadata: dict
└── created_at

Document
├── id, knowledge_base_id
├── external_id: str              # caller's stable doc identity, unique per KB
├── title, source_uri, metadata
├── status: READY | FAILED        # PENDING / INDEXING reserved for async
├── content_hash: str             # sha256 of raw content
├── doc_version: int              # 0 at creation, +1 per changed ingest
├── error: str | None             # last ingest failure
├── anchor_count, child_count
└── created_at, updated_at

AnchorSection (parsed, pure)
├── path: list[str]               # anchor heading texts, root -> self
├── key: str                      # sha256(display_path)
├── display_path: str             # " > "-joined (+ " [n]" for duplicates)
├── level: int                    # heading level; 0 for preamble
├── heading: str                  # heading text ("" for preamble)
├── content: str                  # full section text (heading included)
├── content_hash: str             # sha256(section content)
└── start, end: int               # char span in the source document

ParentChunk                     # persisted; one per anchor section
├── id, document_id, knowledge_base_id
├── anchor_key, display_path, anchor_level, heading
├── content, content_hash, child_count

ChildChunk                       # persisted; retrieval unit
├── id, parent_id, document_id, knowledge_base_id, child_index
├── content                       # overlapping window of parent content
├── embedding: list[float]        # provider output
├── start, end                   # char span within the parent
└── display_path                 # provenance copy

IngestReport
├── document_id, external_id, doc_version, content_hash
├── anchors_added, anchors_changed, anchors_removed
├── children_indexed, children_deleted
└── unchanged: bool               # content-hash short-circuit
```

---

## 4. Anchor Model (normative)

**A1 Anchor levels.** `anchor_levels` is a non-empty, de-duplicated,
ascending list drawn from `{1..6}`. Default `[2, 3]`.

**A2 Heading syntax.** ATX headings only: a line matching
`^(#{1,6})\s+(.+?)\s*#*\s*$` outside a fenced code block. Fenced code
blocks (``` or ~~~, 3+ markers) suppress heading recognition inside
them. Setext headings are body text.

**A3 Section boundaries.** A heading whose level is in
`anchor_levels` starts a new anchor section and closes the previous
one — regardless of whether the new level is higher or lower. A
heading whose level is NOT in `anchor_levels` is ordinary body text of
the enclosing section and never closes a section.

**A4 Path.** The anchor path is the chain of anchor-level heading
texts from the document root to the anchor, maintained with a
heading-level stack (a new anchor at level L pops all anchors with
level >= L). Non-anchor headings never appear in any path.

**A5 Preamble.** Content before the first anchor heading is a
synthetic anchor: path `[]`, display path `"(preamble)"`, level 0,
heading `""`. A whitespace-only preamble yields no anchor and no
parent.

**A6 Identity.** `display_path` = `" > ".join(path)`. Duplicate paths
within one document get an occurrence suffix in document order:
`"Examples [2]"`. `key = sha256(display_path)`. The preamble key is
the constant `sha256("(preamble)")`.

**A7 Content hash.** `content_hash = sha256(content)` where content
is the exact section text from the heading line to the last
non-whitespace character before the next boundary. Any byte change —
including whitespace — marks the anchor changed. No normalization, no
fuzzy matching.

**A8 Rename semantics.** Renaming a heading changes the path, hence
the key: the old anchor is removed and the new one added, and the
section re-indexes. This is intentional: identity follows structure.

**A9 Char spans.** Every anchor records its `[start, end)` char span
in the source document; children record spans within the parent.
Spans are provenance/debug data, not identity.

---

## 5. Chunking Model (normative)

**C1 Parent = anchor section.** The parent content is the full anchor
section (heading included). Parents are stored once and never
embedded. The parent is the unit that enters the LLM context.

**C2 Children = overlapping windows.** Children are produced by
LangChain `RecursiveCharacterTextSplitter` with
`chunk_size = child_chunk_size`, `chunk_overlap = child_overlap`,
`add_start_index=True`, separators
`["\n\n", "\n", "。", "！", "？", ". ", " ", ""]`, applied to the
parent content.

**C3 Overlap requirement.** `0 < child_overlap < child_chunk_size`.
The overlap guarantees that a retrieval-relevant sentence straddling
a split boundary survives in at least one child.

**C4 Validation ranges.** `100 <= child_chunk_size <= 8000`.
`anchor_levels` per A1. Violations are rejected at KB creation (422).

**C5 Determinism.** Same content + same parameters produce identical
children (same count, order, contents, spans). No timestamps, no
randomness, no locale-dependent splitting.

**C6 Coverage.** Ordered by `child_index`, children reconstruct the
parent content (overlap regions appear in both neighbors by design).

**C7 Degenerate parents.** A section whose content is only the heading
line still produces a parent; children follow C2 (at least one child
while content is non-empty).

---

## 6. Incremental Update Contract (normative)

**I1 Document identity.** `(knowledge_base_id, external_id)` is
unique. Uploading with an existing `external_id` updates that
document; there is no separate update endpoint.

**I2 Fast path.** If `sha256(content)` equals the stored
`content_hash`, the ingest is a no-op: report `unchanged: true`,
version and index untouched, no parse, no embedding calls.

**I3 Diff.** With `new = parse(content, anchor_levels)` and `old` =
stored anchors of the document, keyed by `anchor_key`:

```text
added   = keys(new) - keys(old)
changed = {k in both : content_hash differs}
removed = keys(old) - keys(new)
```

**I4 Effects.** `removed`: delete parents and children. `changed`:
delete children, upsert parent, insert re-chunked children. `added`:
insert parent and children. `unchanged∩both`: untouched (no delete, no
re-embed).

**I5 Embedding scope.** Only children of `added ∪ changed` anchors are
passed to the embedding provider. The provider is called at most once
per ingest for the batch.

**I6 Transactionality.** All row deletions/inserts and the document
update commit in one store transaction. The parse/diff/embed phase
precedes it; on any failure in that phase nothing is written except
the document status.

**I7 Failure marking.** On embedding-provider failure the document
row is updated to `status=FAILED` with `error` recorded (separate
small transaction); the previously indexed version remains queryable.
The API surfaces 503 (provider unavailable) or 409 (dimension
mismatch, see E4).

**I8 Versioning.** Each non-no-op successful ingest increments
`doc_version` and refreshes `content_hash`, counts, `updated_at`, and
sets `status=READY` with `error=None`.

**I9 Deletion.** Deleting a document removes its parents and children.
Deleting a KB cascades to its documents, parents, children. Both are
single transactions.

**I10 KB immutability.** Anchor levels and chunk parameters are fixed
at KB creation; no update path exists in V1 (a config change
invalidates every anchor key; a re-index operation is reserved).

---

## 7. Retrieval Contract (normative)

**R1 Request.**

```text
retrieve(knowledge_base_id, query, *,
         top_k = 8,            # children fetched, 1..100
         max_parents = 4,      # parents returned, 1..top_k
         document_ids = ())    # optional document filter
```

**R2 Pipeline.** Embed the query (E-rules) → cosine similarity against
all children of the KB (optionally filtered by `document_ids`) →
top-`top_k` children → group by parent → parent score = max child
score → order parents by score descending → cap at `max_parents` →
fetch parent content.

**R3 Results.** Each `RetrievedContext` carries: parent `content`,
`score`, `display_path`, `anchor_level`, `heading`, `document_id`,
`external_id`, `knowledge_base_id`, and document metadata. Parent
content is returned verbatim — the LLM context unit.

**R4 Empty states.** Unknown KB → 404. KB with no children →
`results: []`. Empty `document_ids` filter matching nothing →
`results: []`.

**R5 get_context.** `get_context(kb, query, ...)` = retrieve +
formatting into one string; each item rendered with a source header:

```text
## Source: {display_path} ({external_id})
{parent content}

```

**R6 Rerank.** Reserved: the capability lists `rerank` in its
interface contract; V1 performs no reranking.

---

## 8. Embedding Contract (normative)

**E1 Port.**

```python
class EmbeddingProvider(Protocol):
    name: str                                   # "openai:text-embedding-3-small"
    def embed_documents(self, texts: list[str]) -> list[list[float]]
    def embed_query(self, text: str) -> list[float]
```

**E2 Providers.** `openai` (default): `langchain_openai.
OpenAIEmbeddings(model=KNOWLEDGE_OPENAI_EMBEDDING_MODEL)`, key from
host env `OPENAI_API_KEY` (platform key rule). `fake`:
deterministic token-hash vectors (tests/dev only).

**E3 Laziness.** The provider is constructed on first use, not at
startup. An unavailable provider (missing key, provider down) fails
only the ingest/retrieve call with 503; KB/document management routes
stay functional.

**E4 Dimension consistency.** The first successful ingest writes
`embedding_model` + `embedding_dimension` onto the KB. Any later
ingest or query whose vector dimension differs is rejected with 409
`embedding model changed; knowledge base requires re-indexing`
(re-index is reserved; operators recreate the KB).

**E5 No key storage.** API keys never appear in settings payloads, KB
metadata, or logs.

---

## 9. Storage Contract (normative)

**S1 Port.** `KnowledgeStore` (Protocol) covers KB CRUD, document
CRUD-by-(kb, external_id), parent/child lifecycle, anchor inventory
per document, and `search_children(kb_id, embedding, top_k,
document_ids)`.

**S2 Implementations.** `InMemoryKnowledgeStore` (tests) and
`SQLAlchemyKnowledgeStore` (production; house conventions: queryable
columns + JSON payload, timezone-aware datetimes, per-URL SQLite
singleton for in-memory URLs).

**S3 Tables.**

```text
knowledge_bases      id PK, name UNIQUE, created_at, payload JSON
knowledge_documents  id PK, kb_id (idx), external_id, status,
                     content_hash, doc_version, created_at, updated_at,
                     payload JSON          UNIQUE (kb_id, external_id)
knowledge_parents    id PK, document_id (idx), kb_id (idx), anchor_key,
                     anchor_level, content_hash, payload JSON
                     UNIQUE (document_id, anchor_key)
knowledge_children   id PK, parent_id (idx), document_id (idx), kb_id (idx),
                     child_index, payload JSON
```

`embedding` lives in the child payload JSON (portable). The pgvector
adapter (VECTOR column + SQL scoring) is reserved and replaces the
store implementation, not the port.

**S4 Search.** V1 loads the KB's (filtered) child rows and ranks by
numpy cosine in-process. Documented comfort limit ~100k children per
KB; beyond that the pgvector path applies.

**S5 Migration.** Alembic `0002` creates the four tables with
`create_all` semantics mirroring `0001` (fresh DB: full schema;
existing DB: idempotent).

---

## 10. REST API Contract

All routes attached under `/api/v1/knowledge` at the API composition
root. Global middleware (rate limit, body limit, CORS) applies
unchanged; the body limit bounds document size.

```text
POST   /api/v1/knowledge/bases
       {name, anchor_levels?, child_chunk_size?, child_overlap?, metadata?}
       -> 201 KB detail
GET    /api/v1/knowledge/bases                    -> {bases: [...]}
GET    /api/v1/knowledge/bases/{kb_id}            -> KB detail + stats
DELETE /api/v1/knowledge/bases/{kb_id}            -> 204 (cascade)

POST   /api/v1/knowledge/bases/{kb_id}/documents
       {external_id, content, title?, source_uri?, metadata?}
       -> 200 IngestReport            (upload AND incremental update)
GET    /api/v1/knowledge/bases/{kb_id}/documents   -> {documents: [...]}
GET    /api/v1/knowledge/bases/{kb_id}/documents/{document_id}
       -> document detail + anchor inventory
DELETE /api/v1/knowledge/bases/{kb_id}/documents/{document_id} -> 204

POST   /api/v1/knowledge/bases/{kb_id}/retrieve
       {query, top_k?, max_parents?, document_ids?}
       -> 200 {results: [RetrievedContext...]}
```

Errors: 404 unknown KB/document; 422 empty name/external_id/content,
invalid `anchor_levels` / chunk parameters, out-of-range retrieval
parameters; 409 embedding-dimension mismatch (E4); 503 provider
unavailable (E3). Ingest responses are `200` (upload is an upsert, not
a creation).

---

## 11. Capability and Tool Contract

```python
class KnowledgeCapability(ABC):
    def retrieve(self, knowledge_base_id, query, *, top_k=8,
                 max_parents=4, document_ids=()) -> list[RetrievedContext]
    def get_document(self, knowledge_base_id, document_id) -> dict | None
    def get_context(self, knowledge_base_id, query, **kw) -> str
    # rerank: reserved (runtime-capabilities-spec section 4)
```

`knowledge/capability.py` implements it over `KnowledgeService`;
`knowledge/tool.py` exposes a `retrieve_knowledge` ToolSpec
(`knowledge_base_id`, `query`, optional `top_k`) whose handler returns
R5-formatted context. The worker registers the tool only when
`KNOWLEDGE_TOOL_ENABLED=true` (default off: existing agent tool
surfaces must not change).

---

## 12. Failure Semantics

```text
failure                              behavior
---------------------------------------------------------------
provider unavailable (no key/down)  503; doc marked FAILED (I7);
                                    previous version queryable
embedding dimension mismatch        409; ingest aborted pre-write
corrupt/empty content parse          422 (unparseable = still text;
                                    parse never raises on valid str)
store write failure                 500; transaction rolled back;
                                    previous version intact
retrieve on empty KB                results: [] (not an error)
unknown KB / document               404
duplicate external_id upload        incremental update (I1)
KB delete with documents             cascade delete (I9)
```

---

## 13. Observability

Structured logs per ingest (diff counts, children indexed, duration,
kb/document ids) and per retrieve (score count, top score, latency,
kb id). MetricsCollector integration is reserved until knowledge ops
are run-correlated (collector is RuntimeEvent-scoped).

---

## 14. Configuration

House conventions (frozen Settings, `_env_*` helpers, keys stay in
host env):

```text
KNOWLEDGE_EMBEDDING_PROVIDER=openai        # openai | fake
KNOWLEDGE_OPENAI_EMBEDDING_MODEL=text-embedding-3-small
KNOWLEDGE_TOOL_ENABLED=false               # agent tool registration (worker)
```

No enable flag for the module itself: routes attach like Evaluation
(pure DB-backed CRUD; no external dependency at import time).
Dependencies added: `langchain-openai`, `numpy` (declared in
pyproject).

---

## 15. Acceptance Criteria

### Anchor parsing

* [ ] Default levels `[2, 3]`: h2 and h3 close sections; h1/h4-h6 are
      body text
* [ ] Content before the first anchor = preamble anchor; whitespace-only
      preamble produces nothing
* [ ] Headings inside fenced code blocks are not anchors
* [ ] Duplicate paths get occurrence suffixes; keys are unique per
      document
* [ ] Path excludes non-anchor headings; renaming a non-anchor
      heading does not change descendant keys
* [ ] Section spans and content hashes match the source text

### Chunking

* [ ] Children cover the parent content in order (C6)
* [ ] Children of adjacent windows overlap by construction (C3)
* [ ] Same content + parameters → identical children across runs
* [ ] Violations of C3/C4 rejected at KB creation

### Incremental updates

* [ ] Same content re-upload → no-op report, version unchanged, zero
      embedding calls
* [ ] Single-section edit → exactly one `changed` anchor; embedding
      call count equals that section's re-chunked children only
* [ ] Added/removed sections produce `added`/`removed` anchors and
      correct child counts
* [ ] Heading rename = remove + add (A8)
* [ ] Provider failure during ingest → document FAILED, previous
      version still retrievable
* [ ] Document delete removes parents + children; KB delete cascades

### Retrieval

* [ ] top_k children grouped to parents; parent score = max child
      score; order descending; `max_parents` cap enforced
* [ ] `document_ids` filter respected
* [ ] Empty KB returns `[]`; unknown KB 404
* [ ] Dimension mismatch on query → 409

### API / integration

* [ ] Full route contract (section 10) including 404/422/409/503
* [ ] App boots and KB management works with no embedding key
      (lazy provider); ingest/retrieve return 503 until a provider is
      available
* [ ] `KnowledgeCapability` retrieve/get_document/get_context behave
      per R-rules
* [ ] `retrieve_knowledge` tool absent by default; present when
      `KNOWLEDGE_TOOL_ENABLED=true`
* [ ] Architecture boundary tests extended and green
* [ ] Full existing suite passes unmodified (443+ baseline holds)

---

## 16. V1 Non-Goals

* Rerankers (cross-encoder / LLM)
* Hybrid search (BM25), query rewriting, multi-query expansion
* pgvector / Elasticsearch adapters
* Async (Celery) ingestion, re-index API, KB config updates
* PDF / HTML / DOCX parsing (markdown + plain text only)
* Multi-tenant ACL on knowledge bases
* Cross-KB federated retrieval
* Contextual-retrieval enrichment (prepending anchor context to
  children before embedding)
* Recall@K retrieval evaluation (Evaluation system's reserved scope)
