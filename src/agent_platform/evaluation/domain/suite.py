"""EvaluationSuite / EvaluationAsset / EvaluationEnvironment
（evaluation-spec.md sections 4, 5, 9）。
"""

from dataclasses import dataclass, field
from datetime import datetime

from agent_platform.evaluation.domain.task import utcnow

SUITE_E2E = "E2E"
SUITE_PROCESS = "PROCESS"

ASSET_GOLDEN = "GOLDEN"
ASSET_REGRESSION = "REGRESSION"
ASSET_CHALLENGE = "CHALLENGE"
ASSET_CALIBRATION = "CALIBRATION"
ASSET_INSPECTION = "INSPECTION"


@dataclass(frozen=True)
class EvaluationSuite:
    id: str
    name: str
    type: str = SUITE_E2E
    task_ids: list[str] = field(default_factory=list)
    rubric_ids: list[str] = field(default_factory=list)
    environment_id: str | None = None
    version: str = "1"
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class EvaluationAsset:
    """长期评测资产：Golden / Regression / Challenge / Calibration / Inspection。"""

    id: str
    name: str
    type: str = ASSET_GOLDEN
    task_ids: list[str] = field(default_factory=list)
    suite_id: str | None = None
    version: str = "1"
    source: str = ""
    metadata: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class EvaluationEnvironment:
    """V1：版本化环境描述符，记录配置与版本供 Trial 关联。

    Sandbox 预置 / workspace 快照恢复 / KB/MCP 版本固定属于保留能力
    （05-evaluation-architecture.md section 13 的完整形态）。
    """

    id: str
    name: str
    version: str = "1"
    config: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
