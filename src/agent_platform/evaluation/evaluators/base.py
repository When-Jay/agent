"""Evaluator contract, evaluation context and registry (plan 050 section 8).

Evaluator 定义"怎么评价"，通过 Registry 插拔（架构文档 section 9）。
Evaluator 只消费 EvaluationContext（outcome / output / events / artifacts），
不接触执行代码 —— Evaluation 消费 Runtime 数据，不反向依赖（依赖方向，
AGENTS.md section 7）。
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_platform.evaluation.domain import EvaluationResult, ResultValue, Rubric, Task, Trial
from agent_platform.runtime.core.events import RuntimeEvent


@dataclass(frozen=True)
class EvaluationContext:
    """一次 Trial 的全部评价输入（架构文档 section 3 的观察面）。"""

    task: Task
    trial: Trial
    outcome: dict  # task_completed / output / artifacts / error ...
    output: dict  # runtime run output
    events: list[RuntimeEvent] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    rubrics: list[Rubric] = field(default_factory=list)

    @property
    def task_id(self) -> str:
        return self.task.id

    @property
    def trial_id(self) -> str:
        return self.trial.id


def make_result(
    context: EvaluationContext,
    evaluator_id: str,
    result: ResultValue,
    *,
    criterion_id: str | None = None,
    rubric_id: str | None = None,
    score: float | None = None,
    evidence: list | None = None,
    confidence: float | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        task_id=context.task_id,
        run_id=context.trial.evaluation_run_id,
        trial_id=context.trial_id,
        evaluator_id=evaluator_id,
        rubric_id=rubric_id,
        criterion_id=criterion_id,
        result=result,
        score=score,
        evidence=evidence or [],
        confidence=confidence,
    )


@runtime_checkable
class Evaluator(Protocol):
    """可插拔评价器：id 唯一，evaluate 返回该 Trial 的全部结果。"""

    id: str

    def evaluate(self, context: EvaluationContext) -> list[EvaluationResult]: ...


class EvaluatorRegistry:
    """按 id 注册/查找 Evaluator（plan 050 验收：Evaluator 可以通过 Registry 插拔）。"""

    def __init__(self) -> None:
        self._evaluators: dict[str, Evaluator] = {}

    def register(self, evaluator: Evaluator) -> None:
        self._evaluators[evaluator.id] = evaluator

    def get(self, evaluator_id: str) -> Evaluator | None:
        return self._evaluators.get(evaluator_id)

    def ids(self) -> list[str]:
        return sorted(self._evaluators)

    def evaluate(
        self, evaluator_ids: list[str], context: EvaluationContext
    ) -> list[EvaluationResult]:
        """Run the named evaluators in order; unknown ids degrade to UNKNOWN."""
        results: list[EvaluationResult] = []
        for evaluator_id in evaluator_ids:
            evaluator = self._evaluators.get(evaluator_id)
            if evaluator is None:
                results.append(
                    make_result(
                        context,
                        evaluator_id,
                        ResultValue.UNKNOWN,
                        evidence=[f"evaluator not registered: {evaluator_id}"],
                    )
                )
                continue
            try:
                results.extend(evaluator.evaluate(context))
            except Exception as exc:  # noqa: BLE001 - evaluator errors are first-class
                results.append(
                    make_result(
                        context,
                        evaluator_id,
                        ResultValue.ERROR,
                        evidence=[f"{type(exc).__name__}: {exc}"],
                    )
                )
        return results


def rule_result_value(results: list[EvaluationResult]) -> ResultValue:
    """Aggregate evaluation results into a trial verdict.

    保守裁决：任一 FAIL/ERROR → FAILED；否则任一 UNKNOWN → FAILED
    （UNKNOWN 不得默认通过，spec section 18；升级机制为后续能力）；
    全部 PASS → PASSED；无结果 → UNKNOWN。
    """
    if not results:
        return ResultValue.UNKNOWN
    values = {r.result for r in results}
    if ResultValue.FAIL in values or ResultValue.ERROR in values:
        return ResultValue.FAIL
    if ResultValue.UNKNOWN in values:
        return ResultValue.UNKNOWN
    return ResultValue.PASS
