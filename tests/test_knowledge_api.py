"""Knowledge API contract tests (plan 070 section 12, test 9).

Route contract + error mapping (404/422/409/503) over create_app with
an injected KnowledgeService; plus default-composition boot proof
(lazy provider, no embedding key needed for management routes).
"""

import pytest
from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.config import Settings
from agent_platform.knowledge.application import KnowledgeService
from agent_platform.knowledge.domain import ProviderUnavailableError
from agent_platform.knowledge.embeddings import FakeEmbeddingProvider
from agent_platform.knowledge.storage import InMemoryKnowledgeStore

DOC = (
    "Intro paragraph.\n"
    "\n"
    "## Alpha\n"
    "alpha body one\n"
    "\n"
    "## Beta\n"
    "beta body\n"
)


class UnavailableProvider:
    """Provider that always fails (503 path; mirrors missing key)."""

    name = "unavailable"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise ProviderUnavailableError("embedding provider unavailable")

    def embed_query(self, text: str) -> list[float]:
        raise ProviderUnavailableError("embedding provider unavailable")


class DimensionProvider:
    """Fixed one-dimension vectors (409 path)."""

    name = "dim:1"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0]


def _client(service: KnowledgeService) -> TestClient:
    settings = Settings(celery_task_always_eager=True)
    return TestClient(create_app(settings, knowledge_service=service))


def _fake_service() -> KnowledgeService:
    return KnowledgeService(InMemoryKnowledgeStore(), FakeEmbeddingProvider())


def test_knowledge_base_lifecycle():
    client = _client(_fake_service())
    created = client.post(
        "/api/v1/knowledge/bases",
        json={"name": "docs", "metadata": {"team": "core"}},
    )
    assert created.status_code == 201
    kb = created.json()
    assert kb["anchor_levels"] == [2, 3]
    assert kb["child_chunk_size"] == 800 and kb["child_overlap"] == 150
    assert kb["stats"] == {"documents": 0, "children": 0}
    kb_id = kb["id"]

    listing = client.get("/api/v1/knowledge/bases")
    assert listing.status_code == 200
    assert [b["name"] for b in listing.json()["bases"]] == ["docs"]

    detail = client.get(f"/api/v1/knowledge/bases/{kb_id}")
    assert detail.status_code == 200 and detail.json()["id"] == kb_id
    assert client.get("/api/v1/knowledge/bases/missing").status_code == 404

    deleted = client.delete(f"/api/v1/knowledge/bases/{kb_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/knowledge/bases/{kb_id}").status_code == 404


def test_kb_creation_validation_errors():
    client = _client(_fake_service())
    assert client.post("/api/v1/knowledge/bases", json={"name": "  "}).status_code == 422
    assert (
        client.post("/api/v1/knowledge/bases", json={"name": "x", "anchor_levels": [9]}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/knowledge/bases", json={"name": "x", "child_overlap": 9999}
        ).status_code
        == 422
    )
    # Duplicate name.
    client.post("/api/v1/knowledge/bases", json={"name": "dup"})
    assert client.post("/api/v1/knowledge/bases", json={"name": "dup"}).status_code == 422


def test_document_ingest_upsert_and_inventory():
    client = _client(_fake_service())
    kb_id = client.post("/api/v1/knowledge/bases", json={"name": "docs"}).json()["id"]

    first = client.post(
        f"/api/v1/knowledge/bases/{kb_id}/documents",
        json={"external_id": "doc1", "content": DOC, "title": "T"},
    )
    assert first.status_code == 200  # upsert semantics, not 201
    report = first.json()
    assert report["anchors_added"] == 3 and report["unchanged"] is False
    assert report["doc_version"] == 0
    document_id = report["document_id"]

    # Same content -> no-op.
    again = client.post(
        f"/api/v1/knowledge/bases/{kb_id}/documents",
        json={"external_id": "doc1", "content": DOC},
    )
    assert again.status_code == 200
    assert again.json()["unchanged"] is True

    # Edited content -> one changed anchor.
    edited = client.post(
        f"/api/v1/knowledge/bases/{kb_id}/documents",
        json={"external_id": "doc1", "content": DOC.replace("beta body", "beta v2")},
    )
    assert edited.json()["anchors_changed"] == 1
    assert edited.json()["doc_version"] == 1

    listing = client.get(f"/api/v1/knowledge/bases/{kb_id}/documents")
    assert listing.status_code == 200
    assert listing.json()["documents"][0]["status"] == "READY"

    detail = client.get(f"/api/v1/knowledge/bases/{kb_id}/documents/{document_id}")
    assert detail.status_code == 200
    anchors = detail.json()["anchors"]
    assert [a["display_path"] for a in anchors] == ["(preamble)", "Alpha", "Beta"]

    assert (
        client.get(f"/api/v1/knowledge/bases/{kb_id}/documents/missing").status_code == 404
    )
    assert (
        client.post(
            "/api/v1/knowledge/bases/missing/documents",
            json={"external_id": "x", "content": DOC},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/knowledge/bases/{kb_id}/documents",
            json={"external_id": "x", "content": "   "},
        ).status_code
        == 422
    )

    deleted = client.delete(f"/api/v1/knowledge/bases/{kb_id}/documents/{document_id}")
    assert deleted.status_code == 204
    assert (
        client.get(f"/api/v1/knowledge/bases/{kb_id}/documents/{document_id}").status_code
        == 404
    )


