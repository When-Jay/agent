"""SQLAlchemy-backed KnowledgeStore (knowledge-rag-spec.md S3).

Durable adapter mirroring evaluation_sqlalchemy_store.py conventions:
JSON payload columns keep aggregate shapes schemaless; queryable
columns exist for filtering; datetime columns are timezone-aware;
in-memory SQLite URLs share a per-URL singleton so the API and tests
see one store. Exactly four tables per spec S3 — the anchor inventory
is derived from parent rows (parent = anchor section). Embeddings live
in the child payload JSON (portable); the pgvector adapter is reserved
and replaces this implementation, not the port.
"""

from typing import Any
import dataclasses
import datetime as dt

from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, String, Table, UniqueConstraint, insert, select

from agent_platform.infrastructure.sqlalchemy_store import create_sqlalchemy_engine, ensure_schema
from agent_platform.knowledge.domain import (
    ChildChunk,
    Document,
    DocumentStatus,
    IngestWrite,
    KnowledgeBase,
    ParentChunk,
    StoredAnchor,
)
from agent_platform.knowledge.storage import cosine_scores

metadata = MetaData()

_bases = Table(
    "knowledge_bases", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
)
_documents = Table(
    "knowledge_documents", metadata,
    Column("id", String(64), primary_key=True),
    Column("kb_id", String(64), nullable=False, index=True),
    Column("external_id", String(255), nullable=False),
    Column("status", String(32), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("doc_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("kb_id", "external_id", name="uq_knowledge_documents_kb_external"),
)
_parents = Table(
    "knowledge_parents", metadata,
    Column("id", String(255), primary_key=True),
    Column("document_id", String(64), nullable=False, index=True),
    Column("kb_id", String(64), nullable=False, index=True),
    Column("anchor_key", String(64), nullable=False),
    Column("anchor_level", Integer, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("document_id", "anchor_key", name="uq_knowledge_parents_doc_anchor"),
)
_children = Table(
    "knowledge_children", metadata,
    Column("id", String(255), primary_key=True),
    Column("parent_id", String(255), nullable=False, index=True),
    Column("document_id", String(64), nullable=False, index=True),
    Column("kb_id", String(64), nullable=False, index=True),
    Column("child_index", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
)


def _dump(obj: Any) -> dict[str, Any]:
    """Frozen dataclass -> JSON-safe dict (datetime -> iso; enums are str
    subclasses and encode via their value; tuples become lists)."""
    data = dataclasses.asdict(obj)
    for key, value in data.items():
        if isinstance(value, dt.datetime):
            data[key] = value.isoformat()
    return data


def _upsert(conn, table, pk: str, values: dict[str, Any]) -> None:
    existing = conn.execute(select(table.c[pk]).where(table.c[pk] == values[pk])).first()
    if existing:
        conn.execute(table.update().where(table.c[pk] == values[pk]).values(**values))
    else:
        conn.execute(insert(table).values(**values))


def _load(cls, row) -> Any:
    """Rebuild a domain object from the payload (datetime/enum/tuple
    fields restored explicitly; the domain shapes are flat)."""
    data = dict(row["payload"] or {})
    for key in ("created_at", "updated_at"):
        if isinstance(data.get(key), str):
            data[key] = dt.datetime.fromisoformat(data[key])
    if cls is KnowledgeBase:
        data["anchor_levels"] = tuple(data.get("anchor_levels") or ())
    elif cls is Document:
        data["status"] = DocumentStatus(data.get("status", DocumentStatus.READY.value))
    return cls(**data)


def _delete_document_rows(conn, document_id: str) -> None:
    conn.execute(_children.delete().where(_children.c.document_id == document_id))
    conn.execute(_parents.delete().where(_parents.c.document_id == document_id))
    conn.execute(_documents.delete().where(_documents.c.id == document_id))


class SQLKnowledgeStore:
    """KnowledgeStore implementation over SQLAlchemy engines."""

    def __init__(self, database_url: str) -> None:
        self.engine = create_sqlalchemy_engine(database_url)
        ensure_schema(self.engine, metadata)

    # -- knowledge bases -------------------------------------------------
    def save_knowledge_base(self, kb: KnowledgeBase) -> None:
        values = {"id": kb.id, "name": kb.name, "created_at": kb.created_at, "payload": _dump(kb)}
        with self.engine.begin() as conn:
            _upsert(conn, _bases, "id", values)

    def get_knowledge_base(self, kb_id: str) -> KnowledgeBase | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_bases).where(_bases.c.id == kb_id)).mappings().first()
        return _load(KnowledgeBase, row) if row else None

    def get_knowledge_base_by_name(self, name: str) -> KnowledgeBase | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(_bases).where(_bases.c.name == name)).mappings().first()
        return _load(KnowledgeBase, row) if row else None

    def list_knowledge_bases(self) -> list[KnowledgeBase]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(_bases).order_by(_bases.c.created_at)).mappings().all()
        return [_load(KnowledgeBase, row) for row in rows]

    def delete_knowledge_base(self, kb_id: str) -> None:
        with self.engine.begin() as conn:
            document_ids = [
                row["id"]
                for row in conn.execute(
                    select(_documents.c.id).where(_documents.c.kb_id == kb_id)
                ).mappings().all()
            ]
            for document_id in document_ids:
                _delete_document_rows(conn, document_id)
            conn.execute(_bases.delete().where(_bases.c.id == kb_id))

    # -- documents ---------------------------------------------------------
    def find_document(self, kb_id: str, external_id: str) -> Document | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_documents).where(
                    _documents.c.kb_id == kb_id, _documents.c.external_id == external_id
                )
            ).mappings().first()
        return _load(Document, row) if row else None

    def get_document(self, document_id: str) -> Document | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_documents).where(_documents.c.id == document_id)
            ).mappings().first()
        return _load(Document, row) if row else None

    def list_documents(self, kb_id: str) -> list[Document]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_documents)
                .where(_documents.c.kb_id == kb_id)
                .order_by(_documents.c.created_at)
            ).mappings().all()
        return [_load(Document, row) for row in rows]

    def save_document(self, document: Document) -> None:
        with self.engine.begin() as conn:
            _upsert(conn, _documents, "id", _document_values(document))

    def delete_document(self, document_id: str) -> None:
        with self.engine.begin() as conn:
            _delete_document_rows(conn, document_id)

    # -- parents / anchors ---------------------------------------------------
    def list_parents(self, document_id: str) -> list[ParentChunk]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_parents)
                .where(_parents.c.document_id == document_id)
                .order_by(_parents.c.id)
            ).mappings().all()
        return [_load(ParentChunk, row) for row in rows]

    def get_parent(self, parent_id: str) -> ParentChunk | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_parents).where(_parents.c.id == parent_id)
            ).mappings().first()
        return _load(ParentChunk, row) if row else None

    def list_anchors(self, document_id: str) -> list[StoredAnchor]:
        """Anchor inventory derived from parent rows (parent = anchor
        section; content lives in the parent, identity here)."""
        return [
            StoredAnchor(
                key=parent.anchor_key,
                content_hash=parent.content_hash,
                display_path=parent.display_path,
                anchor_level=parent.anchor_level,
                heading=parent.heading,
            )
            for parent in self.list_parents(document_id)
        ]

    def apply_ingest(self, write: IngestWrite) -> int:
        """Commit one ingest in a single transaction (I6). Returns the
        number of deleted children (for the ingest report)."""
        deleted_children = 0
        with self.engine.begin() as conn:
            doomed_keys = set(write.delete_parent_keys) | set(write.delete_children_of_keys)
            if doomed_keys:
                doomed_parent_ids = [
                    row["id"]
                    for row in conn.execute(
                        select(_parents.c.id).where(
                            _parents.c.document_id == write.document.id,
                            _parents.c.anchor_key.in_(doomed_keys),
                        )
                    ).mappings().all()
                ]
                if doomed_parent_ids:
                    result = conn.execute(
                        _children.delete().where(_children.c.parent_id.in_(doomed_parent_ids))
                    )
                    deleted_children = int(result.rowcount or 0)
                if write.delete_parent_keys:
                    conn.execute(
                        _parents.delete().where(
                            _parents.c.document_id == write.document.id,
                            _parents.c.anchor_key.in_(write.delete_parent_keys),
                        )
                    )
            for parent in write.upsert_parents:
                _upsert(
                    conn,
                    _parents,
                    "id",
                    {
                        "id": parent.id,
                        "document_id": parent.document_id,
                        "kb_id": parent.knowledge_base_id,
                        "anchor_key": parent.anchor_key,
                        "anchor_level": parent.anchor_level,
                        "content_hash": parent.content_hash,
                        "payload": _dump(parent),
                    },
                )
            for child in write.insert_children:
                conn.execute(
                    insert(_children).values(
                        id=child.id,
                        parent_id=child.parent_id,
                        document_id=child.document_id,
                        kb_id=child.knowledge_base_id,
                        child_index=child.child_index,
                        payload=_dump(child),
                    )
                )
            _upsert(conn, _documents, "id", _document_values(write.document))
            if write.knowledge_base is not None:
                kb = write.knowledge_base
                _upsert(
                    conn,
                    _bases,
                    "id",
                    {
                        "id": kb.id,
                        "name": kb.name,
                        "created_at": kb.created_at,
                        "payload": _dump(kb),
                    },
                )
        return deleted_children

    # -- retrieval ------------------------------------------------------------
    def search_children(
        self,
        kb_id: str,
        embedding: list[float],
        top_k: int,
        document_ids: list[str] | None = None,
    ) -> list[tuple[ChildChunk, float]]:
        conditions = [_children.c.kb_id == kb_id]
        if document_ids:
            conditions.append(_children.c.document_id.in_(document_ids))
        with self.engine.connect() as conn:
            rows = conn.execute(select(_children).where(*conditions)).mappings().all()
        children = [_load(ChildChunk, row) for row in rows]
        scores = cosine_scores(embedding, [child.embedding for child in children])
        scored = sorted(
            zip(children, scores),
            key=lambda pair: (
                -pair[1],
                pair[0].document_id,
                pair[0].parent_id,
                pair[0].child_index,
            ),
        )
        return scored[:top_k]


def _document_values(document: Document) -> dict[str, Any]:
    return {
        "id": document.id,
        "kb_id": document.knowledge_base_id,
        "external_id": document.external_id,
        "status": document.status.value,
        "content_hash": document.content_hash,
        "doc_version": document.doc_version,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "payload": _dump(document),
    }


# In-memory SQLite singletons (dispatch.orchestrator pattern): the API and
# in-process tests must observe one shared store per memory URL.
_memory_stores: dict[str, Any] = {}


def create_knowledge_store(database_url: str):
    """Build a KnowledgeStore for a database URL.

    `sqlite:///:memory:` keeps one shared store per URL so the API and
    in-process tests observe the same state; durable URLs get their own
    engine against the shared database.
    """
    if database_url.startswith("sqlite") and ":memory:" in database_url:
        from agent_platform.knowledge.storage import InMemoryKnowledgeStore

        store = _memory_stores.get(database_url)
        if store is None:
            store = InMemoryKnowledgeStore()
            _memory_stores[database_url] = store
        return store
    return SQLKnowledgeStore(database_url)
