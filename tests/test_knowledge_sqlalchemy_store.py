"""SQLAlchemy KnowledgeStore contract tests (plan 070 section 12, test 8):
file-based SQLite + in-memory singleton."""

import pytest
from sqlalchemy.exc import IntegrityError

from agent_platform.infrastructure.knowledge_sqlalchemy_store import (
    SQLKnowledgeStore,
    create_knowledge_store,
)
from agent_platform.knowledge.domain import (
    ChildChunk,
    Document,
    DocumentStatus,
    IngestWrite,
    KnowledgeBase,
    ParentChunk,
    StoredAnchor,
)


def _kb(name: str) -> KnowledgeBase:
    return KnowledgeBase(id=f"kb-{name}", name=name)


def _document(kb_id: str, external_id: str) -> Document:
    return Document(
        id=f"doc-{kb_id}-{external_id}",
        knowledge_base_id=kb_id,
        external_id=external_id,
        content_hash="hash-" + external_id,
        anchor_count=1,
        child_count=1,
    )


def _parent(document: Document, key: str = "k1") -> ParentChunk:
    return ParentChunk(
        id=f"{document.id}:{key}",
        document_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        anchor_key=key,
        display_path="H",
        anchor_level=2,
        heading="H",
        content="parent content",
        content_hash="p-hash",
        child_count=1,
    )


def _child(parent: ParentChunk, index: int, embedding: list[float]) -> ChildChunk:
    return ChildChunk(
        id=f"{parent.id}:{index}",
        parent_id=parent.id,
        document_id=parent.document_id,
        knowledge_base_id=parent.knowledge_base_id,
        child_index=index,
        content=f"child {index}",
        embedding=embedding,
        start=0,
        end=6,
        display_path=parent.display_path,
    )


def _write(document: Document, parent: ParentChunk, children: list[ChildChunk]) -> IngestWrite:
    return IngestWrite(
        document=document,
        anchors=[
            StoredAnchor(
                key=parent.anchor_key,
                content_hash=parent.content_hash,
                display_path=parent.display_path,
                anchor_level=parent.anchor_level,
                heading=parent.heading,
            )
        ],
        upsert_parents=[parent],
        delete_parent_keys=[],
        delete_children_of_keys=[],
        insert_children=children,
    )


def test_crud_round_trips_on_file_sqlite(tmp_path):
    store = SQLKnowledgeStore(f"sqlite:///{tmp_path/'knowledge.db'}")

    store.save_knowledge_base(_kb("base"))
    assert store.get_knowledge_base("kb-base").name == "base"
    assert store.get_knowledge_base_by_name("base").id == "kb-base"
    assert store.get_knowledge_base_by_name("nope") is None
    assert [kb.id for kb in store.list_knowledge_bases()] == ["kb-base"]

    document = _document("kb-base", "ext-1")
    store.save_document(document)
    assert store.find_document("kb-base", "ext-1").id == document.id
    assert store.find_document("kb-base", "other") is None
    assert store.get_document(document.id).status is DocumentStatus.READY
    assert store.list_documents("kb-base")[0].external_id == "ext-1"

    parent = _parent(document)
    children = [_child(parent, 0, [1.0, 0.0])]
    deleted = store.apply_ingest(_write(document, parent, children))
    assert deleted == 0
    assert store.list_parents(document.id)[0].id == parent.id
    assert store.get_parent(parent.id).content == "parent content"
    anchors = store.list_anchors(document.id)
    assert anchors[0].key == "k1" and anchors[0].display_path == "H"

    loaded = store.search_children("kb-base", [1.0, 0.0], top_k=5)
    assert [child.id for child, _ in loaded] == [children[0].id]
    assert loaded[0][1] == pytest.approx(1.0)


def test_unique_constraints(tmp_path):
    store = SQLKnowledgeStore(f"sqlite:///{tmp_path/'knowledge.db'}")
    store.save_knowledge_base(_kb("base"))
    store.save_document(_document("kb-base", "ext-1"))

    # Same (kb, external_id) with a different primary key -> violation.
    duplicate = _document("kb-base", "ext-1")
    object.__setattr__(duplicate, "id", "doc-other")
    with pytest.raises(IntegrityError):
        store.save_document(duplicate)


def test_duplicate_kb_name_rejected(tmp_path):
    store = SQLKnowledgeStore(f"sqlite:///{tmp_path/'knowledge.db'}")
    store.save_knowledge_base(_kb("same"))
    # Same name, different primary key -> UNIQUE(name) violation. (The
    # store itself upserts by id; the service enforces name uniqueness.)
    with pytest.raises(IntegrityError):
        store.save_knowledge_base(KnowledgeBase(id="kb-other", name="same"))


