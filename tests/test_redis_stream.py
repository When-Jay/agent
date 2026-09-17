"""Redis Stream event fanout and SSE streaming tests (dispatch-spec section 6).

Real-Redis coverage is gated on a reachable server, mirroring the
Docker-gated sandbox tests; everything else runs in-process.
"""

import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.config import Settings
from agent_platform.runtime.core import (
    RuntimeEvent,
    RuntimeEventType,
    SessionManager,
    event_from_json,
    event_to_json,
)
from agent_platform.runtime.dispatch import RuntimeOrchestrator, create_runtime_store
from agent_platform.runtime.dispatch import tasks as dispatch_tasks
from agent_platform.runtime.dispatch.redis_stream import (
    InMemoryStreamBackend,
    RedisEventPublisher,
    RedisStreamBackend,
    is_terminal_event,
)
from test_api_runs import _StubAgentAdapter


def _event(run_id: str = "run-1", event_type: RuntimeEventType = RuntimeEventType.RUN_STARTED):
    return RuntimeEvent(id=str(uuid4()), run_id=run_id, event_type=event_type, payload={})


# --- event serialization -------------------------------------------------------------


def test_event_json_round_trip():
    event = RuntimeEvent(
        id=str(uuid4()),
        run_id="run-1",
        event_type=RuntimeEventType.RUN_COMPLETED,
        payload={"output": {"final": "ok"}},
    )

    restored = event_from_json(event_to_json(event))

    assert restored.id == event.id
    assert restored.run_id == event.run_id
    assert restored.event_type is event.event_type
    assert restored.payload == event.payload
    assert restored.created_at == event.created_at


def test_is_terminal_event_flags_lifecycle_events():
    assert is_terminal_event(event_to_json(_event(event_type=RuntimeEventType.RUN_COMPLETED)))
    assert is_terminal_event(event_to_json(_event(event_type=RuntimeEventType.RUN_FAILED)))
    assert is_terminal_event(event_to_json(_event(event_type=RuntimeEventType.RUN_CANCELLED)))
    assert not is_terminal_event(event_to_json(_event(event_type=RuntimeEventType.RUN_STARTED)))
    assert not is_terminal_event("not json")


# --- stream backends -------------------------------------------------------------------


def test_in_memory_backend_read_blocks_until_add():
    backend = InMemoryStreamBackend()

    def produce():
        time.sleep(0.1)
        backend.add(event_to_json(_event()))

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    entries = backend.read("0-0", block_ms=2000, count=10)
    producer.join()

    assert len(entries) == 1
    assert event_from_json(entries[0][1]).event_type is RuntimeEventType.RUN_STARTED
    # Reading past the returned entry times out instead of blocking forever.
    assert backend.read(entries[0][0], block_ms=100, count=10) == []


def test_publisher_failure_is_isolated():
    class _BrokenBackend:
        def add(self, data: str) -> str:
            raise RuntimeError("stream down")

    publisher = RedisEventPublisher(_BrokenBackend())  # type: ignore[arg-type]

    publisher(_event())  # must not raise


def test_redis_stream_backend_round_trip():
    pytest.importorskip("redis")
    backend = RedisStreamBackend("redis://localhost:6379/0")
    try:
        backend.read("0-0", block_ms=100, count=1)
    except Exception:
        pytest.skip("Redis server not available")

    entry_id = backend.add(event_to_json(_event(run_id="redis-run")))

    entries = backend.read("0-0", block_ms=1000, count=100)
    assert any(eid == entry_id and event_from_json(data).run_id == "redis-run" for eid, data in entries)


# --- SSE endpoint ------------------------------------------------------------------------


def _app_settings(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'stream.db'}"
    return Settings(database_url=database_url, celery_task_always_eager=True), database_url


def test_sse_stream_replays_terminal_run(tmp_path):
    settings, database_url = _app_settings(tmp_path)
    backend = InMemoryStreamBackend()
    store = create_runtime_store(database_url)

    dispatch_tasks.set_orchestrator_builder(
        lambda: RuntimeOrchestrator(store, agent_adapter=_StubAgentAdapter(store))
    )
    try:
        client = TestClient(create_app(settings, stream_backend=backend))
        application = SessionManager(store).create_application(name="stream-demo")
        created = client.post(
            "/api/v1/runs", json={"application_id": application.id, "runtime_type": "agent"}
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]
        with client.stream("GET", f"/api/v1/runs/{run_id}/events/stream") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = list(response.iter_lines())
    finally:
        dispatch_tasks.set_orchestrator_builder(None)

    event_lines = [line for line in lines if line.startswith("event: ")]
    assert event_lines == ["event: RunStarted", "event: RunCompleted"]
    data_lines = [line for line in lines if line.startswith("data: ")]
    assert len(data_lines) == 2


def test_sse_stream_follows_live_events_and_filters_other_runs(tmp_path):
    settings, database_url = _app_settings(tmp_path)
    backend = InMemoryStreamBackend()
    client = TestClient(create_app(settings, stream_backend=backend))

    runs = _seed_run_via_api(database_url)
    target_event = _event(run_id=runs["run_id"])

    def produce():
        time.sleep(0.2)
        # Another run's terminal event must be filtered out, a duplicated
        # event id must be de-duplicated, then the terminal event ends the stream.
        backend.add(event_to_json(_event(run_id="other-run", event_type=RuntimeEventType.RUN_COMPLETED)))
        backend.add(event_to_json(target_event))
        backend.add(event_to_json(target_event))
        backend.add(event_to_json(_event(run_id=runs["run_id"], event_type=RuntimeEventType.RUN_COMPLETED)))

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    with client.stream("GET", f"/api/v1/runs/{runs['run_id']}/events/stream") as response:
        lines = list(response.iter_lines())
    producer.join()

    assert lines.count("event: RunStarted") == 1  # duplicate id dropped
    assert lines.count("event: RunCompleted") == 1  # other-run terminal filtered
    started = lines.index("event: RunStarted")
    completed = lines.index("event: RunCompleted")
    assert started < completed


def test_sse_stream_unknown_run_returns_404(tmp_path):
    settings, _ = _app_settings(tmp_path)
    client = TestClient(create_app(settings))

    assert client.get(f"/api/v1/runs/{uuid4()}/events/stream").status_code == 404


# --- seeding helpers ----------------------------------------------------------------------


def _seed_run_via_api(database_url: str) -> dict:
    """Create a queued run in the app's store (no dispatch, no execution)."""
    from agent_platform.runtime.core import RunManager, RunStatus

    store = create_runtime_store(database_url)
    application = SessionManager(store).create_application(name="live-demo")
    run = RunManager(store).create_run(
        application_id=application.id,
        session_id=SessionManager(store).create_session(application_id=application.id).id,
        runtime_type="agent",
        status=RunStatus.QUEUED,
    )
    return {"run_id": run.id, "application_id": application.id}
