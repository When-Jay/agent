"""Evolution platform: controlled optimization loop over Evaluation results.

evolution-platform-spec.md / 060-evolution.md. Evolution 通过稳定接口消费
Evaluation（EvaluationGateway），编排候选生成、实验、选优、门禁、审批与
版本发布；不实现评测引擎，不执行 Runtime。
"""

from agent_platform.evolution.application import EvolutionService
from agent_platform.evolution.domain import (
    EvolutionCandidate,
    EvolutionRun,
    EvolutionTask,
    EvolutionVersion,
)
from agent_platform.evolution.experiment import (
    EvaluationGateway,
    EvaluationServiceGateway,
    ExperimentRunner,
)
from agent_platform.evolution.storage import EvolutionStore, InMemoryEvolutionStore

__all__ = [
    "EvaluationGateway",
    "EvaluationServiceGateway",
    "EvolutionCandidate",
    "EvolutionRun",
    "EvolutionService",
    "EvolutionStore",
    "EvolutionTask",
    "EvolutionVersion",
    "ExperimentRunner",
    "InMemoryEvolutionStore",
]
