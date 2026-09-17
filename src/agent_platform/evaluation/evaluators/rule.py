"""RuleEvaluator: deterministic evaluation (spec section 20 的第一级).

"能确定性判断的问题，不使用 LLM"（架构文档 section 20 cascade 顶端）。

规则规格（dict）作用于 EvaluationContext：

作用域       规则 type              参数
output      required_fields        fields: list[str]（点路径）
output      exact_match            path, value
output      regex                  path, pattern
output      forbidden_substrings   values: list[str], path(可选, 缺省全文)
output      output_not_empty       -
outcome     task_completed         -
events      tool_called            tool(可选), at_least(缺省 1)
events      tool_not_called        tool
artifacts   artifact_present       name(可选)

未知规则类型 -> UNKNOWN（不强行判定，spec section 18）。
"""

import re
from typing import Any

from agent_platform.evaluation.domain import ResultValue
from agent_platform.evaluation.evaluators.base import (
    EvaluationContext,
    Evaluator,
    make_result,
)

_EVALUATOR_ID = "rule"


def get_path(data: Any, path: str) -> tuple[bool, Any]:
    """Resolve a dotted path; returns (found, value)."""
    current = data
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return False, None
    return True, current


def _evaluate_rule(rule: dict, context: EvaluationContext) -> tuple[ResultValue, list]:
    rule_type = rule.get("type")
    evidence: list = []

    if rule_type == "task_completed":
        ok = bool(context.outcome.get("task_completed"))
        return (ResultValue.PASS if ok else ResultValue.FAIL), [
            f"task_completed={ok}"
        ]

    if rule_type == "required_fields":
        missing = [
            field for field in rule.get("fields", [])
            if not get_path(context.output, field)[0]
        ]
        if missing:
            return ResultValue.FAIL, [{"missing_fields": missing}]
        return ResultValue.PASS, [{"fields": rule.get("fields", [])}]

    if rule_type == "exact_match":
        found, value = get_path(context.output, rule.get("path", ""))
        expected = rule.get("value")
        if not found:
            return ResultValue.UNKNOWN, [{"missing_path": rule.get("path")}]
        ok = value == expected
        return (ResultValue.PASS if ok else ResultValue.FAIL), [
            {"path": rule.get("path"), "expected": expected, "actual": value}
        ]

    if rule_type == "regex":
        found, value = get_path(context.output, rule.get("path", ""))
        if not found or not isinstance(value, str):
            return ResultValue.UNKNOWN, [{"missing_path": rule.get("path")}]
        ok = re.search(rule.get("pattern", ""), value) is not None
        return (ResultValue.PASS if ok else ResultValue.FAIL), [
            {"path": rule.get("path"), "pattern": rule.get("pattern")}
        ]

    if rule_type == "forbidden_substrings":
        text = _resolve_text(context, rule.get("path"))
        if text is None:
            return ResultValue.UNKNOWN, [{"missing_path": rule.get("path")}]
        hits = [v for v in rule.get("values", []) if v in text]
        if hits:
            return ResultValue.FAIL, [{"forbidden_found": hits}]
        return ResultValue.PASS, [{"forbidden_checked": rule.get("values", [])}]

    if rule_type == "output_not_empty":
        ok = bool(context.output)
        return (ResultValue.PASS if ok else ResultValue.FAIL), [
            {"output_keys": sorted(context.output)}
        ]

    if rule_type == "tool_called":
        at_least = int(rule.get("at_least", 1))
        wanted = rule.get("tool")
        count = _tool_call_count(context.events, wanted)
        ok = count >= at_least
        return (ResultValue.PASS if ok else ResultValue.FAIL), [
            {"tool": wanted, "calls": count, "required": at_least}
        ]

    if rule_type == "tool_not_called":
        wanted = rule.get("tool")
        count = _tool_call_count(context.events, wanted)
        ok = count == 0
        return (ResultValue.PASS if ok else ResultValue.FAIL), [
            {"tool": wanted, "calls": count}
        ]

    if rule_type == "artifact_present":
        wanted = rule.get("name")
        names = [a.get("name") for a in context.artifacts]
        ok = (wanted in names) if wanted else bool(names)
        return (ResultValue.PASS if ok else ResultValue.FAIL), [
            {"artifacts": names, "expected": wanted}
        ]

    return ResultValue.UNKNOWN, [{"unknown_rule_type": rule_type}]


def _resolve_text(context: EvaluationContext, path: str | None) -> str | None:
    if path:
        found, value = get_path(context.output, path)
        return str(value) if found and value is not None else None
    import json

    return json.dumps(context.output, ensure_ascii=False, default=str)


def _tool_call_count(events, wanted: str | None) -> int:
    from agent_platform.runtime.core.events import RuntimeEventType

    count = 0
    for event in events:
        if event.event_type is not RuntimeEventType.TOOL_CALL_COMPLETED:
            continue
        tool = event.payload.get("tool")
        if wanted is None or tool == wanted:
            count += 1
    return count


class RuleEvaluator:
    """确定性 Evaluator：执行 task 断言与 rubric criterion 规则。"""

    id = _EVALUATOR_ID

    def evaluate(self, context: EvaluationContext) -> list:
        results = []
        rules: list[tuple[str | None, dict]] = [
            (None, rule) for rule in context.task.success_criteria
        ]
        rules += [(None, rule) for rule in context.task.process_assertions]
        rules += [(None, rule) for rule in context.task.outcome_assertions]
        for rubric in context.rubrics:
            for criterion in rubric.criteria:
                if criterion.rule:
                    rules.append((rubric.id, {**criterion.rule, "id": criterion.id}))
        for rubric_id, rule in rules:
            value, evidence = _evaluate_rule(rule, context)
            results.append(
                make_result(
                    context,
                    _EVALUATOR_ID,
                    value,
                    criterion_id=rule.get("id"),
                    rubric_id=rubric_id,
                    evidence=evidence,
                    score=1.0 if value is ResultValue.PASS else 0.0,
                )
            )
        return results
