"""Evolution platform tests (plan 060 V1 scope).

Covers: patch engine + edit budget, SkillOptimizer generation, candidate
selection (hard constraints + lexicographic objectives), the full run
lifecycle (human review / auto release / reject), versioning + rollback,
evidence, the store contract (in-memory + SQLAlchemy), the API, and the
Evolution -> Evaluation gateway integration.
"""

import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from agent_platform.config import Settings
from agent_platform.errors import NotFoundError
from agent_platform.evolution.application import EvolutionService
from agent_platform.evolution.domain import (
    CandidateValidationStatus,
    DecisionType,
    DeploymentKind,
    DeploymentRecord,
    DeploymentStatus,
    EvolutionCandidate,
    EvolutionDecision,
    EvolutionEvent,
    EvolutionRunStatus,
    EvolutionVersion,
    Experiment,
    Patch,
    PatchOperation,
)
from agent_platform.evolution.experiment import ExperimentRunner
from agent_platform.evolution.governance import GATE_FAIL, GATE_PASS, GATE_SKIPPED
from agent_platform.evolution.optimizers import (
    OptimizerError,
    SkillOptimizer,
    default_optimizers,
)
from agent_platform.evolution.patching import apply_patches, validate_patches
from agent_platform.evolution.selection import lexicographic_compare
from agent_platform.evolution.storage import InMemoryEvolutionStore
from agent_platform.evolution.domain.task import (
    EditBudget,
    EvolutionObjective,
    HardConstraint,
    OptimizationObjective,
)
from agent_platform.infrastructure.evolution_sqlalchemy_store import (
    SQLEvolutionStore,
    create_evolution_store,
)

SKILL_CONTENT = """# search_skill

## tool_selection
Use the search tool when the user asks factual questions.

## tool_usage
Always cite sources.
"""


# --- stubs ---------------------------------------------------------------------------------


class StubGateway:
    """EvaluationGateway double: canned summaries keyed by suite id."""

    def __init__(self, summaries=None, gate_action="PASS") -> None:
        self.summaries = summaries or {}
        self.gate_action = gate_action
        self.calls: list[tuple[str, str]] = []
        self._seq = 0

    def run_suite(self, *, suite_id, application_id, runtime_type, agent_version):
        self.calls.append((suite_id, agent_version))
        self._seq += 1
        summary = dict(self.summaries.get(suite_id) or {"pass_rate": 1.0})
        return SimpleNamespace(
            id=f"eval-run-{self._seq}", environment_id=None, summary=summary
        )

    def check_gate(self, *, gate_id, run_id):
        return SimpleNamespace(action=self.gate_action, rule_results=[])


def _default_proposals() -> list[dict]:
    return [
        {
            "patches": [
                {
                    "operation": "REPLACE",
                    "path": "tool_usage",
                    "old_value": "Always cite sources.",
                    "new_value": "Always cite sources and include links.",
                }
            ],
            "reason": "richer citations",
            "evidence": ["bad case c1"],
            "expected_impact": "task success +5%",
        },
        {
            "patches": [
                {
                    "operation": "REPLACE",
                    "path": "tool_selection",
                    "old_value": "Use the search tool when the user asks factual questions.",
                    "new_value": "Use the search tool for factual questions; "
                    "otherwise answer directly.",
                }
            ],
            "reason": "narrower tool trigger",
            "evidence": [],
            "expected_impact": "fewer tool calls",
        },
    ]


def _stub_model_fn(proposals=None):
    payload = json.dumps(_default_proposals() if proposals is None else proposals)
    return lambda prompt: payload


_STUB = "__stub__"


def make_service(
    *, proposals=None, summaries=None, gate_action="PASS", model_fn=_STUB
) -> tuple[EvolutionService, StubGateway]:
    store = InMemoryEvolutionStore()
    gateway = StubGateway(summaries=summaries, gate_action=gate_action)
    optimizers = {
        o.name: o for o in default_optimizers(
            _stub_model_fn(proposals) if model_fn is _STUB else model_fn
        )
    }
    service = EvolutionService(
        store, ExperimentRunner(gateway, store), optimizers=optimizers
    )
    return service, gateway


