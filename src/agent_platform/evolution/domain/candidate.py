"""EvolutionCandidate 与 Patch（evolution-platform-spec.md section 7-8）。

候选是"提议的修改"而不是已发布版本（architecture doc section 2.3）；
只有通过评测与门禁（及审批）后才成为不可变版本。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from agent_platform.evolution.domain.task import new_id, utcnow


class PatchOperation(str, Enum):
    ADD = "ADD"
    INSERT = "INSERT"
    REPLACE = "REPLACE"
    DELETE = "DELETE"


@dataclass(frozen=True)
class Patch:
    """单处编辑。文本目标（PROMPT/SKILL）按 markdown 小节寻址（patching.py）。

    * REPLACE/DELETE: path 为小节标题；old_value 为原小节正文（预算校验需要）。
    * ADD/INSERT: new_value 为含 "## 标题" 的完整小节文本；INSERT.path 为
      插入锚点小节（"" 表示插到最前）。
    """

    operation: PatchOperation = PatchOperation.REPLACE
    path: str = ""
    old_value: str | None = None
    new_value: str | None = None


class CandidateValidationStatus(str, Enum):
    PENDING = "PENDING"  # 尚未校验
    VALID = "VALID"
    INVALID = "INVALID"  # 语法/schema/预算校验失败：实验前拒绝（spec section 8）


@dataclass(frozen=True)
class EvolutionCandidate:
    id: str = field(default_factory=new_id)
    task_id: str = ""
    # 候选在 Run 内生成（spec 7 只列 task_id；run 归属用于存储与 API 查询）。
    evolution_run_id: str = ""
    base_version: str = ""
    patch: list[Patch] = field(default_factory=list)
    reason: str = ""
    evidence: list = field(default_factory=list)
    expected_impact: str = ""
    edit_summary: str = ""
    validation_status: CandidateValidationStatus = CandidateValidationStatus.PENDING
    validation_errors: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)
