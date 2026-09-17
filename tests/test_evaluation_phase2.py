"""Evaluation Phase 2: async run dispatch + Langfuse Score export.

Covers the create/execute split of EvaluationService (API dispatches,
worker executes — evaluation-spec.md section 32) and the observability
Score exporter that maps EvaluationResults onto trial traces
(evaluation-spec.md section 33, outside the evaluation execution path).
"""

import uuid

import pytest

from agent_platform.evaluation.application import EvaluationService
from agent_platform.evaluation.domain import (
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    ResultValue,
    Trial,
    TrialStatus,
)
from agent_platform.evaluation.storage import InMemoryEvaluationStore
from agent_platform.observability.evaluation_scores import LangfuseScoreExporter
from agent_platform.runtime.core import InMemoryRuntimeStore
from agent_platform.runtime.dispatch import tasks as dispatch_tasks
from agent_platform.runtime.dispatch.celery_app import create_celery_app, enqueue_evaluation_run
from agent_platform.errors import InvalidStateTransitionError

from test_evaluation import (
    _application,
    _service_over,
    make_task,
)


# --- create / execute split ---------------------------------------------------------


def test_create_evaluation_run_persists_running_without_trials():
    store = InMemoryEvaluationStore()
    runtime_store = InMemoryRuntimeStore()
    service = _service_over(store, runtime_store)
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    application_id = _application(runtime_store)

    run = service.create_evaluation_run(suite_id=suite.id, application_id=application_id)

    assert run.status is EvaluationRunStatus.RUNNING
    assert store.get_evaluation_run(run.id) is run
    assert store.list_trials_for_run(run.id) == []


def test_run_evaluation_run_executes_and_finalizes():
    store = InMemoryEvaluationStore()
    runtime_store = InMemoryRuntimeStore()
    service = _service_over(store, runtime_store)
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    application_id = _application(runtime_store)

    run = service.create_evaluation_run(suite_id=suite.id, application_id=application_id)
    finished = service.run_evaluation_run(run.id)

    assert finished.status is EvaluationRunStatus.COMPLETED
    assert finished.summary["pass_rate"] == 1.0
    assert len(store.list_trials_for_run(run.id)) == 1


def test_run_evaluation_run_rejects_non_running_run():
    store = InMemoryEvaluationStore()
    runtime_store = InMemoryRuntimeStore()
    service = _service_over(store, runtime_store)
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    application_id = _application(runtime_store)
    finished = service.start_evaluation_run(suite_id=suite.id, application_id=application_id)
    assert finished.status is EvaluationRunStatus.COMPLETED

    with pytest.raises(InvalidStateTransitionError):
        service.run_evaluation_run(finished.id)


# --- dispatch task ------------------------------------------------------------------


def test_enqueue_evaluation_run_executes_via_task():
    store = InMemoryEvaluationStore()
    runtime_store = InMemoryRuntimeStore()
    service = _service_over(store, runtime_store)
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    application_id = _application(runtime_store)
    run = service.create_evaluation_run(suite_id=suite.id, application_id=application_id)

    dispatch_tasks.set_evaluation_runner_builder(lambda: service)
    try:
        eager_celery = create_celery_app("redis://localhost:6379/0", eager=True)
        enqueue_evaluation_run(eager_celery, run.id)
    finally:
        dispatch_tasks.set_evaluation_runner_builder(None)

    finished = store.get_evaluation_run(run.id)
    assert finished.status is EvaluationRunStatus.COMPLETED
    assert finished.summary["pass_rate"] == 1.0


class _RecordingExporter:
    def __init__(self):
        self.exported: list[str] = []

    def export_run(self, evaluation_run_id: str) -> int:
        self.exported.append(evaluation_run_id)
        return 1


class _ExplodingExporterBuilder:
    def __call__(self):
        raise RuntimeError("langfuse down")


def test_execute_evaluation_run_exports_scores_after_completion():
    store = InMemoryEvaluationStore()
    runtime_store = InMemoryRuntimeStore()
    service = _service_over(store, runtime_store)
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    application_id = _application(runtime_store)
    run = service.create_evaluation_run(suite_id=suite.id, application_id=application_id)
    exporter = _RecordingExporter()

    dispatch_tasks.set_evaluation_runner_builder(lambda: service)
    dispatch_tasks.set_evaluation_score_exporter_builder(lambda: exporter)
    try:
        dispatch_tasks.execute_evaluation_run.apply(args=[run.id])
    finally:
        dispatch_tasks.set_evaluation_runner_builder(None)
        dispatch_tasks.set_evaluation_score_exporter_builder(None)

    assert store.get_evaluation_run(run.id).status is EvaluationRunStatus.COMPLETED
    assert exporter.exported == [run.id]


def test_score_export_failure_does_not_fail_evaluation_task():
    store = InMemoryEvaluationStore()
    runtime_store = InMemoryRuntimeStore()
    service = _service_over(store, runtime_store)
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    application_id = _application(runtime_store)
    run = service.create_evaluation_run(suite_id=suite.id, application_id=application_id)

    dispatch_tasks.set_evaluation_runner_builder(lambda: service)
    dispatch_tasks.set_evaluation_score_exporter_builder(_ExplodingExporterBuilder())
    try:
        result = dispatch_tasks.execute_evaluation_run.apply(args=[run.id])
    finally:
        dispatch_tasks.set_evaluation_runner_builder(None)
        dispatch_tasks.set_evaluation_score_exporter_builder(None)

    assert result.successful()
    assert store.get_evaluation_run(run.id).status is EvaluationRunStatus.COMPLETED


