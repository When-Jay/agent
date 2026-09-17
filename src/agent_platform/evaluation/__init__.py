"""Evaluation platform: quality control loop over Runtime data.

05-evaluation-architecture.md / evaluation-spec.md. Evaluation consumes
Runtime events and Run outcomes through the standard dispatch path; it
never executes agents or workflows itself.
"""

from agent_platform.evaluation.application import EvaluationService
from agent_platform.evaluation.domain import (
    Criterion,
    EvaluationAsset,
    EvaluationEnvironment,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    ExpectedBehavior,
    QualityGate,
    Rubric,
    Task,
    Trial,
)
from agent_platform.evaluation.evaluators import (
    EvaluationContext,
    EvaluatorRegistry,
    LLMJudgeEvaluator,
    RuleEvaluator,
)
from agent_platform.evaluation.harness import TrialRunner
from agent_platform.evaluation.storage import EvaluationStore, InMemoryEvaluationStore

__all__ = [
    "Criterion",
    "EvaluationAsset",
    "EvaluationContext",
    "EvaluationEnvironment",
    "EvaluationResult",
    "EvaluationRun",
    "EvaluationService",
    "EvaluationStore",
    "EvaluationSuite",
    "ExpectedBehavior",
    "InMemoryEvaluationStore",
    "LLMJudgeEvaluator",
    "QualityGate",
    "Rubric",
    "RuleEvaluator",
    "Task",
    "Trial",
    "TrialRunner",
]
