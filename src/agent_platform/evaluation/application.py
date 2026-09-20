"""Evaluation application services (plan 050 Phase 0/1/3/5/10 for V1).

EvaluationService 管理评测资产（Task/Rubric/Suite/Asset/Environment）、
驱动离线评测 Run（通过 harness.TrialRunner）并汇总 summary；
Quality Gate 检查基于 run summary（架构文档 section 22）。

依赖：Runtime Core（events/managers/models/stores）+ 注入的 dispatcher；
不导入 Agent/Workflow 执行模块、不导入 infrastructure（组合根接线）。
"""

import logging
import math
from dataclasses import replace

from agent_platform.errors import InvalidStateTransitionError, NotFoundError
from agent_platform.evaluation.domain import (
    ASSET_REGRESSION,
    EvaluationAsset,
    EvaluationEnvironment,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationSuite,
    GateAction,
    GateDecision,
    GateRule,
    GateRuleResult,
    GateSeverity,
    QualityGate,
    Rubric,
    Task,
    TrialStatus,
    new_id,
    utcnow,
)
from agent_platform.evaluation.harness import TrialRunner
from agent_platform.runtime.core.events import RuntimeEventType
from agent_platform.runtime.core.stores import RuntimeStore
from agent_platform.evaluation.storage import EvaluationStore

logger = logging.getLogger(__name__)

_VALID_RUNTIME_TYPES = {"agent", "workflow"}


