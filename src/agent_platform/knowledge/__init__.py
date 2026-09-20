"""Knowledge / RAG module (knowledge-rag-spec.md).

Knowledge base + document management, anchor-structured markdown
parsing, parent/child chunking with overlapping children,
anchor-granular incremental updates and small-to-big retrieval.
LangChain is confined to chunking.py and embeddings.py (boundary
tests enforce it).
"""

from agent_platform.knowledge.application import KnowledgeService
from agent_platform.knowledge.capability import PlatformKnowledgeCapability
from agent_platform.knowledge.domain import (
    AnchorSection,
    ChildChunk,
    Document,
    DocumentStatus,
    EmbeddingModelChangedError,
    IngestReport,
    IngestWrite,
    KnowledgeBase,
    ParentChunk,
    ProviderUnavailableError,
    StoredAnchor,
    validate_anchor_levels,
    validate_chunk_params,
)
from agent_platform.knowledge.incremental import AnchorDiff, diff_anchors
from agent_platform.knowledge.storage import InMemoryKnowledgeStore, KnowledgeStore

__all__ = [
    "AnchorDiff",
    "AnchorSection",
    "ChildChunk",
    "Document",
    "DocumentStatus",
    "EmbeddingModelChangedError",
    "InMemoryKnowledgeStore",
    "IngestReport",
    "IngestWrite",
    "KnowledgeBase",
    "KnowledgeService",
    "KnowledgeStore",
    "ParentChunk",
    "PlatformKnowledgeCapability",
    "ProviderUnavailableError",
    "StoredAnchor",
    "diff_anchors",
    "validate_anchor_levels",
    "validate_chunk_params",
]
