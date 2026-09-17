"""LangGraph adapter for the Workflow Runtime.

This is the ONLY module allowed to import LangGraph. It compiles the
platform WorkflowDefinition into a LangGraph StateGraph and drives it
superstep by superstep. Without a checkpointer each run gets an
in-memory MemorySaver (in-process resume only); an injected durable
checkpointer (StoreCheckpointSaver) reattaches interrupted threads
across processes, keyed by run_id.
"""

from collections.abc import Iterator
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent_platform.errors import NotFoundError
from agent_platform.runtime.workflow.definition import WorkflowDefinition
from agent_platform.runtime.workflow.engine import INTERRUPTED_KEY, Superstep, WorkflowEngine
from agent_platform.runtime.workflow.nodes import NodeExecutor


def _merge_values(left: dict, right: dict) -> dict:
    """Reducer so parallel nodes merging into one key combine instead of clashing."""
    return {**left, **right}


class _EngineState(TypedDict, total=False):
    values: Annotated[dict, _merge_values]


class LangGraphWorkflowEngine(WorkflowEngine):
    """Compiles WorkflowDefinitions to LangGraph and streams supersteps."""

    def __init__(self, checkpointer: BaseCheckpointSaver | None = None) -> None:
        # run_id -> compiled graph + invocation config (in-process resume cache)
        self._runs: dict[str, tuple[Any, dict[str, Any]]] = {}
        # None -> per-run MemorySaver; a durable saver enables cross-process resume.
        self._checkpointer = checkpointer

    def start(
        self,
        *,
        definition: WorkflowDefinition,
        state: dict[str, Any],
        run_id: str,
        node_executor: NodeExecutor,
    ) -> Iterator[Superstep]:
        # A start is always a fresh execution (first dispatch or retry):
        # drop the cached graph and any durable thread left by an earlier
        # attempt so stale channel values cannot leak into the new run.
        self._runs.pop(run_id, None)
        if self._checkpointer is not None:
            self._checkpointer.delete_thread(run_id)
        compiled, config = self._compile(definition, node_executor, run_id)
        return self._stream(compiled, config, {"values": dict(state)})

    def resume(
        self,
        *,
        definition: WorkflowDefinition,
        run_id: str,
        node_executor: NodeExecutor,
        response: Any = None,
    ) -> Iterator[Superstep]:
        compiled, config = self._compile(definition, node_executor, run_id)
        input_state = None if response is None else Command(resume=response)
        return self._stream(compiled, config, input_state)

    def _compile(
        self,
        definition: WorkflowDefinition,
        node_executor: NodeExecutor,
        run_id: str,
    ) -> tuple[Any, dict[str, Any]]:
        cached = self._runs.get(run_id)
        if cached is not None:
            return cached

        graph = StateGraph(_EngineState)
        for node in definition.nodes:
            graph.add_node(node.name, self._wrap(node, node_executor))

        graph.add_edge(START, definition.entry)
        for node in definition.nodes:
            self._wire_outgoing(graph, definition, node.name)
        for node in definition.nodes:
            if not definition.outgoing(node.name):
                graph.add_edge(node.name, END)

        compiled = graph.compile(
            checkpointer=self._checkpointer if self._checkpointer is not None else MemorySaver()
        )
        config = {"configurable": {"thread_id": run_id}}
        self._runs[run_id] = (compiled, config)
        return compiled, config

    def _wire_outgoing(self, graph: StateGraph, definition: WorkflowDefinition, source: str) -> None:
        outgoing = definition.outgoing(source)
        conditional = [edge for edge in outgoing if edge.condition is not None]
        defaults = [edge for edge in outgoing if edge.condition is None]

        if conditional:
            # Conditional source: unconditional edges become the router
            # fallback so they never fire in parallel with a branch.
            default_target = defaults[0].target if defaults else None

            def router(state: dict) -> str:
                values = state.get("values", {})
                for edge in conditional:
                    if edge.condition(values) and edge.target is not None:
                        return edge.target
                return default_target if default_target is not None else END

            graph.add_conditional_edges(source, router)
            return

        for edge in defaults:
            if edge.target is None:
                graph.add_edge(source, END)
            else:
                graph.add_edge(source, edge.target)

    def _wrap(self, node, node_executor: NodeExecutor):
        def _run(state: dict) -> dict:
            values = state.get("values", {})
            if node.node_type == "human":
                request = node_executor.request_human_input(node, values)
                # Pauses the graph; on resume the node replays and this
                # returns the supplied human response.
                decision = interrupt(request)
                return {"values": node_executor.apply_human_response(node, decision)}
            update = node_executor.execute(node, values)
            return {"values": update} if update else {}

        return _run

    def _stream(self, compiled: Any, config: dict[str, Any], input_state: dict | Any) -> Iterator[Superstep]:
        for chunk in compiled.stream(input_state, config, stream_mode="updates"):
            superstep: Superstep = {}
            for node_name, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                values = update.get("values")
                superstep[node_name] = dict(values) if values else None
            if superstep:
                yield superstep

        # The stream ends after an interrupt; surface the pause to the
        # runner together with the pending node and request payload.
        snapshot = compiled.get_state(config)
        if snapshot.next:
            node_name = snapshot.next[0]
            request = next(
                (task.interrupts[0].value for task in snapshot.tasks if task.interrupts), None
            )
            yield {INTERRUPTED_KEY: {"node": node_name, "request": request}}

    def drop(self, run_id: str) -> None:
        """Release in-process engine state for a run."""
        if run_id not in self._runs:
            raise NotFoundError(f"no engine state for run: {run_id}")
        del self._runs[run_id]
