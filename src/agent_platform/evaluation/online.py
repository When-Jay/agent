"""Online Evaluation 服务：A/B 分流与度量（evaluation-spec.md section 21）。

分流语义（sticky assignment）：

* 以 `sha256(ab_test_id + session_id)` 派生两个均匀随机数：第一个决定
  会话是否入选实验（sampling_rate），第二个在入选后按权重分给 variant。
* 同一会话在同一实验下的分配结果永远一致（sticky），无需外部状态。
* 未入选 / 无 RUNNING 实验 / runtime_type 不匹配 → 不产生 Assignment。

V1 边界：Assignment 是度量事实（run ↔ variant 绑定），不改变执行行为；
variant 的 agent_version 为调用方标签，执行侧切换待 Agent 版本化落地。
"""

import hashlib
import logging
from dataclasses import replace

from agent_platform.errors import InvalidStateTransitionError, NotFoundError
from agent_platform.evaluation.domain.online import (
    AB_COMPLETED,
    AB_DRAFT,
    AB_PAUSED,
    AB_RUNNING,
    ABAssignment,
    ABTest,
    ABVariant,
)
from agent_platform.evaluation.storage import EvaluationStore
from agent_platform.runtime.core.models import RunStatus
from agent_platform.runtime.core.stores import RuntimeStore

logger = logging.getLogger(__name__)


