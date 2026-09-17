"""Case / DiagnosisResult（evaluation-spec.md sections 15-16, 24; plan 050 Phase 8）。

Case 是 Online 与 Offline 之间的桥梁（架构文档 sections 16-17）：线上信号
（生产 Run、评测失败、用户反馈）经 Case Mining 沉淀为 Case，经 Diagnosis
归因后可提升进 Regression Set（plan 050 section 19 Second Stage 闭环）。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from agent_platform.evaluation.domain.task import new_id, utcnow


class CaseSource(str, Enum):
    """Case 来源（spec section 15）。"""

    USER_FEEDBACK = "USER_FEEDBACK"
    PRODUCTION_SAMPLE = "PRODUCTION_SAMPLE"
    MONITORING = "MONITORING"
    EVALUATION = "EVALUATION"
    RANDOM_SAMPLE = "RANDOM_SAMPLE"


class CaseType(str, Enum):
    """Case 分类（spec section 15）。"""

    GOOD = "GOOD"
    BAD = "BAD"
    UNCERTAIN = "UNCERTAIN"
    UNCOVERED = "UNCOVERED"


# Case 生命周期状态（V2 实现口径：OPEN → DIAGNOSED → PROMOTED，可 DISMISSED）。
CASE_OPEN = "OPEN"
CASE_DIAGNOSED = "DIAGNOSED"
CASE_PROMOTED = "PROMOTED"
CASE_DISMISSED = "DISMISSED"


@dataclass(frozen=True)
class Case:
    """一次线上/评测信号的沉淀（spec section 15）。

    trace_id 指向 Runtime Run id（与 Trial.trace_id 同一 trace 语义），
    通过它回查事件与执行过程；attribution 保存最近一次 Diagnosis 结果
    （spec section 16 CaseAttribution 的字典形态）。
    """

    id: str = field(default_factory=new_id)
    source: CaseSource = CaseSource.PRODUCTION_SAMPLE
    type: CaseType = CaseType.BAD
    status: str = CASE_OPEN
    task_id: str | None = None
    trace_id: str = ""
    input: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    expected_behavior: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    attribution: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


class DiagnosisCategory(str, Enum):
    """Diagnosis 一级归因（spec section 16/24）。"""

    AGENT_FAILURE = "AGENT_FAILURE"
    EVALUATION_FAILURE = "EVALUATION_FAILURE"
    COVERAGE_GAP = "COVERAGE_GAP"


class RecommendedAction(str, Enum):
    """Diagnosis 推荐动作（spec section 24）。"""

    AGENT_FIX = "AGENT_FIX"
    EVAL_FIX = "EVAL_FIX"
    ADD_COVERAGE = "ADD_COVERAGE"
    HUMAN_REVIEW = "HUMAN_REVIEW"


@dataclass(frozen=True)
class DiagnosisResult:
    """diagnose(case_id) 的输出（spec section 24）。

    V2 确定性规则只产出 AGENT_FAILURE 与 COVERAGE_GAP；
    EVALUATION_FAILURE 需要 Judge 校准证据，保留给 Phase 6。
    """

    id: str = field(default_factory=new_id)
    case_id: str = ""
    category: DiagnosisCategory = DiagnosisCategory.COVERAGE_GAP
    component: str = ""
    evidence: list = field(default_factory=list)
    confidence: float = 0.0
    recommended_action: RecommendedAction = RecommendedAction.HUMAN_REVIEW
    created_at: datetime = field(default_factory=utcnow)
