"""Platform workflow definition concepts.

These are the Workflow Runtime's own objects. LangGraph never leaks
through this module; the adapter compiles a WorkflowDefinition into an
engine graph.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable

NodeHandler = Callable[[dict[str, Any]], dict[str, Any] | None]
EdgeCondition = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True)
class NodeSpec:
    """One executable workflow node.

    handler receives the shared state dict and returns a partial state
    update (None means no update). node_type selects the execution
    flavor: "code" runs the handler directly; "human" uses the handler
    to build the human-input request payload and pauses the run until a
    response arrives (workflow-runtime-spec.md section 4).
    """

    name: str
    handler: NodeHandler
    retries: int = 0
    timeout_seconds: float | None = None
    node_type: str = "code"  # "code" | "human"


@dataclass(frozen=True)
class EdgeSpec:
    """Directed edge between nodes.

    A source node may declare several conditional edges (evaluated in
    definition order) plus at most one unconditional default edge. When
    no condition matches, the default edge (or END) is taken. Sources
    with multiple unconditional edges fan out in parallel.
    """

    source: str
    target: str | None = None  # None -> END
    condition: EdgeCondition | None = None


class WorkflowDefinitionError(ValueError):
    """Raised when a workflow definition is structurally invalid."""


def _callable_id(fn: Callable | None) -> str:
    """Stable identity for a handler/condition (module + qualname).

    Content hashes must be reproducible across processes, so memory
    addresses (repr of lambdas/partial objects) are avoided; callables
    without a qualname fall back to repr within a single process.
    """
    if fn is None:
        return ""
    module = getattr(fn, "__module__", None)
    qualname = getattr(fn, "__qualname__", None)
    if module and qualname:
        return f"{module}:{qualname}"
    return repr(fn)


@dataclass
class WorkflowDefinition:
    name: str
    version: str
    nodes: list[NodeSpec] = field(default_factory=list)
    edges: list[EdgeSpec] = field(default_factory=list)
    entry: str = ""

    def __post_init__(self) -> None:
        self._validate()

    @property
    def node_map(self) -> dict[str, NodeSpec]:
        return {node.name: node for node in self.nodes}

    @property
    def content_hash(self) -> str:
        """Stable fingerprint of this definition's content (section 6).

        Binds a Run to the exact definition it executed, beyond the
        version label: two definitions with the same name@version but
        different nodes/edges/wiring hash differently. Handler and
        condition callables are identified by module+qualname.
        """
        payload = {
            "name": self.name,
            "version": self.version,
            "entry": self.entry,
            "nodes": [
                [node.name, node.node_type, node.retries, node.timeout_seconds,
                 _callable_id(node.handler)]
                for node in self.nodes
            ],
            "edges": [
                [edge.source, edge.target, _callable_id(edge.condition)]
                for edge in self.edges
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def outgoing(self, source: str) -> list[EdgeSpec]:
        return [edge for edge in self.edges if edge.source == source]

    def _validate(self) -> None:
        names = [node.name for node in self.nodes]
        if len(names) != len(set(names)):
            raise WorkflowDefinitionError("duplicate node names in workflow definition")
        for node in self.nodes:
            if node.node_type not in {"code", "human"}:
                raise WorkflowDefinitionError(f"unknown node_type on {node.name}: {node.node_type}")
        if not self.nodes:
            raise WorkflowDefinitionError("workflow definition requires at least one node")
        node_set = set(names)
        if self.entry not in node_set:
            raise WorkflowDefinitionError(f"entry node not found: {self.entry}")
        for edge in self.edges:
            if edge.source not in node_set:
                raise WorkflowDefinitionError(f"edge source not found: {edge.source}")
            if edge.target is not None and edge.target not in node_set:
                raise WorkflowDefinitionError(f"edge target not found: {edge.target}")
        # Entry node must be reachable as a start; nodes without outgoing
        # edges are implicit END nodes and are wired by the engine.
