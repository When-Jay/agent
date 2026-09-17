"""候选选择（plan 060 section 10 / architecture doc section 6）。

三段式：硬约束过滤 -> 目标比较 -> 选择。V1 使用字典序多目标比较，
不合成单一加权分。选择逻辑属于 Evolution；质量门禁属于 Evaluation
（governance.py 只调用不实现）。
"""

import math

from agent_platform.evolution.domain.task import (
    HardConstraint,
    OptimizationObjective,
)


def resolve_metric(summaries: list[dict], metric: str) -> float | None:
    """按顺序在多个评测 summary 中解析指标（先命中先用）。

    支持 "task_pass_rate:<task_id>" 形式（与 Evaluation 的 gate 规则一致）。
    """
    for summary in summaries:
        if metric.startswith("task_pass_rate:"):
            stats = (summary.get("per_task") or {}).get(metric.split(":", 1)[1])
            value = stats.get("pass_rate") if stats else None
        else:
            value = summary.get(metric)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def hard_constraint_filter(
    candidates: list,
    constraints: list[HardConstraint],
    metrics: dict[str, list[dict]],
) -> tuple[list, dict[str, list[str]]]:
    """Stage 1：返回 (有效候选, {候选id: 违规说明})。

    指标缺失视为未通过（不强行判定，与 Evaluation UNKNOWN 语义一致）。
    """
    valid: list = []
    rejected: dict[str, list[str]] = {}
    for candidate in candidates:
        summaries = metrics.get(candidate.id, [])
        failures: list[str] = []
        for constraint in constraints:
            value = resolve_metric(summaries, constraint.metric)
            if value is None:
                failures.append(
                    f"{constraint.metric}: metric missing from evaluation summaries"
                )
                continue
            if not _compare(value, constraint.op, constraint.threshold):
                failures.append(
                    f"{constraint.metric}={value} violates "
                    f"{constraint.metric} {constraint.op} {constraint.threshold}"
                )
        if failures:
            rejected[candidate.id] = failures
        else:
            valid.append(candidate)
    return valid, rejected


def _compare(value: float, op: str, threshold: float) -> bool:
    return {
        ">=": value >= threshold,
        "<=": value <= threshold,
        ">": value > threshold,
        "<": value < threshold,
        "==": math.isclose(value, threshold),
    }.get(op, False)


def lexicographic_compare(
    a: list[dict], b: list[dict], objectives: list[OptimizationObjective]
) -> int:
    """逐目标字典序比较：-1 表示 a 更优，1 表示 b 更优，0 表示无差异。

    无有效优化目标时按 pass_rate 兜底（最大化）。
    """
    effective = objectives or [OptimizationObjective(metric="pass_rate")]
    for objective in effective:
        value_a = resolve_metric(a, objective.metric)
        value_b = resolve_metric(b, objective.metric)
        if value_a is None and value_b is None:
            continue
        if value_a is None:
            return 1
        if value_b is None:
            return -1
        if math.isclose(value_a, value_b):
            continue
        better = value_a > value_b if objective.direction == "maximize" else value_a < value_b
        return -1 if better else 1
    return 0


def select_best(
    candidates: list, metrics: dict[str, list[dict]], objectives: list[OptimizationObjective]
):
    """Stage 2/3：返回最优有效候选；无可比候选时返回 None。"""
    best = None
    best_summaries: list[dict] = []
    for candidate in candidates:
        summaries = metrics.get(candidate.id, [])
        if best is None:
            best, best_summaries = candidate, summaries
            continue
        verdict = lexicographic_compare(summaries, best_summaries, objectives)
        if verdict < 0:
            best, best_summaries = candidate, summaries
    return best
