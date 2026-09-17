"""Evolution application service（plan 060 Phase 0-11 / spec sections 9-21）。

EvolutionService 编排完整进化循环：
PLANNING -> GENERATING -> EXPERIMENTING -> EVALUATING -> SELECTING
-> GATING -> DECIDING（-> HUMAN_REVIEW -> ACCEPT/REJECT）。

边界（architecture doc section 16）：
* 评测通过 EvaluationGateway 调用，不实现评测引擎；
* 不执行 Agent/Workflow/工具，不修改生产配置 —— 产出是不可变版本 + 证据；
* 失败隔离：Run 失败不影响生产状态，被拒候选保留为历史证据。
"""

import logging
from dataclasses import replace

from agent_platform.errors import InvalidStateTransitionError, NotFoundError
from agent_platform.evolution import governance
from agent_platform.evolution.domain.candidate import (
    CandidateValidationStatus,
    EvolutionCandidate,
)
from agent_platform.evolution.domain.run import (
    Approval,
    DecisionType,
    DeploymentKind,
    DeploymentRecord,
    DeploymentStatus,
    EvolutionDecision,
    EvolutionEvent,
    EvolutionRun,
    EvolutionRunStatus,
    EvolutionVersion,
    ExperimentStatus,
    is_terminal,
)
from agent_platform.evolution.domain.task import (
    EvolutionTask,
    EvolutionTarget,
    RiskLevel,
    TargetType,
    TaskStatus,
    new_id,
    utcnow,
)
from agent_platform.evolution.experiment import ExperimentRunner
from agent_platform.evolution.optimizers import (
    OptimizerError,
    resolve_optimizer_name,
)
from agent_platform.evolution.patching import apply_patches, validate_patches
from agent_platform.evolution.selection import (
    hard_constraint_filter,
    select_best,
)
from agent_platform.evolution.storage import EvolutionStore
from agent_platform.evolution.strategies import build_strategy

logger = logging.getLogger(__name__)

_REQUIRED_SUITE_PURPOSES = ("validation",)


