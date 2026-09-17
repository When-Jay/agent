"""EvaluationRun / Trial / EvaluationResult（evaluation-spec.md sections 10, 11, 17）。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from agent_platform.evaluation.domain.task import new_id, utcnow


class EvaluationRunStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class TrialStatus(str, Enum):
    RUNNING = "RUNNING"
    # 评估裁决：执行终态后由 Evaluator 结果得出。
    PASSED = "PASSED"
    FAILED = "FAILED"
    # harness 级终态：执行超时 / harness 异常 / 取消。
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


class ResultValue(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    # UNKNOWN 是一等结果（spec section 18），不得强行判定。
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


@dataclass(frozen=True)
class EvaluationRun:
    """一次离线评测：suite x agent_version x environment_version。"""

    id: str
    suite_id: str
    application_id: str
    runtime_type: str = "agent"
    agent_version: str = "dev"
    environment_id: str | None = None
    environment_version: str = "unversioned"
    trials_per_task: int = 1
    evaluator_ids: list[str] = field(default_factory=lambda: ["rule"])
    status: EvaluationRunStatus = EvaluationRunStatus.RUNNING
    summary: dict = field(default_factory=dict)
    error: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None


@dataclass(frozen=True)
class Trial:
    """Task 的一次执行尝试；run_id/trace_id 指向 Runtime Run（即 Langfuse trace）。"""

    id: str
    task_id: str
    evaluation_run_id: str
    attempt: int = 1
    agent_version: str = "dev"
    environment_version: str = "unversioned"
    status: TrialStatus = TrialStatus.RUNNING
    run_id: str = ""
    trace_id: str = ""
    # Outcome（spec section 12）: task_completed / output / artifacts / error ...
    outcome: dict = field(default_factory=dict)
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None


@dataclass(frozen=True)
class EvaluationResult:
    """单个 Evaluator 对单个 criterion 的判定。"""

    id: str = field(default_factory=new_id)
    task_id: str = ""
    run_id: str = ""  # evaluation run id
    trial_id: str = ""
    evaluator_id: str = ""
    rubric_id: str | None = None
    criterion_id: str | None = None
    result: ResultValue = ResultValue.UNKNOWN
    score: float | None = None
    evidence: list = field(default_factory=list)
    confidence: float | None = None
    created_at: datetime = field(default_factory=utcnow)
