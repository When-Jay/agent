"""Evaluation remaining features (plan-productionization G1-G4).

Covers: RANDOM_SAMPLE mining (GOOD cases + dedup), MONITORING forced-BAD
mining + dedup, EVALUATION_FAILURE attribution (harness failure vs scored
failure), shadow comparison assignments, and the regression patrol.
"""

import asyncio

import pytest

from agent_platform.evaluation.application import EvaluationService
from agent_platform.evaluation.cases import CaseMiner, CaseService
from agent_platform.evaluation.diagnosis import DiagnosisService
from agent_platform.evaluation.domain import (
    ASSET_REGRESSION,
    ABVariant,
    Case,
    CaseSource,
    CaseType,
    DiagnosisCategory,
    EvaluationAsset,
    EvaluationResult,
    RecommendedAction,
    ResultValue,
    Trial,
    TrialStatus,
    new_id,
)
from agent_platform.evaluation.evaluators import EvaluatorRegistry, RuleEvaluator
from agent_platform.evaluation.harness import TrialRunner
from agent_platform.evaluation.online import OnlineEvaluationService
from agent_platform.evaluation.storage import InMemoryEvaluationStore
from agent_platform.errors import InvalidStateTransitionError, NotFoundError
from agent_platform.runtime.core import (
    EventBus,
    InMemoryRuntimeStore,
    RunManager,
    RunStatus,
    RuntimeEventType,
    SessionManager,
)
from agent_platform.runtime.dispatch import RuntimeOrchestrator


class _StubAgentAdapter:
    """Minimal agent adapter: RUN_STARTED -> (complete | fail)."""

    def __init__(self, store, *, output=None, fail=False) -> None:
        self._runs = RunManager(store)
        self._events = EventBus(store)
        self._output = output if output is not None else {"final": "ok"}
        self._fail = fail

    async def run(self, run_id: str):
        self._runs.start_run(run_id)
        self._events.publish(run_id=run_id, event_type=RuntimeEventType.RUN_STARTED)
        if self._fail:
            self._runs.fail_run(run_id, error="boom")
            self._events.publish(
                run_id=run_id,
                event_type=RuntimeEventType.RUN_FAILED,
                payload={"error": "boom"},
            )
        else:
            self._runs.complete_run(run_id, output=self._output)
            self._events.publish(
                run_id=run_id, event_type=RuntimeEventType.RUN_COMPLETED
            )


def _make_production_run(store, *, fail=False, output=None) -> str:
    """Create and complete one production run directly against Runtime Core."""
    sessions = SessionManager(store)
    runs = RunManager(store)
    application = sessions.create_application(name="app")
    session = sessions.create_session(application_id=application.id)
    run = runs.create_run(
        application_id=application.id,
        session_id=session.id,
        runtime_type="agent",
        input={"message": "do the thing"},
        status=RunStatus.QUEUED,
    )
    asyncio.run(_StubAgentAdapter(store, fail=fail, output=output).run(run.id))
    return run.id


def _make_evaluation_service(runtime_store, evaluation_store) -> EvaluationService:
    registry = EvaluatorRegistry()
    registry.register(RuleEvaluator())

    def dispatch(run_id: str) -> None:
        RuntimeOrchestrator(
            runtime_store,
            agent_adapter=_StubAgentAdapter(runtime_store),
        ).execute(run_id)

    return EvaluationService(
        runtime_store,
        evaluation_store,
        TrialRunner(runtime_store, evaluation_store, dispatcher=dispatch, registry=registry),
    )


def _make_services() -> tuple:
    runtime_store = InMemoryRuntimeStore()
    evaluation_store = InMemoryEvaluationStore()
    case_service = CaseService(
        runtime_store,
        evaluation_store,
        miner=CaseMiner(runtime_store, evaluation_store),
    )
    diagnosis = DiagnosisService(runtime_store, evaluation_store)
    return runtime_store, evaluation_store, case_service, diagnosis


# --- G1: RANDOM_SAMPLE -------------------------------------------------------


