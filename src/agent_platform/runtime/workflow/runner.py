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
from agent_platform.runtime.workflow.engine import INTERRUPTED_KEY, Superstep, WorkflowEngine
from agent_platform.runtime.workflow.langgraph_engine import LangGraphWorkflowEngine
from agent_platform.runtime.workflow.nodes import NodeExecutor, WorkflowNodeError
from agent_platform.runtime.workflow.registry import WorkflowRegistry


@dataclass(frozen=True)
class WorkflowRunResult:
    status: str  # "completed" | "failed" | "waiting_for_human"
    state: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class WorkflowRunner:
    def __init__(
        self,
        store: RuntimeStore,
        *,
        engine: WorkflowEngine | None = None,
        registry: WorkflowRegistry | None = None,
    ) -> None:
        self._store = store
        self._engine = engine or LangGraphWorkflowEngine()
        self._registry = registry
        self._runs = RunManager(store)
        self._states = StateManager(store)
        self._events = EventBus(store)
        self._checkpoints = CheckpointStore(store)

    def resolve(self, name: str, version: str | None = None) -> WorkflowDefinition:
        """Resolve a name@version reference through the registry (dispatch path)."""
        if self._registry is None:
            raise KeyError("no workflow registry configured on this runner")
        return self._registry.resolve(name, version)

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
            {
                "workflow": definition.name,
                "version": definition.version,
                "definition_hash": definition.content_hash,
                "input": input or {},
            },
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

    def resume(
        self,
        *,
        run_id: str,
        definition: WorkflowDefinition,
        response: Any = None,
    ) -> WorkflowRunResult:
        run = self._runs.get_run(run_id)
        if run.status not in {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.WAITING_FOR_HUMAN}:
            raise InvalidStateTransitionError(f"cannot resume run from {run.status.value}")
        state = self._load_state(run_id)
        self._ensure_same_workflow(state, definition)
        waiting = run.status is RunStatus.WAITING_FOR_HUMAN
        if waiting and response is None:
            raise InvalidStateTransitionError(
                f"run {run_id} waits for human input; a response is required to resume"
            )
        if run.status is not RunStatus.RUNNING:
            self._runs.restart_run(run_id)
        if waiting:
            self._publish(
                run_id,
                RuntimeEventType.HUMAN_RESPONSE_RECEIVED,
                {"node": state.get("waiting_node"), "response": response},
            )
            self._publish(run_id, RuntimeEventType.RUN_RESUMED, {"reason": "human_response"})
        stream = self._engine.resume(
            definition=definition,
            run_id=run_id,
            node_executor=self._node_executor(run_id),
            response=response if waiting else None,
        )
        return self._consume(run_id, stream, state)

    # --- internal -----------------------------------------------------------

    def _consume(self, run_id: str, stream: Iterator[Superstep], state: dict[str, Any]) -> WorkflowRunResult:
        try:
            for superstep in stream:
                if INTERRUPTED_KEY in superstep:
                    return self._pause(run_id, state, superstep[INTERRUPTED_KEY])
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
        self._publish(
            run_id,
            RuntimeEventType.RUN_COMPLETED,
            {"output": dict(state["values"]), "workflow": dict(state.get("definition", {}))},
        )
        self._engine.drop(run_id)
        return WorkflowRunResult(status="completed", state=dict(state["values"]))

    def _pause(self, run_id: str, state: dict[str, Any], interrupted: Any) -> WorkflowRunResult:
        """Suspend the run on human input (waiting_for_human + HITL events)."""
        info = interrupted if isinstance(interrupted, dict) else {}
        node_name = info.get("node")
        request = info.get("request")
        state["status"] = "waiting_for_human"
        state["waiting_node"] = node_name
        self._persist(run_id, state)
        self._runs.pause_run(run_id)
        self._publish(run_id, RuntimeEventType.RUN_PAUSED, {"node": node_name})
        self._publish(
            run_id,
            RuntimeEventType.APPROVAL_REQUIRED,
            {"node": node_name, "request": request},
        )
        return WorkflowRunResult(
            status="waiting_for_human", state=dict(state["values"]), error=None
        )

    def _initial_state(self, definition: WorkflowDefinition, input: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "definition": {
                "name": definition.name,
                "version": definition.version,
                "hash": definition.content_hash,
            },
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
        # Content binding (section 6): same version label with different
        # nodes/edges/handlers is a different workflow. Runs persisted
        # before hashing carry no "hash" and stay resumable.
        bound_hash = bound.get("hash")
        if bound_hash is not None and bound_hash != definition.content_hash:
            raise InvalidStateTransitionError(
                f"run is bound to workflow {definition.name}@{definition.version} "
                f"content {bound_hash}, got {definition.content_hash}"
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
