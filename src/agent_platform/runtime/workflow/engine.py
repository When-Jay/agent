"""Workflow engine boundary.

The engine executes a WorkflowDefinition. LangGraph is the V1 engine
and lives behind this interface; it must never leak outside the
Workflow Runtime implementation layer.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from agent_platform.runtime.workflow.definition import WorkflowDefinition
from agent_platform.runtime.workflow.nodes import NodeExecutor

# A superstep is {node_name: partial_state_update}; parallel nodes in the
# same superstep appear together. None update means the node wrote nothing.
Superstep = dict[str, dict[str, Any] | None]


class WorkflowEngine(ABC):
    @abstractmethod
    def start(
        self,
        *,
        definition: WorkflowDefinition,
        state: dict[str, Any],
        run_id: str,
        node_executor: NodeExecutor,
    ) -> Iterator[Superstep]:
        """Execute the workflow from its entry node, yielding per superstep."""

    @abstractmethod
    def resume(
        self,
        *,
        definition: WorkflowDefinition,
        run_id: str,
        node_executor: NodeExecutor,
    ) -> Iterator[Superstep]:
        """Continue an interrupted execution from the engine checkpoint."""
