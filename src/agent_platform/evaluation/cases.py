"""Case Mining 与 Case 生命周期（plan 050 Phase 8/9; spec sections 15, 23）。

Mining 流程（spec section 23）：Signal → Candidate Case → Deduplication →
Trace Enrichment → Case。V2 信号源：生产 Run 失败信号（PRODUCTION_SAMPLE）、
评测失败 Trial（EVALUATION）、人工反馈（USER_FEEDBACK）；Monitoring/
Random Sampling 为保留信号源。提升（promote）实现 plan Phase 9 的
Bad Case → Diagnosis → Regression Set 反向沉淀。
"""

import logging
from dataclasses import replace

from agent_platform.errors import InvalidStateTransitionError, NotFoundError
from agent_platform.evaluation.domain import (
    ASSET_REGRESSION,
    Case,
    CaseSource,
    CaseType,
    ExpectedBehavior,
    CASE_PROMOTED,
    EvaluationAsset,
    Task,
    TrialStatus,
    new_id,
)
from agent_platform.evaluation.storage import EvaluationStore
from agent_platform.runtime.core.events import RuntimeEventType
from agent_platform.runtime.core.models import RunStatus
from agent_platform.runtime.core.stores import RuntimeStore

logger = logging.getLogger(__name__)

# 生产 Run 中的失败信号事件（mine_run 的 BAD 判定依据）。
_FAILURE_EVENT_TYPES = (
    RuntimeEventType.RUN_FAILED,
    RuntimeEventType.TOOL_CALL_FAILED,
    RuntimeEventType.LLM_FAILED,
    RuntimeEventType.NODE_FAILED,
)


class CaseMiner:
    """从 Runtime/评测数据中挖掘 Candidate Case（dedup + trace enrichment）。"""

    def __init__(self, runtime_store: RuntimeStore, evaluation_store: EvaluationStore) -> None:
        self._runtime_store = runtime_store
        self._evaluation_store = evaluation_store

    def mine_run(
        self, run_id: str, *, source: CaseSource = CaseSource.PRODUCTION_SAMPLE
    ) -> Case | None:
        """Mine one production Run: failure signals produce a BAD case.

        干净完成的 Run 不产 Case（Random Sampling 属保留能力）；
        同一 (source, trace_id) 已有 Case 时幂等返回既有 Case。
        """
        run = self._require_run(run_id)
        existing = self._evaluation_store.find_case_by_trace(source.value, run_id)
        if existing is not None:
            return existing
        events = self._runtime_store.list_events(run_id)
        failures = [e for e in events if e.event_type in _FAILURE_EVENT_TYPES]
        if run.status is not RunStatus.FAILED and not failures:
            return None
        evidence = [
            {
                "event_type": e.event_type.value,
                "payload": e.payload,
                "created_at": e.created_at.isoformat(),
            }
            for e in failures
        ]
        case = Case(
            source=source,
            type=CaseType.BAD,
            trace_id=run_id,
            input=run.input,
            output=run.output or {},
            evidence=evidence,
        )
        self._evaluation_store.save_case(case)
        return case

    def mine_evaluation_run(self, evaluation_run_id: str) -> list[Case]:
        """Mine failed trials of an offline evaluation run (spec section 23
        EVALUATION_FAILURE 信号源)：FAILED Trial 即 BAD case 候选。"""
        run = self._evaluation_store.get_evaluation_run(evaluation_run_id)
        if run is None:
            raise NotFoundError(f"evaluation run not found: {evaluation_run_id}")
        cases: list[Case] = []
        for trial in self._evaluation_store.list_trials_for_run(evaluation_run_id):
            if trial.status is not TrialStatus.FAILED:
                continue
            existing = self._evaluation_store.find_case_by_trace(
                CaseSource.EVALUATION.value, trial.run_id
            )
            if existing is not None:
                cases.append(existing)
                continue
            task = self._evaluation_store.get_task(trial.task_id)
            case = Case(
                source=CaseSource.EVALUATION,
                type=CaseType.BAD,
                task_id=trial.task_id,
                trace_id=trial.run_id,
                input=dict(task.input) if task else {},
                output=trial.outcome.get("output") or {},
                expected_behavior=_expected_behavior_dict(task),
                evidence=[
                    {
                        "evaluation_run_id": evaluation_run_id,
                        "trial_id": trial.id,
                        "trial_status": trial.status.value,
                        "error": trial.outcome.get("error"),
                    }
                ],
            )
            self._evaluation_store.save_case(case)
            cases.append(case)
        return cases

    def _require_run(self, run_id: str):
        run = self._runtime_store.get_run(run_id)
        if run is None:
            raise NotFoundError(f"run not found: {run_id}")
        return run


