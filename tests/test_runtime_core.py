import pytest

from agent_platform.errors import NotFoundError
from agent_platform.runtime.core import (
    ArtifactStore,
    CheckpointStore,
    EventBus,
    InMemoryRuntimeStore,
    RunManager,
    RunStatus,
    RuntimeEventType,
    RuntimeStore,
    SessionManager,
    StateManager,
)


def test_run_manager_moves_run_through_lifecycle():
    store = InMemoryRuntimeStore()
    manager = RunManager(store)

    run = manager.create_run(application_id="app-1", session_id="session-1", runtime_type="agent")
    started = manager.start_run(run.id)
    completed = manager.complete_run(run.id, output={"answer": "done"})

    assert run.status is RunStatus.CREATED
    assert started.status is RunStatus.RUNNING
    assert completed.status is RunStatus.COMPLETED
    assert completed.output == {"answer": "done"}


def test_run_manager_rejects_invalid_lifecycle_transition():
    store = InMemoryRuntimeStore()
    manager = RunManager(store)
    run = manager.create_run(application_id="app-1", session_id="session-1", runtime_type="agent")

    with pytest.raises(ValueError, match="cannot complete"):
        manager.complete_run(run.id)


def test_event_bus_publishes_and_replays_events():
    store = InMemoryRuntimeStore()
    event_bus = EventBus(store)

    event = event_bus.publish(run_id="run-1", event_type=RuntimeEventType.RUN_STARTED, payload={"step": 1})

    assert event_bus.list_events("run-1") == [event]
    assert event.event_type is RuntimeEventType.RUN_STARTED
    assert event.payload == {"step": 1}


def test_checkpoint_store_creates_and_loads_resume_state():
    store = InMemoryRuntimeStore()
    checkpoints = CheckpointStore(store)

    checkpoint = checkpoints.create(run_id="run-1", state={"turn": 2})

    assert checkpoints.get(checkpoint.id) == checkpoint
    assert checkpoints.latest_for_run("run-1") == checkpoint
    assert checkpoint.state == {"turn": 2}


def test_artifact_store_keeps_metadata_separate_from_content_reference():
    store = InMemoryRuntimeStore()
    artifacts = ArtifactStore(store)

    artifact = artifacts.create(
        run_id="run-1",
        name="result.json",
        uri="memory://result.json",
        metadata={"content_type": "application/json"},
    )

    assert artifacts.get(artifact.id) == artifact
    assert artifact.metadata == {"content_type": "application/json"}


def test_session_manager_creates_and_loads_application_and_session():
    store = InMemoryRuntimeStore()
    sessions = SessionManager(store)

    application = sessions.create_application(name="assistant", metadata={"team": "ai"})
    session = sessions.create_session(application_id=application.id, metadata={"channel": "web"})

    assert sessions.get_application(application.id) == application
    assert sessions.get_session(session.id) == session
    assert session.application_id == application.id


def test_session_manager_rejects_session_for_unknown_application():
    sessions = SessionManager(InMemoryRuntimeStore())

    with pytest.raises(NotFoundError, match="application not found"):
        sessions.create_session(application_id="missing")


def test_session_manager_raises_for_missing_resources():
    sessions = SessionManager(InMemoryRuntimeStore())

    with pytest.raises(NotFoundError):
        sessions.get_application("missing")
    with pytest.raises(NotFoundError):
        sessions.get_session("missing")


def test_state_manager_starts_empty_and_merges_updates():
    store = InMemoryRuntimeStore()
    run = RunManager(store).create_run(
        application_id="app-1", session_id="session-1", runtime_type="agent"
    )
    states = StateManager(store)

    assert states.get_state(run.id).values == {}

    states.update_state(run.id, values={"step": 1})
    state = states.update_state(run.id, values={"answer": 42})

    assert state.values == {"step": 1, "answer": 42}
    assert states.get_state(run.id).values == {"step": 1, "answer": 42}


def test_state_manager_requires_existing_run():
    states = StateManager(InMemoryRuntimeStore())

    with pytest.raises(NotFoundError, match="run not found"):
        states.get_state("missing")
    with pytest.raises(NotFoundError, match="run not found"):
        states.update_state("missing", values={"step": 1})


def test_event_bus_notifies_subscribers_and_supports_unsubscribe():
    store = InMemoryRuntimeStore()
    event_bus = EventBus(store)
    received: list = []

    unsubscribe = event_bus.subscribe(received.append)
    event = event_bus.publish(run_id="run-1", event_type=RuntimeEventType.RUN_STARTED)

    assert received == [event]
    assert event_bus.list_events("run-1") == [event]

    unsubscribe()
    event_bus.publish(run_id="run-1", event_type=RuntimeEventType.RUN_COMPLETED)

    assert len(received) == 1


def test_event_bus_isolates_failing_subscribers():
    event_bus = EventBus(InMemoryRuntimeStore())
    received: list = []

    def broken(event) -> None:
        raise RuntimeError("subscriber boom")

    event_bus.subscribe(broken)
    event_bus.subscribe(received.append)

    event = event_bus.publish(run_id="run-1", event_type=RuntimeEventType.RUN_STARTED)

    assert received == [event]


def test_in_memory_store_satisfies_runtime_store_contract():
    assert isinstance(InMemoryRuntimeStore(), RuntimeStore)
