"""Budget capability: multi-dimensional run budgets (budget-steering-spec.md).

一个 Run 的资源预算覆盖 Time / Token / Turn 维度（cost / tool_calls 为
保留维度）。每个维度都有 soft / hard 阈值：

* Soft Limit（soft_ratio 默认 0.8）→ ``FINISHING``：不 kill Agent，由
  BudgetMiddleware 在下一次 Model Call 前注入 Runtime Notice，引导 Agent
  收敛（graceful finishing）。
* Hard Limit → 强制终止：BudgetMiddleware 抛 BudgetExceededError；
  LLM/Tool 挂死场景由 adapter 层的 watchdog 超时兜底（spec section 26）。

``BudgetCapability`` 是对外稳定接口；``InMemoryBudget`` 是默认实现，
同时承担 spec section 24 的 BudgetManager 决策计算职责。
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

# spec section 12: RUNNING -> FINISHING -> EXHAUSTED
PHASE_RUNNING = "RUNNING"
PHASE_FINISHING = "FINISHING"
PHASE_EXHAUSTED = "EXHAUSTED"


@dataclass(frozen=True)
class BudgetSpec:
    """Limits enforced by a budget implementation; None means unlimited."""

    max_tokens: int | None = None  # total tokens (spec: max_total_tokens)
    max_cost: float | None = None
    max_tool_calls: int | None = None
    max_time_ms: int | None = None
    max_turns: int | None = None  # final turn 计入 max_turns（spec section 22）
    soft_ratio: float = 0.8
    grace_time_ms: int = 60_000
    max_final_turns: int = 1


@dataclass
class BudgetState:
    """Accumulated consumption and lifecycle phase (spec section 12)."""

    elapsed_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    turns: int = 0
    tool_calls: int = 0
    cost: float = 0.0
    phase: str = PHASE_RUNNING
    triggered_limits: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BudgetDecision:
    """Budget checker output (spec section 13); the checker never stops the
    agent itself — the middleware maps decisions to notices/errors."""

    exceeded: bool
    entering_finishing: bool
    hard_exceeded: bool
    triggered_dimensions: list[str] = field(default_factory=list)
    newly_soft: list[str] = field(default_factory=list)
    newly_hard: list[str] = field(default_factory=list)
    remaining: dict[str, float | None] = field(default_factory=dict)
    phase: str = PHASE_RUNNING


class BudgetCapability(ABC):
    """Consumption accounting used to trigger budget-based stops.

    Subclasses only implementing record/usage/exceeded keep working: the
    default ``decision``/``enter_turn``/``timeout_seconds`` below degrade to
    the legacy hard-only semantics.
    """

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

    def enter_turn(self) -> None:
        """Mark the start of one agent model decision cycle (spec section 21)."""

    def decision(self) -> BudgetDecision:
        """Current budget decision; defaults to legacy hard-only semantics."""
        dimension = self.exceeded()
        hard = dimension is not None
        return BudgetDecision(
            exceeded=hard,
            entering_finishing=False,
            hard_exceeded=hard,
            triggered_dimensions=[dimension] if dimension else [],
            newly_hard=[dimension] if dimension else [],
            phase=PHASE_EXHAUSTED if hard else PHASE_RUNNING,
        )

    def timeout_seconds(self) -> float | None:
        """Watchdog budget cap for the agent loop, if any (spec section 26)."""
        return None


class InMemoryBudget(BudgetCapability):
    """Process-local multi-dimensional budget engine (spec sections 11-24).

    One instance covers one run: the adapter's budget factory creates it at
    run start, so the monotonic clock origin approximates the run start
    (spec section 18).
    """

    def __init__(
        self,
        spec: BudgetSpec | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._spec = spec or BudgetSpec()
        self._clock = clock
        self._started_at = clock()
        self._usage: dict[str, float] = {
            "input_tokens": 0.0,
            "output_tokens": 0.0,
            "cost": 0.0,
            "tool_calls": 0.0,
        }
        self._turns = 0
        self._phase = PHASE_RUNNING
        self._soft_seen: set[str] = set()
        self._hard_seen: set[str] = set()
        self._finishing_model_calls = 0
        self._finishing_announced = False
        self._finishing_started_at: float | None = None

    # --- accounting ---------------------------------------------------------

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
        """Legacy hard-only view: first exceeded hard dimension, or None."""
        limits = self._spec
        total_tokens = self._usage["input_tokens"] + self._usage["output_tokens"]
        if limits.max_tokens is not None and total_tokens >= limits.max_tokens:
            return "tokens"
        if limits.max_cost is not None and self._usage["cost"] >= limits.max_cost:
            return "cost"
        if limits.max_tool_calls is not None and self._usage["tool_calls"] >= limits.max_tool_calls:
            return "tool_calls"
        if limits.max_time_ms is not None and self._elapsed_ms() >= limits.max_time_ms:
            return "time"
        if limits.max_turns is not None and self._turns >= limits.max_turns:
            return "turns"
        return None

    # --- decision engine ----------------------------------------------------

    def enter_turn(self) -> None:
        """Count one model decision cycle; final turns are budgeted too."""
        if self._phase is PHASE_FINISHING:
            self._finishing_model_calls += 1
        self._turns += 1

    def decision(self) -> BudgetDecision:
        limits = self._spec
        elapsed_ms = self._elapsed_ms()
        current = {
            "time": float(elapsed_ms),
            "tokens": self._usage["input_tokens"] + self._usage["output_tokens"],
            "turns": float(self._turns),
            "cost": self._usage["cost"],
            "tool_calls": self._usage["tool_calls"],
        }
        thresholds: dict[str, tuple[float | None, float | None]] = {
            # (soft, hard) per dimension; None means the dimension is off.
            "time": (
                float(limits.max_time_ms * limits.soft_ratio) if limits.max_time_ms else None,
                float(limits.max_time_ms) if limits.max_time_ms else None,
            ),
            "tokens": (
                float(limits.max_tokens * limits.soft_ratio) if limits.max_tokens else None,
                float(limits.max_tokens) if limits.max_tokens else None,
            ),
            "turns": (
                float(limits.max_turns * limits.soft_ratio) if limits.max_turns else None,
                float(limits.max_turns) if limits.max_turns else None,
            ),
            "cost": (
                float(limits.max_cost * limits.soft_ratio) if limits.max_cost else None,
                float(limits.max_cost) if limits.max_cost else None,
            ),
            "tool_calls": (
                float(limits.max_tool_calls * limits.soft_ratio)
                if limits.max_tool_calls
                else None,
                float(limits.max_tool_calls) if limits.max_tool_calls else None,
            ),
        }

        newly_soft: list[str] = []
        newly_hard: list[str] = []
        triggered: list[str] = list(self._soft_seen | self._hard_seen)
        for dimension, (soft, hard) in thresholds.items():
            if hard is not None and current[dimension] >= hard and dimension not in self._hard_seen:
                self._hard_seen.add(dimension)
                newly_hard.append(dimension)
            elif soft is not None and current[dimension] >= soft and dimension not in self._soft_seen:
                self._soft_seen.add(dimension)
                newly_soft.append(dimension)
        triggered = list(self._soft_seen | self._hard_seen)

        # FINISHING exhaustion (spec sections 15/17): the grace window and
        # the final-turn allowance both force a hard stop.
        if (
            self._phase is PHASE_FINISHING
            and self._finishing_started_at is not None
            and (self._clock() - self._finishing_started_at) * 1000 > limits.grace_time_ms
        ):
            newly_hard.append("time")
        if self._phase is PHASE_FINISHING and limits.max_final_turns > 0:
            if self._finishing_model_calls > limits.max_final_turns:
                newly_hard.append("final_turns")

        if self._phase is not PHASE_EXHAUSTED and newly_hard:
            self._phase = PHASE_EXHAUSTED
        elif newly_soft and self._phase is PHASE_RUNNING:
            self._phase = PHASE_FINISHING
            self._finishing_started_at = self._clock()
        entering_finishing = self._phase is PHASE_FINISHING and not self._finishing_announced
        if entering_finishing:
            self._finishing_announced = True

        remaining: dict[str, float | None] = {}
        for dimension, (soft, hard) in thresholds.items():
            if hard is not None:
                remaining[dimension] = max(0.0, hard - current[dimension])
        return BudgetDecision(
            exceeded=bool(self._hard_seen),
            entering_finishing=entering_finishing,
            hard_exceeded=bool(newly_hard) or self._phase is PHASE_EXHAUSTED,
            triggered_dimensions=triggered,
            newly_soft=newly_soft,
            newly_hard=newly_hard,
            remaining=remaining,
            phase=self._phase,
        )

    def state(self) -> BudgetState:
        total_tokens = self._usage["input_tokens"] + self._usage["output_tokens"]
        return BudgetState(
            elapsed_ms=self._elapsed_ms(),
            input_tokens=int(self._usage["input_tokens"]),
            output_tokens=int(self._usage["output_tokens"]),
            total_tokens=total_tokens,
            turns=self._turns,
            tool_calls=int(self._usage["tool_calls"]),
            cost=self._usage["cost"],
            phase=self._phase,
            triggered_limits=sorted(self._soft_seen | self._hard_seen),
        )

    def timeout_seconds(self) -> float | None:
        """Watchdog cap: the absolute max_time hard limit (spec section 26)."""
        if self._spec.max_time_ms is None:
            return None
        return max(0.0, self._spec.max_time_ms / 1000)

    def _elapsed_ms(self) -> int:
        return int((self._clock() - self._started_at) * 1000)