def test_mine_random_samples_clean_run_yields_good_case_and_dedups():
    runtime_store, _, case_service, _ = _make_services()
    run_id = _make_production_run(runtime_store, output={"final": "ok"})

    cases = case_service.mine_random_samples(limit=5)
    assert len(cases) == 1
    case = cases[0]
    assert case.source is CaseSource.RANDOM_SAMPLE
    assert case.type is CaseType.GOOD
    assert case.trace_id == run_id

    again = case_service.mine_random_samples(limit=5)
    assert [c.id for c in again] == [case.id]


def _stores():
    runtime_store = InMemoryRuntimeStore()
    return runtime_store, InMemoryEvaluationStore()


def test_mine_random_samples_failed_run_yields_bad_case():
    runtime_store, _, case_service, _ = _make_services()
    _make_production_run(runtime_store, fail=True)

    cases = case_service.mine_random_samples(limit=5)
    assert len(cases) == 1
    assert cases[0].source is CaseSource.RANDOM_SAMPLE
    assert cases[0].type is CaseType.BAD


def test_mine_run_include_clean_only_applies_to_random_sample_source():
    runtime_store, evaluation_store = _stores()
    miner = CaseMiner(runtime_store, evaluation_store)
    run_id = _make_production_run(runtime_store, output={"final": "ok"})

    assert miner.mine_run(run_id, include_clean=True) is None
    case = miner.mine_run(
        run_id, source=CaseSource.RANDOM_SAMPLE, include_clean=True
    )
    assert case is not None
    assert case.type is CaseType.GOOD


# --- G2: MONITORING ----------------------------------------------------------


def test_mine_monitoring_signal_forces_bad_case_on_clean_run_and_dedups():
    runtime_store, _, case_service, _ = _make_services()
    run_id = _make_production_run(runtime_store, output={"final": "ok"})

    case = case_service.mine_monitoring_signal(
        run_id, reason="latency SLO breach", monitor_ref="grafana://panel/1"
    )
    assert case.source is CaseSource.MONITORING
    assert case.type is CaseType.BAD
    assert case.trace_id == run_id
    assert case.evidence[0]["reason"] == "latency SLO breach"
    assert case.evidence[0]["monitor_ref"] == "grafana://panel/1"

    again = case_service.mine_monitoring_signal(run_id, reason="duplicate alert")
    assert again.id == case.id


def test_monitoring_source_still_uses_production_attribution_rules():
    runtime_store, evaluation_store = _stores()
    case_service = CaseService(
        runtime_store, evaluation_store, miner=CaseMiner(runtime_store, evaluation_store)
    )
    diagnosis = DiagnosisService(runtime_store, evaluation_store)
    run_id = _make_production_run(runtime_store, fail=True)

    case = case_service.mine_monitoring_signal(run_id, reason="alert")
    result = diagnosis.diagnose(case.id)
    assert result.category is DiagnosisCategory.AGENT_FAILURE
    assert result.recommended_action is RecommendedAction.AGENT_FIX


# --- G3: EVALUATION_FAILURE attribution ---------------------------------------


def _save_harness_failure_case(runtime_store, evaluation_store, eval_service) -> Case:
    """EVALUATION-source case whose trial failed in the harness (error, no score)."""
    application = SessionManager(runtime_store).create_application(name="harness-app")
    task = eval_service.create_task(name="t", payload={"input": {"message": "hi"}})
    suite = eval_service.create_suite(name="s", payload={"task_ids": [task.id]})
    eval_run = eval_service.create_evaluation_run(
        suite_id=suite.id, application_id=application.id
    )
    trial = Trial(
        id=new_id(),
        task_id=task.id,
        evaluation_run_id=eval_run.id,
        status=TrialStatus.ERROR,
        run_id="",
        outcome={"error": "harness exploded"},
    )
    evaluation_store.save_trial(trial)
    case = Case(
        source=CaseSource.EVALUATION,
        type=CaseType.BAD,
        task_id=task.id,
        evidence=[
            {
                "evaluation_run_id": eval_run.id,
                "trial_id": trial.id,
                "trial_status": trial.status.value,
                "error": trial.outcome.get("error"),
            }
        ],
    )
    evaluation_store.save_case(case)
    return case