class OnlineEvaluationService:
    """A/B 实验生命周期、分流分配与逐 variant 度量报表。"""

    def __init__(self, runtime_store: RuntimeStore, evaluation_store: EvaluationStore) -> None:
        self._runtime_store = runtime_store
        self._store = evaluation_store

    # --- lifecycle ------------------------------------------------------------

    def create_ab_test(
        self,
        *,
        name: str,
        application_id: str,
        runtime_type: str = "agent",
        variants: list[ABVariant],
        sampling_rate: float = 1.0,
        metadata: dict | None = None,
    ) -> ABTest:
        """Create a DRAFT experiment; validates structure, not behavior.

        规则：至少 2 个 variant；key 非空且唯一；weight ≥ 0 且总和 > 0；
        sampling_rate ∈ [0, 1]；application 必须存在。
        """
        if self._runtime_store.get_application(application_id) is None:
            raise NotFoundError(f"application not found: {application_id}")
        if len(variants) < 2:
            raise ValueError("an AB test requires at least two variants")
        keys = [v.key for v in variants]
        if any(not k for k in keys) or len(set(keys)) != len(keys):
            raise ValueError("variant keys must be non-empty and unique")
        if any(v.weight < 0 for v in variants) or sum(v.weight for v in variants) <= 0:
            raise ValueError("variant weights must be >= 0 and sum > 0")
        if not 0.0 <= sampling_rate <= 1.0:
            raise ValueError("sampling_rate must be within [0, 1]")
        test = ABTest(
            name=name,
            application_id=application_id,
            runtime_type=runtime_type,
            variants=list(variants),
            sampling_rate=sampling_rate,
            metadata=dict(metadata or {}),
        )
        self._store.save_ab_test(test)
        return test

    def start_ab_test(self, ab_test_id: str) -> ABTest:
        test = self._require_test(ab_test_id)
        if test.status not in {AB_DRAFT, AB_PAUSED}:
            raise InvalidStateTransitionError(
                f"cannot start AB test from {test.status}"
            )
        running = self._store.list_ab_tests(
            application_id=test.application_id, status=AB_RUNNING
        )
        if any(
            t.id != test.id and t.runtime_type == test.runtime_type for t in running
        ):
            raise InvalidStateTransitionError(
                f"application {test.application_id} already has a RUNNING "
                f"AB test for runtime_type {test.runtime_type}"
            )
        test = replace(test, status=AB_RUNNING)
        self._store.save_ab_test(test)
        return test

    def pause_ab_test(self, ab_test_id: str) -> ABTest:
        test = self._require_test(ab_test_id)
        if test.status != AB_RUNNING:
            raise InvalidStateTransitionError(
                f"cannot pause AB test from {test.status}"
            )
        test = replace(test, status=AB_PAUSED)
        self._store.save_ab_test(test)
        return test

    def complete_ab_test(self, ab_test_id: str) -> ABTest:
        test = self._require_test(ab_test_id)
        if test.status not in {AB_RUNNING, AB_PAUSED}:
            raise InvalidStateTransitionError(
                f"cannot complete AB test from {test.status}"
            )
        test = replace(test, status=AB_COMPLETED)
        self._store.save_ab_test(test)
        return test

    # --- queries ----------------------------------------------------------------

    def get_ab_test(self, ab_test_id: str) -> ABTest:
        return self._require_test(ab_test_id)

    def list_ab_tests(
        self,
        *,
        application_id: str | None = None,
        status: str | None = None,
    ) -> list[ABTest]:
        return self._store.list_ab_tests(
            application_id=application_id, status=status
        )

    # --- assignment -------------------------------------------------------------

    def assign_run(self, run_id: str) -> ABAssignment | None:
        """Sticky-split one run against the active experiment, if any.

        幂等：run 已有 Assignment 时直接返回既有记录。分流只记录事实，
        不改变执行路径。
        """
        run = self._runtime_store.get_run(run_id)
        if run is None:
            raise NotFoundError(f"run not found: {run_id}")
        existing = self._store.get_assignment_for_run(run_id)
        if existing is not None:
            return existing
        test = next(
            (
                t
                for t in self._store.list_ab_tests(
                    application_id=run.application_id, status=AB_RUNNING
                )
                if t.runtime_type == run.runtime_type
            ),
            None,
        )
        if test is None:
            return None
        variant_key = self._split(test, run.session_id)
        if variant_key is None:
            return None
        variant = next(v for v in test.variants if v.key == variant_key)
        assignment = ABAssignment(
            ab_test_id=test.id,
            run_id=run.id,
            session_id=run.session_id,
            variant_key=variant.key,
            agent_version=variant.agent_version,
        )
        self._store.save_assignment(assignment)
        return assignment

    def get_assignment_for_run(self, run_id: str) -> ABAssignment | None:
        return self._store.get_assignment_for_run(run_id)

    def list_assignments(self, ab_test_id: str) -> list[ABAssignment]:
        self._require_test(ab_test_id)
        return self._store.list_assignments_for_test(ab_test_id)

    # --- report -------------------------------------------------------------------

    def report(self, ab_test_id: str) -> dict:
        """Per-variant outcome aggregation over assigned runs.

        指标：run 数、终态分布、完成/失败率、端到端时延（avg/max，
        仅统计有 started_at 与 completed_at 的 run）。
        """
        test = self._require_test(ab_test_id)
        assignments = self._store.list_assignments_for_test(ab_test_id)
        stats = {
            v.key: {
                "agent_version": v.agent_version,
                "runs": 0,
                "completed": 0,
                "failed": 0,
                "cancelled": 0,
                "other": 0,
                "latency_samples_s": [],
            }
            for v in test.variants
        }
        for assignment in assignments:
            bucket = stats.get(assignment.variant_key)
            if bucket is None:  # variant 被修改过的历史 Assignment
                continue
            bucket["runs"] += 1
            run = self._runtime_store.get_run(assignment.run_id)
            if run is None:
                bucket["other"] += 1
                continue
            if run.status is RunStatus.COMPLETED:
                bucket["completed"] += 1
            elif run.status is RunStatus.FAILED:
                bucket["failed"] += 1
            elif run.status is RunStatus.CANCELLED:
                bucket["cancelled"] += 1
            else:
                bucket["other"] += 1
            if run.started_at and run.completed_at:
                bucket["latency_samples_s"].append(
                    (run.completed_at - run.started_at).total_seconds()
                )

        variants_report = {}
        for key, bucket in stats.items():
            runs = bucket.pop("runs")
            total = runs or 1
            samples = bucket.pop("latency_samples_s")
            variants_report[key] = {
                "agent_version": bucket["agent_version"],
                "runs": runs,
                "completed": bucket["completed"],
                "failed": bucket["failed"],
                "cancelled": bucket["cancelled"],
                "other": bucket["other"],
                "completion_rate": round(bucket["completed"] / total, 4),
                "failure_rate": round(bucket["failed"] / total, 4),
                "latency_avg_s": (
                    round(sum(samples) / len(samples), 4) if samples else None
                ),
                "latency_max_s": round(max(samples), 4) if samples else None,
            }
        return {"ab_test": test, "variants": variants_report}

    # --- internals -------------------------------------------------------------------

    def _split(self, test: ABTest, session_id: str) -> str | None:
        """Sticky split: return the variant key, or None when not enrolled."""
        digest = hashlib.sha256(f"{test.id}:{session_id}".encode()).digest()
        enrolled = int.from_bytes(digest[:8], "big") / 2**64
        if enrolled >= test.sampling_rate:
            return None
        slot = int.from_bytes(digest[8:16], "big") / 2**64 * sum(
            v.weight for v in test.variants
        )
        cumulative = 0.0
        for variant in test.variants:
            cumulative += variant.weight
            if slot < cumulative:
                return variant.key
        return test.variants[-1].key

    def _require_test(self, ab_test_id: str) -> ABTest:
        test = self._store.get_ab_test(ab_test_id)
        if test is None:
            raise NotFoundError(f"AB test not found: {ab_test_id}")
        return test
