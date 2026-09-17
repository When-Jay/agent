"""Evaluation platform tests (plan 050 V1 scope).

Covers: store contract (in-memory + SQLAlchemy), rule/LLM-judge evaluators,
the trial harness over eager dispatch, run summary aggregation, quality
gate checks, the evaluation API, and evaluation module import boundaries.
"""

import ast
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.config import Settings
from agent_platform.evaluation.application import EvaluationService
from agent_platform.evaluation.domain import (
    EvaluationRunStatus,
    ResultValue,
    Task,
    TrialStatus,
)
from agent_platform.evaluation.evaluators import (
    EvaluationContext,
    EvaluatorRegistry,
    LLMJudgeEvaluator,
    RuleEvaluator,
)
from agent_platform.evaluation.harness import TrialRunner
from agent_platform.evaluation.storage import InMemoryEvaluationStore
from agent_platform.infrastructure.evaluation_sqlalchemy_store import (
    SQLEvaluationStore,
    create_evaluation_store,
)
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


class StubAgentAdapter:
    """Configurable agent adapter emitting events like the real loop."""

    def __init__(self, store, *, output=None, fail=False, tokens=(10, 5)) -> None:
        self._runs = RunManager(store)
        self._events = EventBus(store)
        self._output = output if output is not None else {"final": "ok"}
        self._fail = fail
        self._tokens = tokens

    async def run(self, run_id: str):
        self._runs.start_run(run_id)
        self._events.publish(run_id=run_id, event_type=RuntimeEventType.RUN_STARTED)
        if self._tokens is not None:
            self._events.publish(
                run_id=run_id,
                event_type=RuntimeEventType.LLM_COMPLETED,
                payload={
                    "input_tokens": self._tokens[0],
                    "output_tokens": self._tokens[1],
                },
            )
        if not self._fail:
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


def make_service(
    runtime_store=None, *, output=None, fail=False
) -> tuple[EvaluationService, InMemoryRuntimeStore]:
    runtime_store = runtime_store or InMemoryRuntimeStore()
    evaluation_store = InMemoryEvaluationStore()
    registry = EvaluatorRegistry()
    registry.register(RuleEvaluator())
    dispatcher = _direct_dispatcher(runtime_store, fail=fail, output=output)
    runner = TrialRunner(
        runtime_store,
        evaluation_store,
        dispatcher=dispatcher,
        registry=registry,
    )
    return EvaluationService(runtime_store, evaluation_store, runner), runtime_store


def _direct_dispatcher(runtime_store, *, fail=False, output=None):
    from threading import local

    def dispatch(run_id: str) -> None:
        orchestrator = RuntimeOrchestrator(
            runtime_store, agent_adapter=StubAgentAdapter(runtime_store, output=output, fail=fail)
        )
        orchestrator.execute(run_id)

    return dispatch


def make_task(service: EvaluationService, **overrides) -> Task:
    payload = {
        "input": {"message": "do the thing"},
        "success_criteria": [
            {"type": "required_fields", "fields": ["final"]},
            {"type": "task_completed"},
        ],
        "process_assertions": [{"type": "tool_called", "tool": "search"}],
    }
    payload.update(overrides)
    return service.create_task(name="demo-task", payload=payload)


# --- evaluators --------------------------------------------------------------------


def test_rule_evaluator_passes_matching_output_and_process_assertions():
    store = InMemoryRuntimeStore()
    task = make_task(_noop_service())
    context = EvaluationContext(
        task=task,
        trial=_trial(),
        outcome={"task_completed": True},
        output={"final": "ok"},
        events=_events_with_tool("search"),
    )
    results = RuleEvaluator().evaluate(context)

    assert [r.result for r in results] == [
        ResultValue.PASS, ResultValue.PASS, ResultValue.PASS
    ]


def test_rule_evaluator_fails_missing_field_and_unknown_rule_is_unknown():
    task = make_task(
        _noop_service(),
        success_criteria=[{"type": "required_fields", "fields": ["missing"]}],
        process_assertions=[{"type": "unknown_rule_type"}],
    )
    context = EvaluationContext(
        task=task, trial=_trial(), outcome={"task_completed": True}, output={}
    )
    results = RuleEvaluator().evaluate(context)

    assert results[0].result is ResultValue.FAIL
    assert results[1].result is ResultValue.UNKNOWN


