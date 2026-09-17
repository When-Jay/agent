"""Evaluation Second Stage closed loop: Online signal -> Case Mining ->
Diagnosis -> Regression Set (050-evaluation.md section 19; evaluation-spec.md
sections 15-16, 23-24).

Covers: production-run mining with dedup, evaluation-failure mining,
deterministic diagnosis attribution rules, regression promotion lifecycle,
store persistence (in-memory + SQLAlchemy), and the case API over eager
dispatch.
"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.config import Settings
from agent_platform.evaluation.application import EvaluationService
from agent_platform.evaluation.cases import CaseMiner, CaseService
from agent_platform.evaluation.diagnosis import DiagnosisService
from agent_platform.evaluation.domain import (
    CASE_DIAGNOSED,
    CASE_OPEN,
    CASE_PROMOTED,
    CaseSource,
    CaseType,
    DiagnosisCategory,
    RecommendedAction,
)
from agent_platform.evaluation.evaluators import EvaluatorRegistry, RuleEvaluator
from agent_platform.evaluation.harness import TrialRunner
from agent_platform.evaluation.storage import InMemoryEvaluationStore
from agent_platform.errors import InvalidStateTransitionError
from agent_platform.infrastructure.evaluation_sqlalchemy_store import SQLEvaluationStore
from agent_platform.runtime.core import (
    EventBus,
    InMemoryRuntimeStore,
    RunManager,
    RunStatus,
    RuntimeEventType,
    SessionManager,
)
from agent_platform.runtime.dispatch import RuntimeOrchestrator


class StubAgentAdapter:
    """Configurable agent adapter emitting events like the real loop."""

    def __init__(self, store, *, output=None, fail=False, tool_failure=False) -> None:
        self._runs = RunManager(store)
        self._events = EventBus(store)
        self._output = output if output is not None else {"final": "ok"}
        self._fail = fail
        self._tool_failure = tool_failure

    async def run(self, run_id: str):
        self._runs.start_run(run_id)
        self._events.publish(run_id=run_id, event_type=RuntimeEventType.RUN_STARTED)
        if self._tool_failure:
            self._events.publish(
                run_id=run_id,
                event_type=RuntimeEventType.TOOL_CALL_FAILED,
                payload={"tool": "search", "error": "timeout"},
            )
        else:
            self._events.publish(
                run_id=run_id,
                event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
                payload={"tool": "search"},
            )
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


def make_case_service(
    runtime_store=None,
) -> tuple[CaseService, DiagnosisService, InMemoryRuntimeStore]:
    runtime_store = runtime_store or InMemoryRuntimeStore()
    evaluation_store = InMemoryEvaluationStore()
    miner = CaseMiner(runtime_store, evaluation_store)
    service = CaseService(runtime_store, evaluation_store, miner=miner)
    diagnosis = DiagnosisService(runtime_store, evaluation_store)
    return service, diagnosis, runtime_store


def make_production_run(
    store, *, fail=False, tool_failure=False, output=None
) -> str:
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
    import asyncio

    asyncio.run(
        StubAgentAdapter(
            store, fail=fail, tool_failure=tool_failure, output=output
        ).run(run.id)
    )
    return run.id


def make_evaluation_service(
    runtime_store, evaluation_store, *, fail=False, output=None
) -> EvaluationService:
    registry = EvaluatorRegistry()
    registry.register(RuleEvaluator())

    def dispatch(run_id: str) -> None:
        RuntimeOrchestrator(
            runtime_store,
            agent_adapter=StubAgentAdapter(
                runtime_store, fail=fail, output=output
            ),
        ).execute(run_id)

    runner = TrialRunner(
        runtime_store, evaluation_store, dispatcher=dispatch, registry=registry
    )
    return EvaluationService(runtime_store, evaluation_store, runner)


def run_offline_evaluation(
    runtime_store, evaluation_store, *, output
) -> str:
    """One-task evaluation run; the task fails unless output has 'final'."""
    service = make_evaluation_service(runtime_store, evaluation_store, output=output)
    application = SessionManager(runtime_store).create_application(name="eval-app")
    task = service.create_task(
        name="demo",
        payload={
            "input": {"message": "hi"},
            "success_criteria": [{"type": "required_fields", "fields": ["final"]}],
        },
    )
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    run = service.start_evaluation_run(suite_id=suite.id, application_id=application.id)
    return run.id


# --- mining ------------------------------------------------------------------------


def test_mine_production_failure_creates_bad_case_and_dedups():
    case_service, _, store = make_case_service()
    run_id = make_production_run(store, fail=True)

    case = case_service.mine_run(run_id)
    assert case is not None
    assert case.type is CaseType.BAD
    assert case.source is CaseSource.PRODUCTION_SAMPLE
    assert case.status == CASE_OPEN
    assert case.trace_id == run_id
    assert case.input == {"message": "do the thing"}
    assert any(e["event_type"] == "RunFailed" for e in case.evidence)

    again = case_service.mine_run(run_id)
    assert again is not None and again.id == case.id


def test_mine_clean_production_run_creates_no_case():
    case_service, _, store = make_case_service()
    run_id = make_production_run(store, output={"final": "ok"})
    assert case_service.mine_run(run_id) is None


def test_mine_tool_failure_on_completed_run_is_bad_case():
    case_service, _, store = make_case_service()
    run_id = make_production_run(store, tool_failure=True, output={"final": "ok"})
    case = case_service.mine_run(run_id)
    assert case is not None
    assert any(e["event_type"] == "ToolCallFailed" for e in case.evidence)


def test_mine_evaluation_run_failed_trials_and_dedup():
    store = InMemoryRuntimeStore()
    evaluation_store = InMemoryEvaluationStore()
    evaluation_run_id = run_offline_evaluation(
        store, evaluation_store, output={"unexpected": "shape"}
    )
    case_service = CaseService(
        store,
        evaluation_store,
        miner=CaseMiner(store, evaluation_store),
    )

    cases = case_service.mine_evaluation_run(evaluation_run_id)
    assert len(cases) == 1
    case = cases[0]
    assert case.source is CaseSource.EVALUATION
    assert case.type is CaseType.BAD
    assert case.task_id
    assert case.trace_id
    assert case.expected_behavior.get("description", "") == ""
    assert case.evidence[0]["evaluation_run_id"] == evaluation_run_id

    again = case_service.mine_evaluation_run(evaluation_run_id)
    assert [c.id for c in again] == [case.id]


def test_mine_evaluation_run_skips_passed_trials():
    store = InMemoryRuntimeStore()
    evaluation_store = InMemoryEvaluationStore()
    evaluation_run_id = run_offline_evaluation(
        store, evaluation_store, output={"final": "ok"}
    )
    case_service = CaseService(
        store,
        evaluation_store,
        miner=CaseMiner(store, evaluation_store),
    )
    assert case_service.mine_evaluation_run(evaluation_run_id) == []


# --- diagnosis ---------------------------------------------------------------------


def test_diagnose_failed_run_attributes_agent_runtime_failure():
    case_service, diagnosis, store = make_case_service()
    run_id = make_production_run(store, fail=True)
    case = case_service.mine_run(run_id)

    result = diagnosis.diagnose(case.id)
    assert result.category is DiagnosisCategory.AGENT_FAILURE
    assert result.component == "runtime"
    assert result.recommended_action is RecommendedAction.AGENT_FIX
    assert result.confidence >= 0.9

    updated = case_service.get_case(case.id)
    assert updated.status == CASE_DIAGNOSED
    assert updated.attribution["category"] == "AGENT_FAILURE"
    assert updated.attribution["diagnosis_id"] == result.id


def test_diagnose_tool_failure_attributes_agent_tool():
    case_service, diagnosis, store = make_case_service()
    run_id = make_production_run(store, tool_failure=True, output={"final": "ok"})
    case = case_service.mine_run(run_id)

    result = diagnosis.diagnose(case.id)
    assert result.category is DiagnosisCategory.AGENT_FAILURE
    assert result.component == "tool"
    assert result.recommended_action is RecommendedAction.AGENT_FIX


def test_diagnose_clean_trace_is_coverage_gap_recommending_add_coverage():
    store = InMemoryRuntimeStore()
    evaluation_store = InMemoryEvaluationStore()
    evaluation_run_id = run_offline_evaluation(
        store, evaluation_store, output={"unexpected": "shape"}
    )
    case_service = CaseService(
        store, evaluation_store, miner=CaseMiner(store, evaluation_store)
    )
    diagnosis = DiagnosisService(store, evaluation_store)
    (case,) = case_service.mine_evaluation_run(evaluation_run_id)

    result = diagnosis.diagnose(case.id)
    assert result.category is DiagnosisCategory.COVERAGE_GAP
    assert result.component == "process"
    assert result.recommended_action is RecommendedAction.ADD_COVERAGE


def test_diagnose_without_trace_recommends_human_review():
    case_service, diagnosis, _ = make_case_service()
    case = case_service.create_case(
        source=CaseSource.USER_FEEDBACK,
        type=CaseType.BAD,
        input={"message": "wrong answer"},
    )
    result = diagnosis.diagnose(case.id)
    assert result.category is DiagnosisCategory.COVERAGE_GAP
    assert result.component == "trace"
    assert result.recommended_action is RecommendedAction.HUMAN_REVIEW
    assert result.confidence == 0.0


def test_diagnose_keeps_history_and_latest_attribution():
    case_service, diagnosis, store = make_case_service()
    run_id = make_production_run(store, fail=True)
    case = case_service.mine_run(run_id)

    first = diagnosis.diagnose(case.id)
    second = diagnosis.diagnose(case.id)
    assert first.id != second.id
    history = diagnosis.list_diagnoses(case.id)
    assert [d.id for d in history] == [first.id, second.id]
    assert case_service.get_case(case.id).attribution["diagnosis_id"] == second.id


# --- regression promotion ----------------------------------------------------------


def test_promote_bad_case_creates_regression_task_and_asset():
    case_service, diagnosis, store = make_case_service()
    run_id = make_production_run(store, fail=True)
    case = case_service.mine_run(run_id)
    diagnosis.diagnose(case.id)

    promoted, task, asset = case_service.promote_to_regression(case.id)

    assert promoted.status == CASE_PROMOTED
    assert promoted.attribution["regression_task_id"] == task.id
    assert promoted.attribution["regression_asset_id"] == asset.id
    assert task.input == {"message": "do the thing"}
    assert task.metadata["source_case"] == case.id
    assert asset.type == "REGRESSION"
    assert asset.task_ids == [task.id]
    assert asset.metadata["promoted_cases"] == [case.id]


def test_promote_appends_to_existing_asset_by_name_and_rejects_repromote():
    case_service, _, store = make_case_service()
    first = case_service.mine_run(make_production_run(store, fail=True))
    second = case_service.mine_run(make_production_run(store, fail=True))
    assert first is not None and second is not None

    _, task_a, asset = case_service.promote_to_regression(first.id, asset_name="core")
    _, task_b, asset_again = case_service.promote_to_regression(second.id, asset_name="core")
    assert asset_again.id == asset.id
    assert asset_again.task_ids == [task_a.id, task_b.id]

    with pytest.raises(InvalidStateTransitionError):
        case_service.promote_to_regression(first.id, asset_name="core")


def test_promote_rejects_non_bad_cases():
    case_service, _, _ = make_case_service()
    case = case_service.create_case(
        source=CaseSource.USER_FEEDBACK, type=CaseType.GOOD, input={}
    )
    with pytest.raises(InvalidStateTransitionError):
        case_service.promote_to_regression(case.id)


def test_dismiss_case_and_status_filters():
    case_service, _, _ = make_case_service()
    case = case_service.create_case(
        source=CaseSource.USER_FEEDBACK, type=CaseType.BAD, input={}
    )
    dismissed = case_service.dismiss_case(case.id)
    assert dismissed.status == "DISMISSED"
    assert case_service.list_cases(status="DISMISSED")[0].id == case.id
    assert case_service.list_cases(status=CASE_OPEN) == []
    assert case_service.list_cases(
        type=CaseType.BAD, source=CaseSource.USER_FEEDBACK
    )


# --- store persistence ---------------------------------------------------------------


def test_case_store_roundtrip_in_memory():
    case_service, diagnosis, runtime = make_case_service()
    run_id = make_production_run(runtime, fail=True)
    case = case_service.mine_run(run_id)
    assert case is not None
    diagnosis.diagnose(case.id)

    loaded = case_service.get_case(case.id)
    assert loaded.source is CaseSource.PRODUCTION_SAMPLE
    assert loaded.type is CaseType.BAD
    assert loaded.attribution["category"] == "AGENT_FAILURE"


def test_sql_store_case_and_diagnosis_roundtrip(tmp_path):
    store = SQLEvaluationStore(f"sqlite:///{tmp_path / 'cases.db'}")
    case = make_case_service()[0].create_case(
        source=CaseSource.USER_FEEDBACK,
        type=CaseType.BAD,
        input={"message": "hi"},
        trace_id="",
    )
    store.save_case(case)
    loaded = store.get_case(case.id)
    assert loaded is not None
    assert loaded.source is CaseSource.USER_FEEDBACK
    assert loaded.type is CaseType.BAD
    assert store.find_case_by_trace("USER_FEEDBACK", "missing") is None

    from agent_platform.evaluation.domain import DiagnosisResult

    result = DiagnosisResult(
        case_id=case.id,
        category=DiagnosisCategory.AGENT_FAILURE,
        component="tool",
        confidence=0.8,
        recommended_action=RecommendedAction.AGENT_FIX,
    )
    store.save_diagnosis(result)
    history = store.list_diagnoses_for_case(case.id)
    assert len(history) == 1
    assert history[0].category is DiagnosisCategory.AGENT_FAILURE
    assert history[0].recommended_action is RecommendedAction.AGENT_FIX

    assert [c.id for c in store.list_cases(case_type="BAD")] == [case.id]
    assert store.list_cases(case_type="GOOD") == []


# --- API -----------------------------------------------------------------------------


def test_case_api_closed_loop(tmp_path):
    from agent_platform.runtime.dispatch import create_runtime_store, tasks

    url = f"sqlite:///{tmp_path / 'api-cases.db'}"
    runtime_store = create_runtime_store(url)
    tasks.set_orchestrator_builder(
        lambda: RuntimeOrchestrator(
            runtime_store,
            agent_adapter=StubAgentAdapter(runtime_store, fail=True),
        )
    )
    try:
        client = TestClient(
            create_app(Settings(database_url=url, celery_task_always_eager=True))
        )
        application = SessionManager(runtime_store).create_application(name="app")

        run_response = client.post(
            "/api/v1/runs",
            json={"application_id": application.id, "input": {"message": "hi"}},
        )
        assert run_response.status_code == 201
        assert run_response.json()["status"] == RunStatus.FAILED.value
        run_id = run_response.json()["id"]

        mined = client.post("/api/v1/evaluation/cases/mine", json={"run_id": run_id})
        assert mined.status_code == 200
        cases = mined.json()["cases"]
        assert len(cases) == 1
        assert cases[0]["type"] == "BAD"
        case_id = cases[0]["id"]

        # Deduplication: re-mining is idempotent.
        again = client.post("/api/v1/evaluation/cases/mine", json={"run_id": run_id})
        assert [c["id"] for c in again.json()["cases"]] == [case_id]

        diagnosed = client.post(f"/api/v1/evaluation/cases/{case_id}/diagnose")
        assert diagnosed.status_code == 200
        assert diagnosed.json()["category"] == "AGENT_FAILURE"
        assert diagnosed.json()["recommended_action"] == "AGENT_FIX"

        promoted = client.post(
            f"/api/v1/evaluation/cases/{case_id}/promote",
            json={"asset_name": "release-regression"},
        )
        assert promoted.status_code == 200
        body = promoted.json()
        assert body["case"]["status"] == "PROMOTED"
        assert body["asset"]["type"] == "REGRESSION"
        assert body["task"]["metadata"]["source_case"] == case_id

        listed = client.get(
            "/api/v1/evaluation/cases", params={"status": "PROMOTED"}
        )
        assert [c["id"] for c in listed.json()] == [case_id]

        history = client.get(f"/api/v1/evaluation/cases/{case_id}/diagnoses")
        assert history.status_code == 200
        assert len(history.json()) == 1

        missing = client.post(f"/api/v1/evaluation/cases/{uuid4()}/diagnose")
        assert missing.status_code == 404
    finally:
        tasks.set_orchestrator_builder(None)


def test_case_api_requires_signal_selector(tmp_path):
    url = f"sqlite:///{tmp_path / 'api-cases-invalid.db'}"
    client = TestClient(create_app(Settings(database_url=url)))
    response = client.post("/api/v1/evaluation/cases/mine", json={})
    assert response.status_code == 422
