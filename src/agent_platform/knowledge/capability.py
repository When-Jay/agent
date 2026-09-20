"""Platform KnowledgeCapability implementation (spec section 11).

Delegates to KnowledgeService; the runtime depends only on
KnowledgeCapability from runtime.capabilities.
"""

from agent_platform.knowledge.application import KnowledgeService
from agent_platform.runtime.capabilities.knowledge import KnowledgeCapability, RetrievedContext


class PlatformKnowledgeCapability(KnowledgeCapability):

    def __init__(self, service: KnowledgeService) -> None:
        self._service = service

    def retrieve(
        self,
        knowledge_base_id: str,
        query: str,
        *,
        top_k: int = 8,
        max_parents: int = 4,
        document_ids: tuple[str, ...] | list[str] = (),
    ) -> list[RetrievedContext]:
        return self._service.retrieve(
            knowledge_base_id,
            query,
            top_k=top_k,
            max_parents=max_parents,
            document_ids=document_ids,
        )

    def get_document(self, knowledge_base_id: str, document_id: str) -> dict | None:
        return self._service.get_document(knowledge_base_id, document_id)

    def get_context(self, knowledge_base_id: str, query: str, **kwargs) -> str:
        return self._service.get_context(knowledge_base_id, query, **kwargs)
