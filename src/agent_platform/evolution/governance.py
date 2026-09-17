"""治理：门禁调用、风险策略与自动发布条件（plan 060 sections 11-12 / spec 15-17）。

门禁配置属于 Evaluation Governance；本模块只封装"调用 + 结果解读"。
自动发布条件（spec section 17）全部满足才允许 ACCEPT，否则 HUMAN_REVIEW。
"""

from agent_platform.evolution.domain.task import RiskLevel


GATE_PASS = "PASS"
GATE_FAIL = "FAIL"
GATE_SKIPPED = "SKIPPED"

# 自动发布条件清单（spec section 17）的顺序化键，供 evidence 呈现。
AUTO_RELEASE_CONDITIONS = (
    "HARD_CONSTRAINTS",
    "REGRESSION",
    "CHALLENGE",
    "EDIT_BUDGET",
    "RISK_POLICY",
    "QUALITY_GATE",
    "AUTO_RELEASE_ENABLED",
)


def risk_policy_allows_auto_release(risk_level: RiskLevel) -> bool:
    """V1：仅 LOW 风险可自动发布；MEDIUM/HIGH 必须人工审批。"""
    return risk_level is RiskLevel.LOW


def gate_outcome(gate_result: dict | None) -> str:
    """解读门禁调用结果：BLOCK -> FAIL，PASS -> PASS，未配置 -> SKIPPED。"""
    if not gate_result:
        return GATE_SKIPPED
    action = str(gate_result.get("action") or "")
    if action == "PASS":
        return GATE_PASS
    return GATE_FAIL


def evaluate_auto_release(
    *,
    hard_constraints_pass: bool,
    regression_pass: bool,
    challenge_pass: bool,
    edit_budget_pass: bool,
    risk_level: RiskLevel,
    gate_result: dict | None,
    auto_release_enabled: bool,
) -> tuple[bool, dict[str, bool], str]:
    """返回 (是否允许自动接受, 条件明细, 原因)。

    未配置的评测资产（regression/challenge/gate）视为该条件通过 ——
    约束来自任务配置而非隐式失败；硬约束在 SELECTING 阶段已过滤。
    """
    conditions = {
        "HARD_CONSTRAINTS": hard_constraints_pass,
        "REGRESSION": regression_pass,
        "CHALLENGE": challenge_pass,
        "EDIT_BUDGET": edit_budget_pass,
        "RISK_POLICY": risk_policy_allows_auto_release(risk_level),
        "QUALITY_GATE": gate_outcome(gate_result) != GATE_FAIL,
        "AUTO_RELEASE_ENABLED": auto_release_enabled,
    }
    allowed = all(conditions.values())
    blocked = [name for name, ok in conditions.items() if not ok]
    reason = "all auto-release conditions satisfied" if allowed else (
        f"auto release blocked by: {', '.join(blocked)}"
    )
    return allowed, conditions, reason
