"""Knowledge capability interface (runtime-capabilities-spec.md section 4).

Runtimes depend on this abstraction only; the implementation lives in
agent_platform.knowledge (knowledge-rag-spec.md section 11).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievedContext:
    """One retrieved parent section (spec R3). content is the whole
    parent — the LLM context unit, returned verbatim."""

    content: str
    score: float
    display_path: str
    anchor_level: int
    heading: str
    document_id: str
    external_id: str
    knowledge_base_id: str
    document_metadata: dict = field(default_factory=dict)


class KnowledgeCapability(ABC):
    """Knowledge retrieval capability (retrieve / get_document /
    get_context). rerank is reserved (runtime-capabilities-spec section 4)."""

    @abstractmethod
    def retrieve(
        self,
        knowledge_base_id: str,
        query: str,
        *,
        top_k: int = 8,
        max_parents: int = 4,
        document_ids: tuple[str, ...] | list[str] = (),
    ) -> list[RetrievedContext]:
        """Retrieve parent sections for a query (spec section 7)."""

    @abstractmethod
    def get_document(self, knowledge_base_id: str, document_id: str) -> dict | None:
        """Document detail with anchor inventory; None when absent."""

    @abstractmethod
    def get_context(self, knowledge_base_id: str, query: str, **kwargs) -> str:
        """retrieve + R5 formatting into one string."""
