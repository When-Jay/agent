"""Knowledge service (plan 070 section 7; spec sections 6/7).

Orchestrates parse -> chunk -> diff -> embed -> store. All state lives
in the store (no in-process caches): the API runs multiple workers and
Celery workers rebuild composition per task.
"""

import logging
import time
from dataclasses import replace
from typing import Any
from uuid import uuid4

from agent_platform.errors import NotFoundError
from agent_platform.knowledge.anchors import parse_anchors
from agent_platform.knowledge.chunking import split_children
from agent_platform.knowledge.domain import (
    ChildChunk,
    Document,
    DocumentStatus,
    EmbeddingModelChangedError,
    IngestReport,
    IngestWrite,
    KnowledgeBase,
    ParentChunk,
    StoredAnchor,
    sha256_text,
    utcnow,
    validate_anchor_levels,
    validate_chunk_params,
)
from agent_platform.knowledge.embeddings import EmbeddingProvider
from agent_platform.knowledge.incremental import diff_anchors
from agent_platform.knowledge.storage import KnowledgeStore
from agent_platform.runtime.capabilities.knowledge import RetrievedContext

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 8
DEFAULT_MAX_PARENTS = 4
MAX_TOP_K = 100
DEFAULT_ANCHOR_LEVELS: tuple[int, ...] = (2, 3)
DEFAULT_CHILD_CHUNK_SIZE = 800
DEFAULT_CHILD_OVERLAP = 150


