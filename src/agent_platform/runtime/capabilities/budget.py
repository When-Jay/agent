"""Budget capability interface with an in-memory default implementation."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetSpec:
    """Limits enforced by a budget implementation; None means unlimited."""

    max_tokens: int | None = None
    max_cost: float | None = None
    max_tool_calls: int | None = None


class BudgetCapability(ABC):
    """Consumption accounting used to trigger budget-based stops."""

    @abstractmethod
    def record(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost: float = 0.0,
        tool_calls: int = 0,
    ) -> None:
        """Accumulate consumption deltas."""

    @abstractmethod
    def usage(self) -> dict[str, float]:
        """Return the accumulated consumption per dimension."""

    @abstractmethod
    def exceeded(self) -> str | None:
        """Return the name of the first exceeded dimension, or None."""


class InMemoryBudget(BudgetCapability):
    """Process-local budget tracker; sufficient for V1 and tests."""

    def __init__(self, spec: BudgetSpec | None = None) -> None:
        self._spec = spec or BudgetSpec()
        self._usage: dict[str, float] = {
            "input_tokens": 0.0,
            "output_tokens": 0.0,
            "cost": 0.0,
            "tool_calls": 0.0,
        }

    def record(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost: float = 0.0,
        tool_calls: int = 0,
    ) -> None:
        self._usage["input_tokens"] += input_tokens
        self._usage["output_tokens"] += output_tokens
        self._usage["cost"] += cost
        self._usage["tool_calls"] += tool_calls

    def usage(self) -> dict[str, float]:
        return dict(self._usage)

    def exceeded(self) -> str | None:
        limits = self._spec
        total_tokens = self._usage["input_tokens"] + self._usage["output_tokens"]
        if limits.max_tokens is not None and total_tokens >= limits.max_tokens:
            return "tokens"
        if limits.max_cost is not None and self._usage["cost"] >= limits.max_cost:
            return "cost"
        if limits.max_tool_calls is not None and self._usage["tool_calls"] >= limits.max_tool_calls:
            return "tool_calls"
        return None
