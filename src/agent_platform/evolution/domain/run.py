"""EvolutionRun / Experiment / Decision / Version（spec sections 9-10, 14, 18-19, 27）。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from agent_platform.evolution.domain.task import new_id, utcnow


class EvolutionRunStatus(str, Enum):
    """Run 生命周期（spec section 9）。失败可发生在任意阶段。"""

    CREATED = "CREATED"
    PLANNING = "PLANNING"
    GENERATING = "GENERATING"
    EXPERIMENTING = "EXPERIMENTING"
    EVALUATING = "EVALUATING"
    SELECTING = "SELECTING"
    GATING = "GATING"
    DECIDING = "DECIDING"
    WAITING_APPROVAL = "WAITING_APPROVAL"  # HUMAN_REVIEW 决策后的挂起态
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_TERMINAL_STATUSES = {
    EvolutionRunStatus.COMPLETED,
    EvolutionRunStatus.FAILED,
    EvolutionRunStatus.CANCELLED,
}


def is_terminal(status: EvolutionRunStatus) -> bool:
    return status in _TERMINAL_STATUSES


class ExperimentStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Experiment:
    """一个候选在受控环境 + 指定评测资产下的一次执行（spec section 10）。

    Evolution 不实现评测引擎：evaluation_run_id 指向 Evaluation 平台的
    评测 Run，summary 为其产出的指标汇总。
    """

    id: str = field(default_factory=new_id)
    evolution_run_id: str = ""
    candidate_id: str = ""
    purpose: str = "VALIDATION"  # VALIDATION | REGRESSION | CHALLENGE | OPTIMIZATION
    environment_id: str | None = None
    evaluation_run_id: str = ""
    agent_version: str = ""  # candidate:<candidate_id>
    status: ExperimentStatus = ExperimentStatus.COMPLETED
    summary: dict = field(default_factory=dict)
    error: str | None = None
    created_at: datetime = field(default_factory=utcnow)


class DecisionType(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    HUMAN_REVIEW = "HUMAN_REVIEW"


@dataclass(frozen=True)
class Approval:
    approver: str = ""
    decision: str = ""  # "approved" | "rejected"
    reason: str = ""
    timestamp: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class EvolutionDecision:
    """最终决策记录（spec section 14）：必须能回答"为什么接受这个候选"。"""

    id: str = field(default_factory=new_id)
    evolution_run_id: str = ""
    decision: DecisionType = DecisionType.REJECT
    selected_candidate_id: str | None = None
    rejected_candidate_ids: list[str] = field(default_factory=list)
    gate_result: dict = field(default_factory=dict)
    evaluation_summary: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    approval: Approval | None = None
    reason: str = ""
    created_at: datetime = field(default_factory=utcnow)


class DeploymentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class EvolutionVersion:
    """不可变版本（spec section 18）：内容一经创建不得修改；回滚只产生部署记录。"""

    id: str = field(default_factory=new_id)
    target_type: str = "SKILL"
    resource_id: str = ""
    version: str = "v1"
    parent_version: str = ""  # 父版本标签；种子版本为 ""
    candidate_id: str | None = None  # 种子/基线版本无候选
    evolution_run_id: str | None = None
    content: str = ""
    evaluation_summary: dict = field(default_factory=dict)
    approval: Approval | None = None
    deployment_status: DeploymentStatus = DeploymentStatus.SUPERSEDED
    created_at: datetime = field(default_factory=utcnow)


class DeploymentKind(str, Enum):
    RELEASE = "RELEASE"
    ROLLBACK = "ROLLBACK"


@dataclass(frozen=True)
class DeploymentRecord:
    """部署事件（spec section 19）：回滚引用历史版本，不改写任何版本内容。"""

    id: str = field(default_factory=new_id)
    target_type: str = "SKILL"
    resource_id: str = ""
    version_id: str = ""
    kind: DeploymentKind = DeploymentKind.RELEASE
    actor: str = ""
    reason: str = ""
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class EvolutionEvent:
    """Run 级生命周期事件（spec section 27），留存于 Evolution 侧证据链。

    与 Runtime 事件解耦：Evolution 不是 Runtime 执行，事件不进 RuntimeEventBus。
    """

    id: str = field(default_factory=new_id)
    evolution_run_id: str = ""
    event_type: str = ""  # EvolutionStarted / CandidateGenerated / ...
    payload: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class EvolutionRun:
    id: str = field(default_factory=new_id)
    task_id: str = ""
    target_type: str = "SKILL"
    resource_id: str = ""
    base_version: str = ""
    strategy_type: str = "MULTI_CANDIDATE"
    optimizer_name: str = ""
    status: EvolutionRunStatus = EvolutionRunStatus.CREATED
    error: str | None = None
    decision: EvolutionDecision | None = None
    evidence: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