class EvaluationService:
    def __init__(
        self,
        runtime_store: RuntimeStore,
        evaluation_store: EvaluationStore,
        trial_runner: TrialRunner,
    ) -> None:
        self._runtime_store = runtime_store
        self._store = evaluation_store
        self._trials = trial_runner

    # --- assets ----------------------------------------------------------------

    def create_task(
        self, *, name: str, payload: dict | None = None
    ) -> Task:
        data = payload or {}
        task = Task(
            id=new_id(),
            name=name,
            input=data.get("input") or {},
            user_context=data.get("user_context") or {},
            expected_behavior=_expected_behavior(data.get("expected_behavior")),
            success_criteria=list(data.get("success_criteria") or []),
            process_assertions=list(data.get("process_assertions") or []),
            outcome_assertions=list(data.get("outcome_assertions") or []),
            environment_id=data.get("environment_id"),
            suite_id=data.get("suite_id"),
            metadata=data.get("metadata") or {},
        )
        self._store.save_task(task)
        return task

    def get_task(self, task_id: str) -> Task:
        return self._require(self._store.get_task(task_id), f"task not found: {task_id}")

    def list_tasks(self) -> list[Task]:
        return self._store.list_tasks()

    def create_rubric(self, *, name: str, payload: dict | None = None) -> Rubric:
        from agent_platform.evaluation.domain import Criterion

        data = payload or {}
        rubric = Rubric(
            id=new_id(),
            name=name,
            dimension=data.get("dimension") or "",
            criteria=[
                Criterion(
                    id=c.get("id") or new_id(),
                    description=c.get("description") or "",
                    type=c.get("type") or "BINARY",
                    rule=c.get("rule") or {},
                    weight=float(c.get("weight", 1.0)),
                    expected_evidence=list(c.get("expected_evidence") or []),
                )
                for c in data.get("criteria") or []
            ],
            aggregation=data.get("aggregation") or {"strategy": "all_pass"},
        )
        self._store.save_rubric(rubric)
        return rubric

    def get_rubric(self, rubric_id: str) -> Rubric:
        return self._require(self._store.get_rubric(rubric_id), f"rubric not found: {rubric_id}")

    def list_rubrics(self) -> list[Rubric]:
        return self._store.list_rubrics()

    def create_suite(self, *, name: str, payload: dict | None = None) -> EvaluationSuite:
        data = payload or {}
        task_ids = list(data.get("task_ids") or [])
        for task_id in task_ids:
            self.get_task(task_id)  # referential validation
        suite = EvaluationSuite(
            id=new_id(),
            name=name,
            type=data.get("type") or "E2E",
            task_ids=task_ids,
            rubric_ids=list(data.get("rubric_ids") or []),
            environment_id=data.get("environment_id"),
        )
        self._store.save_suite(suite)
        return suite

    def get_suite(self, suite_id: str) -> EvaluationSuite:
        return self._require(self._store.get_suite(suite_id), f"suite not found: {suite_id}")

    def list_suites(self) -> list[EvaluationSuite]:
        return self._store.list_suites()

    def create_asset(self, *, name: str, payload: dict | None = None) -> EvaluationAsset:
        data = payload or {}
        task_ids = list(data.get("task_ids") or [])
        for task_id in task_ids:
            self.get_task(task_id)
        asset = EvaluationAsset(
            id=new_id(),
            name=name,
            type=data.get("type") or "GOLDEN",
            task_ids=task_ids,
            suite_id=data.get("suite_id"),
            source=data.get("source") or "",
            metadata=data.get("metadata") or {},
        )
        self._store.save_asset(asset)
        return asset

    def get_asset(self, asset_id: str) -> EvaluationAsset:
        return self._require(self._store.get_asset(asset_id), f"asset not found: {asset_id}")

    def list_assets(self, asset_type: str | None = None) -> list[EvaluationAsset]:
        return self._store.list_assets(asset_type)

    def create_environment(
        self, *, name: str, payload: dict | None = None
    ) -> EvaluationEnvironment:
        data = payload or {}
        environment = EvaluationEnvironment(
            id=new_id(),
            name=name,
            version=str(data.get("version") or "1"),
            config=data.get("config") or {},
        )
        self._store.save_environment(environment)
        return environment

    def get_environment(self, environment_id: str) -> EvaluationEnvironment:
        return self._require(
            self._store.get_environment(environment_id),
            f"environment not found: {environment_id}",
        )

    # --- offline evaluation ------------------------------------------------------

    def create_evaluation_run(
        self,
        *,
        suite_id: str,
        application_id: str,
        runtime_type: str = "agent",
        agent_version: str = "dev",
        environment_id: str | None = None,
        trials_per_task: int = 1,
        evaluator_ids: list[str] | None = None,
    ) -> EvaluationRun:
        """Validate inputs and persist one evaluation run in RUNNING status.

        只创建不执行：执行由 run_evaluation_run 驱动（API 经 dispatch
        派发到 worker，evaluation-spec.md section 32 异步派发）。
        """
        if runtime_type not in _VALID_RUNTIME_TYPES:
            raise ValueError(f"runtime_type must be one of {sorted(_VALID_RUNTIME_TYPES)}")
        suite = self.get_suite(suite_id)
        self._require(
            self._runtime_store.get_application(application_id),
            f"application not found: {application_id}",
        )
        environment_version = "unversioned"
        if environment_id is not None:
            environment_version = self.get_environment(environment_id).version
        elif suite.environment_id is not None:
            environment_id = suite.environment_id
            environment_version = self.get_environment(suite.environment_id).version

        run = EvaluationRun(
            id=new_id(),
            suite_id=suite.id,
            application_id=application_id,
            runtime_type=runtime_type,
            agent_version=agent_version,
            environment_id=environment_id,
            environment_version=environment_version,
            trials_per_task=max(1, int(trials_per_task)),
            evaluator_ids=list(evaluator_ids or ["rule"]),
        )
        self._store.save_evaluation_run(run)
        return run

    def run_evaluation_run(self, run_id: str) -> EvaluationRun:
        """Execute every trial of a persisted RUNNING evaluation run and finalize.

        Worker 侧入口（dispatch task 调用）；同步驱动 trial 循环，
        每个 Trial 的 Runtime Run 仍走标准 dispatch 路径。
        """
        run = self.get_evaluation_run(run_id)
        if run.status is not EvaluationRunStatus.RUNNING:
            if run.status is EvaluationRunStatus.CANCELLED:
                # Cancelled between creation and execution (API cancel /
                # another thread): graceful no-op, consistent with the
                # between-trials cancellation check below.
                run = replace(run, finished_at=utcnow())
                self._store.save_evaluation_run(run)
                return run
            raise InvalidStateTransitionError(
                f"cannot execute evaluation run from {run.status.value}"
            )
        rubrics = [self.get_rubric(r) for r in self.get_suite(run.suite_id).rubric_ids]
        try:
            for task_id in self.get_suite(run.suite_id).task_ids:
                task = self.get_task(task_id)
                # Between-trials cancellation check (cancel from another
                # thread/process takes effect at the next trial boundary).
                current = self._store.get_evaluation_run(run.id)
                if current is not None and current.status is EvaluationRunStatus.CANCELLED:
                    run = replace(current, finished_at=utcnow())
                    self._store.save_evaluation_run(run)
                    return run
                for attempt in range(1, run.trials_per_task + 1):
                    self._trials.execute_trial(run, task, attempt, rubrics=rubrics)
        except Exception as exc:  # noqa: BLE001 - harness failures finish the run
            logger.exception("evaluation run %s failed", run.id)
            run = replace(
                self._store.get_evaluation_run(run.id) or run,
                status=EvaluationRunStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                finished_at=utcnow(),
            )
            self._store.save_evaluation_run(run)
            return run

        current = self._store.get_evaluation_run(run.id)
        if current is not None and current.status is EvaluationRunStatus.CANCELLED:
            run = replace(current, finished_at=utcnow())
            self._store.save_evaluation_run(run)
            return run
        run = replace(
            self._store.get_evaluation_run(run.id) or run,
            status=EvaluationRunStatus.COMPLETED,
            finished_at=utcnow(),
        )
        run = replace(run, summary=self.summarize(run.id))
        self._store.save_evaluation_run(run)
        return run

    def start_evaluation_run(
        self,
        *,
        suite_id: str,
        application_id: str,
        runtime_type: str = "agent",
        agent_version: str = "dev",
        environment_id: str | None = None,
        trials_per_task: int = 1,
        evaluator_ids: list[str] | None = None,
    ) -> EvaluationRun:
        """Create and synchronously execute one evaluation run (inline)."""
        run = self.create_evaluation_run(
            suite_id=suite_id,
            application_id=application_id,
            runtime_type=runtime_type,
            agent_version=agent_version,
            environment_id=environment_id,
            trials_per_task=trials_per_task,
            evaluator_ids=evaluator_ids,
        )
        return self.run_evaluation_run(run.id)

    # --- regression patrol（spec section 22 巡检; plan-productionization G4） ---

    def run_regression_patrol(
        self,
        *,
        asset_name: str,
        application_id: str,
        agent_version: str = "patrol",
        evaluator_ids: list[str] | None = None,
    ) -> EvaluationRun:
        """Replay every task of a REGRESSION asset as one patrol evaluation run.

        巡检（INSPECTION）：把 regression-set 资产沉淀的任务集周期性重放。
        资产任务集每次同步进固定名称的 patrol suite（不存在则创建），
        之后完全复用 create/start_evaluation_run 流程；调用方（celery
        patrol 任务）负责提供 application_id 并按需调度。
        """
        asset = next(
            (
                a
                for a in self._store.list_assets(ASSET_REGRESSION)
                if a.name == asset_name
            ),
            None,
        )
        if asset is None:
            raise NotFoundError(f"regression asset not found: {asset_name}")
        if not asset.task_ids:
            raise ValueError(f"regression asset {asset_name} has no tasks")
        suite_name = f"patrol-{asset_name}"
        suite = next(
            (s for s in self._store.list_suites() if s.name == suite_name), None
        )
        if suite is None:
            suite = EvaluationSuite(
                id=new_id(),
                name=suite_name,
                type="E2E",
                task_ids=list(asset.task_ids),
            )
        else:
            suite = replace(suite, task_ids=list(asset.task_ids))
        self._store.save_suite(suite)
        return self.start_evaluation_run(
            suite_id=suite.id,
            application_id=application_id,
            agent_version=agent_version,
            evaluator_ids=evaluator_ids or ["rule"],
        )

    def get_evaluation_run(self, run_id: str) -> EvaluationRun:
        return self._require(
            self._store.get_evaluation_run(run_id), f"evaluation run not found: {run_id}"
        )

    def list_evaluation_runs(self, suite_id: str | None = None) -> list[EvaluationRun]:
        return self._store.list_evaluation_runs(suite_id)

    def cancel_evaluation_run(self, run_id: str) -> EvaluationRun:
        run = self.get_evaluation_run(run_id)
        if run.status is EvaluationRunStatus.RUNNING:
            run = replace(run, status=EvaluationRunStatus.CANCELLED)
            self._store.save_evaluation_run(run)
        return run

    def list_results(self, run_id: str) -> list[EvaluationResult]:
        self.get_evaluation_run(run_id)
        return self._store.list_results_for_run(run_id)

    # --- summary -----------------------------------------------------------------

    def summarize(self, run_id: str) -> dict:
        """Aggregate run-level summary (spec section 11)。

        pass_rate 基于所有 Trial；pass@k 为无偏估计，k = trials_per_task。
        latency/token_usage 聚合自 Runtime Run 与事件。
        """
        run = self.get_evaluation_run(run_id)
        trials = self._store.list_trials_for_run(run_id)
        evaluated = [t for t in trials if t.status is not TrialStatus.RUNNING]

        per_task: dict[str, dict] = {}
        for trial in evaluated:
            stats = per_task.setdefault(
                trial.task_id, {"trials": 0, "passed": 0, "pass_rate": 0.0, "pass_at_k": 0.0}
            )
            stats["trials"] += 1
            if trial.status is TrialStatus.PASSED:
                stats["passed"] += 1
        for task_id, stats in per_task.items():
            stats["pass_rate"] = stats["passed"] / stats["trials"] if stats["trials"] else 0.0
            stats["pass_at_k"] = _pass_at_k(
                stats["trials"], stats["passed"], run.trials_per_task
            )

        total = len(evaluated)
        passed = sum(1 for t in evaluated if t.status is TrialStatus.PASSED)
        latencies = [
            (t.finished_at - t.started_at).total_seconds()
            for t in evaluated
            if t.finished_at is not None
        ]
        return {
            "pass_rate": passed / total if total else 0.0,
            "pass_at_k": _pass_at_k(total, passed, run.trials_per_task) if total else 0.0,
            "trials": total,
            "passed": passed,
            "per_task": per_task,
            "latency": {
                "avg_s": sum(latencies) / len(latencies) if latencies else 0.0,
                "max_s": max(latencies) if latencies else 0.0,
            },
            "token_usage": self._token_usage(run_id),
        }

    def _token_usage(self, run_id: str) -> dict:
        input_tokens = 0
        output_tokens = 0
        for trial in self._store.list_trials_for_run(run_id):
            for event in self._runtime_store.list_events(trial.run_id):
                if event.event_type is not RuntimeEventType.LLM_COMPLETED:
                    continue
                input_tokens += int(event.payload.get("input_tokens", 0) or 0)
                output_tokens += int(event.payload.get("output_tokens", 0) or 0)
        return {"input": input_tokens, "output": output_tokens}

    # --- quality gate ---------------------------------------------------------------

    def create_gate(self, *, name: str, payload: dict | None = None) -> QualityGate:
        data = payload or {}
        gate = QualityGate(
            id=new_id(),
            name=name,
            rules=[
                GateRule(
                    id=r.get("id") or new_id(),
                    metric=r["metric"],
                    threshold=float(r["threshold"]),
                    op=r.get("op") or ">=",
                    severity=GateSeverity(r.get("severity") or "BLOCK"),
                )
                for r in data.get("rules") or []
            ],
        )
        self._store.save_gate(gate)
        return gate

    def get_gate(self, gate_id: str) -> QualityGate:
        return self._require(self._store.get_gate(gate_id), f"gate not found: {gate_id}")

    def list_gates(self) -> list[QualityGate]:
        return self._store.list_gates()

    def check_gate(self, gate_id: str, run_id: str) -> GateDecision:
        """Release check: any failed BLOCK rule blocks the release (spec section 27)."""
        gate = self.get_gate(gate_id)
        summary = self.get_evaluation_run(run_id).summary
        rule_results = [_check_rule(rule, summary) for rule in gate.rules]
        action = (
            GateAction.BLOCK
            if any(
                not r.passed and r.severity is GateSeverity.BLOCK for r in rule_results
            )
            else GateAction.PASS
        )
        return GateDecision(gate_id=gate.id, action=action, rule_results=rule_results)

    # --- helpers -------------------------------------------------------------

    def _require(self, value, message: str):
        if value is None:
            raise NotFoundError(message)
        return value


