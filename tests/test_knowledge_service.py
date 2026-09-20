"""Knowledge service tests (plan 070 section 12, tests 4-7):
in-memory store + fake/counting providers."""

import pytest

from agent_platform.errors import NotFoundError
from agent_platform.knowledge.anchors import parse_anchors
from agent_platform.knowledge.application import KnowledgeService
from agent_platform.knowledge.capability import PlatformKnowledgeCapability
from agent_platform.knowledge.chunking import split_children
from agent_platform.knowledge.domain import (
    DocumentStatus,
    EmbeddingModelChangedError,
    ProviderUnavailableError,
)
from agent_platform.knowledge.embeddings import FakeEmbeddingProvider
from agent_platform.knowledge.storage import InMemoryKnowledgeStore
from agent_platform.knowledge.tool import (
    KNOWLEDGE_TOOL_SPEC,
    create_knowledge_tool_handler,
)

DOC = (
    "Intro paragraph.\n"
    "\n"
    "## Alpha\n"
    "alpha body one\n"
    "\n"
    "### Sub\n"
    "sub body\n"
    "\n"
    "## Beta\n"
    "beta body\n"
)


class CountingProvider:
    """FakeEmbeddingProvider with call recording (incrementality proof)."""

    def __init__(self, dim: int = 64) -> None:
        self._fake = FakeEmbeddingProvider(dim)
        self.name = f"counting:{self._fake.name}"
        self.document_calls: list[list[str]] = []
        self.query_calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls.append(list(texts))
        return self._fake.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return self._fake.embed_query(text)


class SwitchableProvider:
    """Normal until .fail is set (I7 failure marking)."""

    def __init__(self) -> None:
        self._fake = FakeEmbeddingProvider()
        self.name = "switchable"
        self.fail = False

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("provider down")
        return self._fake.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        if self.fail:
            raise RuntimeError("provider down")
        return self._fake.embed_query(text)


class FixedDimProvider:
    """Constant-dimension vectors (E4 mismatch tests)."""

    def __init__(self, dim: int) -> None:
        self._dim = dim
        self.name = f"fixed:{dim}"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self._dim for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * self._dim


def _service(provider=None) -> tuple[KnowledgeService, CountingProvider]:
    counting = provider or CountingProvider()
    return KnowledgeService(InMemoryKnowledgeStore(), counting), counting


# -- KB CRUD -----------------------------------------------------------------


def test_create_kb_defaults_and_validation():
    service, _ = _service()
    kb = service.create_knowledge_base("docs")
    assert kb.anchor_levels == (2, 3)
    assert kb.child_chunk_size == 800 and kb.child_overlap == 150

    for kwargs in (
        {"name": "  "},
        {"name": "x", "anchor_levels": []},
        {"name": "x", "anchor_levels": [0]},
        {"name": "x", "anchor_levels": [7]},
        {"name": "x", "child_chunk_size": 99},
        {"name": "x", "child_chunk_size": 8001},
        {"name": "x", "child_chunk_size": 800, "child_overlap": 800},
        {"name": "x", "child_overlap": 0},
    ):
        with pytest.raises(ValueError):
            service.create_knowledge_base(**kwargs)


def test_duplicate_kb_name_rejected():
    service, _ = _service()
    service.create_knowledge_base("dup")
    with pytest.raises(ValueError, match="already exists"):
        service.create_knowledge_base("dup")


def test_kb_list_get_delete():
    service, _ = _service()
    kb = service.create_knowledge_base("one")
    service.create_knowledge_base("two")
    assert [b.name for b in service.list_knowledge_bases()] == ["one", "two"]
    detail = service.get_knowledge_base(kb.id)
    assert detail["name"] == "one" and detail["stats"]["documents"] == 0

    service.delete_knowledge_base(kb.id)
    with pytest.raises(NotFoundError):
        service.get_knowledge_base(kb.id)
    with pytest.raises(NotFoundError):
        service.list_documents(kb.id)


# -- ingest ------------------------------------------------------------------


def test_first_ingest_captures_embedding_identity_and_counts():
    service, _ = _service()
    kb = service.create_knowledge_base("kb")
    report = service.ingest(kb.id, "doc1", DOC)
    assert report.anchors_added == 4  # preamble, Alpha, Sub, Beta
    assert report.children_indexed > 0
    assert report.unchanged is False
    assert report.doc_version == 0

    detail = service.get_knowledge_base(kb.id)
    assert detail["embedding_model"].startswith("counting:")
    assert detail["embedding_dimension"] == 64
    assert detail["stats"]["documents"] == 1
    assert detail["stats"]["children"] == report.children_indexed

    document = service.list_documents(kb.id)[0]
    assert document.status is DocumentStatus.READY
    assert document.anchor_count == 4
    assert document.child_count == report.children_indexed