def test_diagnose_evaluation_harness_failure_attributes_eval_fix():
    runtime_store, evaluation_store, _, diagnosis = _make_services()
    eval_service = _make_evaluation_service(runtime_store, evaluation_store)
    case = _save_harness_failure_case(runtime_store, evaluation_store, eval_service)

    result = diagnosis.diagnose(case.id)
    assert result.category is DiagnosisCategory.EVALUATION_FAILURE
    assert result.component == "harness"
    assert result.recommended_action is RecommendedAction.EVAL_FIX
    assert result.evidence[0]["error"] == "harness exploded"


def test_diagnose_scored_evaluation_failure_falls_through_to_runtime_rule():
    """Scored trial (agent ran, was judged) + failed run -> AGENT_FAILURE."""
    runtime_store, evaluation_store, _, diagnosis = _make_services()
    run_id = _make_production_run(runtime_store, fail=True)
    eval_run_id, task_id = new_id(), new_id()
    trial = Trial(
        id=new_id(),
        task_id=task_id,
        evaluation_run_id=eval_run_id,
        status=TrialStatus.FAILED,
        run_id=run_id,
        trace_id=run_id,
        outcome={"error": "boom"},
    )
    evaluation_store.save_trial(trial)
    evaluation_store.save_result(
        EvaluationResult(
            task_id=task_id,
            run_id=eval_run_id,
            trial_id=trial.id,
            evaluator_id="rule",
            result=ResultValue.FAIL,
        )
    )
    case = Case(
        source=CaseSource.EVALUATION,
        type=CaseType.BAD,
        trace_id=run_id,
        evidence=[
            {"evaluation_run_id": eval_run_id, "trial_id": trial.id, "error": "boom"}
        ],
    )
    evaluation_store.save_case(case)

    result = diagnosis.diagnose(case.id)
    assert result.category is DiagnosisCategory.AGENT_FAILURE
    assert result.component == "runtime"


def test_diagnose_scored_evaluation_failure_on_clean_run_adds_coverage():
    """Scored trial whose runtime run completed -> COVERAGE_GAP, not harness."""
    runtime_store, evaluation_store, _, diagnosis = _make_services()
    run_id = _make_production_run(runtime_store, output={"unexpected": "shape"})
    eval_run_id, task_id = new_id(), new_id()
    trial = Trial(
        id=new_id(),
        task_id=task_id,
        evaluation_run_id=eval_run_id,
        status=TrialStatus.FAILED,
        run_id=run_id,
        trace_id=run_id,
        outcome={"output": {"unexpected": "shape"}},
    )
    evaluation_store.save_trial(trial)
    evaluation_store.save_result(
        EvaluationResult(
            task_id=task_id,
            run_id=eval_run_id,
            trial_id=trial.id,
            evaluator_id="rule",
            result=ResultValue.FAIL,
        )
    )
    case = Case(
        source=CaseSource.EVALUATION,
        type=CaseType.BAD,
        trace_id=run_id,
        evidence=[{"evaluation_run_id": eval_run_id, "trial_id": trial.id}],
    )
    evaluation_store.save_case(case)

    result = diagnosis.diagnose(case.id)
    assert result.category is DiagnosisCategory.COVERAGE_GAP
    assert result.recommended_action is RecommendedAction.ADD_COVERAGE


# --- G4: shadow comparison -----------------------------------------------------