def make_task(service: EvolutionService, **overrides):
    suites = {"validation": "suite-validation"}
    for purpose in ("regression", "challenge"):
        if overrides.pop(purpose, None):
            suites[purpose] = f"suite-{purpose}"
    payload = {
        "target_type": "SKILL",
        "resource_id": "search_skill",
        "trigger": {"type": "production_bad_case", "case_ids": ["c1", "c2"]},
        "diagnosis": {"type": "SKILL_FAILURE"},
        "evaluation_assets": {
            "application_id": "app-1",
            "runtime_type": "agent",
            "suites": suites,
            "gate_id": overrides.pop("gate_id", None),
        },
        "strategy": {
            "type": "MULTI_CANDIDATE",
            "candidate_count": 2,
            "edit_budget": {"max_edits": 3, "max_tokens_added": 200},
        },
        "objective": {
            "hard_constraints": [
                {"metric": "pass_rate", "op": ">=", "threshold": 1.0}
            ],
            "optimization_objectives": [
                {"metric": "pass_rate", "direction": "maximize"}
            ],
        },
    }
    payload.update(overrides)
    return service.create_task(name="improve search skill", payload=payload)


def register_target(service: EvolutionService):
    return service.register_target(
        target_type="SKILL",
        resource_id="search_skill",
        content=SKILL_CONTENT,
        created_by="tester",
    )


# --- patch engine -----------------------------------------------------------------------------


def test_patch_engine_add_insert_replace_delete():
    content = SKILL_CONTENT
    replaced = apply_patches(
        content,
        [Patch(PatchOperation.REPLACE, "tool_usage", "Always cite sources.", "Cite sources.")],
    )
    assert "Cite sources." in replaced and "Always cite sources." not in replaced
    assert "## tool_usage" in replaced

    deleted = apply_patches(content, [Patch(PatchOperation.DELETE, "tool_selection")])
    assert "## tool_selection" not in deleted and "## tool_usage" in deleted

    added = apply_patches(
        content,
        [Patch(PatchOperation.ADD, "", None, "## guardrails\n\nNever fabricate facts.")],
    )
    assert "## guardrails" in added and "Never fabricate facts." in added

    inserted = apply_patches(
        content,
        [Patch(PatchOperation.INSERT, "tool_selection", None, "## scope\n\nWeb only.")],
    )
    assert inserted.index("## scope") < inserted.index("## tool_usage")


def test_patch_budget_violations():
    budget = EditBudget(max_edits=2, max_tokens_added=5, allowed_operations=["REPLACE"])
    # edit count
    violations = validate_patches([Patch(PatchOperation.REPLACE)] * 3, budget)
    assert any("max_edits" in v for v in violations)
    # disallowed operation
    violations = validate_patches([Patch(PatchOperation.ADD, "", None, "## x\n\nhi")], budget)
    assert any("not allowed" in v for v in violations)
    # disallowed section
    tight = EditBudget(allowed_sections=["tool_selection"])
    violations = validate_patches(
        [Patch(PatchOperation.REPLACE, "tool_usage", "old", "new")], tight
    )
    assert any("not editable" in v for v in violations)
    # token budget
    fat = EditBudget(max_tokens_added=5)
    violations = validate_patches(
        [Patch(PatchOperation.REPLACE, "tool_usage", "a b", "x " * 10)], fat
    )
    assert any("max_tokens_added" in v for v in violations)
    # missing old_value on REPLACE
    violations = validate_patches([Patch(PatchOperation.REPLACE, "tool_usage")], EditBudget())
    assert any("old_value" in v for v in violations)
    assert validate_patches([], EditBudget()) == []


# --- optimizer --------------------------------------------------------------------------------


