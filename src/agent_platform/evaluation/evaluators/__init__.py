"""Evaluator package: pluggable evaluation mechanisms (plan 050 section 8)."""

from agent_platform.evaluation.evaluators.base import (
    EvaluationContext,
    Evaluator,
    EvaluatorRegistry,
    make_result,
    rule_result_value,
)
from agent_platform.evaluation.evaluators.llm_judge import LLMJudgeEvaluator
from agent_platform.evaluation.evaluators.rule import RuleEvaluator

__all__ = [
    "EvaluationContext",
    "Evaluator",
    "EvaluatorRegistry",
    "LLMJudgeEvaluator",
    "RuleEvaluator",
    "make_result",
    "rule_result_value",
]
