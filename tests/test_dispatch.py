"""Runtime dispatch tests (runtime-dispatch-spec.md).

Covers: idempotent worker execution, runtime_type routing, the API run
endpoints (queued creation + eager worker execution) and the durable
SQLAlchemy store contract.
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.config import Settings
from agent_platform.runtime.core import (
    EventBus,
    InMemoryRuntimeStore,
    RunManager,
    RunStatus,
    RuntimeEventType,
    SessionManager,
)
from agent_platform.runtime.dispatch import (
    RuntimeOrchestrator,
    create_runtime_store,
)
from agent_platform.runtime.workflow import (
    EdgeSpec,
    NodeSpec,
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowRunner,
)


class _StubAgentAdapter:
    """Minimal agent adapter: start -> RUN_STARTED -> complete -> RUN_COMPLETED."""

    def __init__(self, store) -> None:
        self._runs = RunManager(store)
        self._events = EventBus(store)
        self.executed: list[str] = []

    async def run(self, run_id: str):
        self.executed.append(run_id)
        self._runs.start_run(run_id)
        self._events.publish(
            run_id=run_id, event_type=RuntimeEventType.RUN_STARTED, payload={}
        )
        self._runs.complete_run(run_id, output={"final": "ok"})
        self._events.publish(
            run_id=run_id, event_type=RuntimeEventType.RUN_COMPLETED, payload={}
        )


def _queued_run(store, *, runtime_type="agent", metadata=None, input=None):
    runs = RunManager(store)
    apps = SessionManager(store)
    application = apps.create_application(name="demo", metadata=metadata)
    session = apps.create_session(application_id=application.id)
    return runs.create_run(
        application_id=application.id,
        session_id=session.id,
        runtime_type=runtime_type,
        input=input,
        status=RunStatus.QUEUED,
    )


def _double_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="double",
        version="1",
        nodes=[
            NodeSpec(name="add", handler=lambda state: {"value": state["value"] * 2}),
            NodeSpec(name="mark", handler=lambda state: {"done": True}),
        ],
        edges=[EdgeSpec(source="add", target="mark")],
        entry="add",
    )


def _workflow_runner(store) -> WorkflowRunner:
    registry = WorkflowRegistry()
    registry.register(_double_workflow())
    return WorkflowRunner(store, registry=registry)


def _flaky_workflow() -> WorkflowDefinition:
    """Fails on the first node execution, succeeds on the second (resume)."""
    calls = {"flaky": 0}

    def flaky(state):
        calls["flaky"] += 1
        if calls["flaky"] == 1:
            raise ValueError("flaky boom")
        return {"flaky": "ok"}

    def after(state):
        return {"after": "done"}

    return WorkflowDefinition(
        name="flaky",
        version="1",
        nodes=[
            NodeSpec(name="flaky", handler=flaky),
            NodeSpec(name="after", handler=after),
        ],
        edges=[EdgeSpec(source="flaky", target="after")],
        entry="flaky",
    )


def _flaky_runner(store) -> WorkflowRunner:
    registry = WorkflowRegistry()
    registry.register(_flaky_workflow())
    return WorkflowRunner(store, registry=registry)


# --- orchestrator -----------------------------------------------------------------


def test_orchestrator_skips_terminal_runs():
    store = InMemoryRuntimeStore()
    runs = RunManager(store)
    run = _queued_run(store)
    runs.start_run(run.id)
    runs.complete_run(run.id, output={"done": True})

    orchestrator = RuntimeOrchestrator(store)  # no adapter configured
    orchestrator.execute(run.id)

    saved = runs.get_run(run.id)
    assert saved.status is RunStatus.COMPLETED
    assert saved.output == {"done": True}


def test_orchestrator_skips_cancelled_runs():
    store = InMemoryRuntimeStore()
    adapter = _StubAgentAdapter(store)
    run = _queued_run(store)
    RunManager(store).cancel_run(run.id)

    RuntimeOrchestrator(store, agent_adapter=adapter).execute(run.id)

    assert adapter.executed == []
    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.CANCELLED


# --- orchestrator resume -----------------------------------------------------------


def test_orchestrator_resume_continues_failed_workflow_run():
    store = InMemoryRuntimeStore()
    runner = _flaky_runner(store)
    run = _queued_run(
        store,
        runtime_type="workflow",
        metadata={"workflow": {"name": "flaky", "version": "1"}},
        input={"value": 1},
    )

    orchestrator = RuntimeOrchestrator(store, workflow_runner=runner)
    orchestrator.execute(run.id)
    assert RunManager(store).get_run(run.id).status is RunStatus.FAILED

    orchestrator.resume(run.id)

    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.COMPLETED
    assert saved.output["flaky"] == "ok"
    types = [event.event_type.value for event in EventBus(store).list_events(run.id)]
    assert "RunResumed" in types
    assert types[-1] == "RunCompleted"


def test_orchestrator_resume_skips_non_failed_and_agent_runs():
    store = InMemoryRuntimeStore()
    runs = RunManager(store)
    run = _queued_run(store)

    orchestrator = RuntimeOrchestrator(store)  # no runtimes configured
    orchestrator.resume(run.id)
    assert runs.get_run(run.id).status is RunStatus.QUEUED

    runs.fail_run(run.id, error="boom")
    orchestrator.resume(run.id)  # agent run: resume unsupported in V1 -> skip
    saved = runs.get_run(run.id)
    assert saved.status is RunStatus.FAILED
    assert saved.error == "boom"


def test_orchestrator_resume_without_engine_state_keeps_run_failed():
    store = InMemoryRuntimeStore()
    run = _queued_run(
        store,
        runtime_type="workflow",
        metadata={"workflow": {"name": "double", "version": "1"}},
    )
    runs = RunManager(store)
    runs.fail_run(run.id, error="simulated worker crash")

    # A fresh runner has no in-process engine state for this run; resume
    # must not crash and must preserve the original failure.
    RuntimeOrchestrator(store, workflow_runner=_workflow_runner(store)).resume(run.id)

    saved = runs.get_run(run.id)
    assert saved.status is RunStatus.FAILED
    assert saved.error == "simulated worker crash"


def test_orchestrator_fails_unknown_runtime_type():
    store = InMemoryRuntimeStore()
    run = _queued_run(store, runtime_type="mystery")

    RuntimeOrchestrator(store).execute(run.id)

    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.FAILED
    assert "unknown runtime_type" in saved.error
    failed = [
        event
        for event in EventBus(store).list_events(run.id)
        if event.event_type is RuntimeEventType.RUN_FAILED
    ]
    assert failed and failed[-1].payload.get("reason") == "dispatch"


def test_orchestrator_executes_agent_runs_through_adapter():
    store = InMemoryRuntimeStore()
    adapter = _StubAgentAdapter(store)
    run = _queued_run(store)

    RuntimeOrchestrator(store, agent_adapter=adapter).execute(run.id)

    assert adapter.executed == [run.id]
    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.COMPLETED
    assert saved.output == {"final": "ok"}


def test_orchestrator_executes_workflow_runs_through_runner():
    store = InMemoryRuntimeStore()
    run = _queued_run(
        store,
        runtime_type="workflow",
        metadata={"workflow": {"name": "double", "version": "1"}},
        input={"value": 21},
    )

    RuntimeOrchestrator(store, workflow_runner=_workflow_runner(store)).execute(run.id)

    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.COMPLETED
    assert saved.output == {"value": 42, "done": True}
    types = [event.event_type.value for event in EventBus(store).list_events(run.id)]
    assert types[0] == "RunStarted"
    assert types[-1] == "RunCompleted"
    assert "NodeStarted" in types and "NodeCompleted" in types
    assert "CheckpointCreated" in types


def test_orchestrator_fails_workflow_run_when_definition_not_registered():
    store = InMemoryRuntimeStore()
    run = _queued_run(
        store,
        runtime_type="workflow",
        metadata={"workflow": {"name": "ghost", "version": "9"}},
    )

    RuntimeOrchestrator(store, workflow_runner=_workflow_runner(store)).execute(run.id)

    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.FAILED
    assert "workflow not registered: ghost@9" in saved.error


def test_orchestrator_fails_workflow_run_without_workflow_reference():
    store = InMemoryRuntimeStore()
    run = _queued_run(store, runtime_type="workflow")

    RuntimeOrchestrator(store, workflow_runner=_workflow_runner(store)).execute(run.id)

    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.FAILED
    assert "workflow not registered" in saved.error


def test_orchestrator_fails_workflow_run_without_runtime_configured():
    store = InMemoryRuntimeStore()
    run = _queued_run(store, runtime_type="workflow")

    RuntimeOrchestrator(store).execute(run.id)

    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.FAILED
    assert "workflow runtime is not configured" in saved.error


# --- API + eager worker ------------------------------------------------------------


def test_api_creates_queued_run_and_worker_executes_it():
    from agent_platform.runtime.dispatch import tasks

    # The same in-memory singleton store backs both the API and the worker.
    store = create_runtime_store("sqlite:///:memory:")
    tasks.set_orchestrator_builder(
        lambda: RuntimeOrchestrator(store, agent_adapter=_StubAgentAdapter(store))
    )
    try:
        client = TestClient(create_app(Settings(celery_task_always_eager=True)))
        application = SessionManager(store).create_application(name="demo")
        response = client.post(
            "/api/v1/runs",
            json={
                "application_id": application.id,
                "runtime_type": "agent",
                "input": {"message": "hi"},
            },
        )
        run_id = response.json()["id"]
        events_response = client.get(f"/api/v1/runs/{run_id}/events")
    finally:
        tasks.set_orchestrator_builder(None)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["output"] == {"final": "ok"}
    assert body["runtime_type"] == "agent"
    event_types = [event["event_type"] for event in events_response.json()["events"]]
    assert event_types == ["RunStarted", "RunCompleted"]


def test_api_workflow_run_executes_through_eager_worker():
    from agent_platform.runtime.dispatch import tasks

    store = create_runtime_store("sqlite:///:memory:")
    tasks.set_orchestrator_builder(
        lambda: RuntimeOrchestrator(store, workflow_runner=_workflow_runner(store))
    )
    try:
        client = TestClient(create_app(Settings(celery_task_always_eager=True)))
        application = SessionManager(store).create_application(
            name="flow", metadata={"workflow": {"name": "double", "version": "1"}}
        )
        response = client.post(
            "/api/v1/runs",
            json={
                "application_id": application.id,
                "runtime_type": "workflow",
                "input": {"value": 2},
            },
        )
    finally:
        tasks.set_orchestrator_builder(None)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["output"] == {"value": 4, "done": True}
    assert body["runtime_type"] == "workflow"


def test_api_rejects_unknown_application_and_runtime_type():
    store = InMemoryRuntimeStore()
    client = TestClient(create_app(Settings(celery_task_always_eager=True)))

    missing_application = client.post(
        "/api/v1/runs", json={"application_id": str(uuid4())}
    )
    assert missing_application.status_code == 404

    invalid_type = client.post(
        "/api/v1/runs",
        json={
            "application_id": SessionManager(store).create_application(name="x").id,
            "runtime_type": "sql",
        },
    )
    assert invalid_type.status_code == 422

    missing_run = client.get(f"/api/v1/runs/{uuid4()}")
    assert missing_run.status_code == 404


# --- durable store -----------------------------------------------------------------


def test_sqlalchemy_store_persists_runs_across_instances(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'runtime.db'}"

    first = create_runtime_store(database_url)
    runs = RunManager(first)
    apps = SessionManager(first)
    application = apps.create_application(name="durable")
    session = apps.create_session(application_id=application.id)
    run = runs.create_run(
        application_id=application.id,
        session_id=session.id,
        runtime_type="agent",
        input={"message": "hi"},
        status=RunStatus.QUEUED,
    )
    runs.start_run(run.id)
    EventBus(first).publish(
        run_id=run.id, event_type=RuntimeEventType.RUN_STARTED, payload={}
    )

    second = create_runtime_store(database_url)
    reloaded = RunManager(second).get_run(run.id)
    assert reloaded.status is RunStatus.RUNNING
    assert reloaded.input == {"message": "hi"}
    events = EventBus(second).list_events(run.id)
    assert [event.event_type for event in events] == [RuntimeEventType.RUN_STARTED]


def test_create_runtime_store_shares_in_memory_instance():
    first = create_runtime_store("sqlite:///:memory:")
    second = create_runtime_store("sqlite:///:memory:")
    assert first is second
    assert isinstance(first, InMemoryRuntimeStore)