def test_skill_optimizer_marks_out_of_budget_candidate_invalid():
    over_budget = [
        {
            "patches": [
                {"operation": "REPLACE", "path": "tool_usage", "old_value": "a", "new_value": "x " * 300}
            ],
            "reason": "rewrite",
        },
        _default_proposals()[0],
    ]
    optimizer = SkillOptimizer(_stub_model_fn(over_budget))
    budget = EditBudget(max_edits=3, max_tokens_added=50)
    candidates = optimizer.generate_candidates(
        task=SimpleNamespace(
            id="t1",
            diagnosis={"type": "SKILL_FAILURE"},
            trigger={"type": "production_bad_case"},
        ),
        target=SimpleNamespace(type=SimpleNamespace(value="SKILL"), base_version="v1", resource_id="s"),
        base_content=SKILL_CONTENT,
        budget=budget,
        count=2,
    )
    assert candidates[0].validation_status is CandidateValidationStatus.INVALID
    assert candidates[0].validation_errors
    assert candidates[1].validation_status is CandidateValidationStatus.VALID


def test_optimizer_without_model_fails_generation():
    service, _ = make_service(model_fn=None)
    register_target(service)
    task = make_task(service)
    # start_run 只做优化器预检（已注册即通过）；生成模型缺失在执行阶段暴露。
    started = service.start_run(task.id)
    run = service.execute_run(started.id)
    assert run.status is EvolutionRunStatus.FAILED
    assert "no generation model" in run.error


# --- selection --------------------------------------------------------------------------------


def test_lexicographic_selection_prefers_better_candidate():
    objectives = [
        OptimizationObjective(metric="pass_rate"),
        OptimizationObjective(metric="cost", direction="minimize"),
    ]
    a = [{"pass_rate": 0.9, "cost": 5.0}]
    b = [{"pass_rate": 0.9, "cost": 3.0}]
    c = [{"pass_rate": 0.8, "cost": 1.0}]
    assert lexicographic_compare(b, a, objectives) == -1
    assert lexicographic_compare(a, b, objectives) == 1
    assert lexicographic_compare(c, a, objectives) == 1  # first objective dominates


# --- run lifecycle ----------------------------------------------------------------------------


def test_run_human_review_then_approve_creates_version():
    service, _ = make_service()
    register_target(service)
    task = make_task(service)  # auto_release defaults to False
    run = service.start_run(task.id)
    assert run.status is EvolutionRunStatus.CREATED

    executed = service.execute_run(run.id)
    assert executed.status is EvolutionRunStatus.WAITING_APPROVAL
    assert executed.decision.decision is DecisionType.HUMAN_REVIEW

    candidates = service.list_candidates(run.id)
    assert len(candidates) == 2
    assert all(c.validation_status is CandidateValidationStatus.VALID for c in candidates)
    assert any(e.event_type == "ApprovalRequired" for e in service.list_events(run.id))

    approved = service.approve(run.id, approver="alice", reason="evidence looks good")
    assert approved.status is EvolutionRunStatus.COMPLETED
    assert approved.decision.decision is DecisionType.ACCEPT
    assert approved.decision.approval.approver == "alice"

    versions = service.list_versions("SKILL", "search_skill")
    assert [v.version for v in versions] == ["v1", "v2"]
    v2 = versions[1]
    assert v2.parent_version == "v1"
    assert v2.deployment_status is DeploymentStatus.ACTIVE
    assert "include links" in v2.content
    # 证据链（spec section 20）
    assert approved.evidence["decision"]["decision"] == "ACCEPT"
    assert approved.evidence["evaluation_results"]
    assert any(e.event_type == "VersionCreated" for e in service.list_events(run.id))


def test_run_human_review_reject_keeps_candidates():
    service, _ = make_service()
    register_target(service)
    started = service.start_run(make_task(service).id)
    run = service.execute_run(started.id)
    assert run.status is EvolutionRunStatus.WAITING_APPROVAL
    rejected = service.reject(run.id, approver="bob", reason="not convinced")
    assert rejected.status is EvolutionRunStatus.COMPLETED
    assert rejected.decision.decision is DecisionType.REJECT
    assert rejected.decision.approval.decision == "rejected"
    assert len(service.list_candidates(run.id)) == 2
    assert service.list_versions("SKILL", "search_skill")[0].version == "v1"