class EvolutionService:
    def __init__(
        self,
        store: EvolutionStore,
        experiments: ExperimentRunner,
        optimizers: dict | None = None,
    ) -> None:
        self._store = store
        self._experiments = experiments
        self._optimizers = dict(optimizers or {})

    # --- targets & versions -----------------------------------------------------

    def register_target(
        self,
        *,
        target_type: str,
        resource_id: str,
        content: str,
        risk_level: str = "LOW",
        created_by: str = "",
    ) -> EvolutionVersion:
        """注册进化目标并写入种子版本 v1（候选版本的基线）。"""
        if not resource_id.strip():
            raise ValueError("resource_id must not be empty")
        target_type_enum = TargetType(target_type)
        version = EvolutionVersion(
            target_type=target_type_enum.value,
            resource_id=resource_id,
            version="v1",
            content=content,
            deployment_status=DeploymentStatus.ACTIVE,
        )
        self._store.save_version(version)
        self._store.record_deployment(
            DeploymentRecord(
                target_type=version.target_type,
                resource_id=resource_id,
                version_id=version.id,
                kind=DeploymentKind.RELEASE,
                actor=created_by,
                reason="base version registration",
            )
        )
        return version

    def get_version(self, version_id: str) -> EvolutionVersion:
        return self._require(self._store.get_version(version_id), f"version not found: {version_id}")

    def list_versions(self, target_type: str, resource_id: str) -> list[EvolutionVersion]:
        return self._store.list_versions_for_target(target_type, resource_id)

    def _active_version(self, target_type: str, resource_id: str) -> EvolutionVersion | None:
        active = [
            v
            for v in self._store.list_versions_for_target(target_type, resource_id)
            if v.deployment_status is DeploymentStatus.ACTIVE
        ]
        return active[0] if active else None

    # --- tasks --------------------------------------------------------------------

    def create_task(self, *, name: str, payload: dict | None = None) -> EvolutionTask:
        data = payload or {}
        assets = dict(data.get("evaluation_assets") or {})
        suites = dict(assets.get("suites") or {})
        missing = [p for p in _REQUIRED_SUITE_PURPOSES if not suites.get(p)]
        if not assets.get("application_id"):
            raise ValueError("evaluation_assets.application_id is required")
        if missing:
            raise ValueError(f"evaluation_assets.suites requires purposes: {missing}")
        strategy_data = dict(data.get("strategy") or {})
        strategy = _build_strategy_config(strategy_data)
        objective = _build_objective(data.get("objective") or {})
        target = EvolutionTarget(
            type=TargetType(data.get("target_type") or "SKILL"),
            resource_id=str(data.get("resource_id") or ""),
            base_version=str(data.get("base_version") or ""),
            editable_scope=list(data.get("editable_scope") or []),
            risk_level=RiskLevel(data.get("target_risk_level") or data.get("risk_level") or "LOW"),
        )
        task = EvolutionTask(
            name=name,
            description=str(data.get("description") or ""),
            trigger=dict(data.get("trigger") or {}),
            diagnosis=dict(data.get("diagnosis") or {}),
            target=target,
            objective=objective,
            constraints=list(data.get("constraints") or []),
            evaluation_assets=assets,
            strategy=strategy,
            risk_level=RiskLevel(data.get("risk_level") or target.risk_level.value),
            auto_release=bool(data.get("auto_release") or False),
            status=TaskStatus.READY,
            created_by=str(data.get("created_by") or ""),
        )
        self._store.save_task(task)
        return task

    def get_task(self, task_id: str) -> EvolutionTask:
        return self._require(self._store.get_task(task_id), f"evolution task not found: {task_id}")

    def list_tasks(self) -> list[EvolutionTask]:
        return self._store.list_tasks()

    # --- runs ---------------------------------------------------------------------

    def start_run(self, task_id: str) -> EvolutionRun:
        """创建进化 Run 并转交 dispatch 执行（API 只创建，worker 驱动生命周期）。

        幂等性（spec section 29）：任务已有未终结 Run 时直接返回该 Run，
        重复 start 不会产生不受控的重复发布。
        """
        task = self.get_task(task_id)
        for existing in self._store.list_runs(task_id):
            if not is_terminal(existing.status):
                return existing
        self._validate_task_assets(task)
        optimizer_name = resolve_optimizer_name(task, task.strategy.type)
        self._resolve_optimizer(optimizer_name)  # 提前失败：优化器必须已注入
        active = self._active_version(task.target.type.value, task.target.resource_id)
        if active is None:
            raise NotFoundError(
                f"no registered version for target "
                f"{task.target.type.value}:{task.target.resource_id}; "
                "register_target first"
            )
        run = EvolutionRun(
            task_id=task.id,
            target_type=task.target.type.value,
            resource_id=task.target.resource_id,
            base_version=active.version,
            strategy_type=task.strategy.type.value,
            optimizer_name=optimizer_name,
            status=EvolutionRunStatus.CREATED,
            started_at=utcnow(),
        )
        self._store.save_run(run)
        self._set_task_status(task, TaskStatus.RUNNING)
        return run

    def execute_run(self, run_id: str) -> EvolutionRun:
        """驱动一个 CREATED Run 走完整生命周期（worker 侧入口）。"""
        run = self.get_run(run_id)
        if run.status is not EvolutionRunStatus.CREATED:
            logger.warning("evolution run %s already executed (%s)", run_id, run.status.value)
            return run
        task = self.get_task(run.task_id)
        try:
            return self._drive(run, task)
        except Exception as exc:  # noqa: BLE001 - 失败可发生在任意阶段（spec section 9）
            logger.exception("evolution run %s failed", run_id)
            failed = replace(
                self.get_run(run_id),
                status=EvolutionRunStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                completed_at=utcnow(),
            )
            self._store.save_run(failed)
            self._append_event(run_id, "EvolutionFailed", {"error": failed.error or ""})
            self._set_task_status(task, TaskStatus.FAILED)
            return failed

    def _drive(self, run: EvolutionRun, task: EvolutionTask) -> EvolutionRun:
        # PLANNING ------------------------------------------------------------
        run = replace(run, status=EvolutionRunStatus.PLANNING)
        self._store.save_run(run)
        self._append_event(run.id, "EvolutionStarted", {
            "task_id": task.id,
            "target": f"{run.target_type}:{run.resource_id}",
            "base_version": run.base_version,
            "strategy": run.strategy_type,
        })
        active = self._active_version(run.target_type, run.resource_id)
        if active is None or active.version != run.base_version:
            raise InvalidStateTransitionError(
                f"base version {run.base_version} is no longer active for "
                f"{run.target_type}:{run.resource_id} (target immutable during a run)"
            )
        optimizer = self._resolve_optimizer(run.optimizer_name)
        strategy = build_strategy(task.strategy, optimizer)

        # GENERATING ----------------------------------------------------------
        run = replace(run, status=EvolutionRunStatus.GENERATING)
        self._store.save_run(run)
        generated = strategy.generate(task, task.target, active.content, task.strategy)
        candidates: list[EvolutionCandidate] = []
        for candidate in generated:
            violations = validate_patches(candidate.patch, task.strategy.edit_budget)
            if violations and candidate.validation_status is CandidateValidationStatus.VALID:
                candidate = replace(
                    candidate,
                    validation_status=CandidateValidationStatus.INVALID,
                    validation_errors=violations,
                )
            candidate = replace(candidate, evolution_run_id=run.id)
            self._store.save_candidate(candidate)
            candidates.append(candidate)
        valid = [c for c in candidates if c.validation_status is CandidateValidationStatus.VALID]
        self._append_event(run.id, "CandidateGenerated", {
            "generated": len(candidates),
            "valid": len(valid),
            "rejected": [c.id for c in candidates if c.validation_status is not CandidateValidationStatus.VALID],
        })
        if not valid:
            decision = EvolutionDecision(
                evolution_run_id=run.id,
                decision=DecisionType.REJECT,
                rejected_candidate_ids=[c.id for c in candidates],
                reason="NO_ACCEPTABLE_CANDIDATE: no candidate passed pre-experiment validation",
            )
            return self._finalize(run, task, decision, evidence_experiments={})

        # EXPERIMENTING / EVALUATING ------------------------------------------
        run = replace(run, status=EvolutionRunStatus.EXPERIMENTING)
        self._store.save_run(run)
        metrics: dict[str, list[dict]] = {}
        experiments_by_candidate: dict[str, dict[str, object]] = {}
        for candidate in valid:
            experiments = self._experiments.run_candidate(run, task, candidate)
            experiments_by_candidate[candidate.id] = experiments
            summaries = [
                dict(experiments[p].summary or {})
                for p in ("VALIDATION", "REGRESSION", "CHALLENGE")
                if p in experiments
            ]
            metrics[candidate.id] = summaries
        run = replace(run, status=EvolutionRunStatus.EVALUATING)
        self._store.save_run(run)
        self._append_event(run.id, "EvaluationCompleted", {
            "candidates_evaluated": len(metrics),
        })

        # 可选候选必须具备已完成的验证实验才可参与选择。
        selectable = [
            c
            for c in valid
            if self._validation_completed(experiments_by_candidate.get(c.id, {}))
        ]

        # SELECTING -----------------------------------------------------------
        run = replace(run, status=EvolutionRunStatus.SELECTING)
        self._store.save_run(run)
        passed, rejected = hard_constraint_filter(
            selectable, task.objective.hard_constraints, metrics
        )
        best = select_best(passed, metrics, task.objective.optimization_objectives)
        self._append_event(run.id, "SelectionCompleted", {
            "selectable": [c.id for c in selectable],
            "passed_constraints": [c.id for c in passed],
            "rejected": rejected,
            "selected": best.id if best is not None else None,
        })

        # GATING --------------------------------------------------------------
        gate_result: dict | None = None
        gate_id = (task.evaluation_assets or {}).get("gate_id")
        if best is not None and gate_id:
            run = replace(run, status=EvolutionRunStatus.GATING)
            self._store.save_run(run)
            validation_experiment = experiments_by_candidate[best.id].get("VALIDATION")
            gate_result = self._experiments.check_gate(
                gate_id=gate_id,
                evaluation_run_id=validation_experiment.evaluation_run_id,  # type: ignore[union-attr]
            )
            self._append_event(run.id, "GateEvaluated", {"result": gate_result})

        # DECIDING --------------------------------------------------------------
        run = replace(run, status=EvolutionRunStatus.DECIDING)
        self._store.save_run(run)
        if best is None:
            decision = EvolutionDecision(
                evolution_run_id=run.id,
                decision=DecisionType.REJECT,
                rejected_candidate_ids=[c.id for c in candidates],
                gate_result=gate_result or {},
                reason="NO_ACCEPTABLE_CANDIDATE",
            )
            return self._finalize(run, task, decision, evidence_experiments=experiments_by_candidate)

        validation_experiment = experiments_by_candidate[best.id].get("VALIDATION")
        regression_ok = self._purpose_ok(experiments_by_candidate.get(best.id, {}), "REGRESSION")
        challenge_ok = self._purpose_ok(experiments_by_candidate.get(best.id, {}), "CHALLENGE")
        allowed, conditions, release_reason = governance.evaluate_auto_release(
            hard_constraints_pass=True,
            regression_pass=regression_ok,
            challenge_pass=challenge_ok,
            edit_budget_pass=all(
                c.validation_status is CandidateValidationStatus.VALID for c in candidates
            ),
            risk_level=task.risk_level,
            gate_result=gate_result,
            auto_release_enabled=task.auto_release,
        )
        if allowed:
            decision = EvolutionDecision(
                evolution_run_id=run.id,
                decision=DecisionType.ACCEPT,
                selected_candidate_id=best.id,
                rejected_candidate_ids=[
                    c.id for c in candidates if c.id != best.id
                ],
                gate_result=gate_result or {},
                reason=release_reason,
            )
            # 先发布版本再完成 Run，保证 VersionCreated 先于 EvolutionCompleted。
            self._create_version(run, task, best, validation_experiment, approval=None)
            return self._finalize(run, task, decision, evidence_experiments=experiments_by_candidate)

        decision = EvolutionDecision(
            evolution_run_id=run.id,
            decision=DecisionType.HUMAN_REVIEW,
            selected_candidate_id=best.id,
            rejected_candidate_ids=[c.id for c in candidates if c.id != best.id],
            gate_result=gate_result or {},
            reason=release_reason,
        )
        run = replace(run, status=EvolutionRunStatus.WAITING_APPROVAL)
        decision = self._commit_decision(run, task, decision, experiments_by_candidate)
        self._append_event(run.id, "ApprovalRequired", {
            "candidate_id": best.id,
            "reason": release_reason,
        })
        return self.get_run(run.id)

    # --- approval -----------------------------------------------------------------

    def approve(self, run_id: str, *, approver: str, reason: str = "") -> EvolutionRun:
        """人工批准 HUMAN_REVIEW 决策：记录审批并创建新版本。"""
        run = self.get_run(run_id)
        if run.status is not EvolutionRunStatus.WAITING_APPROVAL:
            raise InvalidStateTransitionError(
                f"run {run_id} is not waiting for approval (status {run.status.value})"
            )
        decision = self._latest_decision(run_id)
        task = self.get_task(run.task_id)
        best = self._require(
            self._store.get_candidate(decision.selected_candidate_id or ""),
            "selected candidate missing",
        )
        experiments = self._experiments_by_candidate(run_id)
        validation_experiment = experiments.get(best.id, {}).get("VALIDATION")
        approval = Approval(approver=approver, decision="approved", reason=reason)
        accepted = replace(
            decision,
            decision=DecisionType.ACCEPT,
            approval=approval,
            reason=reason or decision.reason,
        )
        self._store.save_decision(accepted)
        self._append_event(run_id, "DecisionCreated", {"decision": "ACCEPT", "approval": True})
        self._create_version(run, task, best, validation_experiment, approval=approval)
        final = replace(
            run,
            status=EvolutionRunStatus.COMPLETED,
            decision=accepted,
            evidence=self._build_evidence(run, task, accepted, experiments),
            completed_at=utcnow(),
        )
        self._store.save_run(final)
        self._append_event(run_id, "EvolutionCompleted", {"decision": "ACCEPT"})
        self._set_task_status(task, TaskStatus.COMPLETED)
        return self.get_run(run_id)

    def reject(self, run_id: str, *, approver: str, reason: str = "") -> EvolutionRun:
        run = self.get_run(run_id)
        if run.status is not EvolutionRunStatus.WAITING_APPROVAL:
            raise InvalidStateTransitionError(
                f"run {run_id} is not waiting for approval (status {run.status.value})"
            )
        decision = self._latest_decision(run_id)
        task = self.get_task(run.task_id)
        approval = Approval(approver=approver, decision="rejected", reason=reason)
        rejected = replace(
            decision,
            decision=DecisionType.REJECT,
            approval=approval,
            selected_candidate_id=None,
            reason=reason or decision.reason,
        )
        self._store.save_decision(rejected)
        self._append_event(run_id, "DecisionCreated", {"decision": "REJECT", "approval": True})
        experiments = self._experiments_by_candidate(run_id)
        final = replace(
            run,
            status=EvolutionRunStatus.COMPLETED,
            decision=rejected,
            evidence=self._build_evidence(run, task, rejected, experiments),
            completed_at=utcnow(),
        )
        self._store.save_run(final)
        self._append_event(run_id, "EvolutionCompleted", {"decision": "REJECT"})
        self._set_task_status(task, TaskStatus.COMPLETED)
        return self.get_run(run_id)

    # --- rollback -----------------------------------------------------------------

    def rollback(self, version_id: str, *, actor: str = "", reason: str = "") -> DeploymentRecord:
        """回滚到历史版本（spec section 19）：只产生部署事件，不改写版本内容。"""
        version = self.get_version(version_id)
        current = self._active_version(version.target_type, version.resource_id)
        if current is not None and current.id == version.id:
            raise InvalidStateTransitionError(
                f"version {version_id} is already the active version"
            )
        if current is not None:
            self._store.save_version(
                replace(current, deployment_status=DeploymentStatus.SUPERSEDED)
            )
        self._store.save_version(replace(version, deployment_status=DeploymentStatus.ACTIVE))
        record = DeploymentRecord(
            target_type=version.target_type,
            resource_id=version.resource_id,
            version_id=version.id,
            kind=DeploymentKind.ROLLBACK,
            actor=actor,
            reason=reason,
        )
        self._store.record_deployment(record)
        return record

    # --- queries --------------------------------------------------------------------

    def get_run(self, run_id: str) -> EvolutionRun:
        return self._require(self._store.get_run(run_id), f"evolution run not found: {run_id}")

    def list_runs(self, task_id: str | None = None) -> list[EvolutionRun]:
        return self._store.list_runs(task_id)

    def list_candidates(self, run_id: str) -> list[EvolutionCandidate]:
        self.get_run(run_id)
        return self._store.list_candidates_for_run(run_id)

    def list_experiments(self, run_id: str) -> list:
        self.get_run(run_id)
        return self._store.list_experiments_for_run(run_id)

    def list_events(self, run_id: str) -> list[EvolutionEvent]:
        self.get_run(run_id)
        return self._store.list_events_for_run(run_id)

    # --- internals --------------------------------------------------------------------

    def _resolve_optimizer(self, name: str):
        optimizer = self._optimizers.get(name)
        if optimizer is None:
            raise OptimizerError(
                f"optimizer not registered: {name} "
                f"(registered: {sorted(self._optimizers)})"
            )
        return optimizer

    def _finalize(
        self,
        run: EvolutionRun,
        task: EvolutionTask,
        decision: EvolutionDecision,
        *,
        evidence_experiments: dict,
    ) -> EvolutionRun:
        decision = self._commit_decision(run, task, decision, evidence_experiments)
        # 终态决策（ACCEPT/REJECT）完成 Run；HUMAN_REVIEW 走 WAITING_APPROVAL 分支。
        status = (
            EvolutionRunStatus.COMPLETED
            if decision.decision in (DecisionType.REJECT, DecisionType.ACCEPT)
            else run.status
        )
        final = replace(
            run,
            status=status,
            decision=decision,
            evidence=self._build_evidence(run, task, decision, evidence_experiments),
            completed_at=utcnow() if status is EvolutionRunStatus.COMPLETED else None,
        )
        self._store.save_run(final)
        if status is EvolutionRunStatus.COMPLETED:
            self._append_event(run.id, "EvolutionCompleted", {"decision": decision.decision.value})
            self._set_task_status(task, TaskStatus.COMPLETED)
        return final

    def _commit_decision(
        self,
        run: EvolutionRun,
        task: EvolutionTask,
        decision: EvolutionDecision,
        experiments_by_candidate: dict,
    ) -> EvolutionDecision:
        """落决策 + 证据（证据属于决策时刻的快照，spec section 20）。"""
        decision = replace(
            decision, evidence=self._build_evidence(run, task, decision, experiments_by_candidate)
        )
        self._store.save_decision(decision)
        self._append_event(run.id, "DecisionCreated", {"decision": decision.decision.value})
        run_with_decision = replace(run, decision=decision)
        self._store.save_run(run_with_decision)
        return decision

    def _create_version(
        self,
        run: EvolutionRun,
        task: EvolutionTask,
        candidate: EvolutionCandidate,
        validation_experiment,
        *,
        approval: Approval | None,
    ) -> EvolutionVersion:
        base = self._active_version(run.target_type, run.resource_id)
        content = apply_patches(base.content, candidate.patch)  # type: ignore[union-attr]
        existing = self._store.list_versions_for_target(run.target_type, run.resource_id)
        version = EvolutionVersion(
            target_type=run.target_type,
            resource_id=run.resource_id,
            version=f"v{len(existing) + 1}",
            parent_version=base.version if base else "",
            candidate_id=candidate.id,
            evolution_run_id=run.id,
            content=content,
            evaluation_summary=dict(validation_experiment.summary or {}) if validation_experiment else {},
            approval=approval,
            deployment_status=DeploymentStatus.ACTIVE,
        )
        self._store.save_version(version)
        if base is not None:
            self._store.save_version(
                replace(base, deployment_status=DeploymentStatus.SUPERSEDED)
            )
        self._store.record_deployment(
            DeploymentRecord(
                target_type=version.target_type,
                resource_id=version.resource_id,
                version_id=version.id,
                kind=DeploymentKind.RELEASE,
                actor=approval.approver if approval else "auto-release",
                reason=f"evolution run {run.id}",
            )
        )
        self._append_event(run.id, "VersionCreated", {
            "version": version.version,
            "version_id": version.id,
            "candidate_id": candidate.id,
        })
        return version

    def _build_evidence(
        self,
        run: EvolutionRun,
        task: EvolutionTask,
        decision: EvolutionDecision,
        experiments_by_candidate: dict,
    ) -> dict:
        evaluation_results = {
            candidate_id: {
                purpose: {
                    "evaluation_run_id": experiment.evaluation_run_id,
                    "status": experiment.status.value,
                    "summary": experiment.summary,
                }
                for purpose, experiment in experiments.items()
            }
            for candidate_id, experiments in experiments_by_candidate.items()
        }
        return {
            "trigger": task.trigger,
            "diagnosis": task.diagnosis,
            "base_version": run.base_version,
            "evaluation_assets": task.evaluation_assets,
            "candidates": [
                {
                    "id": c.id,
                    "edit_summary": c.edit_summary,
                    "reason": c.reason,
                    "validation_status": c.validation_status.value,
                }
                for c in self._store.list_candidates_for_run(run.id)
            ],
            "evaluation_results": evaluation_results,
            "gate_result": decision.gate_result,
            "decision": {
                "decision": decision.decision.value,
                "selected_candidate_id": decision.selected_candidate_id,
                "reason": decision.reason,
            },
        }

    def _experiments_by_candidate(self, run_id: str) -> dict[str, dict]:
        grouped: dict[str, dict] = {}
        for experiment in self._store.list_experiments_for_run(run_id):
            grouped.setdefault(experiment.candidate_id, {})[experiment.purpose] = experiment
        return grouped

    def _validation_completed(self, experiments: dict) -> bool:
        experiment = experiments.get("VALIDATION")
        return experiment is not None and experiment.status is ExperimentStatus.COMPLETED

    def _purpose_ok(self, experiments: dict, purpose: str) -> bool:
        """可选用途：未配置视为通过；已配置则实验须完成（spec section 17）。"""
        experiment = experiments.get(purpose)
        return experiment is None or experiment.status is ExperimentStatus.COMPLETED

    def _set_task_status(self, task: EvolutionTask, status: TaskStatus) -> None:
        self._store.save_task(replace(task, status=status))

    def _append_event(self, run_id: str, event_type: str, payload: dict) -> None:
        self._store.append_event(
            EvolutionEvent(evolution_run_id=run_id, event_type=event_type, payload=payload)
        )

    def _latest_decision(self, run_id: str) -> EvolutionDecision:
        decision = self.get_run(run_id).decision
        if decision is None:
            raise InvalidStateTransitionError(f"run {run_id} has no decision recorded")
        return decision

    def _validate_task_assets(self, task: EvolutionTask) -> None:
        assets = task.evaluation_assets or {}
        suites = assets.get("suites") or {}
        missing = [p for p in _REQUIRED_SUITE_PURPOSES if not suites.get(p)]
        if not assets.get("application_id") or missing:
            raise ValueError(
                f"task {task.id} evaluation assets incomplete "
                f"(application_id + suites{list(_REQUIRED_SUITE_PURPOSES)} required)"
            )

    def _require(self, value, message: str):
        if value is None:
            raise NotFoundError(message)
        return value