def _expected_behavior(data: dict | None):
    from agent_platform.evaluation.domain import ExpectedBehavior

    if not data:
        return ExpectedBehavior()
    return ExpectedBehavior(
        description=data.get("description") or "",
        required_actions=list(data.get("required_actions") or []),
        forbidden_actions=list(data.get("forbidden_actions") or []),
        expected_outcome=data.get("expected_outcome") or {},
    )


def _pass_at_k(n: int, passed: int, k: int) -> float:
    """Unbiased pass@k estimator: 1 - C(n-p, k) / C(n, k)."""
    k = max(1, min(k, n))
    if n - passed < k:
        return 1.0
    return 1.0 - math.comb(n - passed, k) / math.comb(n, k)


def _resolve_metric(summary: dict, metric: str) -> float | None:
    if metric.startswith("task_pass_rate:"):
        task_id = metric.split(":", 1)[1]
        stats = (summary.get("per_task") or {}).get(task_id)
        return stats.get("pass_rate") if stats else None
    value = summary.get(metric)
    return value if isinstance(value, (int, float)) else None


def _compare(value: float, op: str, threshold: float) -> bool:
    return {
        ">=": value >= threshold,
        "<=": value <= threshold,
        ">": value > threshold,
        "<": value < threshold,
        "==": value == threshold,
    }.get(op, False)


def _check_rule(rule: GateRule, summary: dict) -> GateRuleResult:
    value = _resolve_metric(summary, rule.metric)
    if value is None:
        return GateRuleResult(
            rule_id=rule.id, metric=rule.metric, value=None,
            threshold=rule.threshold, op=rule.op, severity=rule.severity,
            passed=False, detail="metric missing from summary",
        )
    passed = _compare(value, rule.op, rule.threshold)
    return GateRuleResult(
        rule_id=rule.id, metric=rule.metric, value=value,
        threshold=rule.threshold, op=rule.op, severity=rule.severity,
        passed=passed,
        detail=f"{rule.metric}={value} {rule.op} {rule.threshold} -> {'ok' if passed else 'failed'}",
    )
