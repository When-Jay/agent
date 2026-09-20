"""Knowledge management + retrieval routes (knowledge-rag-spec.md section 10).

All routes live under /api/v1/knowledge. Error mapping: NotFoundError
-> 404, ValueError (validation) -> 422, EmbeddingModelChangedError ->
409, ProviderUnavailableError -> 503. Ingest responses are 200 (upload
is an upsert, not a creation).
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent_platform.errors import NotFoundError
from agent_platform.knowledge.application import KnowledgeService
from agent_platform.knowledge.domain import EmbeddingModelChangedError, ProviderUnavailableError


class CreateKnowledgeBaseRequest(BaseModel):
    name: str
    anchor_levels: list[int] | None = None
    child_chunk_size: int | None = Field(default=None, ge=1, le=100000)
    child_overlap: int | None = Field(default=None, ge=0, le=100000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestDocumentRequest(BaseModel):
    external_id: str
    content: str
    title: str = ""
    source_uri: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrieveRequest(BaseModel):
    query: str
    top_k: int = Field(default=8, ge=1, le=100)
    max_parents: int = Field(default=4, ge=1, le=100)
    document_ids: list[str] = Field(default_factory=list)


def attach_knowledge_routes(app, service: KnowledgeService) -> None:
    """Mount knowledge routes; per-route error mapping keeps the global
    handler surface untouched (mirrors evaluation.api)."""
    router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

    @router.post("/bases", status_code=201)
    def create_knowledge_base(request: CreateKnowledgeBaseRequest) -> dict[str, Any]:
        try:
            kb = service.create_knowledge_base(
                request.name,
                anchor_levels=request.anchor_levels,
                child_chunk_size=request.child_chunk_size,
                child_overlap=request.child_overlap,
                metadata=request.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return service.get_knowledge_base(kb.id)

    @router.get("/bases")
    def list_knowledge_bases() -> dict[str, Any]:
        return {"bases": [service.get_knowledge_base(kb.id) for kb in service.list_knowledge_bases()]}

    @router.get("/bases/{kb_id}")
    def get_knowledge_base(kb_id: str) -> dict[str, Any]:
        try:
            return service.get_knowledge_base(kb_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete("/bases/{kb_id}", status_code=204)
    def delete_knowledge_base(kb_id: str) -> None:
        try:
            service.delete_knowledge_base(kb_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/bases/{kb_id}/documents")
    def ingest_document(kb_id: str, request: IngestDocumentRequest) -> dict[str, Any]:
        try:
            report = service.ingest(
                kb_id,
                request.external_id,
                request.content,
                title=request.title,
                source_uri=request.source_uri,
                metadata=request.metadata,
            )
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except EmbeddingModelChangedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProviderUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "document_id": report.document_id,
            "external_id": report.external_id,
            "doc_version": report.doc_version,
            "content_hash": report.content_hash,
            "anchors_added": report.anchors_added,
            "anchors_changed": report.anchors_changed,
            "anchors_removed": report.anchors_removed,
            "children_indexed": report.children_indexed,
            "children_deleted": report.children_deleted,
            "unchanged": report.unchanged,
        }

    @router.get("/bases/{kb_id}/documents")
    def list_documents(kb_id: str) -> dict[str, Any]:
        try:
            documents = service.list_documents(kb_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "documents": [
                {
                    "id": document.id,
                    "external_id": document.external_id,
                    "title": document.title,
                    "status": document.status.value,
                    "doc_version": document.doc_version,
                    "anchor_count": document.anchor_count,
                    "child_count": document.child_count,
                    "error": document.error,
                    "created_at": document.created_at.isoformat(),
                    "updated_at": document.updated_at.isoformat(),
                }
                for document in documents
            ]
        }

    @router.get("/bases/{kb_id}/documents/{document_id}")
    def get_document(kb_id: str, document_id: str) -> dict[str, Any]:
        try:
            document = service.get_document(kb_id, document_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if document is None:
            raise HTTPException(
                status_code=404, detail=f"document not found: {document_id}"
            )
        return document

    @router.delete("/bases/{kb_id}/documents/{document_id}", status_code=204)
    def delete_document(kb_id: str, document_id: str) -> None:
        try:
            service.delete_document(kb_id, document_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/bases/{kb_id}/retrieve")
    def retrieve(kb_id: str, request: RetrieveRequest) -> dict[str, Any]:
        try:
            results = service.retrieve(
                kb_id,
                request.query,
                top_k=request.top_k,
                max_parents=request.max_parents,
                document_ids=request.document_ids,
            )
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except EmbeddingModelChangedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProviderUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "results": [
                {
                    "content": result.content,
                    "score": result.score,
                    "display_path": result.display_path,
                    "anchor_level": result.anchor_level,
                    "heading": result.heading,
                    "document_id": result.document_id,
                    "external_id": result.external_id,
                    "knowledge_base_id": result.knowledge_base_id,
                    "document_metadata": result.document_metadata,
                }
                for result in results
            ]
        }

    app.include_router(router)
