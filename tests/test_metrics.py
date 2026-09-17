"""Runtime metrics tests (04-observability-architecture.md section 3).

The MetricsCollector derives V1 metrics purely from RuntimeEvents on
the EventBus; the API exposes the process-wide collector snapshot at
GET /api/v1/metrics.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.config import Settings
from agent_platform.observability import (
    MetricsCollector,
    attach_metrics_collector,
    get_metrics_collector,
)
from agent_platform.runtime.core import (
    EventBus,
    InMemoryRuntimeStore,
    RuntimeEvent,
    RuntimeEventType,
)
from agent_platform.runtime.dispatch import create_runtime_store


@pytest.fixture()
def collector():
    get_metrics_collector().reset()
    yield get_metrics_collector()
    get_metrics_collector().reset()


def _publish(bus, event_type, run_id="run-1", payload=None, *, created_at=None):
    """Publish through the bus; backdated events bypass persistence and are
    fanned out directly so duration math stays deterministic."""
    if created_at is not None:
        event = RuntimeEvent(
            id=str(uuid.uuid4()),
            run_id=run_id,
            event_type=event_type,
            payload=payload or {},
            created_at=created_at,
        )
        bus._notify(event)
        return event
    return bus.publish(run_id=run_id, event_type=event_type, payload=payload or {})


def test_collector_counts_runs_llm_tools_and_tokens():
    collector = MetricsCollector()
    bus = EventBus(InMemoryRuntimeStore())
    collector.attach(bus)

    _publish(bus, RuntimeEventType.RUN_STARTED, "run-1")
    _publish(bus, RuntimeEventType.RUN_STARTED, "run-2")
    _publish(
        bus,
        RuntimeEventType.LLM_COMPLETED,
        "run-1",
        {"model": "m", "input_tokens": 10, "output_tokens": 4},
    )
    _publish(bus, RuntimeEventType.LLM_FAILED, "run-1", {"error": "boom"})
    _publish(bus, RuntimeEventType.TOOL_CALL_COMPLETED, "run-1", {"tool": "launch"})
    _publish(bus, RuntimeEventType.TOOL_CALL_FAILED, "run-1", {"tool": "launch"})
    _publish(bus, RuntimeEventType.NODE_STARTED, "run-2")
    _publish(bus, RuntimeEventType.RUN_COMPLETED, "run-1")
    _publish(bus, RuntimeEventType.RUN_FAILED, "run-2")
    _publish(bus, RuntimeEventType.APPROVAL_REQUIRED, "run-2")
    _publish(bus, RuntimeEventType.HUMAN_RESPONSE_RECEIVED, "run-2")
    _publish(bus, RuntimeEventType.CHECKPOINT_CREATED, "run-2")

    snapshot = collector.snapshot()
    assert snapshot["runs"]["started"] == 2
    assert snapshot["runs"]["completed"] == 1
    assert snapshot["runs"]["failed"] == 1
    assert snapshot["llm"] == {
        "started": 0,
        "completed": 1,
        "failed": 1,
        "input_tokens": 10,
        "output_tokens": 4,
    }
    assert snapshot["tools"] == {"started": 0, "completed": 1, "failed": 1}
    assert snapshot["workflow_nodes"] == {"started": 1, "completed": 0, "failed": 0}
    assert snapshot["checkpoints_created"] == 1
    assert snapshot["human"] == {"approvals_required": 1, "responses_received": 1}
    assert snapshot["counters"]["RunCompleted"] == 1


def test_collector_measures_run_duration_from_event_timestamps():
    collector = MetricsCollector()
    bus = EventBus(InMemoryRuntimeStore())
    collector.attach(bus)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    _publish(bus, RuntimeEventType.RUN_STARTED, "run-1", created_at=start)
    _publish(
        bus,
        RuntimeEventType.RUN_COMPLETED,
        "run-1",
        created_at=start + timedelta(seconds=2.5),
    )
    # A terminal event for an unknown run must not fabricate a duration.
    _publish(bus, RuntimeEventType.RUN_FAILED, "run-x")

    summary = collector.snapshot()["runs"]["duration_seconds"]
    assert summary == {"count": 1, "avg": 2.5, "max": 2.5}

    # Reruns measure again from the new RUN_STARTED.
    _publish(bus, RuntimeEventType.RUN_STARTED, "run-1", created_at=start)
    _publish(
        bus,
        RuntimeEventType.RUN_FAILED,
        "run-1",
        created_at=start + timedelta(seconds=1),
    )
    summary = collector.snapshot()["runs"]["duration_seconds"]
    assert summary == {"count": 2, "avg": 1.75, "max": 2.5}


def test_collector_isolates_handler_failures():
    collector = MetricsCollector()
    bus = EventBus(InMemoryRuntimeStore())
    collector.attach(bus)

    # A malformed LLM payload must not corrupt later collection.
    _publish(bus, RuntimeEventType.LLM_COMPLETED, "run-1", {"input_tokens": "abc"})
    _publish(bus, RuntimeEventType.RUN_STARTED, "run-1")

    snapshot = collector.snapshot()
    assert snapshot["runs"]["started"] == 1


def test_attach_metrics_collector_is_idempotent_per_bus():
    get_metrics_collector().reset()
    bus = EventBus(InMemoryRuntimeStore())
    other = EventBus(InMemoryRuntimeStore())

    attach_metrics_collector(bus)
    attach_metrics_collector(bus)  # second attach: no double counting
    attach_metrics_collector(other)

    bus.publish(run_id="run-1", event_type=RuntimeEventType.RUN_STARTED)
    other.publish(run_id="run-2", event_type=RuntimeEventType.RUN_STARTED)

    snapshot = get_metrics_collector().snapshot()
    assert snapshot["runs"]["started"] == 2
    get_metrics_collector().reset()


def test_api_metrics_endpoint_exposes_snapshot(tmp_path):
    get_metrics_collector().reset()
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    settings = Settings(database_url=database_url, celery_task_always_eager=True)
    client = TestClient(create_app(settings))

    bus = EventBus(create_runtime_store(database_url))
    attach_metrics_collector(bus)
    started = datetime.now(timezone.utc)
    _publish(bus, RuntimeEventType.RUN_STARTED, created_at=started)
    _publish(
        bus,
        RuntimeEventType.RUN_COMPLETED,
        created_at=started + timedelta(seconds=1),
    )

    response = client.get("/api/v1/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["runs"]["started"] == 1
    assert body["runs"]["completed"] == 1
    assert body["runs"]["duration_seconds"]["avg"] == 1.0
    get_metrics_collector().reset()
