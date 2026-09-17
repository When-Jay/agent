"""Online Evaluation: A/B 分流领域模型（evaluation-spec.md section 21）。

AB 的目标（架构文档 section 14）：真实用户流量验证业务效果。V1 边界：
平台提供 **分流与度量**（sticky 分流、逐 variant 结果聚合、Case 归因
入口）；variant 的 agent_version 为调用方提供的标签——平台尚无 Agent
版本化概念，执行侧行为切换为保留能力。
"""

from dataclasses import dataclass, field
from datetime import datetime

from agent_platform.evaluation.domain.task import new_id, utcnow

# ABTest 生命周期：DRAFT → RUNNING ⇄ PAUSED → COMPLETED。
AB_DRAFT = "DRAFT"
AB_RUNNING = "RUNNING"
AB_PAUSED = "PAUSED"
AB_COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class ABVariant:
    """一个实验分支：流量权重 + 版本标签（agent_version 由调用方提供）。"""

    key: str = "A"
    agent_version: str = ""
    weight: float = 1.0


@dataclass(frozen=True)
class ABTest:
    """一次线上 A/B 实验（OnlineEvaluation.mode=AB）。

    sampling_rate 为参与实验的会话比例；未入选流量不产生 Assignment，
    按原路径执行。同一 (application_id, runtime_type) 同时只允许一个
    RUNNING 实验。
    """

    id: str = field(default_factory=new_id)
    name: str = ""
    application_id: str = ""
    runtime_type: str = "agent"
    variants: list[ABVariant] = field(default_factory=list)
    sampling_rate: float = 1.0
    status: str = AB_DRAFT
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class ABAssignment:
    """Run 与 variant 的绑定结果（分流的事实记录，供报表与 Case 归因）。"""

    id: str = field(default_factory=new_id)
    ab_test_id: str = ""
    run_id: str = ""
    session_id: str = ""
    variant_key: str = ""
    agent_version: str = ""
    created_at: datetime = field(default_factory=utcnow)