def test_llm_judge_without_model_returns_unknown():
    context = EvaluationContext(
        task=Task(id="t", name="x"), trial=_trial(), outcome={}, output={}
    )
    results = LLMJudgeEvaluator().evaluate(context)
    assert results[0].result is ResultValue.UNKNOWN


def test_llm_judge_parses_model_verdicts():
    def model_fn(prompt: str) -> str:
        return '{"criteria": [{"id": "c1", "verdict": "FAIL", "reason": "made up data"}]}'

    from agent_platform.evaluation.domain import Rubric, Criterion

    rubric = Rubric(id="r1", name="quality", criteria=[Criterion(id="c1", description="honest")])
    context = EvaluationContext(
        task=Task(id="t", name="x"), trial=_trial(), outcome={}, output={}, rubrics=[rubric]
    )
    results = LLMJudgeEvaluator(model_fn).evaluate(context)

    assert len(results) == 1
    assert results[0].result is ResultValue.FAIL
    assert results[0].rubric_id == "r1"
    assert results[0].criterion_id == "c1"


# --- stores --------------------------------------------------------------------------


def test_in_memory_store_roundtrip():
    store = InMemoryEvaluationStore()
    service = _service_over(store)
    task = make_task(service)
    assert store.get_task(task.id).name == "demo-task"
    assert len(store.list_tasks()) == 1


def test_sqlalchemy_store_roundtrip_and_enum_restoration(tmp_path):
    url = f"sqlite:///{tmp_path / 'eval.db'}"
    store = SQLEvaluationStore(url)
    runtime_store = InMemoryRuntimeStore()
    service = _service_over(store, runtime_store=runtime_store)
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    application = SessionManager(runtime_store).create_application(name="app")

    run = service.start_evaluation_run(
        suite_id=suite.id, application_id=application.id, trials_per_task=1
    )

    # Fresh instance reads the same database; enums/datetimes restored.
    reloaded = SQLEvaluationStore(url)
    saved_run = reloaded.get_evaluation_run(run.id)
    assert saved_run.status is EvaluationRunStatus.COMPLETED
    assert saved_run.summary["pass_rate"] == 1.0

    trials = reloaded.list_trials_for_run(run.id)
    assert trials[0].status is TrialStatus.PASSED
    assert isinstance(trials[0].started_at, object)

    results = reloaded.list_results_for_run(run.id)
    assert {r.result for r in results} == {ResultValue.PASS}

    # Factory: memory URLs share one store per URL.
    assert create_evaluation_store("sqlite:///:memory:") is create_evaluation_store(
        "sqlite:///:memory:"
    )


# --- harness + service -----------------------------------------------------------------


def test_evaluation_run_passes_with_matching_output():
    service, runtime_store = make_service()
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    application = _application(runtime_store)

    run = service.start_evaluation_run(
            suite_id=suite.id, application_id=application, agent_version="v1"
        )

    assert run.status is EvaluationRunStatus.COMPLETED
    assert run.summary["pass_rate"] == 1.0
    assert run.summary["token_usage"] == {"input": 10, "output": 5}
    assert run.summary["per_task"][task.id]["pass_at_k"] == 1.0
    trials = service._store.list_trials_for_run(run.id)
    assert trials[0].status is TrialStatus.PASSED
    assert trials[0].agent_version == "v1"
    assert trials[0].run_id  # trace linkage: trial.run_id is the runtime run id


def test_evaluation_run_fails_and_counts_pass_at_k():
    service, runtime_store = make_service(output={"unrelated": True})
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    application = _application(runtime_store)

    run = service.start_evaluation_run(
            suite_id=suite.id, application_id=application, trials_per_task=2
        )

    assert run.status is EvaluationRunStatus.COMPLETED
    assert run.summary["pass_rate"] == 0.0
    assert run.summary["per_task"][task.id]["pass_at_k"] == 0.0
    assert run.summary["token_usage"]["input"] == 20  # 2 trials x 10


def test_failed_run_produces_failed_trial_and_results():
    service, runtime_store = make_service(fail=True)
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    application = _application(runtime_store)

    run = service.start_evaluation_run(suite_id=suite.id, application_id=application)

    assert run.summary["pass_rate"] == 0.0
    trial = service._store.list_trials_for_run(run.id)[0]
    assert trial.status is TrialStatus.FAILED
    assert trial.outcome["task_completed"] is False
    assert trial.outcome["error"] == "boom"
    # Results still recorded for diagnosis (spec section 14).
    results = service.list_results(run.id)
    assert results


