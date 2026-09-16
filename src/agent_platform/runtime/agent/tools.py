"""LangChainToolAdapter: exposes platform tools as LangChain tools.

Per ADR-0002 the platform owns the tool registry and invocation; the
agent execution layer must consume them as native LangChain tools so
that DeepAgents / LangChain drives the tool-calling loop.
"""

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field, create_model
from langchain_core.tools import BaseTool, StructuredTool

from agent_platform.runtime.capabilities.tool import ToolCallRequest, ToolSpec
from agent_platform.runtime.capabilities.tool_capability import ToolCapability
from agent_platform.errors import PlatformError


class ToolAdaptationError(PlatformError):
    """Raised when a platform tool cannot be exposed to LangChain."""


def _schema_to_pydantic(spec: ToolSpec) -> type[BaseModel]:
    """Build a pydantic args model from the ToolSpec JSON-schema parameters."""
    properties: dict[str, Any] = spec.parameters.get("properties", {}) or {}
    required: list[str] = list(spec.parameters.get("required", []) or [])
    if not properties:
        return type(f"{spec.name}_args", (BaseModel,), {"__doc__": spec.description or None})

    fields: dict[str, tuple[type, Any]] = {}
    for prop_name, prop_schema in properties.items():
        annotation: type = Any
        json_type = (prop_schema or {}).get("type")
        if json_type == "string":
            annotation = str
        elif json_type == "integer":
            annotation = int
        elif json_type == "number":
            annotation = float
        elif json_type == "boolean":
            annotation = bool
        fields[prop_name] = (
            annotation,
            Field(
                description=(prop_schema or {}).get("description"),
                default=... if prop_name in required else (prop_schema or {}).get("default"),
            ),
        )
    return create_model(f"{spec.name}_args", __doc__=spec.description or None, **fields)


class LangChainToolAdapter:
    """Adapts platform ToolCapability entries into LangChain tools."""

    def __init__(self, tool_capability: ToolCapability) -> None:
        self._tools = tool_capability

    def adapt(self, names: list[str] | None = None) -> list[BaseTool]:
        """Return LangChain tools; `names` filters the registry (None = all)."""
        selected = [
            spec
            for spec in self._tools.list_tools()
            if names is None or spec.name in set(names)
        ]
        if names is not None:
            missing = set(names) - {spec.name for spec in selected}
            if missing:
                raise ToolAdaptationError(f"tools not registered: {sorted(missing)}")

        adapted: list[BaseTool] = []
        for spec in selected:
            adapted.append(self._adapt_one(spec))
        return adapted

    def _adapt_one(self, spec: ToolSpec) -> BaseTool:
        capability = self._tools

        async def _arun(**kwargs: Any) -> str:
            result = await asyncio.to_thread(
                capability.invoke,
                ToolCallRequest(id=f"lc-{spec.name}", name=spec.name, arguments=kwargs),
            )
            if result.error:
                return json.dumps({"error": result.error})
            return result.content

        def _run(**kwargs: Any) -> str:
            result = capability.invoke(
                ToolCallRequest(id=f"lc-{spec.name}", name=spec.name, arguments=kwargs)
            )
            if result.error:
                return json.dumps({"error": result.error})
            return result.content

        return StructuredTool.from_function(
            func=_run,
            coroutine=_arun,
            name=spec.name,
            description=spec.description or spec.name,
            args_schema=_schema_to_pydantic(spec),
        )
