"""Workflow definition registry.

``NodeSpec`` handlers are Python callables, so WorkflowDefinitions live
in code, not in durable storage. Dispatched runs reference a definition
by ``name@version`` (application metadata), and the worker resolves that
reference through this registry (runtime-dispatch-spec.md section 4).
"""

from agent_platform.runtime.workflow.definition import WorkflowDefinition


class WorkflowRegistry:
    """Resolves workflow references to definitions registered in code."""

    def __init__(self) -> None:
        self._definitions: dict[tuple[str, str], WorkflowDefinition] = {}
        self._latest: dict[str, str] = {}

    def register(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        self._definitions[(definition.name, definition.version)] = definition
        self._latest[definition.name] = definition.version
        return definition

    def resolve(self, name: str, version: str | None = None) -> WorkflowDefinition:
        resolved_version = version or self._latest.get(name)
        if resolved_version is None:
            raise KeyError(f"workflow not registered: {name or '<unnamed>'}@<latest>")
        definition = self._definitions.get((name, resolved_version))
        if definition is None:
            raise KeyError(f"workflow not registered: {name}@{resolved_version}")
        return definition