def test_unchanged_reingest_is_a_noop_with_zero_provider_calls():
    service, provider = _service()
    kb = service.create_knowledge_base("kb")
    service.ingest(kb.id, "doc1", DOC)
    calls_before = len(provider.document_calls)

    report = service.ingest(kb.id, "doc1", DOC)
    assert report.unchanged is True
    assert report.doc_version == 0
    assert report.children_indexed == 0
    assert len(provider.document_calls) == calls_before


def test_single_section_edit_reembeds_only_that_section():
    service, provider = _service()
    kb = service.create_knowledge_base("kb")
    service.ingest(kb.id, "doc1", DOC)
    provider.document_calls.clear()

    edited = DOC.replace("beta body", "beta body UPDATED")
    report = service.ingest(kb.id, "doc1", edited)

    assert report.anchors_changed == 1
    assert report.anchors_added == 0 and report.anchors_removed == 0
    assert report.doc_version == 1
    # Exactly one batch call carrying exactly the re-chunked children of
    # the edited section (I5).
    assert len(provider.document_calls) == 1
    sections = parse_anchors(edited, (2, 3))
    target = next(s for s in sections if s.heading == "Beta")
    expected = [c.content for c in split_children(target.content, 800, 150)]
    assert provider.document_calls[0] == expected


def test_added_and_removed_sections():
    service, _ = _service()
    kb = service.create_knowledge_base("kb")
    service.ingest(kb.id, "doc1", DOC)

    grown = DOC + "\n## Gamma\ngamma body\n"
    report = service.ingest(kb.id, "doc1", grown)
    assert report.anchors_added == 1 and report.anchors_removed == 0

    shrunk = grown.replace("### Sub\nsub body\n\n", "")
    report = service.ingest(kb.id, "doc1", shrunk)
    assert report.anchors_removed == 1 and report.anchors_added == 0
    assert report.children_deleted > 0

    inventory = service.get_document(kb.id, report.document_id)
    assert [a["heading"] for a in inventory["anchors"]] == ["", "Alpha", "Beta", "Gamma"]


def test_provider_failure_marks_failed_and_keeps_previous_version():
    service, provider = _service(SwitchableProvider())
    kb = service.create_knowledge_base("kb")
    service.ingest(kb.id, "doc1", DOC)
    good = service.get_document(kb.id, service.list_documents(kb.id)[0].id)

    provider.fail = True
    with pytest.raises(RuntimeError, match="provider down"):
        service.ingest(kb.id, "doc1", DOC.replace("alpha body", "alpha v2"))

    document = service.list_documents(kb.id)[0]
    assert document.status is DocumentStatus.FAILED
    assert "provider down" in document.error
    # Previous version stays queryable and unchanged (I7).
    after = service.get_document(kb.id, document.id)
    assert after["content_hash"] == good["content_hash"]
    assert after["doc_version"] == good["doc_version"]
    assert after["anchors"] == good["anchors"]

    # Recovery: same content as the last good index re-ingests with the
    # provider back -> document READY again, previous index intact.
    provider.fail = False
    results = service.retrieve(kb.id, "alpha body")
    assert results and "alpha body one" in results[0].content
    report = service.ingest(kb.id, "doc1", DOC)
    assert service.list_documents(kb.id)[0].status is DocumentStatus.READY
    assert report.anchors_changed == 0


def test_dimension_mismatch_rejects_ingest_and_query():
    store = InMemoryKnowledgeStore()
    service = KnowledgeService(store, FixedDimProvider(64))
    kb = service.create_knowledge_base("kb")
    service.ingest(kb.id, "doc1", "## A\na body\n")

    other = KnowledgeService(store, FixedDimProvider(32))
    with pytest.raises(EmbeddingModelChangedError):
        other.ingest(kb.id, "doc2", "## B\nb body\n")
    with pytest.raises(EmbeddingModelChangedError):
        other.retrieve(kb.id, "query")

    # KB dimension stays captured from the first successful ingest.
    assert service.get_knowledge_base(kb.id)["embedding_dimension"] == 64


def test_ingest_validation_errors():
    service, _ = _service()
    kb = service.create_knowledge_base("kb")
    with pytest.raises(NotFoundError):
        service.ingest("missing", "doc", DOC)
    with pytest.raises(ValueError):
        service.ingest(kb.id, "  ", DOC)
    with pytest.raises(ValueError):
        service.ingest(kb.id, "doc", "   \n")


# -- retrieval -----------------------------------------------------------------


def test_retrieve_groups_children_by_parent_with_max_score():
    service, _ = _service()
    kb = service.create_knowledge_base("kb")
    service.ingest(kb.id, "doc1", DOC)
    # A long Beta section produces several children of one parent.
    long_beta = DOC.replace("beta body", "beta body " + " ".join(f"w{i}" for i in range(120)))
    service.ingest(kb.id, "doc1", long_beta)

    results = service.retrieve(kb.id, "beta body")
    beta_hits = [r for r in results if r.display_path == "Beta"]
    assert len(beta_hits) == 1  # parent dedupe
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    assert beta_hits[0].external_id == "doc1"
    assert beta_hits[0].content.startswith("## Beta")