def test_retrieve_endpoint_contract():
    client = _client(_fake_service())
    kb_id = client.post("/api/v1/knowledge/bases", json={"name": "docs"}).json()["id"]
    client.post(
        f"/api/v1/knowledge/bases/{kb_id}/documents",
        json={"external_id": "doc1", "content": DOC},
    )

    response = client.post(
        f"/api/v1/knowledge/bases/{kb_id}/retrieve",
        json={"query": "alpha body", "top_k": 4, "max_parents": 2},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert results and results[0]["display_path"] == "Alpha"
    assert results[0]["external_id"] == "doc1"
    assert results[0]["knowledge_base_id"] == kb_id
    assert "alpha body one" in results[0]["content"]

    # Empty KB -> [].
    empty_id = client.post("/api/v1/knowledge/bases", json={"name": "empty"}).json()["id"]
    assert (
        client.post(f"/api/v1/knowledge/bases/{empty_id}/retrieve", json={"query": "x"}).json()[
            "results"
        ]
        == []
    )

    assert (
        client.post("/api/v1/knowledge/bases/missing/retrieve", json={"query": "x"}).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/knowledge/bases/{kb_id}/retrieve", json={"query": "  "}).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/v1/knowledge/bases/{kb_id}/retrieve",
            json={"query": "x", "top_k": 0},
        ).status_code
        == 422
    )


def test_provider_unavailable_maps_to_503_and_marks_failed():
    service = KnowledgeService(InMemoryKnowledgeStore(), UnavailableProvider())
    client = _client(service)
    kb_id = client.post("/api/v1/knowledge/bases", json={"name": "docs"}).json()["id"]

    response = client.post(
        f"/api/v1/knowledge/bases/{kb_id}/documents",
        json={"external_id": "doc1", "content": DOC},
    )
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]

    # Document marked FAILED (I7); management routes stay functional.
    listing = client.get(f"/api/v1/knowledge/bases/{kb_id}/documents")
    assert listing.json()["documents"][0]["status"] == "FAILED"

    assert (
        client.post(f"/api/v1/knowledge/bases/{kb_id}/retrieve", json={"query": "x"}).status_code
        == 503
    )


def test_dimension_mismatch_maps_to_409():
    store = InMemoryKnowledgeStore()
    first = KnowledgeService(store, FakeEmbeddingProvider(64))
    client = TestClient(
        create_app(Settings(celery_task_always_eager=True), knowledge_service=first)
    )
    kb_id = client.post("/api/v1/knowledge/bases", json={"name": "docs"}).json()["id"]
    client.post(
        f"/api/v1/knowledge/bases/{kb_id}/documents",
        json={"external_id": "doc1", "content": DOC},
    )

    second = KnowledgeService(store, DimensionProvider())
    other_client = TestClient(
        create_app(Settings(celery_task_always_eager=True), knowledge_service=second)
    )
    assert (
        other_client.post(
            f"/api/v1/knowledge/bases/{kb_id}/documents",
            json={"external_id": "doc2", "content": DOC},
        ).status_code
        == 409
    )
    assert (
        other_client.post(
            f"/api/v1/knowledge/bases/{kb_id}/retrieve", json={"query": "x"}
        ).status_code
        == 409
    )


def test_default_composition_boots_and_manages_without_embedding_key(monkeypatch):
    """Default create_app(): openai provider selected lazily; KB management
    works with no key (ingest would attempt the provider)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(create_app(Settings(celery_task_always_eager=True)))

    created = client.post("/api/v1/knowledge/bases", json={"name": "boot"})
    assert created.status_code == 201
    kb_id = created.json()["id"]
    assert client.get(f"/api/v1/knowledge/bases/{kb_id}").status_code == 200

    # With no key the lazy OpenAI construction fails -> 503 (not a crash).
    response = client.post(
        f"/api/v1/knowledge/bases/{kb_id}/documents",
        json={"external_id": "doc1", "content": DOC},
    )
    assert response.status_code == 503
