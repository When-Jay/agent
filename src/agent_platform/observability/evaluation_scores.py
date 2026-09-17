"""Langfuse Score export for evaluation runs (evaluation-spec.md section 33).

Score 导出由独立的 observability 适配器实现，不进入 evaluation 执行路径：
dispatch worker 在 evaluation run 结束后调用 export_run，把每个
EvaluationResult 写为对应 Trial trace 上的 Langfuse Score。trace 关联与
Trace 导出一致：trace id = UUID(runtime run id).hex
（deepagents-runtime-spec.md section 10）。

UNKNOWN / ERROR 结果不导出数值（spec section 18：不得强行判定）；
PASS/FAIL 无分数时按 1.0 / 0.0 导出。

Disabled unless LANGFUSE_PUBLIC_KEY/SECRET_KEY are configured or a
client is injected (tests). 每条 Score 的写入相互隔离：单条失败只记录
告警，flush 也为 best effort。
"""

import json
import logging
from typing import Any
from uuid import UUID

from agent_platform.config import Settings
from agent_platform.evaluation.domain import ResultValue

logger = logging.getLogger(__name__)


class LangfuseScoreExporter:
    """Exports evaluation results as Langfuse Scores on trial traces."""

    def __init__(
        self,
        evaluation_store,
        *,
        settings: Settings | None = None,
        client: Any | None = None,
    ) -> None:
        resolved = settings or Settings()
        self._store = evaluation_store
        self._client = client
        self._enabled = client is not None or bool(
            resolved.langfuse_public_key and resolved.langfuse_secret_key
        )
        if self._enabled and client is None:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=resolved.langfuse_public_key,
                secret_key=resolved.langfuse_secret_key,
                host=resolved.langfuse_host,
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def export_run(self, evaluation_run_id: str) -> int:
        """Export one evaluation run's results; returns the exported count."""
        if not self._enabled:
            return 0
        run = self._store.get_evaluation_run(evaluation_run_id)
        if run is None:
            return 0
        trials = {t.id: t for t in self._store.list_trials_for_run(evaluation_run_id)}
        exported = 0
        for result in self._store.list_results_for_run(evaluation_run_id):
            trial = trials.get(result.trial_id)
            value = self._numeric_value(result)
            if trial is None or not trial.run_id or value is None:
                continue
            try:
                self._client.score(
                    trace_id=UUID(trial.run_id).hex,
                    name=result.evaluator_id or "evaluation",
                    value=value,
                    comment=json.dumps(
                        {
                            "evaluation_run_id": evaluation_run_id,
                            "trial_id": result.trial_id,
                            "task_id": result.task_id,
                            "result": result.result.value,
                            "rubric_id": result.rubric_id,
                            "criterion_id": result.criterion_id,
                            "evidence": result.evidence,
                            "agent_version": trial.agent_version,
                            "environment_version": trial.environment_version,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                )
                exported += 1
            except Exception:  # noqa: BLE001 - single-score isolation
                logger.warning(
                    "langfuse score export failed for result %s", result.id, exc_info=True
                )
        self._flush()
        return exported

    @staticmethod
    def _numeric_value(result) -> float | None:
        if result.score is not None:
            return float(result.score)
        return {ResultValue.PASS: 1.0, ResultValue.FAIL: 0.0}.get(result.result)

    def _flush(self) -> None:
        try:
            self._client.flush()
        except Exception:  # noqa: BLE001 - flush is best effort
            logger.warning("langfuse flush failed", exc_info=True)
