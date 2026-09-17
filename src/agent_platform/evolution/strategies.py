"""策略框架（plan 060 section 5）：可插拔的候选搜索方式。

策略持有优化器；同一 EvolutionTask 换策略不需要改 Evolution Manager。
ITERATIVE（多轮迭代搜索，plan 060 section 16）与 POPULATION 为后续阶段，
V1 显式拒绝而非静默降级。
"""

from typing import Protocol

from agent_platform.errors import PlatformError
from agent_platform.evolution.domain.candidate import EvolutionCandidate
from agent_platform.evolution.domain.task import (
    EvolutionStrategy,
    EvolutionTarget,
    EvolutionTask,
    StrategyType,
)


class StrategyError(PlatformError):
    """策略未实现或配置非法。"""


class GenerationStrategy(Protocol):
    def generate(
        self,
        task: EvolutionTask,
        target: EvolutionTarget,
        base_content: str,
        strategy_config: EvolutionStrategy,
    ) -> list[EvolutionCandidate]: ...


class SingleCandidateStrategy:
    """SINGLE_CANDIDATE：每次生成 1 个候选。"""

    def __init__(self, optimizer) -> None:
        self._optimizer = optimizer

    def generate(self, task, target, base_content, strategy_config):
        return self._optimizer.generate_candidates(
            task, target, base_content, strategy_config.edit_budget, count=1
        )


class MultiCandidateStrategy:
    """MULTI_CANDIDATE：按 candidate_count 生成多个候选。"""

    def __init__(self, optimizer) -> None:
        self._optimizer = optimizer

    def generate(self, task, target, base_content, strategy_config):
        count = max(1, int(strategy_config.candidate_count))
        return self._optimizer.generate_candidates(
            task, target, base_content, strategy_config.edit_budget, count=count
        )


_STRATEGIES: dict[StrategyType, type] = {
    StrategyType.SINGLE_CANDIDATE: SingleCandidateStrategy,
    StrategyType.MULTI_CANDIDATE: MultiCandidateStrategy,
}

_IMPLEMENTED = (StrategyType.SINGLE_CANDIDATE, StrategyType.MULTI_CANDIDATE)


def build_strategy(strategy_config: EvolutionStrategy, optimizer) -> GenerationStrategy:
    if strategy_config.type not in _IMPLEMENTED:
        raise StrategyError(
            f"strategy {strategy_config.type.value} is not implemented in V1 "
            f"(supported: {[s.value for s in _IMPLEMENTED]})"
        )
    return _STRATEGIES[strategy_config.type](optimizer)