# --- Langfuse score exporter --------------------------------------------------------


class _FakeLangfuse:
    def __init__(self) -> None:
        self.scores: list[dict] = []
        self.flushed = False

    def score(self, **kwargs) -> None:
        self.scores.append(kwargs)

    def flush(self) -> None:
        self.flushed = True


def _run_with_results(store: InMemoryEvaluationStore) -> tuple[str, str]:
    """Seed one evaluation run with pass/fail/unknown results.

    Runtime Run ids are UUIDs in production (trace id = UUID(run_id).hex),
    so the seeded trials use real UUIDs; returns them for assertions.
    """
    run_id_1 = str(uuid.uuid4())
    run_id_2 = str(uuid.uuid4())
    store.save_evaluation_run(
        EvaluationRun(id="er-1", suite_id="suite-1", application_id="app-1")
    )
    store.save_trial(
        Trial(
            id="t1", task_id="task-1", evaluation_run_id="er-1",
            run_id=run_id_1, trace_id=run_id_1, status=TrialStatus.PASSED,
        )
    )
    store.save_trial(
        Trial(
            id="t2", task_id="task-1", evaluation_run_id="er-1",
            run_id=run_id_2, trace_id=run_id_2, status=TrialStatus.FAILED,
        )
    )
    store.save_result(
        EvaluationResult(
            task_id="task-1", run_id="er-1", trial_id="t1",
            evaluator_id="rule", result=ResultValue.PASS,
        )
    )
    store.save_result(
        EvaluationResult(
            task_id="task-1", run_id="er-1", trial_id="t2",
            evaluator_id="rule", result=ResultValue.FAIL,
        )
    )
    # UNKNOWN 是一等结果，不导出数值（spec section 18）。
    store.save_result(
        EvaluationResult(
            task_id="task-1", run_id="er-1", trial_id="t2",
            evaluator_id="rule", result=ResultValue.UNKNOWN,
        )
    )
    # 无对应 trial 的结果跳过。
    store.save_result(
        EvaluationResult(
            task_id="task-1", run_id="er-1", trial_id="missing",
            evaluator_id="rule", result=ResultValue.PASS,
        )
    )
    return run_id_1, run_id_2


def test_export_run_maps_results_to_trial_trace_scores():
    store = InMemoryEvaluationStore()
    run_id_1, run_id_2 = _run_with_results(store)
    client = _FakeLangfuse()

    exporter = LangfuseScoreExporter(store, client=client)
    count = exporter.export_run("er-1")

    assert exporter.enabled
    assert count == 2
    assert [(s["trace_id"], s["name"], s["value"]) for s in client.scores] == [
        (uuid.UUID(run_id_1).hex, "rule", 1.0),
        (uuid.UUID(run_id_2).hex, "rule", 0.0),
    ]
    assert client.flushed


def test_export_run_prefers_explicit_result_score():
    store = InMemoryEvaluationStore()
    store.save_evaluation_run(
        EvaluationRun(id="er-1", suite_id="suite-1", application_id="app-1")
    )
    run_id = str(uuid.uuid4())
    store.save_trial(
        Trial(
            id="t1", task_id="task-1", evaluation_run_id="er-1",
            run_id=run_id, trace_id=run_id, status=TrialStatus.PASSED,
        )
    )
    store.save_result(
        EvaluationResult(
            task_id="task-1", run_id="er-1", trial_id="t1",
            evaluator_id="llm_judge", result=ResultValue.PASS, score=0.75,
        )
    )
    client = _FakeLangfuse()

    assert LangfuseScoreExporter(store, client=client).export_run("er-1") == 1
    assert client.scores[0]["value"] == 0.75
    assert client.scores[0]["trace_id"] == uuid.UUID(run_id).hex


def test_export_run_is_failure_isolated_per_score():
    store = InMemoryEvaluationStore()
    run_id_1, run_id_2 = _run_with_results(store)
    client = _FakeLangfuse()

    def flaky_score(**kwargs):
        if kwargs["trace_id"] == uuid.UUID(run_id_1).hex:
            raise RuntimeError("langfuse 500")
        client.scores.append(kwargs)

    client.score = flaky_score
    count = LangfuseScoreExporter(store, client=client).export_run("er-1")

    assert count == 1  # the failing score is skipped, the rest still exported
    assert len(client.scores) == 1


def test_exporter_disabled_without_keys_or_client():
    store = InMemoryEvaluationStore()
    _run_with_results(store)
    from agent_platform.config import Settings

    exporter = LangfuseScoreExporter(
        store,
        settings=Settings(langfuse_public_key="", langfuse_secret_key=""),
    )

    assert not exporter.enabled
    assert exporter.export_run("er-1") == 0


def test_export_run_returns_zero_for_unknown_run():
    client = _FakeLangfuse()
    exporter = LangfuseScoreExporter(InMemoryEvaluationStore(), client=client)

    assert exporter.export_run("nope") == 0
    assert client.scores == []
