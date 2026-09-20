"""Diagnosis：Case → 归因 → 推荐动作（plan 050 Phase 8; spec sections 16, 24）。

V2 确定性规则（不使用 LLM，spec section 20 原则）按证据强度归因：

1. trace 缺失/不可回查      → COVERAGE_GAP / HUMAN_REVIEW（spec section 18：
                              Trace 不完整不得强行判定，升级人工）
2. Run 终态 FAILED          → AGENT_FAILURE / runtime → AGENT_FIX
3. ToolCallFailed 事件      → AGENT_FAILURE / tool    → AGENT_FIX
4. LLMFailed 事件           → AGENT_FAILURE / model   → AGENT_FIX
5. NodeFailed 事件          → AGENT_FAILURE / workflow → AGENT_FIX
6. trace 完整且干净          → COVERAGE_GAP / process → ADD_COVERAGE
   （E2E 失败但过程无失败信号：缺过程评测覆盖，plan Phase 4 原则）

0. case.source is EVALUATION 且 Trial 有 error 无评分结果
   → EVALUATION_FAILURE / harness → EVAL_FIX（评测设施自身失败，
   plan-productionization G3；有评分但失败的评测信号落入上述规则 1-6）。
"""

import logging
from dataclasses import replace

from agent_platform.errors import NotFoundError
from agent_platform.evaluation.domain import (
    CASE_DIAGNOSED,
    CASE_PROMOTED,
    Case,
    CaseSource,
    DiagnosisCategory,
    DiagnosisResult,
    RecommendedAction,
)
from agent_platform.evaluation.storage import EvaluationStore
from agent_platform.runtime.core.events import RuntimeEventType
from agent_platform.runtime.core.models import RunStatus
from agent_platform.runtime.core.stores import RuntimeStore

logger = logging.getLogger(__name__)

_EVENT_COMPONENTS = (
    (RuntimeEventType.TOOL_CALL_FAILED, "tool", 0.8),
    (RuntimeEventType.LLM_FAILED, "model", 0.8),
    (RuntimeEventType.NODE_FAILED, "workflow", 0.8),
)


class DiagnosisService:
    def __init__(self, runtime_store: RuntimeStore, evaluation_store: EvaluationStore) -> None:
        self._runtime_store = runtime_store
        self._store = evaluation_store

    def diagnose(self, case_id: str) -> DiagnosisResult:
        """Attribute one case and persist the result onto the case.

        每次调用追加一条 DiagnosisResult（保留归因历史）；Case.attribution
        保存最近一次结果，状态推进到 DIAGNOSED（已 PROMOTED 的 Case 不回退）。
        """
        case = self._store.get_case(case_id)
        if case is None:
            raise NotFoundError(f"case not found: {case_id}")
        result = self._attribute(case)
        self._store.save_diagnosis(result)
        case = replace(
            case,
            attribution={
                **case.attribution,
                "diagnosis_id": result.id,
                "category": result.category.value,
                "component": result.component,
                "confidence": result.confidence,
                "recommended_action": result.recommended_action.value,
            },
            status=CASE_DIAGNOSED if case.status != CASE_PROMOTED else case.status,
        )
        self._store.save_case(case)
        logger.info(
            "case %s diagnosed: %s/%s -> %s",
            case.id, result.category.value, result.component,
            result.recommended_action.value,
        )
        return result

    def list_diagnoses(self, case_id: str) -> list[DiagnosisResult]:
        self._require_case(case_id)
        return self._store.list_diagnoses_for_case(case_id)

    # --- attribution rules ---------------------------------------------------

    def _attribute(self, case: Case) -> DiagnosisResult:
        harness_failure = (
            self._evaluation_harness_failure(case)
            if case.source is CaseSource.EVALUATION
            else None
        )
        if harness_failure is not None:
            return DiagnosisResult(
                case_id=case.id,
                category=DiagnosisCategory.EVALUATION_FAILURE,
                component="harness",
                evidence=[harness_failure],
                confidence=0.9,
                recommended_action=RecommendedAction.EVAL_FIX,
            )

        run = (
            self._runtime_store.get_run(case.trace_id) if case.trace_id else None
        )
        if run is None:
            # Trace 不完整：不得强行判定（spec section 18），升级人工。
            return DiagnosisResult(
                case_id=case.id,
                category=DiagnosisCategory.COVERAGE_GAP,
                component="trace",
                evidence=[{"reason": "trace not available", "trace_id": case.trace_id}],
                confidence=0.0,
                recommended_action=RecommendedAction.HUMAN_REVIEW,
            )

        if run.status is RunStatus.FAILED:
            return DiagnosisResult(
                case_id=case.id,
                category=DiagnosisCategory.AGENT_FAILURE,
                component="runtime",
                evidence=[{"event_type": "RUN_FAILED", "error": run.error}],
                confidence=0.9,
                recommended_action=RecommendedAction.AGENT_FIX,
            )

        events = self._runtime_store.list_events(case.trace_id)
        evidence: list = []
        for event_type, component, confidence in _EVENT_COMPONENTS:
            failures = [e for e in events if e.event_type is event_type]
            if failures:
                evidence = [
                    {
                        "event_type": e.event_type.value,
                        "payload": e.payload,
                        "created_at": e.created_at.isoformat(),
                    }
                    for e in failures
                ]
                return DiagnosisResult(
                    case_id=case.id,
                    category=DiagnosisCategory.AGENT_FAILURE,
                    component=component,
                    evidence=evidence,
                    confidence=confidence,
                    recommended_action=RecommendedAction.AGENT_FIX,
                )

        # trace 完整、过程无失败信号：E2E 失败无法定位到过程阶段，
        # 属于过程评测覆盖缺口（plan Phase 4：E2E Failure + 不知道原因）。
        return DiagnosisResult(
            case_id=case.id,
            category=DiagnosisCategory.COVERAGE_GAP,
            component="process",
            evidence=[{
                "reason": "run completed without failure events; "
                          "no process criterion attributes the failure",
                "run_status": run.status.value,
            }],
            confidence=0.4,
            recommended_action=RecommendedAction.ADD_COVERAGE,
        )

    def _evaluation_harness_failure(self, case: Case) -> dict | None:
        """Harness-level failure evidence for an EVALUATION-source case, or None.

        Mining evidence 携带 trial_id（cases.py mine_evaluation_run）：
        Trial.outcome 有 error 且该 Trial 无任何 Evaluator 评分结果 →
        harness/评测设施自身失败，不是 Agent 失败；有评分结果（Agent
        已执行且被判定失败）→ 返回 None，落入现有生产归因规则。
        """
        trial_id = next(
            (
                item.get("trial_id")
                for item in case.evidence
                if isinstance(item, dict) and item.get("trial_id")
            ),
            None,
        )
        if not trial_id:
            return None
        trial = self._store.get_trial(trial_id)
        if trial is None or not trial.outcome.get("error"):
            return None
        scored = any(
            result.trial_id == trial_id
            for result in self._store.list_results_for_run(trial.evaluation_run_id)
        )
        if scored:
            return None
        return {
            "trial_id": trial.id,
            "evaluation_run_id": trial.evaluation_run_id,
            "trial_status": trial.status.value,
            "error": trial.outcome.get("error"),
        }

    def _require_case(self, case_id: str) -> None:
        if self._store.get_case(case_id) is None:
            raise NotFoundError(f"case not found: {case_id}")
