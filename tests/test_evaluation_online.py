"""Online Evaluation mode=AB: sticky traffic split + per-variant measurement
(evaluation-spec.md section 21; plan 050-evaluation.md Phase 7).

V1 boundary: assignments are measurement facts (run <-> variant binding);
variant execution semantics require agent versioning (reserved).
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.config import Settings
from agent_platform.evaluation.domain.online import (
    AB_COMPLETED,
    AB_DRAFT,
    AB_PAUSED,
    AB_RUNNING,
    ABVariant,
)
from agent_platform.evaluation.online import OnlineEvaluationService
from agent_platform.evaluation.storage import InMemoryEvaluationStore
from agent_platform.errors import InvalidStateTransitionError, NotFoundError
from agent_platform.infrastructure.evaluation_sqlalchemy_store import SQLEvaluationStore
from agent_platform.runtime.core import (
    EventBus,
    InMemoryRuntimeStore,
    RunManager,
    RunStatus,
    RuntimeEventType,
    SessionManager,
)


def make_service() -> tuple[OnlineEvaluationService, InMemoryRuntimeStore]:
    runtime_store = InMemoryRuntimeStore()
    return (
        OnlineEvaluationService(runtime_store, InMemoryEvaluationStore()),
        runtime_store,
    )


def make_application(runtime_store):
    return SessionManager(runtime_store).create_application(name="ab-app")


def make_variants(**weights: float) -> list[ABVariant]:
    return [
        ABVariant(key=key, agent_version=f"v1-{key}", weight=weight)
        for key, weight in weights.items()
    ]


def make_run(runtime_store, application, session_id: str) -> str:
    return (
        RunManager(runtime_store)
        .create_run(
            application_id=application.id,
            session_id=session_id,
            runtime_type="agent",
            input={"message": "hi"},
            status=RunStatus.QUEUED,
        )
        .id
    )


def start_experiment(service, runtime_store, **weights):
    application = make_application(runtime_store)
    test = service.create_ab_test(
        name="exp",
        application_id=application.id,
        variants=make_variants(**weights),
    )
    return service.start_ab_test(test.id)


# --- creation validation -------------------------------------------------------------


def test_create_requires_existing_application():
    service, _ = make_service()
    with pytest.raises(NotFoundError):
        service.create_ab_test(
            name="exp",
            application_id="missing",
            variants=make_variants(A=1, B=1),
        )


def test_create_rejects_degenerate_variants_and_rates():
    service, runtime_store = make_service()
    application = make_application(runtime_store)
    bad_requests = [
        ("one-variant", make_variants(A=1), 1.0),
        ("dup-keys", [ABVariant(key="A", weight=1), ABVariant(key="A", weight=1)], 1.0),
        ("zero-weight", make_variants(A=0, B=0), 1.0),
        ("bad-rate", make_variants(A=1, B=1), 1.5),
    ]
    for name, variants, sampling_rate in bad_requests:
        with pytest.raises(ValueError):
            service.create_ab_test(
                name=name,
                application_id=application.id,
                variants=variants,
                sampling_rate=sampling_rate,
            )


# --- sticky assignment ---------------------------------------------------------------


def test_assignment_is_sticky_per_session():
    service, runtime_store = make_service()
    test = start_experiment(service, runtime_store, A=1, B=1)
    application = SessionManager(runtime_store).get_application(test.application_id)

    seen = {}
    for index in range(20):
        session_id = f"session-{index}"
        first = service.assign_run(make_run(runtime_store, application, session_id))
        assert first is not None
        assert first.ab_test_id == test.id
        assert first.agent_version == f"v1-{first.variant_key}"
        # Sticky: the same session always maps to the same variant.
        second = service.assign_run(make_run(runtime_store, application, session_id))
        assert second.variant_key == first.variant_key
        seen[session_id] = first.variant_key
    assert set(seen.values()) == {"A", "B"}


def test_assignment_respects_weights_across_sessions():
    service, runtime_store = make_service()
    test = start_experiment(service, runtime_store, A=1, B=1)
    application = SessionManager(runtime_store).get_application(test.application_id)

    counts = {"A": 0, "B": 0}
    for index in range(200):
        assignment = service.assign_run(
            make_run(runtime_store, application, f"s-{index}")
        )
        counts[assignment.variant_key] += 1
    assert counts["A"] > 60 and counts["B"] > 60


def test_sampling_rate_gates_enrollment_and_runtime_type_filters():
    service, runtime_store = make_service()
    application = make_application(runtime_store)

    sampler = OnlineEvaluationService(runtime_store, InMemoryEvaluationStore())
    test = sampler.create_ab_test(
        name="half",
        application_id=application.id,
        variants=make_variants(A=1, B=1),
        sampling_rate=0.0,
    )
    sampler.start_ab_test(test.id)
    assert sampler.assign_run(make_run(runtime_store, application, "s-1")) is None

    typed = OnlineEvaluationService(runtime_store, InMemoryEvaluationStore())
    test = typed.create_ab_test(
        name="workflow-only",
        application_id=application.id,
        runtime_type="workflow",
        variants=make_variants(A=1, B=1),
        sampling_rate=1.0,
    )
    typed.start_ab_test(test.id)
    assert typed.assign_run(make_run(runtime_store, application, "s-2")) is None
    workflow_run = RunManager(runtime_store).create_run(
        application_id=application.id,
        session_id="s-2",
        runtime_type="workflow",
        input={},
        status=RunStatus.QUEUED,
    )
    assignment = typed.assign_run(workflow_run.id)
    assert assignment is not None and assignment.variant_key in {"A", "B"}


def test_assign_run_is_idempotent():
    service, runtime_store = make_service()
    test = start_experiment(service, runtime_store, A=1, B=1)
    application = SessionManager(runtime_store).get_application(test.application_id)
    run_id = make_run(runtime_store, application, "session-1")

    first = service.assign_run(run_id)
    second = service.assign_run(run_id)
    assert first.id == second.id


def test_no_running_test_means_no_assignment():
    service, runtime_store = make_service()
    application = make_application(runtime_store)
    test = service.create_ab_test(
        name="draft", application_id=application.id, variants=make_variants(A=1, B=1)
    )
    run_id = make_run(runtime_store, application, "session-1")
    assert service.assign_run(run_id) is None  # DRAFT
    service.start_ab_test(test.id)
    assert (
        service.assign_run(make_run(runtime_store, application, "session-2"))
        is not None
    )
    service.pause_ab_test(test.id)
    assert (
        service.assign_run(make_run(runtime_store, application, "session-3")) is None
    )


# --- lifecycle -----------------------------------------------------------------------


def test_lifecycle_transitions():
    service, runtime_store = make_service()
    application = make_application(runtime_store)
    test = service.create_ab_test(
        name="exp", application_id=application.id, variants=make_variants(A=1, B=1)
    )
    with pytest.raises(InvalidStateTransitionError):
        service.complete_ab_test(test.id)
    with pytest.raises(InvalidStateTransitionError):
        service.pause_ab_test(test.id)

    assert service.start_ab_test(test.id).status == AB_RUNNING
    assert service.pause_ab_test(test.id).status == AB_PAUSED
    assert service.start_ab_test(test.id).status == AB_RUNNING
    assert service.complete_ab_test(test.id).status == AB_COMPLETED
    with pytest.raises(InvalidStateTransitionError):
        service.start_ab_test(test.id)


def test_only_one_running_test_per_application_and_runtime_type():
    service, runtime_store = make_service()
    application = make_application(runtime_store)
    first = service.create_ab_test(
        name="first", application_id=application.id, variants=make_variants(A=1, B=1)
    )
    second = service.create_ab_test(
        name="second", application_id=application.id, variants=make_variants(A=1, B=1)
    )
    service.start_ab_test(first.id)
    with pytest.raises(InvalidStateTransitionError):
        service.start_ab_test(second.id)
    service.pause_ab_test(first.id)
    assert service.start_ab_test(second.id).status == AB_RUNNING


# --- report --------------------------------------------------------------------------


def test_report_aggregates_outcomes_per_variant():
    service, runtime_store = make_service()
    test = start_experiment(service, runtime_store, A=1, B=1)
    application = SessionManager(runtime_store).get_application(test.application_id)
    runs = RunManager(runtime_store)

    variant_runs = {"A": [], "B": []}
    for index in range(12):
        run_id = make_run(runtime_store, application, f"s-{index}")
        assignment = service.assign_run(run_id)
        variant_runs[assignment.variant_key].append(run_id)
    assert variant_runs["A"] and variant_runs["B"]

    for run_id in variant_runs["A"]:
        runs.start_run(run_id)
        runs.complete_run(run_id, output={"final": "ok"})
    for run_id in variant_runs["B"]:
        runs.start_run(run_id)
        runs.fail_run(run_id, error="boom")

    report = service.report(test.id)
    stats = report["variants"]
    assert stats["A"]["runs"] == len(variant_runs["A"])
    assert stats["A"]["completed"] == len(variant_runs["A"])
    assert stats["A"]["completion_rate"] == 1.0
    assert stats["A"]["latency_avg_s"] is not None
    assert stats["B"]["failed"] == len(variant_runs["B"])
    assert stats["B"]["failure_rate"] == 1.0
    assert stats["B"]["agent_version"] == "v1-B"


def test_report_on_unknown_test_raises():
    service, _ = make_service()
    with pytest.raises(NotFoundError):
        service.report("missing")


# --- store persistence ---------------------------------------------------------------


def test_sql_store_ab_roundtrip(tmp_path):
    store = SQLEvaluationStore(f"sqlite:///{tmp_path / 'ab.db'}")
    runtime_store = InMemoryRuntimeStore()
    application = SessionManager(runtime_store).create_application(name="app")
    service = OnlineEvaluationService(runtime_store, store)
    test = service.create_ab_test(
        name="exp",
        application_id=application.id,
        variants=make_variants(A=1, B=3),
        sampling_rate=0.5,
        metadata={"owner": "qa"},
    )
    service.start_ab_test(test.id)

    loaded = store.get_ab_test(test.id)
    assert loaded is not None
    assert loaded.status == AB_RUNNING
    assert [v.key for v in loaded.variants] == ["A", "B"]
    assert loaded.variants[1].weight == 3
    assert loaded.sampling_rate == 0.5
    assert store.list_ab_tests(application_id=application.id)[0].id == test.id
    assert store.list_ab_tests(status=AB_DRAFT) == []

    run_id = make_run(runtime_store, application, "session-1")
    assignment = service.assign_run(run_id)
    if assignment is not None:  # depends on the 0.5 sampling roll
        fetched = store.get_assignment_for_run(run_id)
        assert fetched is not None and fetched.id == assignment.id
        assert fetched.variant_key in {"A", "B"}
        assert store.list_assignments_for_test(test.id)[0].id == assignment.id


# --- API -----------------------------------------------------------------------------


class _EagerSuccessAdapter:
    def __init__(self, store) -> None:
        self._runs = RunManager(store)
        self._events = EventBus(store)

    async def run(self, run_id: str) -> None:
        self._runs.start_run(run_id)
        self._events.publish(run_id=run_id, event_type=RuntimeEventType.RUN_STARTED)
        self._runs.complete_run(run_id, output={"final": "ok"})
        self._events.publish(run_id=run_id, event_type=RuntimeEventType.RUN_COMPLETED)


class _EagerSuccessOrchestrator:
    def __init__(self, store) -> None:
        self._store = store

    def execute(self, run_id: str) -> None:
        asyncio.run(_EagerSuccessAdapter(self._store).run(run_id))


def test_ab_api_end_to_end(tmp_path):
    from agent_platform.runtime.dispatch import create_runtime_store, tasks

    url = f"sqlite:///{tmp_path / 'ab-api.db'}"
    runtime_store = create_runtime_store(url)
    tasks.set_orchestrator_builder(
        lambda: _EagerSuccessOrchestrator(runtime_store)
    )
    try:
        client = TestClient(
            create_app(Settings(database_url=url, celery_task_always_eager=True))
        )
        application = client.post(
            "/api/v1/applications", json={"name": "ab-app"}
        ).json()

        created = client.post(
            "/api/v1/evaluation/ab-tests",
            json={
                "name": "exp",
                "application_id": application["id"],
                "variants": [
                    {"key": "A", "agent_version": "v1-a", "weight": 1},
                    {"key": "B", "agent_version": "v1-b", "weight": 1},
                ],
                "sampling_rate": 1.0,
            },
        )
        assert created.status_code == 201
        ab_test_id = created.json()["id"]
        assert created.json()["status"] == AB_DRAFT

        assert client.post(f"/api/v1/evaluation/ab-tests/{ab_test_id}/start").json()[
            "status"
        ] == AB_RUNNING

        run = client.post(
            "/api/v1/runs",
            json={"application_id": application["id"], "input": {"message": "hi"}},
        ).json()
        assert run["status"] == RunStatus.COMPLETED.value

        report = client.get(f"/api/v1/evaluation/ab-tests/{ab_test_id}/report")
        assert report.status_code == 200
        variants = report.json()["variants"]
        assert variants["A"]["runs"] + variants["B"]["runs"] == 1

        # Idempotent re-assignment of the same run.
        again = client.post(
            f"/api/v1/evaluation/ab-tests/{ab_test_id}/assign",
            json={"run_id": run["id"]},
        )
        assert again.status_code == 200

        assert client.post(f"/api/v1/evaluation/ab-tests/{ab_test_id}/pause").json()[
            "status"
        ] == AB_PAUSED
        assert client.post(
            f"/api/v1/evaluation/ab-tests/{ab_test_id}/complete"
        ).json()["status"] == AB_COMPLETED

        listed = client.get(
            "/api/v1/evaluation/ab-tests",
            params={"application_id": application["id"], "status": AB_COMPLETED},
        )
        assert [t["id"] for t in listed.json()] == [ab_test_id]

        assert (
            client.post(
                "/api/v1/evaluation/ab-tests/missing/assign",
                json={"run_id": run["id"]},
            ).status_code
            == 404
        )
        assert (
            client.get("/api/v1/evaluation/ab-tests/missing/report").status_code == 404
        )
        assert (
            client.post(
                "/api/v1/evaluation/ab-tests",
                json={
                    "name": "bad",
                    "application_id": application["id"],
                    "variants": [{"key": "A", "weight": 1}],
                },
            ).status_code
            == 422
        )
    finally:
        tasks.set_orchestrator_builder(None)