def test_shadow_comparison_creates_assignments_and_dispatches_per_variant():
    runtime_store, evaluation_store, _, _ = _make_services()
    dispatched: list[str] = []
    online = OnlineEvaluationService(
        runtime_store,
        evaluation_store,
        run_dispatcher=lambda run_id: dispatched.append(run_id),
    )
    application = SessionManager(runtime_store).create_application(name="app")
    test = online.create_ab_test(
        name="shadow",
        application_id=application.id,
        variants=[ABVariant(key="a", weight=1.0), ABVariant(key="b", weight=1.0)],
        metadata={"mode": "shadow"},
    )
    online.start_ab_test(test.id)

    comparisons = online.run_shadow_comparison(test.id, input={"message": "hi"})
    assert {c["variant_key"] for c in comparisons} == {"a", "b"}
    assert sorted(dispatched) == sorted(c["run_id"] for c in comparisons)
    for comparison in comparisons:
        run = runtime_store.get_run(comparison["run_id"])
        assert run is not None
        assert run.input == {"message": "hi"}
        session = runtime_store.get_session(run.session_id)
        assert session.metadata["mode"] == "shadow"
        assert session.metadata["shadow_test_id"] == test.id
        assert session.metadata["variant_key"] == comparison["variant_key"]
        assignment = online.get_assignment_for_run(comparison["run_id"])
        assert assignment is not None
        assert assignment.variant_key == comparison["variant_key"]

    report = online.report(test.id)
    assert report["variants"]["a"]["runs"] == 1
    assert report["variants"]["b"]["runs"] == 1


def test_shadow_comparison_requires_running_test():
    runtime_store, evaluation_store, _, _ = _make_services()
    online = OnlineEvaluationService(
        runtime_store, evaluation_store, run_dispatcher=lambda run_id: None
    )
    application = SessionManager(runtime_store).create_application(name="app")
    test = online.create_ab_test(
        name="draft",
        application_id=application.id,
        variants=[ABVariant(key="a", weight=1.0), ABVariant(key="b", weight=1.0)],
        metadata={"mode": "shadow"},
    )

    with pytest.raises(InvalidStateTransitionError):
        online.run_shadow_comparison(test.id, input={})


# --- G4: regression patrol ------------------------------------------------------


def _seed_regression_asset(runtime_store, evaluation_store, eval_service) -> str:
    application = SessionManager(runtime_store).create_application(name="app")
    task = eval_service.create_task(
        name="regression task",
        payload={
            "input": {"message": "hi"},
            "success_criteria": [{"type": "required_fields", "fields": ["final"]}],
        },
    )
    evaluation_store.save_asset(
        EvaluationAsset(
            id=new_id(), name="regression-set", type=ASSET_REGRESSION, task_ids=[task.id]
        )
    )
    return application.id


def test_regression_patrol_replays_asset_tasks_as_evaluation_run():
    runtime_store, evaluation_store, _, _ = _make_services()
    eval_service = _make_evaluation_service(runtime_store, evaluation_store)
    application_id = _seed_regression_asset(runtime_store, evaluation_store, eval_service)

    run = eval_service.run_regression_patrol(
        asset_name="regression-set", application_id=application_id
    )
    assert run.status.value == "COMPLETED"
    assert run.summary["trials"] == 1
    suite = next(
        s for s in eval_service.list_suites() if s.name == "patrol-regression-set"
    )
    assert suite.task_ids


def test_regression_patrol_missing_asset_raises():
    runtime_store, evaluation_store, _, _ = _make_services()
    eval_service = _make_evaluation_service(runtime_store, evaluation_store)
    application = SessionManager(runtime_store).create_application(name="app")

    with pytest.raises(NotFoundError):
        eval_service.run_regression_patrol(
            asset_name="nope", application_id=application.id
        )


def test_patrol_task_direct_eager_call_creates_evaluation_run(monkeypatch):
    from agent_platform.runtime.dispatch import tasks as dispatch_tasks

    runtime_store, evaluation_store, _, _ = _make_services()
    eval_service = _make_evaluation_service(runtime_store, evaluation_store)
    application_id = _seed_regression_asset(runtime_store, evaluation_store, eval_service)
    monkeypatch.setattr(
        dispatch_tasks, "_evaluation_runner_builder", lambda: eval_service
    )
    monkeypatch.setattr(
        dispatch_tasks,
        "_settings",
        dispatch_tasks.Settings(
            evaluation_patrol_application_id=application_id,
            evaluation_patrol_asset="regression-set",
        ),
    )

    run_id = dispatch_tasks.execute_evaluation_patrol.apply().get()
    run = eval_service.get_evaluation_run(run_id)
    assert run.status.value == "COMPLETED"