def test_apply_ingest_replaces_changed_and_removed(tmp_path):
    store = SQLKnowledgeStore(f"sqlite:///{tmp_path/'knowledge.db'}")
    document = _document("kb-x", "ext-1")
    old_parent = _parent(document, "old")
    keep_parent = _parent(document, "keep")
    store.apply_ingest(
        _write(document, old_parent, [_child(old_parent, 0, [1.0, 0.0])])
    )
    store.apply_ingest(
        _write(document, keep_parent, [_child(keep_parent, 0, [0.0, 1.0])])
    )

    # Changed parent (same key, new hash/content) with one new child, and
    # the "old" anchor removed entirely.
    new_parent = ParentChunk(
        id=old_parent.id,
        document_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        anchor_key="old",
        display_path="H",
        anchor_level=2,
        heading="H",
        content="parent v2",
        content_hash="p-hash-2",
        child_count=1,
    )
    deleted = store.apply_ingest(
        IngestWrite(
            document=document,
            anchors=[
                StoredAnchor(
                    key="old",
                    content_hash="p-hash-2",
                    display_path="H",
                    anchor_level=2,
                    heading="H",
                ),
                StoredAnchor(
                    key="keep",
                    content_hash="p-hash",
                    display_path="K",
                    anchor_level=2,
                    heading="K",
                ),
            ],
            upsert_parents=[new_parent],
            delete_parent_keys=[],
            delete_children_of_keys=["old"],
            insert_children=[_child(new_parent, 0, [1.0, 0.0])],
        )
    )
    assert deleted == 1  # the old "old" child

    assert store.get_parent(old_parent.id).content == "parent v2"
    assert {p.anchor_key for p in store.list_parents(document.id)} == {"old", "keep"}
    assert len(store.list_anchors(document.id)) == 2
    scores = store.search_children("kb-x", [1.0, 0.0], top_k=10)
    assert len(scores) == 2
    assert scores[0][0].parent_id == old_parent.id  # [1,0] matches v2 child


def test_search_document_ids_filter_and_ranking(tmp_path):
    store = SQLKnowledgeStore(f"sqlite:///{tmp_path/'knowledge.db'}")
    doc_a = _document("kb-s", "a")
    doc_b = _document("kb-s", "b")
    parent_a = _parent(doc_a, "ka")
    parent_b = _parent(doc_b, "kb")
    store.apply_ingest(_write(doc_a, parent_a, [_child(parent_a, 0, [1.0, 0.0])]))
    store.apply_ingest(_write(doc_b, parent_b, [_child(parent_b, 0, [1.0, 0.9])]))

    hits = store.search_children("kb-s", [1.0, 0.0], top_k=10)
    assert [child.document_id for child, _ in hits] == [doc_a.id, doc_b.id]

    filtered = store.search_children("kb-s", [1.0, 0.0], top_k=10, document_ids=[doc_b.id])
    assert [child.document_id for child, _ in filtered] == [doc_b.id]

    # top_k bounds the result count.
    assert len(store.search_children("kb-s", [1.0, 0.0], top_k=1)) == 1


def test_cascade_deletes(tmp_path):
    store = SQLKnowledgeStore(f"sqlite:///{tmp_path/'knowledge.db'}")
    store.save_knowledge_base(_kb("base"))
    document = _document("kb-base", "ext-1")
    parent = _parent(document)
    store.apply_ingest(_write(document, parent, [_child(parent, 0, [1.0, 0.0])]))

    store.delete_document(document.id)
    assert store.get_document(document.id) is None
    assert store.list_parents(document.id) == []
    assert store.list_anchors(document.id) == []
    assert store.search_children("kb-base", [1.0, 0.0], top_k=5) == []

    # Re-create then cascade via KB delete.
    store.apply_ingest(_write(document, parent, [_child(parent, 0, [1.0, 0.0])]))
    store.delete_knowledge_base("kb-base")
    assert store.get_knowledge_base("kb-base") is None
    assert store.get_document(document.id) is None
    assert store.search_children("kb-base", [1.0, 0.0], top_k=5) == []


def test_in_memory_singleton_shares_state():
    first = create_knowledge_store("sqlite:///:memory:")
    second = create_knowledge_store("sqlite:///:memory:")
    assert first is second
    assert isinstance(first, type(second))
    first.save_knowledge_base(_kb("shared"))
    assert second.get_knowledge_base("kb-shared") is not None


def test_failed_document_round_trip(tmp_path):
    store = SQLKnowledgeStore(f"sqlite:///{tmp_path/'knowledge.db'}")
    document = _document("kb-f", "ext-1")
    failed = Document(
        id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        external_id=document.external_id,
        status=DocumentStatus.FAILED,
        content_hash="",
        doc_version=0,
        error="provider down",
    )
    store.save_document(failed)
    loaded = store.get_document(document.id)
    assert loaded.status is DocumentStatus.FAILED
    assert loaded.error == "provider down"
    assert loaded.created_at.tzinfo is not None