def test_auto_release_accepts_low_risk_without_approval():
    service, gateway = make_service()
    register_target(service)
    task = make_task(service, auto_release=True, risk_level="LOW")
    started = service.start_run(task.id)
    run = service.execute_run(started.id)
    assert run.status is EvolutionRunStatus.COMPLETED
    assert run.decision.decision is DecisionType.ACCEPT
    assert run.decision.approval is None
    # rollback restores v1 without regenerating (spec section 19)
    versions_before = {v.version: v for v in service.list_versions("SKILL", "search_skill")}
    v2_content = versions_before["v2"].content
    record = service.rollback(versions_before["v1"].id, actor="ops", reason="regression suspected")
    assert record.kind is DeploymentKind.ROLLBACK
    versions = {v.version: v for v in service.list_versions("SKILL", "search_skill")}
    assert versions["v1"].deployment_status is DeploymentStatus.ACTIVE
    assert versions["v2"].deployment_status is DeploymentStatus.SUPERSEDED
    assert versions["v2"].content == v2_content  # 内容未被改写


def test_high_risk_requires_human_review_even_with_auto_release():
    service, _ = make_service()
    register_target(service)
    task = make_task(service, auto_release=True, risk_level="HIGH")
    started = service.start_run(task.id)
    run = service.execute_run(started.id)
    assert run.status is EvolutionRunStatus.WAITING_APPROVAL


def test_failed_gate_blocks_auto_release():
    service, _ = make_service(gate_action="BLOCK")
    register_target(service)
    task = make_task(service, auto_release=True, gate_id="gate-1")
    started = service.start_run(task.id)
    run = service.execute_run(started.id)
    assert run.status is EvolutionRunStatus.WAITING_APPROVAL
    assert run.decision.gate_result["action"] == "BLOCK"


def test_no_candidate_passing_constraints_rejects():
    service, _ = make_service()
    register_target(service)
    task = make_task(
        service,
        objective={
            "hard_constraints": [{"metric": "pass_rate", "op": ">=", "threshold": 2.0}],
        },
    )
    started = service.start_run(task.id)
    run = service.execute_run(started.id)
    assert run.status is EvolutionRunStatus.COMPLETED
    assert run.decision.decision is DecisionType.REJECT
    assert "NO_ACCEPTABLE_CANDIDATE" in run.decision.reason
    assert len(run.decision.rejected_candidate_ids) == 2


def test_invalid_candidates_are_never_experimented():
    proposals = _default_proposals() + [
        {
            "patches": [
                {"operation": "REPLACE", "path": "tool_usage", "old_value": "a", "new_value": "x " * 500}
            ],
            "reason": "bloat",
        }
    ]
    service, gateway = make_service(proposals=proposals)
    register_target(service)
    task = make_task(service, strategy={"type": "MULTI_CANDIDATE", "candidate_count": 3})
    started = service.start_run(task.id)
    run = service.execute_run(started.id)
    invalid = [c for c in service.list_candidates(run.id) if c.validation_status is CandidateValidationStatus.INVALID]
    assert len(invalid) == 1
    experimented = {agent_version for _, agent_version in gateway.calls}
    assert not any(cid in experimented for cid in [c.id for c in invalid])
    assert all(
        c.validation_status is CandidateValidationStatus.VALID
        for c in service.list_candidates(run.id)
        if f"candidate:{c.id}" in experimented
    )


def test_generation_failure_isolates_production():
    def boom(prompt):
        raise RuntimeError("model unavailable")

    service, _ = make_service(model_fn=boom)
    register_target(service)
    task = make_task(service)
    started = service.start_run(task.id)
    run = service.execute_run(started.id)
    assert run.status is EvolutionRunStatus.FAILED
    assert "model unavailable" in run.error
    # 失败隔离：生产版本未受影响（spec section 29）
    versions = service.list_versions("SKILL", "search_skill")
    assert len(versions) == 1 and versions[0].version == "v1"


