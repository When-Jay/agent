"""Quality Gate（evaluation-spec.md section 27 / 架构文档 section 22）。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from agent_platform.evaluation.domain.task import utcnow


class GateSeverity(str, Enum):
    BLOCK = "BLOCK"
    WARN = "WARN"
    INFO = "INFO"


class GateAction(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class GateRule:
    """对 run summary 中的指标做阈值判断。

    metric: "pass_rate" | "pass_at_k" | "task_pass_rate:<task_id>"
    """

    id: str
    metric: str
    threshold: float
    op: str = ">="  # >= | <= | > | < | ==
    severity: GateSeverity = GateSeverity.BLOCK


@dataclass(frozen=True)
class QualityGate:
    id: str
    name: str
    rules: list[GateRule] = field(default_factory=list)
    version: str = "1"
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class GateRuleResult:
    rule_id: str
    metric: str
    value: float | None
    threshold: float
    op: str
    severity: GateSeverity
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class GateDecision:
    gate_id: str
    action: GateAction
    rule_results: list[GateRuleResult] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)
