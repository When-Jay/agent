"""Human-in-the-loop tests: Human node pause, respond and resume.

Covers the workflow Human node (LangGraph interrupt behind the engine
boundary), the waiting_for_human run state, HITL events, and the
orchestrator/API respond flow.
"""

import pytest
from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.config import Settings
from agent_platform.errors import InvalidStateTransitionError
from agent_platform.runtime.core import (
    EventBus,
    RunManager,
    RunStatus,
    SessionManager,
)
from agent_platform.runtime.dispatch import RuntimeOrchestrator, create_runtime_store
from agent_platform.runtime.dispatch import tasks as dispatch_tasks
from agent_platform.runtime.workflow import (
    EdgeSpec,
    NodeSpec,
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowRunner,
)


def _approval_workflow() -> WorkflowDefinition:
    def build_request(state):
        return {"question": "approve transfer?", "amount": state.get("amount")}

    def done(state):
        return {"done": True}

    return WorkflowDefinition(
        name="approval",
        version="1",
        nodes=[
            NodeSpec(name="approve", handler=build_request, node_type="human"),
            NodeSpec(name="done", handler=done),
        ],
        edges=[EdgeSpec(source="approve", target="done")],
        entry="approve",
    )


def _runner(store) -> WorkflowRunner:
    registry = WorkflowRegistry()
    registry.register(_approval_workflow())
    return WorkflowRunner(store, registry=registry)


def _seed_workflow_run(store, *, status=RunStatus.QUEUED):
    runs = RunManager(store)
    apps = SessionManager(store)
    application = apps.create_application(
        name="approval-app", metadata={"workflow": {"name": "approval", "version": "1"}}
    )
    session = apps.create_session(application_id=application.id)
    return runs.create_run(
        application_id=application.id,
        session_id=session.id,
        runtime_type="workflow",
        input={"amount": 100},
        status=status,
    )


def _event_types(store, run_id) -> list[str]:
    return [event.event_type.value for event in EventBus(store).list_events(run_id)]


def test_workflow_pauses_on_human_node():
    store = create_runtime_store("sqlite:///:memory:")
    runner = _runner(store)
    run = _seed_workflow_run(store)

    result = runner.run(
        run_id=run.id, definition=runner.resolve("approval"), input={"amount": 100}
    )

    assert result.status == "waiting_for_human"
    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.WAITING_FOR_HUMAN

    types = _event_types(store, run.id)
    assert "RunStarted" in types
    assert types.count("NodeStarted") == 1
    assert "RunPaused" in types
    assert "ApprovalRequired" in types

    approval = [
        event
        for event in EventBus(store).list_events(run.id)
        if event.event_type.value == "ApprovalRequired"
    ][0]
    assert approval.payload["node"] == "approve"
    assert approval.payload["request"] == {"question": "approve transfer?", "amount": 100}

    # The waiting state is checkpointed for later resume.
    waiting_state = store.get_state(run.id).values["workflow"]
    assert waiting_state["status"] == "waiting_for_human"


def test_workflow_resumes_with_human_response():
    store = create_runtime_store("sqlite:///:memory:")
    runner = _runner(store)
    run = _seed_workflow_run(store)
    runner.run(run_id=run.id, definition=runner.resolve("approval"), input={"amount": 100})

    result = runner.resume(
        run_id=run.id, definition=runner.resolve("approval"), response={"approved": True}
    )

    assert result.status == "completed"
    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.COMPLETED
    assert saved.output["human_response"] == {"approved": True}
    assert saved.output["human_node"] == "approve"
    assert saved.output["done"] is True

    types = _event_types(store, run.id)
    # LangGraph replays the paused node on resume (approve: 2x NodeStarted)
    # and then executes the remaining "done" node: 3 NodeStarted total.
    assert types.count("NodeStarted") == 3
    assert "NodeCompleted" in types
    assert "HumanResponseReceived" in types
    assert "RunResumed" in types
    assert types[-1] == "RunCompleted"


def test_resume_without_response_is_rejected():
    store = create_runtime_store("sqlite:///:memory:")
    runner = _runner(store)
    run = _seed_workflow_run(store)
    runner.run(run_id=run.id, definition=runner.resolve("approval"))

    with pytest.raises(InvalidStateTransitionError, match="response is required"):
        runner.resume(run_id=run.id, definition=runner.resolve("approval"))

    assert RunManager(store).get_run(run.id).status is RunStatus.WAITING_FOR_HUMAN


def test_waiting_run_cancellable_and_skipped_by_orchestrator():
    store = create_runtime_store("sqlite:///:memory:")
    runner = _runner(store)
    run = _seed_workflow_run(store)
    orchestrator = RuntimeOrchestrator(store, workflow_runner=runner)

    orchestrator.execute(run.id)
    assert RunManager(store).get_run(run.id).status is RunStatus.WAITING_FOR_HUMAN
    # A duplicate dispatch (worker retry) must not re-execute a waiting run.
    orchestrator.execute(run.id)
    assert RunManager(store).get_run(run.id).status is RunStatus.WAITING_FOR_HUMAN

    RunManager(store).cancel_run(run.id)
    assert RunManager(store).get_run(run.id).status is RunStatus.CANCELLED


def test_orchestrator_resume_completes_waiting_run():
    store = create_runtime_store("sqlite:///:memory:")
    runner = _runner(store)
    run = _seed_workflow_run(store)
    orchestrator = RuntimeOrchestrator(store, workflow_runner=runner)

    orchestrator.execute(run.id)
    orchestrator.resume(run.id, response={"approved": False})

    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.COMPLETED
    assert saved.output["human_response"] == {"approved": False}


def test_api_respond_completes_run_eagerly(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'hitl.db'}"
    store = create_runtime_store(database_url)
    settings = Settings(database_url=database_url, celery_task_always_eager=True)
    runner = _runner(store)
    run = _seed_workflow_run(store)

    dispatch_tasks.set_orchestrator_builder(
        lambda: RuntimeOrchestrator(store, workflow_runner=runner)
    )
    try:
        client = TestClient(create_app(settings))
        paused = RuntimeOrchestrator(store, workflow_runner=runner)
        paused.execute(run.id)
        assert RunManager(store).get_run(run.id).status is RunStatus.WAITING_FOR_HUMAN

        response = client.post(f"/api/v1/runs/{run.id}/respond", json={"response": {"ok": 1}})
        body = response.json()

        rejected = client.post(f"/api/v1/runs/{run.id}/respond", json={"response": {"ok": 1}})
        not_found = client.post("/api/v1/runs/missing/respond", json={"response": None})
    finally:
        dispatch_tasks.set_orchestrator_builder(None)

    assert response.status_code == 200
    assert body["status"] == "completed"
    assert body["output"]["human_response"] == {"ok": 1}
    assert rejected.status_code == 409  # already completed
    assert not_found.status_code == 404