def test_start_run_is_idempotent_while_active():
    service, _ = make_service()
    register_target(service)
    task = make_task(service)
    first = service.start_run(task.id)
    second = service.start_run(task.id)
    assert first.id == second.id


def test_start_run_without_target_version_raises():
    service, _ = make_service()
    task = make_task(service)
    try:
        service.start_run(task.id)
        raise AssertionError("expected NotFoundError")
    except NotFoundError:
        pass


def test_regression_and_challenge_assets_are_experimented():
    service, gateway = make_service()
    register_target(service)
    task = make_task(service, regression=True, challenge=True)
    started = service.start_run(task.id)
    run = service.execute_run(started.id)
    assert run.status in (EvolutionRunStatus.COMPLETED, EvolutionRunStatus.WAITING_APPROVAL)
    suite_ids = {suite for suite, _ in gateway.calls}
    assert suite_ids == {"suite-validation", "suite-regression", "suite-challenge"}
    purposes = {e.purpose for e in service.list_experiments(run.id)}
    assert purposes == {"VALIDATION", "REGRESSION", "CHALLENGE"}


# --- store contract ---------------------------------------------------------------------------


def test_sqlalchemy_store_roundtrip(tmp_path):
    store = SQLEvolutionStore(f"sqlite:///{tmp_path / 'evo.db'}")
    service, _ = make_service()
    register_target(service)
    task = make_task(service)
    store.save_task(task)
    assert store.get_task(task.id).objective.hard_constraints[0].metric == "pass_rate"

    run = service.start_run(task.id)
    store.save_run(run)
    loaded = store.get_run(run.id)
    assert loaded.status is EvolutionRunStatus.CREATED
    assert store.list_runs(task.id)[0].id == run.id

    store.save_candidate(
        EvolutionCandidate(id="c-x", task_id=task.id, evolution_run_id=run.id)
    )
    assert store.get_candidate("c-x").evolution_run_id == run.id

    store.save_version(
        EvolutionVersion(id="ver-1", target_type="SKILL", resource_id="s", version="v1")
    )
    assert store.list_versions_for_target("SKILL", "s")[0].version == "v1"
    store.record_deployment(
        DeploymentRecord(id="d-1", target_type="SKILL", resource_id="s", version_id="ver-1")
    )
    assert store.list_deployments_for_target("SKILL", "s")[0].version_id == "ver-1"
    store.save_experiment(Experiment(id="e-1", evolution_run_id=run.id, candidate_id="c-x"))
    assert store.list_experiments_for_run(run.id)[0].candidate_id == "c-x"
    store.save_decision(EvolutionDecision(evolution_run_id=run.id))
    store.append_event(EvolutionEvent(evolution_run_id=run.id, event_type="EvolutionStarted"))
    assert store.list_events_for_run(run.id)[0].event_type == "EvolutionStarted"

    assert create_evolution_store("sqlite:///:memory:") is not None


# --- API --------------------------------------------------------------------------------------