class CaseService:
    """Case 生命周期：创建（反馈）、查询、归因落档、提升 Regression Set。"""

    def __init__(
        self,
        runtime_store: RuntimeStore,
        evaluation_store: EvaluationStore,
        miner: CaseMiner,
    ) -> None:
        self._runtime_store = runtime_store
        self._store = evaluation_store
        self._miner = miner

    # --- mining（委托 CaseMiner，保持信号规则单一出处） ------------------------

    def mine_run(self, run_id: str) -> Case | None:
        return self._miner.mine_run(run_id)

    def mine_evaluation_run(self, evaluation_run_id: str) -> list[Case]:
        return self._miner.mine_evaluation_run(evaluation_run_id)

    # --- manual cases（USER_FEEDBACK 等） --------------------------------------

    def create_case(
        self,
        *,
        source: CaseSource,
        type: CaseType,
        task_id: str | None = None,
        trace_id: str = "",
        input: dict | None = None,
        output: dict | None = None,
        expected_behavior: dict | None = None,
        metadata: dict | None = None,
    ) -> Case:
        """Create a manually submitted case (spec section 23 User Feedback).

        提供 trace_id 时做 Trace Enrichment：input/output 缺省部分从
        Runtime Run 回填（spec section 23 Trace Enrichment 步骤）。
        """
        input = dict(input or {})
        output = dict(output or {})
        if trace_id:
            run = self._runtime_store.get_run(trace_id)
            if run is None:
                raise NotFoundError(f"run not found: {trace_id}")
            input = input or dict(run.input)
            output = output or dict(run.output or {})
        case = Case(
            source=source,
            type=type,
            task_id=task_id,
            trace_id=trace_id,
            input=input,
            output=output,
            expected_behavior=dict(expected_behavior or {}),
            metadata=dict(metadata or {}),
        )
        self._store.save_case(case)
        return case

    # --- queries -----------------------------------------------------------------

    def get_case(self, case_id: str) -> Case:
        case = self._store.get_case(case_id)
        if case is None:
            raise NotFoundError(f"case not found: {case_id}")
        return case

    def list_cases(
        self,
        *,
        type: CaseType | None = None,
        source: CaseSource | None = None,
        status: str | None = None,
    ) -> list[Case]:
        return self._store.list_cases(
            case_type=type.value if type else None,
            source=source.value if source else None,
            status=status,
        )

    def dismiss_case(self, case_id: str) -> Case:
        """Dedup 噪声 / 误报的人工处置出口。"""
        case = self.get_case(case_id)
        if case.status == CASE_PROMOTED:
            raise InvalidStateTransitionError(
                f"case {case_id} already promoted; cannot dismiss"
            )
        case = replace(case, status="DISMISSED")
        self._store.save_case(case)
        return case

    # --- regression promotion（plan 050 Phase 9） -------------------------------

    def promote_to_regression(
        self, case_id: str, *, asset_name: str = "regression-set"
    ) -> tuple[Case, Task, EvaluationAsset]:
        """Bad Case → Task → REGRESSION asset（spec section 25/26 入口）。

        Evaluation Platform 不修改 Agent，只沉淀回归资产：为 Case 生成
        Regression Task 并挂入指定名称的 REGRESSION asset（不存在则创建）。
        """
        case = self.get_case(case_id)
        if case.type is not CaseType.BAD:
            raise InvalidStateTransitionError(
                f"only BAD cases can be promoted to regression, got {case.type.value}"
            )
        if case.status == CASE_PROMOTED:
            raise InvalidStateTransitionError(
                f"case {case_id} already promoted to regression"
            )
        task = Task(
            id=new_id(),
            name=f"regression-{case.id[:8]}",
            input=dict(case.input),
            expected_behavior=_expected_behavior(case.expected_behavior),
            metadata={
                "source_case": case.id,
                "case_source": case.source.value,
                "trace_id": case.trace_id,
            },
        )
        self._store.save_task(task)

        asset = next(
            (a for a in self._store.list_assets(ASSET_REGRESSION) if a.name == asset_name),
            None,
        )
        if asset is None:
            asset = EvaluationAsset(
                id=new_id(),
                name=asset_name,
                type=ASSET_REGRESSION,
                task_ids=[task.id],
                source="case-mining",
                metadata={"promoted_cases": [case.id]},
            )
        else:
            asset = replace(
                asset,
                task_ids=[*asset.task_ids, task.id],
                metadata={
                    **asset.metadata,
                    "promoted_cases": [*asset.metadata.get("promoted_cases", []), case.id],
                },
            )
        self._store.save_asset(asset)

        case = replace(
            case,
            status=CASE_PROMOTED,
            attribution={
                **case.attribution,
                "regression_task_id": task.id,
                "regression_asset_id": asset.id,
            },
        )
        self._store.save_case(case)
        logger.info("case %s promoted to regression task %s", case.id, task.id)
        return case, task, asset


def _expected_behavior(data: dict | None) -> ExpectedBehavior:
    if not data:
        return ExpectedBehavior()
    return ExpectedBehavior(
        description=data.get("description") or "",
        required_actions=list(data.get("required_actions") or []),
        forbidden_actions=list(data.get("forbidden_actions") or []),
        expected_outcome=data.get("expected_outcome") or {},
    )


def _expected_behavior_dict(task) -> dict:
    """Dump a Task's ExpectedBehavior into the Case.expected_behavior dict form."""
    if task is None:
        return {}
    behavior = task.expected_behavior
    return {
        "description": behavior.description,
        "required_actions": list(behavior.required_actions),
        "forbidden_actions": list(behavior.forbidden_actions),
        "expected_outcome": dict(behavior.expected_outcome),
    }