def test_retrieve_max_parents_and_document_filter():
    service, _ = _service()
    kb = service.create_knowledge_base("kb")
    service.ingest(kb.id, "doc1", DOC)
    service.ingest(kb.id, "doc2", "## Alpha\nalpha elsewhere\n")

    capped = service.retrieve(kb.id, "alpha body", top_k=8, max_parents=1)
    assert len(capped) == 1

    doc1_id = service.list_documents(kb.id)[0].id
    filtered = service.retrieve(kb.id, "alpha body", document_ids=[doc1_id])
    assert filtered and all(r.document_id == doc1_id for r in filtered)


def test_retrieve_empty_kb_and_unknown_kb_and_validation():
    service, _ = _service()
    kb = service.create_knowledge_base("empty")
    assert service.retrieve(kb.id, "anything") == []
    with pytest.raises(NotFoundError):
        service.retrieve("missing", "query")
    with pytest.raises(ValueError):
        service.retrieve(kb.id, "   ")
    with pytest.raises(ValueError):
        service.retrieve(kb.id, "query", top_k=0)
    with pytest.raises(ValueError):
        service.retrieve(kb.id, "query", max_parents=99)


def test_get_context_formatting_r5():
    service, _ = _service()
    kb = service.create_knowledge_base("kb")
    service.ingest(kb.id, "doc1", DOC)
    context = service.get_context(kb.id, "alpha body")
    assert context.startswith("## Source: Alpha (doc1)\n")
    assert "alpha body one" in context
    assert context.endswith("\n\n")


# -- document management ---------------------------------------------------------


def test_document_delete_and_kb_cascade():
    store = InMemoryKnowledgeStore()
    provider = CountingProvider()
    service = KnowledgeService(store, provider)
    kb = service.create_knowledge_base("kb")
    service.ingest(kb.id, "doc1", DOC)
    document_id = service.list_documents(kb.id)[0].id

    service.delete_document(kb.id, document_id)
    assert service.get_document(kb.id, document_id) is None
    assert service.retrieve(kb.id, "alpha body") == []
    with pytest.raises(NotFoundError):
        service.delete_document(kb.id, document_id)

    # KB delete cascades to documents, parents, children (I9).
    service.ingest(kb.id, "doc2", DOC)
    doc2_id = service.list_documents(kb.id)[0].id
    service.delete_knowledge_base(kb.id)
    assert store.get_document(doc2_id) is None
    assert store.list_parents(doc2_id) == []
    assert store.list_anchors(doc2_id) == []


def test_get_document_requires_matching_kb():
    service, _ = _service()
    kb1 = service.create_knowledge_base("one")
    kb2 = service.create_knowledge_base("two")
    service.ingest(kb1.id, "doc1", DOC)
    document_id = service.list_documents(kb1.id)[0].id
    assert service.get_document(kb1.id, document_id) is not None
    assert service.get_document(kb2.id, document_id) is None


# -- capability + tool ------------------------------------------------------------


def test_capability_delegates_to_service():
    service, _ = _service()
    kb = service.create_knowledge_base("kb")
    service.ingest(kb.id, "doc1", DOC)
    capability = PlatformKnowledgeCapability(service)

    results = capability.retrieve(kb.id, "alpha body")
    assert results and results[0].display_path == "Alpha"
    document_id = service.list_documents(kb.id)[0].id
    assert capability.get_document(kb.id, document_id)["id"] == document_id
    assert capability.get_document(kb.id, "missing") is None
    assert capability.get_context(kb.id, "alpha body").startswith("## Source:")


def test_tool_handler_formats_and_validates():
    service, _ = _service()
    kb = service.create_knowledge_base("kb")
    service.ingest(kb.id, "doc1", DOC)
    handler = create_knowledge_tool_handler(service)

    assert KNOWLEDGE_TOOL_SPEC.name == "retrieve_knowledge"
    output = handler({"knowledge_base_id": kb.id, "query": "alpha body", "top_k": 4})
    assert "## Source: Alpha (doc1)" in output

    for arguments in (
        {},
        {"knowledge_base_id": kb.id},
        {"query": "q"},
        {"knowledge_base_id": kb.id, "query": "q", "top_k": "x"},
    ):
        with pytest.raises(Exception):
            handler(arguments)


def test_provider_unavailable_maps_to_typed_error():
    class NoKeyProvider:
        name = "openai:none"

        def embed_documents(self, texts):
            raise ProviderUnavailableError("no key")

        def embed_query(self, text):
            raise ProviderUnavailableError("no key")

    service = KnowledgeService(InMemoryKnowledgeStore(), NoKeyProvider())
    kb = service.create_knowledge_base("kb")
    with pytest.raises(ProviderUnavailableError):
        service.ingest(kb.id, "doc", DOC)
    document = service.list_documents(kb.id)[0]
    assert document.status is DocumentStatus.FAILED
    with pytest.raises(ProviderUnavailableError):
        service.retrieve(kb.id, "query")
