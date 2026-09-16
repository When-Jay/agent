"""WorkflowRunner: executes workflows on Runtime Core with a pluggable engine.

Flow per superstep (see workflow-runtime-spec.md section 3):
Load Workflow -> Load State -> Execute Node(s) -> Update State
-> Emit Event -> Checkpoint -> Resolve Next -> Repeat.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from agent_platform.errors import InvalidStateTransitionError
from agent_platform.runtime.core import (
    CheckpointStore,
    EventBus,
    RunManager,
    RunStatus,
    RuntimeEventType,
    RuntimeStore,
    StateManager,
)
from agent_platform.runtime.workflow.definition import WorkflowDefinition
from agent_platform.runtime.workflow.engine import Superstep, WorkflowEngine
from agent_platform.runtime.workflow.langgraph_engine import LangGraphWorkflowEngine
from agent_platform.runtime.workflow.nodes import NodeExecutor, WorkflowNodeError


@dataclass(frozen=True)
class WorkflowRunResult:
    status: str  # "completed" | "failed"
    state: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class WorkflowRunner:
    def __init__(self, store: RuntimeStore, *, engine: WorkflowEngine | None = None) -> None:
        self._store = store
        self._engine = engine or LangGraphWorkflowEngine()
        self._runs = RunManager(store)
        self._states = StateManager(store)
        self._events = EventBus(store)
        self._checkpoints = CheckpointStore(store)

    def run(
        self,
        *,
        run_id: str,
        definition: WorkflowDefinition,
        input: dict[str, Any] | None = None,
    ) -> WorkflowRunResult:
        run = self._runs.get_run(run_id)
        # Dispatched runs arrive as queued; direct runs are created.
        if run.status not in {RunStatus.CREATED, RunStatus.QUEUED}:
            raise InvalidStateTransitionError(f"cannot start run from {run.status.value}")
        self._runs.start_run(run_id)
        self._publish(
            run_id,
            RuntimeEventType.RUN_STARTED,
            {"workflow": definition.name, "version": definition.version, "input": input or {}},
        )
        state = self._initial_state(definition, input)
        self._persist(run_id, state)
        stream = self._engine.start(
            definition=definition,
            state=state["values"],
            run_id=run_id,
            node_executor=self._node_executor(run_id),
        )
        return self._consume(run_id, stream, state)

    def resume(self, *, run_id: str, definition: WorkflowDefinition) -> WorkflowRunResult:
        run = self._runs.get_run(run_id)
        if run.status not in {RunStatus.RUNNING, RunStatus.FAILED}:
            raise InvalidStateTransitionError(f"cannot resume run from {run.status.value}")
        state = self._load_state(run_id)
        self._ensure_same_workflow(state, definition)
        if run.status is RunStatus.FAILED:
            self._runs.restart_run(run_id)
        stream = self._engine.resume(
            definition=definition,
            run_id=run_id,
            node_executor=self._node_executor(run_id),
        )
        return self._consume(run_id, stream, state)

    # --- internal -----------------------------------------------------------

    def _consume(self, run_id: str, stream: Iterator[Superstep], state: dict[str, Any]) -> WorkflowRunResult:
        try:
            for superstep in stream:
                for _node_name, update in superstep.items():
                    if update:
                        state["values"].update(update)
                self._persist(run_id, state)
        except WorkflowNodeError as exc:
            state["status"] = "failed"
            state["error"] = exc.error
            self._persist(run_id, state)
            self._runs.fail_run(run_id, error=exc.error)
            self._publish(
                run_id,
                RuntimeEventType.RUN_FAILED,
                {"error": exc.error, "node": exc.node_name},
            )
            return WorkflowRunResult(status="failed", state=dict(state["values"]), error=exc.error)

        state["status"] = "completed"
        self._persist(run_id, state)
        self._runs.complete_run(run_id, output=dict(state["values"]))
        self._publish(run_id, RuntimeEventType.RUN_COMPLETED, {"output": dict(state["values"])})
        self._engine.drop(run_id)
        return WorkflowRunResult(status="completed", state=dict(state["values"]))

    def _initial_state(self, definition: WorkflowDefinition, input: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "definition": {"name": definition.name, "version": definition.version},
            "values": dict(input or {}),
            "status": "running",
        }

    def _load_state(self, run_id: str) -> dict[str, Any]:
        stored = self._states.get_state(run_id).values.get("workflow")
        if stored is None:
            raise InvalidStateTransitionError(f"run has no workflow state: {run_id}")
        return stored

    def _ensure_same_workflow(self, state: dict[str, Any], definition: WorkflowDefinition) -> None:
        bound = state.get("definition", {})
        if bound.get("name") != definition.name or bound.get("version") != definition.version:
            raise InvalidStateTransitionError(
                f"run is bound to workflow {bound.get('name')}@{bound.get('version')}, "
                f"got {definition.name}@{definition.version}"
            )

    def _persist(self, run_id: str, state: dict[str, Any]) -> None:
        self._states.update_state(run_id, values={"workflow": state})
        checkpoint = self._checkpoints.create(run_id=run_id, state={"workflow": state})
        self._publish(
            run_id,
            RuntimeEventType.CHECKPOINT_CREATED,
            {"checkpoint_id": checkpoint.id, "status": state.get("status")},
        )

    def _node_executor(self, run_id: str) -> NodeExecutor:
        return NodeExecutor(
            lambda event, payload: self._publish_node_event(run_id, event, payload)
        )

    def _publish_node_event(self, run_id: str, event_name: str, payload: dict[str, Any]) -> None:
        event_type = RuntimeEventType(event_name)
        self._publish(run_id, event_type, payload)

    def _publish(self, run_id: str, event_type: RuntimeEventType, payload: dict[str, Any]) -> None:
        self._events.publish(run_id=run_id, event_type=event_type, payload=payload)
