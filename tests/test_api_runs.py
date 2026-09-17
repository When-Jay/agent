"""API run-control and resource-listing endpoint tests.

Covers POST /api/v1/runs/{id}/cancel and the query endpoints for
applications, sessions and runs. Each test uses an isolated SQLite
database so the process-wide in-memory singleton is never touched.
"""

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.config import Settings
from agent_platform.runtime.core import (
    EventBus,
    RunManager,
    RunStatus,
    RuntimeEventType,
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


class _StubAgentAdapter:
    """Minimal agent adapter: start -> RUN_STARTED -> complete -> RUN_COMPLETED."""

    def __init__(self, store) -> None:
        self._runs = RunManager(store)
        self._events = EventBus(store)
        self.executed: list[str] = []

    async def run(self, run_id: str):
        self.executed.append(run_id)
        self._runs.start_run(run_id)
        self._events.publish(run_id=run_id, event_type=RuntimeEventType.RUN_STARTED, payload={})
        self._runs.complete_run(run_id, output={"final": "ok"})
        self._events.publish(
            run_id=run_id, event_type=RuntimeEventType.RUN_COMPLETED, payload={}
        )

    async def resume(self, run_id: str, response: Any = None):
        self.executed.append(f"resume:{run_id}")
        self._runs.restart_run(run_id)
        self._events.publish(run_id=run_id, event_type=RuntimeEventType.RUN_RESUMED, payload={})
        self._runs.complete_run(run_id, output={"final": "resumed"})
        self._events.publish(
            run_id=run_id, event_type=RuntimeEventType.RUN_COMPLETED, payload={}
        )


def _flaky_runner(store) -> WorkflowRunner:
    """Workflow whose first node fails on the first execution, succeeds after."""
    calls = {"flaky": 0}

    def flaky(state):
        calls["flaky"] += 1
        if calls["flaky"] == 1:
            raise ValueError("flaky boom")
        return {"flaky": "ok"}

    def after(state):
        return {"after": "done"}

    definition = WorkflowDefinition(
        name="flaky",
        version="1",
        nodes=[
            NodeSpec(name="flaky", handler=flaky),
            NodeSpec(name="after", handler=after),
        ],
        edges=[EdgeSpec(source="flaky", target="after")],
        entry="flaky",
    )
    registry = WorkflowRegistry()
    registry.register(definition)
    return WorkflowRunner(store, registry=registry)


def _client(tmp_path, **kwargs):
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    settings = Settings(database_url=database_url, celery_task_always_eager=True)
    return TestClient(create_app(settings, **kwargs)), create_runtime_store(database_url)


def _seed_run(store, *, runtime_type="agent", status=RunStatus.QUEUED):
    runs = RunManager(store)
    apps = SessionManager(store)
    application = apps.create_application(name="demo")
    session = apps.create_session(application_id=application.id)
    run = runs.create_run(
        application_id=application.id,
        session_id=session.id,
        runtime_type=runtime_type,
        status=status,
    )
    return application, session, run


# --- run cancellation ----------------------------------------------------------------


def test_cancel_queued_run_returns_cancelled_payload_and_event(tmp_path):
    client, store = _client(tmp_path)
    _, _, run = _seed_run(store)

    response = client.post(f"/api/v1/runs/{run.id}/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["completed_at"] is not None

    events = client.get(f"/api/v1/runs/{run.id}/events").json()["events"]
    assert [event["event_type"] for event in events] == ["RunCancelled"]
    assert events[0]["payload"] == {"reason": "api"}


def test_cancel_rejects_running_run(tmp_path):
    client, store = _client(tmp_path)
    _, _, run = _seed_run(store)
    RunManager(store).start_run(run.id)

    response = client.post(f"/api/v1/runs/{run.id}/cancel")

    assert response.status_code == 409
    assert "running" in response.json()["detail"]
    assert RunManager(store).get_run(run.id).status is RunStatus.RUNNING


def test_cancel_rejects_terminal_runs(tmp_path):
    client, store = _client(tmp_path)
    runs = RunManager(store)
    _, _, completed = _seed_run(store)
    runs.start_run(completed.id)
    runs.complete_run(completed.id, output={"done": True})
    _, _, failed = _seed_run(store)
    runs.fail_run(failed.id, error="boom")

    assert client.post(f"/api/v1/runs/{completed.id}/cancel").status_code == 409
    assert client.post(f"/api/v1/runs/{failed.id}/cancel").status_code == 409


def test_cancel_unknown_run_returns_404(tmp_path):
    client, _ = _client(tmp_path)

    response = client.post(f"/api/v1/runs/{uuid4()}/cancel")

    assert response.status_code == 404


# --- query endpoints -------------------------------------------------------------------


def test_list_and_get_applications(tmp_path):
    client, store = _client(tmp_path)
    application_a, _, _ = _seed_run(store)
    application_b, _, _ = _seed_run(store)

    listing = client.get("/api/v1/applications")
    assert listing.status_code == 200
    assert [app["id"] for app in listing.json()["applications"]] == [
        application_a.id,
        application_b.id,
    ]

    single = client.get(f"/api/v1/applications/{application_a.id}")
    assert single.status_code == 200
    assert single.json() == {
        "id": application_a.id,
        "name": "demo",
        "metadata": {},
    }

    assert client.get(f"/api/v1/applications/{uuid4()}").status_code == 404


def test_list_application_sessions(tmp_path):
    client, store = _client(tmp_path)
    runs = RunManager(store)
    apps = SessionManager(store)
    application_a = apps.create_application(name="a")
    session_a1 = apps.create_session(application_id=application_a.id)
    session_a2 = apps.create_session(application_id=application_a.id)
    application_b = apps.create_application(name="b")
    session_b1 = apps.create_session(application_id=application_b.id)
    runs.create_run(application_id=application_a.id, session_id=session_a1.id, runtime_type="agent")

    sessions_a = client.get(f"/api/v1/applications/{application_a.id}/sessions")
    assert sessions_a.status_code == 200
    assert [s["id"] for s in sessions_a.json()["sessions"]] == [session_a1.id, session_a2.id]

    sessions_b = client.get(f"/api/v1/applications/{application_b.id}/sessions")
    assert [s["id"] for s in sessions_b.json()["sessions"]] == [session_b1.id]

    assert client.get(f"/api/v1/applications/{uuid4()}/sessions").status_code == 404


def test_list_runs_with_optional_filters(tmp_path):
    client, store = _client(tmp_path)
    runs = RunManager(store)
    apps = SessionManager(store)
    application_a = apps.create_application(name="a")
    application_b = apps.create_application(name="b")
    session_a1 = apps.create_session(application_id=application_a.id)
    session_a2 = apps.create_session(application_id=application_a.id)
    for session_id in (session_a1.id, session_a1.id, session_a2.id):
        runs.create_run(application_id=application_a.id, session_id=session_id, runtime_type="agent")
    runs.create_run(application_id=application_b.id, session_id="session-b", runtime_type="workflow")

    all_runs = client.get("/api/v1/runs")
    assert all_runs.status_code == 200
    assert len(all_runs.json()["runs"]) == 4

    by_application = client.get("/api/v1/runs", params={"application_id": application_a.id})
    assert len(by_application.json()["runs"]) == 3

    by_session = client.get(
        "/api/v1/runs",
        params={"application_id": application_a.id, "session_id": session_a1.id},
    )
    assert [run["session_id"] for run in by_session.json()["runs"]] == [session_a1.id, session_a1.id]

    assert client.get("/api/v1/runs", params={"application_id": uuid4()}).status_code == 404
    assert client.get("/api/v1/runs", params={"session_id": uuid4()}).status_code == 404


# --- run retry / resume ---------------------------------------------------------------


def test_retry_failed_run_requeues_and_executes_eagerly(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    store = create_runtime_store(database_url)
    settings = Settings(database_url=database_url, celery_task_always_eager=True)

    runs = RunManager(store)
    apps = SessionManager(store)
    application = apps.create_application(name="demo")
    session = apps.create_session(application_id=application.id)
    run = runs.create_run(
        application_id=application.id,
        session_id=session.id,
        runtime_type="agent",
        status=RunStatus.QUEUED,
    )
    runs.start_run(run.id)
    runs.fail_run(run.id, error="boom")

    dispatch_tasks.set_orchestrator_builder(
        lambda: RuntimeOrchestrator(store, agent_adapter=_StubAgentAdapter(store))
    )
    try:
        client = TestClient(create_app(settings))
        response = client.post(f"/api/v1/runs/{run.id}/retry")
        event_types = [
            event["event_type"]
            for event in client.get(f"/api/v1/runs/{run.id}/events").json()["events"]
        ]
    finally:
        dispatch_tasks.set_orchestrator_builder(None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["output"] == {"final": "ok"}
    # The seeded failure produced no events (manual state transition); the
    # retried attempt runs the full stub lifecycle again.
    assert event_types == ["RunStarted", "RunCompleted"]


def test_retry_rejects_invalid_statuses(tmp_path):
    client, store = _client(tmp_path)
    runs = RunManager(store)
    _, _, queued = _seed_run(store)
    _, _, running = _seed_run(store)
    runs.start_run(running.id)
    _, _, completed = _seed_run(store)
    runs.start_run(completed.id)
    runs.complete_run(completed.id)

    assert client.post(f"/api/v1/runs/{queued.id}/retry").status_code == 409
    assert client.post(f"/api/v1/runs/{running.id}/retry").status_code == 409
    assert client.post(f"/api/v1/runs/{completed.id}/retry").status_code == 409
    assert client.post(f"/api/v1/runs/{uuid4()}/retry").status_code == 404


def test_resume_failed_workflow_run_executes_eagerly(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    store = create_runtime_store(database_url)
    settings = Settings(database_url=database_url, celery_task_always_eager=True)

    runs = RunManager(store)
    apps = SessionManager(store)
    application = apps.create_application(
        name="flow", metadata={"workflow": {"name": "flaky", "version": "1"}}
    )
    session = apps.create_session(application_id=application.id)
    run = runs.create_run(
        application_id=application.id,
        session_id=session.id,
        runtime_type="workflow",
        input={"value": 1},
        status=RunStatus.QUEUED,
    )

    # First attempt fails inside the flaky node; the same runner instance
    # keeps the in-process engine state needed for the resume path.
    runner = _flaky_runner(store)
    RuntimeOrchestrator(store, workflow_runner=runner).execute(run.id)
    assert runs.get_run(run.id).status is RunStatus.FAILED

    dispatch_tasks.set_orchestrator_builder(
        lambda: RuntimeOrchestrator(store, workflow_runner=runner)
    )
    try:
        client = TestClient(create_app(settings))
        response = client.post(f"/api/v1/runs/{run.id}/resume")
        event_types = [
            event["event_type"]
            for event in client.get(f"/api/v1/runs/{run.id}/events").json()["events"]
        ]
    finally:
        dispatch_tasks.set_orchestrator_builder(None)

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert "RunResumed" in event_types
    assert event_types[-1] == "RunCompleted"


def test_resume_rejects_invalid_requests(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    store = create_runtime_store(database_url)
    settings = Settings(database_url=database_url, celery_task_always_eager=True)
    runs = RunManager(store)
    # failed agent run: resume now routes to the agent adapter (durable
    # checkpoints); the eager worker completes it through the stub.
    _, _, agent_run = _seed_run(store)
    runs.start_run(agent_run.id)
    runs.fail_run(agent_run.id, error="boom")
    # queued workflow run: only failed runs are resumable
    workflow_app = SessionManager(store).create_application(name="flow")
    workflow_session = SessionManager(store).create_session(application_id=workflow_app.id)
    workflow_run = runs.create_run(
        application_id=workflow_app.id,
        session_id=workflow_session.id,
        runtime_type="workflow",
        status=RunStatus.QUEUED,
    )

    dispatch_tasks.set_orchestrator_builder(
        lambda: RuntimeOrchestrator(store, agent_adapter=_StubAgentAdapter(store))
    )
    try:
        client = TestClient(create_app(settings))
        agent_resume = client.post(f"/api/v1/runs/{agent_run.id}/resume")
    finally:
        dispatch_tasks.set_orchestrator_builder(None)

    assert agent_resume.status_code == 200
    assert agent_resume.json()["status"] == "completed"
    assert agent_resume.json()["output"] == {"final": "resumed"}

    assert client.post(f"/api/v1/runs/{workflow_run.id}/resume").status_code == 409
    assert client.post(f"/api/v1/runs/{uuid4()}/resume").status_code == 404


# --- CRUD: applications, sessions, workflow binding, tools, artifacts --------


def test_create_application_and_session(tmp_path):
    client, store = _client(tmp_path)

    created = client.post(
        "/api/v1/applications",
        json={"name": "support-bot", "metadata": {"team": "platform"}},
    )
    session = client.post(
        f"/api/v1/applications/{created.json()['id']}/sessions", json={"metadata": {"lang": "zh"}}
    )

    assert created.status_code == 201
    assert created.json()["name"] == "support-bot"
    assert created.json()["metadata"] == {"team": "platform"}
    assert session.status_code == 201
    assert session.json()["application_id"] == created.json()["id"]
    assert SessionManager(store).get_session(session.json()["id"]) is not None

    assert client.post("/api/v1/applications", json={"name": "  "}).status_code == 422
    assert (
        client.post(f"/api/v1/applications/{uuid4()}/sessions", json={}).status_code == 404
    )


def test_workflow_binding_get_put_and_validation(tmp_path):
    client, _ = _client(tmp_path)

    created = client.post("/api/v1/applications", json={"name": "flow"})
    app_id = created.json()["id"]

    missing = client.get(f"/api/v1/applications/{app_id}/workflow")
    assert missing.status_code == 404

    bound = client.put(
        f"/api/v1/applications/{app_id}/workflow", json={"name": "approval", "version": "1"}
    )
    assert bound.status_code == 200
    assert bound.json() == {"workflow": {"name": "approval", "version": "1"}}

    fetched = client.get(f"/api/v1/applications/{app_id}/workflow")
    assert fetched.status_code == 200
    assert fetched.json()["workflow"] == {"name": "approval", "version": "1"}

    # Version is optional.
    client.post("/api/v1/applications", json={"name": "multi"})
    other = client.get("/api/v1/applications").json()["applications"][-1]["id"]
    merged = client.put(f"/api/v1/applications/{other}/workflow", json={"name": "router"})
    assert merged.json() == {"workflow": {"name": "router", "version": None}}

    assert (
        client.put(f"/api/v1/applications/{app_id}/workflow", json={"name": " "}).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/v1/applications/{app_id}/workflow", json={"name": "x", "version": " "}
        ).status_code
        == 422
    )
    assert (
        client.put(f"/api/v1/applications/{uuid4()}/workflow", json={"name": "x"}).status_code
        == 404
    )


def test_list_tools_with_and_without_capability(tmp_path):
    from agent_platform.runtime.capabilities.tool import ToolSpec
    from agent_platform.runtime.capabilities.tool_capability import InMemoryToolCapability

    client, _ = _client(tmp_path)
    assert client.get("/api/v1/tools").json() == {"tools": []}

    capability = InMemoryToolCapability()
    capability.register(
        ToolSpec(
            name="search",
            description="Search the web",
            parameters={"type": "object", "properties": {}},
        ),
        lambda arguments: "ok",
    )
    with_capability, _ = _client(tmp_path, tool_capability=capability)
    body = with_capability.get("/api/v1/tools").json()
    assert body["tools"] == [
        {"name": "search", "description": "Search the web", "parameters": {"type": "object", "properties": {}}}
    ]


def test_register_run_artifact_reference(tmp_path):
    client, store = _client(tmp_path)
    _, _, run = _seed_run(store)

    registered = client.post(
        f"/api/v1/runs/{run.id}/artifacts",
        json={"name": "report", "uri": "s3://bucket/report.pdf", "metadata": {"pages": 3}},
    )
    assert registered.status_code == 201
    artifact_id = registered.json()["id"]
    assert registered.json()["run_id"] == run.id
    assert registered.json()["uri"] == "s3://bucket/report.pdf"

    fetched = client.get(f"/api/v1/artifacts/{artifact_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "report"

    listed = client.get(f"/api/v1/runs/{run.id}/artifacts").json()["artifacts"]
    assert [a["id"] for a in listed] == [artifact_id]

    assert client.post(f"/api/v1/runs/{uuid4()}/artifacts", json={"name": "x", "uri": "u"}).status_code == 404
    assert client.post(f"/api/v1/runs/{run.id}/artifacts", json={"name": " ", "uri": "u"}).status_code == 422
    assert client.post(f"/api/v1/runs/{run.id}/artifacts", json={"name": "x", "uri": ""}).status_code == 422