def _build_strategy_config(data: dict):
    from agent_platform.evolution.domain.task import EditBudget, EvolutionStrategy, StrategyType

    strategy_type = StrategyType(data.get("type") or "MULTI_CANDIDATE")
    budget_data = dict(data.get("edit_budget") or {})
    budget = EditBudget(
        max_edits=int(budget_data.get("max_edits", 3)),
        max_tokens_added=int(budget_data.get("max_tokens_added", 200)),
        max_tokens_removed=int(budget_data.get("max_tokens_removed", 100)),
        allowed_operations=list(
            budget_data.get("allowed_operations") or ["ADD", "INSERT", "REPLACE", "DELETE"]
        ),
        allowed_sections=list(budget_data.get("allowed_sections") or []),
    )
    return EvolutionStrategy(
        type=strategy_type,
        optimizer=str(data.get("optimizer") or ""),
        candidate_count=max(1, int(data.get("candidate_count", 3))),
        max_iterations=max(1, int(data.get("max_iterations", 1))),
        edit_budget=budget,
        selection_strategy=str(data.get("selection_strategy") or "BEST_VALID_CANDIDATE"),
        stop_condition=str(data.get("stop_condition") or "MAX_ITERATIONS"),
        generation_model=str(data.get("generation_model") or ""),
    )


def _build_objective(data: dict):
    from agent_platform.evolution.domain.task import (
        EvolutionObjective,
        HardConstraint,
        OptimizationObjective,
    )

    return EvolutionObjective(
        hard_constraints=[
            HardConstraint(
                metric=str(c["metric"]),
                op=str(c.get("op") or ">="),
                threshold=float(c.get("threshold", 0.0)),
            )
            for c in data.get("hard_constraints") or []
        ],
        optimization_objectives=[
            OptimizationObjective(
                metric=str(o["metric"]),
                direction=str(o.get("direction") or "maximize"),
            )
            for o in data.get("optimization_objectives") or []
        ],
    )