class KnowledgeService:
    """Knowledge base management, incremental ingestion and retrieval."""

    def __init__(self, store: KnowledgeStore, embedding_provider: EmbeddingProvider) -> None:
        self._store = store
        self._embeddings = embedding_provider

    # -- knowledge bases ------------------------------------------------------
    def create_knowledge_base(
        self,
        name: str,
        *,
        anchor_levels: list[int] | tuple[int, ...] | None = None,
        child_chunk_size: int | None = None,
        child_overlap: int | None = None,
        metadata: dict | None = None,
    ) -> KnowledgeBase:
        name = (name or "").strip()
        if not name:
            raise ValueError("name must not be empty")
        if self._store.get_knowledge_base_by_name(name) is not None:
            raise ValueError(f"knowledge base name already exists: {name}")
        levels = validate_anchor_levels(
            anchor_levels if anchor_levels is not None else DEFAULT_ANCHOR_LEVELS
        )
        chunk_size = child_chunk_size if child_chunk_size is not None else DEFAULT_CHILD_CHUNK_SIZE
        overlap = child_overlap if child_overlap is not None else DEFAULT_CHILD_OVERLAP
        validate_chunk_params(chunk_size, overlap)
        kb = KnowledgeBase(
            id=uuid4().hex,
            name=name,
            anchor_levels=levels,
            child_chunk_size=chunk_size,
            child_overlap=overlap,
            metadata=dict(metadata or {}),
        )
        self._store.save_knowledge_base(kb)
        return kb

    def list_knowledge_bases(self) -> list[KnowledgeBase]:
        return self._store.list_knowledge_bases()

    def get_knowledge_base(self, kb_id: str) -> dict[str, Any]:
        """KB detail with stats (document/child counts)."""
        kb = self._require_kb(kb_id)
        documents = self._store.list_documents(kb_id)
        return {
            "id": kb.id,
            "name": kb.name,
            "anchor_levels": list(kb.anchor_levels),
            "child_chunk_size": kb.child_chunk_size,
            "child_overlap": kb.child_overlap,
            "embedding_model": kb.embedding_model,
            "embedding_dimension": kb.embedding_dimension,
            "metadata": kb.metadata,
            "created_at": kb.created_at.isoformat(),
            "stats": {
                "documents": len(documents),
                "children": sum(document.child_count for document in documents),
            },
        }

    def delete_knowledge_base(self, kb_id: str) -> None:
        self._require_kb(kb_id)  # cascade happens in the store (I9)
        self._store.delete_knowledge_base(kb_id)

    # -- documents --------------------------------------------------------------
    def ingest(
        self,
        kb_id: str,
        external_id: str,
        content: str,
        *,
        title: str = "",
        source_uri: str = "",
        metadata: dict | None = None,
    ) -> IngestReport:
        """Upload (first time) or incrementally update (I1) a document."""
        kb = self._require_kb(kb_id)
        external_id = (external_id or "").strip()
        if not external_id:
            raise ValueError("external_id must not be empty")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must not be empty")

        existing = self._store.find_document(kb_id, external_id)
        content_hash = sha256_text(content)
        if (
            existing is not None
            and existing.status is DocumentStatus.READY
            and existing.content_hash == content_hash
        ):
            # I2 fast path: no parse, no embedding calls, nothing touched.
            return IngestReport(
                document_id=existing.id,
                external_id=external_id,
                doc_version=existing.doc_version,
                content_hash=content_hash,
                anchors_added=0,
                anchors_changed=0,
                anchors_removed=0,
                children_indexed=0,
                children_deleted=0,
                unchanged=True,
            )

        document_id = existing.id if existing is not None else uuid4().hex
        sections = parse_anchors(content, kb.anchor_levels)
        old = {
            anchor.key: anchor for anchor in self._store.list_anchors(document_id)
        } if existing is not None else {}
        diff = diff_anchors(old, sections)

        # Parents + re-chunked children of added ∪ changed; single batched
        # embedding call (I5).
        upsert_parents: list[ParentChunk] = []
        pending_children: list[ChildChunk] = []
        embed_texts: list[str] = []
        for section in [*diff.added, *diff.changed]:
            parent_id = f"{document_id}:{section.key}"
            splits = split_children(section.content, kb.child_chunk_size, kb.child_overlap)
            upsert_parents.append(
                ParentChunk(
                    id=parent_id,
                    document_id=document_id,
                    knowledge_base_id=kb_id,
                    anchor_key=section.key,
                    display_path=section.display_path,
                    anchor_level=section.level,
                    heading=section.heading,
                    content=section.content,
                    content_hash=section.content_hash,
                    child_count=len(splits),
                )
            )
            for index, split in enumerate(splits):
                pending_children.append(
                    ChildChunk(
                        id=f"{parent_id}:{index}",
                        parent_id=parent_id,
                        document_id=document_id,
                        knowledge_base_id=kb_id,
                        child_index=index,
                        content=split.content,
                        embedding=[],
                        start=split.start,
                        end=split.end,
                        display_path=section.display_path,
                    )
                )
                embed_texts.append(split.content)

        embeddings: list[list[float]] = []
        if embed_texts:
            try:
                embeddings = self._embeddings.embed_documents(embed_texts)
            except Exception as exc:
                # I7: mark FAILED in its own small transaction; the previous
                # index stays queryable. content_hash keeps the last
                # successfully indexed value (the failed content was NOT
                # indexed). Re-raise for API mapping (503/500).
                self._store.save_document(
                    Document(
                        id=document_id,
                        knowledge_base_id=kb_id,
                        external_id=external_id,
                        title=title,
                        source_uri=source_uri,
                        metadata=dict(metadata or {}),
                        status=DocumentStatus.FAILED,
                        content_hash=existing.content_hash if existing is not None else "",
                        doc_version=existing.doc_version if existing is not None else 0,
                        error=str(exc),
                        anchor_count=existing.anchor_count if existing is not None else 0,
                        child_count=existing.child_count if existing is not None else 0,
                        created_at=existing.created_at if existing is not None else utcnow(),
                        updated_at=utcnow(),
                    )
                )
                raise

        # E4 dimension capture / mismatch check, strictly pre-write.
        kb_update: KnowledgeBase | None = None
        if embeddings:
            dimension = len(embeddings[0])
            if kb.embedding_dimension is None:
                kb_update = replace(
                    kb,
                    embedding_model=self._embeddings.name,
                    embedding_dimension=dimension,
                )
            elif dimension != kb.embedding_dimension:
                raise EmbeddingModelChangedError(
                    "embedding model changed; knowledge base requires re-indexing "
                    f"(dimension {dimension} != {kb.embedding_dimension})"
                )

        insert_children = [
            replace(child, embedding=list(vector))
            for child, vector in zip(pending_children, embeddings)
        ]

        # Final counts: untouched anchors keep their stored children.
        existing_parents = {
            parent.anchor_key: parent for parent in self._store.list_parents(document_id)
        }
        changed_keys = {section.key for section in diff.changed}
        untouched = set(old) - changed_keys - set(diff.removed)
        kept_children = sum(
            existing_parents[key].child_count for key in untouched if key in existing_parents
        )
        # I8: version bumps only off a READY predecessor — a document that
        # never indexed successfully (FAILED from birth) keeps version 0
        # through its first success.
        previous_version = existing.doc_version if existing is not None else 0
        bump = existing is not None and existing.status is DocumentStatus.READY
        document = Document(
            id=document_id,
            knowledge_base_id=kb_id,
            external_id=external_id,
            title=title,
            source_uri=source_uri,
            metadata=dict(metadata or {}),
            status=DocumentStatus.READY,
            error=None,
            content_hash=content_hash,
            doc_version=previous_version + 1 if bump else previous_version,
            anchor_count=len(sections),
            child_count=kept_children + len(insert_children),
            created_at=existing.created_at if existing is not None else utcnow(),
            updated_at=utcnow(),
        )
        anchors = [
            StoredAnchor(
                key=section.key,
                content_hash=section.content_hash,
                display_path=section.display_path,
                anchor_level=section.level,
                heading=section.heading,
            )
            for section in sections
        ]
        children_deleted = self._store.apply_ingest(
            IngestWrite(
                document=document,
                anchors=anchors,
                upsert_parents=upsert_parents,
                delete_parent_keys=list(diff.removed),
                delete_children_of_keys=[section.key for section in diff.changed],
                insert_children=insert_children,
                knowledge_base=kb_update,
            )
        )
        logger.info(
            "knowledge ingest kb=%s document=%s external_id=%s added=%d changed=%d "
            "removed=%d indexed=%d deleted=%d version=%d unchanged=False",
            kb_id,
            document_id,
            external_id,
            len(diff.added),
            len(diff.changed),
            len(diff.removed),
            len(insert_children),
            children_deleted,
            document.doc_version,
        )
        return IngestReport(
            document_id=document_id,
            external_id=external_id,
            doc_version=document.doc_version,
            content_hash=content_hash,
            anchors_added=len(diff.added),
            anchors_changed=len(diff.changed),
            anchors_removed=len(diff.removed),
            children_indexed=len(insert_children),
            children_deleted=children_deleted,
            unchanged=False,
        )

    def list_documents(self, kb_id: str) -> list[Document]:
        self._require_kb(kb_id)
        return self._store.list_documents(kb_id)

    def get_document(self, kb_id: str, document_id: str) -> dict[str, Any] | None:
        """Document detail with the anchor inventory; None when absent
        (capability contract, API maps to 404)."""
        self._require_kb(kb_id)
        document = self._store.get_document(document_id)
        if document is None or document.knowledge_base_id != kb_id:
            return None
        return {
            "id": document.id,
            "knowledge_base_id": document.knowledge_base_id,
            "external_id": document.external_id,
            "title": document.title,
            "source_uri": document.source_uri,
            "metadata": document.metadata,
            "status": document.status.value,
            "content_hash": document.content_hash,
            "doc_version": document.doc_version,
            "error": document.error,
            "anchor_count": document.anchor_count,
            "child_count": document.child_count,
            "created_at": document.created_at.isoformat(),
            "updated_at": document.updated_at.isoformat(),
            "anchors": [
                {
                    "key": anchor.key,
                    "display_path": anchor.display_path,
                    "anchor_level": anchor.anchor_level,
                    "heading": anchor.heading,
                    "content_hash": anchor.content_hash,
                }
                for anchor in self._store.list_anchors(document_id)
            ],
        }

    def delete_document(self, kb_id: str, document_id: str) -> None:
        self._require_kb(kb_id)
        document = self._store.get_document(document_id)
        if document is None or document.knowledge_base_id != kb_id:
            raise NotFoundError(f"document not found: {document_id}")
        self._store.delete_document(document_id)

    # -- retrieval ---------------------------------------------------------------
    def retrieve(
        self,
        kb_id: str,
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        max_parents: int = DEFAULT_MAX_PARENTS,
        document_ids: tuple[str, ...] | list[str] = (),
    ) -> list[RetrievedContext]:
        """Small-to-big retrieval (spec section 7): children search,
        parent grouping with max-child score, parent content returned."""
        kb = self._require_kb(kb_id)
        query = (query or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        if not 1 <= top_k <= MAX_TOP_K:
            raise ValueError(f"top_k must be within 1..{MAX_TOP_K}: {top_k}")
        if not 1 <= max_parents <= top_k:
            raise ValueError(f"max_parents must be within 1..top_k ({top_k}): {max_parents}")
        started = time.perf_counter()
        query_vector = self._embeddings.embed_query(query)
        if kb.embedding_dimension is not None and len(query_vector) != kb.embedding_dimension:
            raise EmbeddingModelChangedError(
                "embedding model changed; knowledge base requires re-indexing "
                f"(dimension {len(query_vector)} != {kb.embedding_dimension})"
            )
        hits = self._store.search_children(
            kb_id, query_vector, top_k, list(document_ids) or None
        )
        best: dict[str, tuple[float, Any]] = {}
        for child, score in hits:
            current = best.get(child.parent_id)
            if current is None or score > current[0]:
                best[child.parent_id] = (score, child)
        ranked = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))[:max_parents]
        documents: dict[str, Document] = {}
        results: list[RetrievedContext] = []
        for parent_id, (score, child) in ranked:
            parent = self._store.get_parent(parent_id)
            if parent is None:
                continue
            document = documents.get(child.document_id)
            if document is None:
                fetched = self._store.get_document(child.document_id)
                if fetched is None:
                    continue
                document = fetched
                documents[child.document_id] = document
            results.append(
                RetrievedContext(
                    content=parent.content,
                    score=score,
                    display_path=parent.display_path,
                    anchor_level=parent.anchor_level,
                    heading=parent.heading,
                    document_id=document.id,
                    external_id=document.external_id,
                    knowledge_base_id=kb_id,
                    document_metadata=dict(document.metadata),
                )
            )
        logger.info(
            "knowledge retrieve kb=%s hits=%d results=%d top_score=%.4f latency_ms=%.1f",
            kb_id,
            len(hits),
            len(results),
            results[0].score if results else 0.0,
            (time.perf_counter() - started) * 1000,
        )
        return results

    def get_context(
        self,
        kb_id: str,
        query: str,
        **kwargs: Any,
    ) -> str:
        """retrieve + R5 formatting into one LLM-ready string."""
        results = self.retrieve(kb_id, query, **kwargs)
        return "".join(
            f"## Source: {result.display_path} ({result.external_id})\n"
            f"{result.content}\n\n"
            for result in results
        )

    # -- internals ---------------------------------------------------------------
    def _require_kb(self, kb_id: str) -> KnowledgeBase:
        kb = self._store.get_knowledge_base(kb_id)
        if kb is None:
            raise NotFoundError(f"knowledge base not found: {kb_id}")
        return kb
