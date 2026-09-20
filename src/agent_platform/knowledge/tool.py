"""retrieve_knowledge agent tool (spec section 11).

Registered on the worker's native tool registry only when
KNOWLEDGE_TOOL_ENABLED=true (default off: existing agent tool surfaces
must not change). The handler returns R5-formatted context.
"""

from collections.abc import Callable
from typing import Any

from agent_platform.knowledge.application import KnowledgeService
from agent_platform.runtime.capabilities.tool import ToolSpec

KNOWLEDGE_TOOL_NAME = "retrieve_knowledge"

KNOWLEDGE_TOOL_SPEC = ToolSpec(
    name=KNOWLEDGE_TOOL_NAME,
    description=(
        "Search a knowledge base and return the most relevant whole "
        "document sections (with source headers) as context."
    ),
    parameters={
        "type": "object",
        "properties": {
            "knowledge_base_id": {
                "type": "string",
                "description": "ID of the knowledge base to search.",
            },
            "query": {
                "type": "string",
                "description": "Natural-language search query.",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "How many child chunks to fetch before parent grouping.",
            },
        },
        "required": ["knowledge_base_id", "query"],
    },
)


def create_knowledge_tool_handler(service: KnowledgeService) -> Callable[[dict[str, Any]], str]:
    """Handler over KnowledgeService.get_context; errors surface through
    the tool result (the registry catches exceptions)."""

    def handler(arguments: dict[str, Any]) -> str:
        knowledge_base_id = arguments.get("knowledge_base_id")
        query = arguments.get("query")
        if not isinstance(knowledge_base_id, str) or not knowledge_base_id.strip():
            raise ValueError("knowledge_base_id must be a non-empty string")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        kwargs: dict[str, Any] = {}
        top_k = arguments.get("top_k")
        if top_k is not None:
            if not isinstance(top_k, int) or isinstance(top_k, bool):
                raise ValueError("top_k must be an integer")
            kwargs["top_k"] = top_k
        return service.get_context(knowledge_base_id.strip(), query, **kwargs)

    return handler