def test_cancellation_between_trials():
    service, runtime_store = make_service()
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    application = _application(runtime_store)
    evaluation_store = service._store

    # Cancel right after the run starts (first trial dispatch flips it back).
    original = evaluation_store.save_evaluation_run

    def cancel_when_running(run):
        original(run)
        if run.status is EvaluationRunStatus.RUNNING and not _cancelled_flag["done"]:
            _cancelled_flag["done"] = True
            service.cancel_evaluation_run(run.id)

    _cancelled_flag = {"done": False}
    evaluation_store.save_evaluation_run = cancel_when_running
    try:
        run = service.start_evaluation_run(suite_id=suite.id, application_id=application)
    finally:
        evaluation_store.save_evaluation_run = original

    assert run.status is EvaluationRunStatus.CANCELLED


# --- quality gate ------------------------------------------------------------------------


def test_gate_blocks_below_threshold_and_passes_above():
    service, runtime_store = make_service()
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    application = _application(runtime_store)
    gate = service.create_gate(
        name="release",
        payload={"rules": [{"metric": "pass_rate", "threshold": 0.9, "severity": "BLOCK"}]},
    )

    run = service.start_evaluation_run(suite_id=suite.id, application_id=application)
    decision = service.check_gate(gate.id, run.id)
    assert decision.action == "PASS"

    # A gate over a different (failing) run blocks.
    failing_service, failing_store = make_service(fail=True)
    failing_task = make_task(failing_service)
    failing_suite = failing_service.create_suite(
        name="s", payload={"task_ids": [failing_task.id]}
    )
    failing_gate = failing_service.create_gate(
        name="release",
        payload={"rules": [{"metric": "pass_rate", "threshold": 0.9, "severity": "BLOCK"}]},
    )
    failing_run = failing_service.start_evaluation_run(
        suite_id=failing_suite.id, application_id=_application(failing_store)
    )
    blocked = failing_service.check_gate(failing_gate.id, failing_run.id)
    assert blocked.action == "BLOCK"
    assert blocked.rule_results[0].severity == "BLOCK"


def test_gate_missing_metric_fails_rule():
    service, runtime_store = make_service()
    task = make_task(service)
    suite = service.create_suite(name="s", payload={"task_ids": [task.id]})
    gate = service.create_gate(
        name="g",
        payload={"rules": [{"metric": "task_pass_rate:nope", "threshold": 0.5}]},
    )
    run = service.start_evaluation_run(
        suite_id=suite.id, application_id=_application(runtime_store)
    )
    decision = service.check_gate(gate.id, run.id)
    assert decision.action == "BLOCK"
    assert decision.rule_results[0].value is None


# --- API ---------------------------------------------------------------------------------


def test_evaluation_api_end_to_end(tmp_path):
    from agent_platform.runtime.dispatch import tasks

    url = f"sqlite:///{tmp_path / 'api-eval.db'}"
    runtime_store = create_runtime_store(url)
    tasks.set_orchestrator_builder(
        lambda: RuntimeOrchestrator(
            runtime_store, agent_adapter=StubAgentAdapter(runtime_store)
        )
    )
    tasks.set_evaluation_runner_builder(
        lambda: _evaluation_service_over(url, runtime_store)
    )
    try:
        client = TestClient(
            create_app(Settings(database_url=url, celery_task_always_eager=True))
        )
        application = SessionManager(runtime_store).create_application(name="app")

        task_response = client.post(
            "/api/v1/evaluation/tasks",
            json={
                "name": "demo",
                "input": {"message": "hi"},
                "success_criteria": [{"type": "required_fields", "fields": ["final"]}],
                "process_assertions": [{"type": "tool_called", "tool": "search"}],
            },
        )
        assert task_response.status_code == 201
        task_id = task_response.json()["id"]

        suite_response = client.post(
            "/api/v1/evaluation/suites",
            json={"name": "e2e", "type": "E2E", "task_ids": [task_id]},
        )
        assert suite_response.status_code == 201
        suite_id = suite_response.json()["id"]

        run_response = client.post(
            "/api/v1/evaluation/runs",
            json={
                "suite_id": suite_id,
                "application_id": application.id,
                "agent_version": "v1",
            },
        )
        assert run_response.status_code == 201
        run_body = run_response.json()
        assert run_body["status"] == "COMPLETED"
        assert run_body["summary"]["pass_rate"] == 1.0

        results_response = client.get(f"/api/v1/evaluation/runs/{run_body['id']}/results")
        assert results_response.status_code == 200
        assert results_response.json()

        gate_response = client.post(
            "/api/v1/evaluation/gates",
            json={
                "name": "release",
                "rules": [{"metric": "pass_rate", "threshold": 0.9}],
            },
        )
        gate_id = gate_response.json()["id"]
        check = client.post(
            "/api/v1/evaluation/gates/check",
            json={"gate_id": gate_id, "run_id": run_body["id"]},
        )
        assert check.status_code == 200
        assert check.json()["action"] == "PASS"

        missing = client.get(f"/api/v1/evaluation/tasks/{uuid4()}")
        assert missing.status_code == 404
    finally:
        tasks.set_orchestrator_builder(None)
        tasks.set_evaluation_runner_builder(None)


