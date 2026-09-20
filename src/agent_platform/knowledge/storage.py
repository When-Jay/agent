"""KnowledgeStore port + in-memory implementation (spec S1/S2).

Pure module: no LangChain. numpy is used for the shared cosine scoring
helper so both stores rank identically (S4).
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import numpy as np

from agent_platform.knowledge.domain import (
    ChildChunk,
    Document,
    IngestWrite,
    KnowledgeBase,
    ParentChunk,
    StoredAnchor,
)


def cosine_scores(query: Sequence[float], rows: Sequence[Sequence[float]]) -> list[float]:
    """Cosine similarity of the query against every row; zero vectors
    score 0.0 (guards the empty/unknown-embedding corner)."""
    if not rows:
        return []
    matrix = np.asarray(rows, dtype=np.float64)
    vector = np.asarray(query, dtype=np.float64)
    row_norms = np.linalg.norm(matrix, axis=1)
    query_norm = float(np.linalg.norm(vector))
    if query_norm == 0.0:
        return [0.0] * len(rows)
    denominators = row_norms * query_norm
    dots = matrix @ vector
    return [
        float(dot / denominator) if denominator > 0.0 else 0.0
        for dot, denominator in zip(dots, denominators)
    ]


@runtime_checkable
class KnowledgeStore(Protocol):
    """Persistence port for the knowledge module (spec S1).

    Ingest commits through apply_ingest (one transaction, I6); the
    remaining methods are single-row operations used by management and
    retrieval paths.
    """

    # -- knowledge bases -------------------------------------------------
    def save_knowledge_base(self, kb: KnowledgeBase) -> None: ...

    def get_knowledge_base(self, kb_id: str) -> KnowledgeBase | None: ...

    def get_knowledge_base_by_name(self, name: str) -> KnowledgeBase | None: ...

    def list_knowledge_bases(self) -> list[KnowledgeBase]: ...

    def delete_knowledge_base(self, kb_id: str) -> None:
        """Cascade to documents, parents and children (I9)."""
        ...

    # -- documents ---------------------------------------------------------
    def find_document(self, kb_id: str, external_id: str) -> Document | None: ...

    def get_document(self, document_id: str) -> Document | None: ...

    def list_documents(self, kb_id: str) -> list[Document]: ...

    def save_document(self, document: Document) -> None:
        """Upsert one document row (ingest reports, FAILED marking I7)."""
        ...

    def delete_document(self, document_id: str) -> None:
        """Remove the document and its parents/children (I9)."""
        ...

    # -- parents / anchors ---------------------------------------------------
    def list_parents(self, document_id: str) -> list[ParentChunk]: ...

    def get_parent(self, parent_id: str) -> ParentChunk | None: ...

    def list_anchors(self, document_id: str) -> list[StoredAnchor]: ...

    def apply_ingest(self, write: IngestWrite) -> int:
        """Commit one ingest atomically (I6): delete children/parents of
        removed+changed anchors, upsert parents, insert children, replace
        the anchor inventory, upsert the document (and optionally the KB
        for dimension capture). Returns the deleted-children count."""
        ...

    # -- retrieval ------------------------------------------------------------
    def search_children(
        self,
        kb_id: str,
        embedding: list[float],
        top_k: int,
        document_ids: list[str] | None = None,
    ) -> list[tuple[ChildChunk, float]]:
        """Top-k children of the KB by cosine similarity (S4)."""
        ...


class InMemoryKnowledgeStore:
    """Dict-backed KnowledgeStore for unit tests and in-memory URLs.

    apply_ingest is atomic by construction (single-threaded mutation).
    """

    def __init__(self) -> None:
        self._bases: dict[str, KnowledgeBase] = {}
        self._documents: dict[str, Document] = {}
        self._parents: dict[str, ParentChunk] = {}
        self._children: dict[str, ChildChunk] = {}
        self._anchors: dict[str, list[StoredAnchor]] = {}

    # -- knowledge bases -------------------------------------------------
    def save_knowledge_base(self, kb: KnowledgeBase) -> None:
        self._bases[kb.id] = kb

    def get_knowledge_base(self, kb_id: str) -> KnowledgeBase | None:
        return self._bases.get(kb_id)

    def get_knowledge_base_by_name(self, name: str) -> KnowledgeBase | None:
        for kb in self._bases.values():
            if kb.name == name:
                return kb
        return None

    def list_knowledge_bases(self) -> list[KnowledgeBase]:
        return sorted(self._bases.values(), key=lambda kb: kb.created_at)

    def delete_knowledge_base(self, kb_id: str) -> None:
        self._bases.pop(kb_id, None)
        for document in [
            d for d in self._documents.values() if d.knowledge_base_id == kb_id
        ]:
            self.delete_document(document.id)

    # -- documents ---------------------------------------------------------
    def find_document(self, kb_id: str, external_id: str) -> Document | None:
        for document in self._documents.values():
            if document.knowledge_base_id == kb_id and document.external_id == external_id:
                return document
        return None

    def get_document(self, document_id: str) -> Document | None:
        return self._documents.get(document_id)

    def list_documents(self, kb_id: str) -> list[Document]:
        return sorted(
            (d for d in self._documents.values() if d.knowledge_base_id == kb_id),
            key=lambda d: d.created_at,
        )

    def save_document(self, document: Document) -> None:
        self._documents[document.id] = document

    def delete_document(self, document_id: str) -> None:
        self._documents.pop(document_id, None)
        self._anchors.pop(document_id, None)
        for parent in [p for p in self._parents.values() if p.document_id == document_id]:
            self._parents.pop(parent.id, None)
        for child in [c for c in self._children.values() if c.document_id == document_id]:
            self._children.pop(child.id, None)

    # -- parents / anchors ---------------------------------------------------
    def list_parents(self, document_id: str) -> list[ParentChunk]:
        return [p for p in self._parents.values() if p.document_id == document_id]

    def get_parent(self, parent_id: str) -> ParentChunk | None:
        return self._parents.get(parent_id)

    def list_anchors(self, document_id: str) -> list[StoredAnchor]:
        return list(self._anchors.get(document_id, []))

    def apply_ingest(self, write: IngestWrite) -> int:
        document_id = write.document.id
        removed = set(write.delete_parent_keys)
        changed = set(write.delete_children_of_keys)
        deleted_children = 0

        # Children of removed parents and of changed (re-chunked) parents.
        doomed_parent_ids = {
            p.id for p in self._parents.values() if p.document_id == document_id
            and p.anchor_key in removed | changed
        }
        for child in [c for c in self._children.values() if c.parent_id in doomed_parent_ids]:
            self._children.pop(child.id, None)
            deleted_children += 1
        # Removed parents go with their children.
        for parent in [p for p in self._parents.values() if p.document_id == document_id
                       and p.anchor_key in removed]:
            self._parents.pop(parent.id, None)

        for parent in write.upsert_parents:
            self._parents[parent.id] = parent
        for child in write.insert_children:
            self._children[child.id] = child
        self._documents[document_id] = write.document
        self._anchors[document_id] = list(write.anchors)
        if write.knowledge_base is not None:
            self._bases[write.knowledge_base.id] = write.knowledge_base
        return deleted_children

    # -- retrieval ------------------------------------------------------------
    def search_children(
        self,
        kb_id: str,
        embedding: list[float],
        top_k: int,
        document_ids: list[str] | None = None,
    ) -> list[tuple[ChildChunk, float]]:
        rows = [
            c
            for c in self._children.values()
            if c.knowledge_base_id == kb_id
            and (document_ids is None or c.document_id in document_ids)
        ]
        scores = cosine_scores(embedding, [c.embedding for c in rows])
        scored = sorted(
            zip(rows, scores),
            key=lambda pair: (-pair[1], pair[0].document_id, pair[0].parent_id, pair[0].child_index),
        )
        return scored[:top_k]
