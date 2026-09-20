"""Knowledge / RAG domain models (knowledge-rag-spec.md section 3).

Pure module: stdlib only, no LangChain, no I/O. Validation helpers are
shared by the service (KB creation) and the API layer (422 mapping).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
import hashlib

from agent_platform.errors import PlatformError


def sha256_text(text: str) -> str:
    """Stable content hash used for anchors, documents and sections (A7)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(UTC)


class DocumentStatus(str, Enum):
    """Lifecycle of a document index. PENDING / INDEXING are reserved for
    async ingestion (out of V1 scope, spec section 2.2)."""

    READY = "READY"
    FAILED = "FAILED"


class ProviderUnavailableError(PlatformError):
    """Embedding provider cannot be reached (missing key, provider down) -> 503."""


class EmbeddingModelChangedError(PlatformError):
    """Vector dimension differs from the KB's captured dimension -> 409 (E4)."""


# Chunk parameter ranges (spec C4).
MIN_CHILD_CHUNK_SIZE = 100
MAX_CHILD_CHUNK_SIZE = 8000


def validate_anchor_levels(anchor_levels: list[int] | tuple[int, ...]) -> tuple[int, ...]:
    """Normalize and validate anchor levels (spec A1): non-empty, de-duplicated,
    ascending, subset of {1..6}. Raises ValueError (API maps to 422)."""
    if not anchor_levels:
        raise ValueError("anchor_levels must not be empty")
    levels: list[int] = []
    for raw in anchor_levels:
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise ValueError(f"anchor level must be an integer: {raw!r}")
        if not 1 <= raw <= 6:
            raise ValueError(f"anchor level out of range 1..6: {raw}")
        if raw not in levels:
            levels.append(raw)
    return tuple(sorted(levels))


def validate_chunk_params(child_chunk_size: int, child_overlap: int) -> None:
    """Validate chunk parameters (spec C3/C4). Raises ValueError."""
    if not isinstance(child_chunk_size, int) or isinstance(child_chunk_size, bool):
        raise ValueError(f"child_chunk_size must be an integer: {child_chunk_size!r}")
    if not isinstance(child_overlap, int) or isinstance(child_overlap, bool):
        raise ValueError(f"child_overlap must be an integer: {child_overlap!r}")
    if not MIN_CHILD_CHUNK_SIZE <= child_chunk_size <= MAX_CHILD_CHUNK_SIZE:
        raise ValueError(
            f"child_chunk_size must be within {MIN_CHILD_CHUNK_SIZE}..{MAX_CHILD_CHUNK_SIZE}: "
            f"{child_chunk_size}"
        )
    if not 0 < child_overlap < child_chunk_size:
        raise ValueError(
            f"child_overlap must satisfy 0 < child_overlap < child_chunk_size "
            f"({child_overlap} !< {child_chunk_size})"
        )


@dataclass(frozen=True)
class KnowledgeBase:
    """A knowledge base: fixed chunk/anchor configuration + captured
    embedding identity (E4). anchor_levels is a tuple (immutable)."""

    id: str
    name: str
    anchor_levels: tuple[int, ...] = (2, 3)
    child_chunk_size: int = 800
    child_overlap: int = 150
    embedding_model: str | None = None
    embedding_dimension: int | None = None
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class Document:
    """An ingested document. doc_version is 0 at creation and +1 per
    changed ingest (I8); content_hash is the sha256 of the last
    successfully indexed content (kept stale on FAILED, see I7)."""

    id: str
    knowledge_base_id: str
    external_id: str
    title: str = ""
    source_uri: str = ""
    metadata: dict = field(default_factory=dict)
    status: DocumentStatus = DocumentStatus.READY
    content_hash: str = ""
    doc_version: int = 0
    error: str | None = None
    anchor_count: int = 0
    child_count: int = 0
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class AnchorSection:
    """Parsed anchor section (spec section 3). path is a tuple of anchor
    heading texts root -> self; level 0 marks the synthetic preamble."""

    path: tuple[str, ...]
    key: str
    display_path: str
    level: int
    heading: str
    content: str
    content_hash: str
    start: int
    end: int


@dataclass(frozen=True)
class StoredAnchor:
    """Persisted anchor inventory entry (identity + hash only; the
    content itself lives in the parent chunk)."""

    key: str
    content_hash: str
    display_path: str
    anchor_level: int
    heading: str


@dataclass(frozen=True)
class ParentChunk:
    """Persisted parent: one per anchor section; the LLM context unit (C1).
    Never embedded. id is deterministic per (document, anchor_key)."""

    id: str
    document_id: str
    knowledge_base_id: str
    anchor_key: str
    display_path: str
    anchor_level: int
    heading: str
    content: str
    content_hash: str
    child_count: int


@dataclass(frozen=True)
class ChildChunk:
    """Persisted retrieval unit: overlapping window of the parent content
    (C2). embedding lives in the payload JSON (portable, spec S3)."""

    id: str
    parent_id: str
    document_id: str
    knowledge_base_id: str
    child_index: int
    content: str
    embedding: list[float]
    start: int
    end: int
    display_path: str


@dataclass(frozen=True)
class IngestWrite:
    """Everything one ingest commits in a single transaction (I6). The
    store applies deletes -> upserts -> inserts atomically and returns
    the number of deleted children for the report."""

    document: Document
    anchors: list[StoredAnchor]
    upsert_parents: list[ParentChunk]
    delete_parent_keys: list[str]
    delete_children_of_keys: list[str]
    insert_children: list[ChildChunk]
    knowledge_base: KnowledgeBase | None = None  # dimension capture (E4)


@dataclass(frozen=True)
class IngestReport:
    """Result of one ingest call (spec section 3)."""

    document_id: str
    external_id: str
    doc_version: int
    content_hash: str
    anchors_added: int
    anchors_changed: int
    anchors_removed: int
    children_indexed: int
    children_deleted: int
    unchanged: bool
