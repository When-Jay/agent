"""LLM Judge evaluator (spec section 7 / cascade 第二级).

模型依赖通过注入的 `model_fn: Callable[[str], str]` 端口进入（capabilities
可替换原则，AGENTS.md section 2.4），evaluation 模块不绑定任何 SDK。
未配置 model_fn 或判定不明时返回 UNKNOWN —— 不强行判定（spec section 18）。
"""

import json
import re

from agent_platform.evaluation.domain import ResultValue
from agent_platform.evaluation.evaluators.base import (
    EvaluationContext,
    Evaluator,
    make_result,
)

_EVALUATOR_ID = "llm_judge"

_VERDICTS = {v.value for v in ResultValue} - {"ERROR"}

_PROMPT_TEMPLATE = """You are evaluating an AI agent's work on a task.

## Task input
{task_input}

## Expected behavior
{expected}

## Agent output
{output}

## Evaluation criteria
{criteria}

Decide a verdict for each criterion. Respond with a JSON object only:
{{"criteria": [{{"id": "<criterion id or null>", "verdict": "PASS|FAIL|UNKNOWN", "reason": "<short reason>"}}]}}
Use UNKNOWN when evidence is insufficient - do not guess.
"""


class LLMJudgeEvaluator:
    id = _EVALUATOR_ID

    def __init__(self, model_fn=None) -> None:
        self._model_fn = model_fn

    def evaluate(self, context: EvaluationContext) -> list:
        if self._model_fn is None:
            return [
                make_result(
                    context,
                    _EVALUATOR_ID,
                    ResultValue.UNKNOWN,
                    evidence=["no judge model configured"],
                )
            ]
        criteria = self._collect_criteria(context)
        if not criteria:
            return [
                make_result(
                    context,
                    _EVALUATOR_ID,
                    ResultValue.UNKNOWN,
                    evidence=["no criteria to judge"],
                )
            ]
        prompt = _PROMPT_TEMPLATE.format(
            task_input=json.dumps(context.task.input, ensure_ascii=False, default=str),
            expected=context.task.expected_behavior.description or "(unspecified)",
            output=json.dumps(context.output, ensure_ascii=False, default=str),
            criteria=json.dumps(criteria, ensure_ascii=False),
        )
        try:
            response = self._model_fn(prompt)
        except Exception as exc:  # noqa: BLE001 - judge failure is first-class ERROR
            return [
                make_result(
                    context, _EVALUATOR_ID, ResultValue.ERROR,
                    evidence=[f"judge model failed: {type(exc).__name__}: {exc}"],
                )
            ]
        return self._parse(context, criteria, response)

    def _collect_criteria(self, context: EvaluationContext) -> list[dict]:
        """Judgeable criteria: rubric criteria without executable rules."""
        items = []
        for rubric in context.rubrics:
            for criterion in rubric.criteria:
                if criterion.rule:
                    continue  # deterministic rules belong to the RuleEvaluator
                items.append(
                    {
                        "id": criterion.id,
                        "description": criterion.description,
                        "rubric_id": rubric.id,
                    }
                )
        if not items and context.task.expected_behavior.description:
            items.append(
                {
                    "id": "expected_behavior",
                    "description": context.task.expected_behavior.description,
                    "rubric_id": None,
                }
            )
        return items

    def _parse(self, context, criteria: list[dict], response: str) -> list:
        verdicts, parse_error = _extract_verdicts(response)
        results = []
        for criterion in criteria:
            verdict = verdicts.get(criterion["id"])
            if verdict is None:
                results.append(
                    make_result(
                        context, _EVALUATOR_ID, ResultValue.UNKNOWN,
                        rubric_id=criterion["rubric_id"],
                        criterion_id=criterion["id"],
                        evidence=["judge returned no verdict for criterion"],
                    )
                )
                continue
            value, reason = verdict
            if value not in _VERDICTS:
                results.append(
                    make_result(
                        context, _EVALUATOR_ID, ResultValue.UNKNOWN,
                        rubric_id=criterion["rubric_id"],
                        criterion_id=criterion["id"],
                        evidence=[f"invalid verdict: {value!r}"],
                    )
                )
                continue
            results.append(
                make_result(
                    context, _EVALUATOR_ID, ResultValue(value),
                    rubric_id=criterion["rubric_id"],
                    criterion_id=criterion["id"],
                    evidence=[reason],
                    confidence=None,
                )
            )
        if parse_error:
            results.append(
                make_result(
                    context, _EVALUATOR_ID, ResultValue.UNKNOWN,
                    evidence=[f"judge response not fully parseable: {parse_error}"],
                )
            )
        return results


def _extract_verdicts(response: str) -> tuple[dict[str, tuple[str, str]], str | None]:
    """Extract {criterion_id: (verdict, reason)} from a judge response."""
    match = re.search(r"\{.*\}", response, re.DOTALL)
    if not match:
        return {}, "no JSON object found"
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return {}, str(exc)
    verdicts: dict[str, tuple[str, str]] = {}
    for item in data.get("criteria", []):
        cid = item.get("id")
        verdict = item.get("verdict")
        if cid is None or verdict is None:
            continue
        verdicts[str(cid)] = (str(verdict), str(item.get("reason", "")))
    return verdicts, None