def test_evolution_api_end_to_end(tmp_path):
    from agent_platform.api.app import create_app
    from agent_platform.evaluation.evaluators import EvaluatorRegistry, RuleEvaluator
    from agent_platform.runtime.core import RunStatus, SessionManager
    from agent_platform.runtime.dispatch import RuntimeOrchestrator, create_runtime_store
    from agent_platform.runtime.dispatch import tasks as dispatch_tasks
    from test_evaluation import StubAgentAdapter, _evaluation_service_over

    url = f"sqlite:///{tmp_path / 'api-evo.db'}"
    runtime_store = create_runtime_store(url)
    dispatch_tasks.set_orchestrator_builder(
        lambda: RuntimeOrchestrator(
            runtime_store, agent_adapter=StubAgentAdapter(runtime_store)
        )
    )
    dispatch_tasks.set_evaluation_runner_builder(
        lambda: _evaluation_service_over(url, runtime_store)
    )

    def _evolution_builder():
        from agent_platform.evolution.experiment import (
            EvaluationServiceGateway,
            ExperimentRunner,
        )
        from agent_platform.evolution.optimizers import SkillOptimizer

        store = create_evolution_store(url)
        optimizer = SkillOptimizer(_stub_model_fn())
        runner = ExperimentRunner(
            EvaluationServiceGateway(_evaluation_service_over(url, runtime_store)), store
        )
        return EvolutionService(store, runner, optimizers={optimizer.name: optimizer})

    dispatch_tasks.set_evolution_runner_builder(_evolution_builder)
    try:
        client = TestClient(
            create_app(Settings(database_url=url, celery_task_always_eager=True))
        )
        application = SessionManager(runtime_store).create_application(name="app")

        eval_task = client.post(
            "/api/v1/evaluation/tasks",
            json={
                "name": "demo",
                "input": {"message": "hi"},
                "success_criteria": [{"type": "required_fields", "fields": ["final"]}],
                "process_assertions": [{"type": "tool_called", "tool": "search"}],
            },
        )
        eval_task_id = eval_task.json()["id"]
        suite = client.post(
            "/api/v1/evaluation/suites",
            json={"name": "validation", "task_ids": [eval_task_id]},
        ).json()
        gate = client.post(
            "/api/v1/evaluation/gates",
            json={"name": "release", "rules": [{"metric": "pass_rate", "threshold": 0.9}]},
        ).json()

        target = client.post(
            "/api/v1/evolution/targets",
            json={"target_type": "SKILL", "resource_id": "search_skill", "content": SKILL_CONTENT},
        )
        assert target.status_code == 201
        assert target.json()["version"] == "v1"

        task_response = client.post(
            "/api/v1/evolution/tasks",
            json={
                "name": "improve search skill",
                "target_type": "SKILL",
                "resource_id": "search_skill",
                "trigger": {"type": "production_bad_case"},
                "diagnosis": {"type": "SKILL_FAILURE"},
                "evaluation_assets": {
                    "application_id": application.id,
                    "suites": {"validation": suite["id"]},
                    "gate_id": gate["id"],
                },
                "strategy": {"type": "MULTI_CANDIDATE", "candidate_count": 1},
                "auto_release": True,
            },
        )
        assert task_response.status_code == 201
        task_id = task_response.json()["id"]

        run_response = client.post(f"/api/v1/evolution/tasks/{task_id}/runs")
        assert run_response.status_code == 201
        run = run_response.json()
        assert run["status"] == "COMPLETED"
        assert run["decision"]["decision"] == "ACCEPT"
        assert run["candidates"]
        assert run["events"]

        task_detail = client.get(
            f"/api/v1/evolution/tasks/{task_id}"
        )
        assert task_detail.status_code == 200

        # idempotent start: task already terminal -> a second run is allowed,
        # but while the first was ACTIVE the same run would be returned.
        rollback = client.post(
            f"/api/v1/evolution/versions/{target.json()['id']}/rollback",
            json={"actor": "ops", "reason": "drill"},
        )
        assert rollback.status_code == 200
        assert rollback.json()["kind"] == "ROLLBACK"

        missing = client.get(f"/api/v1/evolution/runs/{uuid4()}")
        assert missing.status_code == 404
    finally:
        dispatch_tasks.set_orchestrator_builder(None)
        dispatch_tasks.set_evaluation_runner_builder(None)
        dispatch_tasks.set_evolution_runner_builder(None)


def test_evolution_api_rejects_invalid_task(tmp_path):
    from agent_platform.api.app import create_app

    url = f"sqlite:///{tmp_path / 'api-invalid.db'}"
    client = TestClient(create_app(Settings(database_url=url)))
    response = client.post(
        "/api/v1/evolution/tasks",
        json={"name": "broken", "evaluation_assets": {"application_id": "app-1"}},
    )
    # 资产/策略配置不完整属于请求校验错误（422），而非服务器故障。
    assert response.status_code == 422