def test_evaluation_api_asset_listing(tmp_path):
    url = f"sqlite:///{tmp_path / 'api-assets.db'}"
    client = TestClient(create_app(Settings(database_url=url)))
    task = client.post("/api/v1/evaluation/tasks", json={"name": "t"})
    task_id = task.json()["id"]

    regression = client.post(
        "/api/v1/evaluation/assets",
        json={"name": "reg", "type": "REGRESSION", "task_ids": [task_id]},
    )
    assert regression.status_code == 201

    listed = client.get("/api/v1/evaluation/assets", params={"type": "REGRESSION"})
    assert [a["name"] for a in listed.json()] == ["reg"]


# --- boundaries --------------------------------------------------------------------------


def test_evaluation_module_does_not_import_execution_or_infrastructure():
    """Evaluation consumes Runtime Core only; execution/infra stay out (AGENTS.md 7)."""
    import ast

    src_root = Path(__file__).resolve().parents[1] / "src" / "agent_platform" / "evaluation"
    forbidden_prefixes = (
        "agent_platform.runtime.agent",
        "agent_platform.runtime.workflow",
        "agent_platform.runtime.dispatch",
        "agent_platform.infrastructure",
        "agent_platform.api",
    )
    offenders = []
    for path in src_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.startswith(forbidden_prefixes):
                    offenders.append((path.name, name))
    assert offenders == []


# --- helpers ------------------------------------------------------------------------------


def _noop_service() -> EvaluationService:
    return make_service()[0]


def _trial():
    from agent_platform.evaluation.domain import Trial, utcnow

    return Trial(
        id=str(uuid4()), task_id="t", evaluation_run_id="r",
        started_at=utcnow(), finished_at=utcnow(),
    )


def _events_with_tool(tool: str):
    from agent_platform.runtime.core.events import RuntimeEvent

    return [
        RuntimeEvent(
            id=str(uuid4()), run_id="r",
            event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
            payload={"tool": tool},
        )
    ]


def _application(runtime_store) -> str:
    return SessionManager(runtime_store).create_application(name="app").id


def _service_over(store, runtime_store=None) -> EvaluationService:
    runtime_store = runtime_store or InMemoryRuntimeStore()
    registry = EvaluatorRegistry()
    registry.register(RuleEvaluator())
    runner = TrialRunner(
        runtime_store,
        store,
        dispatcher=_direct_dispatcher(runtime_store),
        registry=registry,
    )
    return EvaluationService(runtime_store, store, runner)


def _evaluation_service_over(url: str, runtime_store) -> EvaluationService:
    """Worker-side EvaluationService mirroring dispatch task composition.

    Reuses the same runtime_store; the trial dispatcher enqueues Runtime
    Runs through an eager celery app (in-process, no broker).
    """
    from agent_platform.runtime.dispatch.celery_app import create_celery_app, enqueue_run

    evaluation_store = create_evaluation_store(url)
    registry = EvaluatorRegistry()
    registry.register(RuleEvaluator())
    eager_celery = create_celery_app("redis://localhost:6379/0", eager=True)
    return EvaluationService(
        runtime_store,
        evaluation_store,
        TrialRunner(
            runtime_store,
            evaluation_store,
            dispatcher=lambda run_id: enqueue_run(eager_celery, run_id),
            registry=registry,
        ),
    )
