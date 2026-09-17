"""Task aggregate (evaluation-spec.md sections 2-3).

Task 是 Evaluation 的基本执行单元；Expected Behavior 内嵌于 Task，
与 Task 一起版本化（V1 简化，spec 中独立实体为后续可选拆分）。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ExpectedBehavior:
    """描述 Agent 应该如何完成任务，而不是只提供固定答案。"""

    description: str = ""
    required_actions: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    expected_outcome: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Task:
    id: str
    name: str
    input: dict = field(default_factory=dict)
    user_context: dict = field(default_factory=dict)
    expected_behavior: ExpectedBehavior = field(default_factory=ExpectedBehavior)
    # 可执行规则规格（rule.py 定义 schema），作用于 run output / outcome。
    success_criteria: list[dict] = field(default_factory=list)
    # 作用于执行事件（Trace）：如 {"type": "tool_called", "tool": "search"}。
    process_assertions: list[dict] = field(default_factory=list)
    # 作用于 outcome（artifacts / environment_state 等）。
    outcome_assertions: list[dict] = field(default_factory=list)
    environment_id: str | None = None
    suite_id: str | None = None
    version: str = "1"
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
