"""Rubric 评价标准（evaluation-spec.md section 6）。

Rubric 定义"评价什么"，Evaluator 定义"怎么评价"（架构文档 section 9）。
Criterion 优先使用二元判定；rule 字段是可执行规则规格（rule.py schema），
供确定性 Evaluator 直接执行。
"""

from dataclasses import dataclass, field
from datetime import datetime

from agent_platform.evaluation.domain.task import utcnow


@dataclass(frozen=True)
class Criterion:
    id: str
    description: str = ""
    # BINARY | CATEGORICAL | NUMERIC | SCALAR（spec 推荐 BINARY）
    type: str = "BINARY"
    # 可执行规则规格；为空时该 criterion 只能由 LLM Judge / Human 判定。
    rule: dict = field(default_factory=dict)
    weight: float = 1.0
    expected_evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Rubric:
    id: str
    name: str
    dimension: str = ""
    criteria: list[Criterion] = field(default_factory=list)
    # {"strategy": "all_pass"} | {"strategy": "weighted"}
    aggregation: dict = field(default_factory=lambda: {"strategy": "all_pass"})
    version: str = "1"
    created_at: datetime = field(default_factory=utcnow)
