"""Evolution domain: task / target / objective / strategy / budget.

evolution-platform-spec.md sections 3-8. EvolutionTask 描述"为什么要进化"，
是一个问题定义而不是实验；EvolutionRun 才是具体实验（architecture doc
section 5.1）。本模块不依赖任何其他平台模块（evaluation/runtime 均不导入），
依赖方向：evolution -> evaluation（消费评测结果），见 architecture doc section 4。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TargetType(str, Enum):
    """EvolutionTarget 类型（spec section 4）。V1 支持 PROMPT/SKILL/RAG 文本类目标。"""

    PROMPT = "PROMPT"
    SKILL = "SKILL"
    RAG = "RAG"
    TOOL = "TOOL"
    POLICY = "POLICY"
    MODEL = "MODEL"
    CONTEXT = "CONTEXT"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TaskStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StrategyType(str, Enum):
    SINGLE_CANDIDATE = "SINGLE_CANDIDATE"
    MULTI_CANDIDATE = "MULTI_CANDIDATE"
    # ITERATIVE / POPULATION: 后续阶段（plan 060 section 16），V1 未实现。
    ITERATIVE = "ITERATIVE"
    POPULATION = "POPULATION"


@dataclass(frozen=True)
class HardConstraint:
    """硬约束：违反即淘汰，与其它改进无关（architecture doc section 6）。"""

    metric: str
    op: str = ">="
    threshold: float = 0.0


@dataclass(frozen=True)
class OptimizationObjective:
    metric: str
    direction: str = "maximize"  # maximize | minimize


@dataclass(frozen=True)
class EvolutionObjective:
    hard_constraints: list[HardConstraint] = field(default_factory=list)
    # 按列表顺序做字典序比较（plan 060 section 10：avoid a single opaque score）。
    optimization_objectives: list[OptimizationObjective] = field(default_factory=list)


@dataclass(frozen=True)
class EditBudget:
    """有界修改预算（spec section 8）：违反预算的候选必须在实验前被拒绝。"""

    max_edits: int = 3
    max_tokens_added: int = 200
    max_tokens_removed: int = 100
    allowed_operations: list[str] = field(
        default_factory=lambda: ["ADD", "INSERT", "REPLACE", "DELETE"]
    )
    allowed_sections: list[str] = field(default_factory=list)  # 空 = 不限制


@dataclass(frozen=True)
class EvolutionTarget:
    """可被进化的资源引用；base_version 在一次 EvolutionRun 期间不可变。"""

    type: TargetType = TargetType.SKILL
    resource_id: str = ""
    base_version: str = ""  # target 版本注册表中的版本标签，如 "v12"
    editable_scope: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW


@dataclass(frozen=True)
class EvolutionStrategy:
    """候选搜索方式（spec section 6）。optimizer 名解析自优化器注册表。"""

    type: StrategyType = StrategyType.MULTI_CANDIDATE
    optimizer: str = "skill_optimizer"
    candidate_count: int = 3
    max_iterations: int = 1
    edit_budget: EditBudget = field(default_factory=EditBudget)
    selection_strategy: str = "BEST_VALID_CANDIDATE"
    stop_condition: str = "MAX_ITERATIONS"
    generation_model: str = ""


@dataclass(frozen=True)
class EvolutionTask:
    """进化任务：问题定义（trigger/diagnosis）+ 目标 + 目标函数 + 资产绑定。"""

    id: str = field(default_factory=new_id)
    name: str = ""
    description: str = ""
    # trigger: {"type": "production_bad_case", "case_ids": [...], ...}
    trigger: dict = field(default_factory=dict)
    # diagnosis: {"type": "SKILL_FAILURE", ...}（诊断映射表见 optimizers.py）
    diagnosis: dict = field(default_factory=dict)
    target: EvolutionTarget = field(default_factory=EvolutionTarget)
    objective: EvolutionObjective = field(default_factory=EvolutionObjective)
    # 人读业务约束（spec section 3.1）；机器可校验约束在 objective.hard_constraints。
    constraints: list[str] = field(default_factory=list)
    # 评测资产绑定（spec section 12 用途分离）:
    # {"application_id", "runtime_type", "suites": {"validation", "regression"?,
    #  "challenge"?, "optimization"?}, "gate_id"?}
    evaluation_assets: dict = field(default_factory=dict)
    strategy: EvolutionStrategy = field(default_factory=EvolutionStrategy)
    risk_level: RiskLevel = RiskLevel.LOW
    # 自动发布总开关（spec section 17）：LOW 风险且全部门禁通过才可能自动接受。
    auto_release: bool = False
    status: TaskStatus = TaskStatus.READY
    created_at: datetime = field(default_factory=utcnow)
    created_by: str = ""
