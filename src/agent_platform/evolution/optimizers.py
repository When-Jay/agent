"""优化器插件框架（plan 060 section 6 / spec section 22）。

优化器接口与目标类型无关；目标特定逻辑在各实现内。生成能力通过注入的
`model_fn: Callable[[str], str]` 端口进入（与 LLMJudgeEvaluator 相同的
可替换原则），evolution 模块不绑定任何 SDK。未配置 model_fn 时生成阶段
显式失败 —— 绝不静默产出空转候选。
"""

import json
import logging
import re
from typing import Callable, Protocol

from agent_platform.errors import PlatformError
from agent_platform.evolution.domain.candidate import (
    CandidateValidationStatus,
    EvolutionCandidate,
    Patch,
    PatchOperation,
)
from agent_platform.evolution.domain.task import (
    EvolutionTask,
    EvolutionTarget,
    StrategyType,
)
from agent_platform.evolution.patching import summarize_edits, validate_patches

logger = logging.getLogger(__name__)

ModelFn = Callable[[str], str]


class OptimizerError(PlatformError):
    """候选生成失败（生成模型缺失 / 输出不可解析）。"""


class EvolutionOptimizer(Protocol):
    """优化器端口（spec section 22）：generate_candidates 与目标类型无关。"""

    name: str

    def generate_candidates(
        self,
        task: EvolutionTask,
        target: EvolutionTarget,
        base_content: str,
        budget,
        count: int,
    ) -> list[EvolutionCandidate]: ...


_PROMPT_TEMPLATE = """You are optimizing the {target_type} "{resource_id}" (version {base_version}).

## Diagnosis
{diagnosis}

## Trigger cases
{trigger}

## Current content
{content}

## Edit budget
- max edits: {max_edits}
- max tokens added: {max_tokens_added}
- max tokens removed: {max_tokens_removed}
- allowed operations: {allowed_operations}
- allowed sections: {allowed_sections}

Produce {count} distinct candidate improvement(s). Respond with a JSON array only;
each item is an object:
{{"patches": [{{"operation": "ADD|INSERT|REPLACE|DELETE", "path": "<section title>",
"old_value": "<required for REPLACE/DELETE>", "new_value": "<new text>"}}],
"reason": "<why this change helps>", "evidence": ["<supporting fact>"],
"expected_impact": "<expected metric impact>"}}
Respect the edit budget; out-of-budget candidates are rejected before experiments.
"""

# 诊断 -> 优化器映射（spec section 24）。可版本化；V1 为模块级常量。
DIAGNOSIS_OPTIMIZER_MAP: dict[str, str] = {
    "PROMPT_FAILURE": "prompt_optimizer",
    "SKILL_FAILURE": "skill_optimizer",
    "RETRIEVAL_RECALL_FAILURE": "rag_optimizer",
    "RETRIEVAL_PRECISION_FAILURE": "rag_optimizer",
    "TOOL_SELECTION_FAILURE": "tool_optimizer",
    "TOOL_ARGUMENT_FAILURE": "tool_optimizer",
    "CONTEXT_OVERFLOW": "context_optimizer",
    "MODEL_CAPABILITY_FAILURE": "model_selector",
    "EXCESSIVE_COST": "cost_optimizer",
    "EXCESSIVE_LATENCY": "performance_optimizer",
}


class SkillOptimizer:
    """Skill 目标优化器（plan 060 section 6：有界文本编辑模型）。

    model_fn 负责产出候选补丁 JSON；本类负责提示词构造、解析、预算校验
    与候选装配。校验失败的候选保留为 INVALID（历史证据，不进入实验）。
    """

    name = "skill_optimizer"

    def __init__(self, model_fn: ModelFn | None = None) -> None:
        self._model_fn = model_fn

    def generate_candidates(
        self,
        task: EvolutionTask,
        target: EvolutionTarget,
        base_content: str,
        budget,
        count: int,
    ) -> list[EvolutionCandidate]:
        if self._model_fn is None:
            raise OptimizerError(
                "no generation model configured for skill_optimizer; "
                "provide one at composition time"
            )
        budget_obj = budget
        prompt = _PROMPT_TEMPLATE.format(
            target_type=target.type.value,
            resource_id=target.resource_id,
            base_version=target.base_version,
            diagnosis=json.dumps(task.diagnosis, ensure_ascii=False, default=str),
            trigger=json.dumps(task.trigger, ensure_ascii=False, default=str),
            content=base_content,
            max_edits=budget_obj.max_edits,
            max_tokens_added=budget_obj.max_tokens_added,
            max_tokens_removed=budget_obj.max_tokens_removed,
            allowed_operations=", ".join(budget_obj.allowed_operations),
            allowed_sections=", ".join(budget_obj.allowed_sections) or "(any)",
            count=count,
        )
        response = self._model_fn(prompt)
        proposals = _parse_proposals(response)
        candidates: list[EvolutionCandidate] = []
        for proposal in proposals[: max(1, count)]:
            patches = _parse_patches(proposal.get("patches") or [])
            violations = validate_patches(patches, budget_obj)
            status = (
                CandidateValidationStatus.VALID
                if not violations
                else CandidateValidationStatus.INVALID
            )
            evidence = [str(item) for item in proposal.get("evidence") or []]
            if violations:
                evidence.extend(f"budget violation: {v}" for v in violations)
            candidates.append(
                EvolutionCandidate(
                    task_id=task.id,
                    base_version=target.base_version,
                    patch=patches,
                    reason=str(proposal.get("reason") or ""),
                    evidence=evidence,
                    expected_impact=str(proposal.get("expected_impact") or ""),
                    edit_summary=summarize_edits(patches),
                    validation_status=status,
                    validation_errors=violations,
                )
            )
        return candidates


class PromptOptimizer(SkillOptimizer):
    """Prompt 目标复用同一有界文本编辑模型（后续可拆分策略）。"""

    name = "prompt_optimizer"


def _parse_proposals(response: str) -> list[dict]:
    match = re.search(r"\[.*\]", response, re.DOTALL)
    if not match:
        raise OptimizerError("optimizer output contained no JSON array")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise OptimizerError(f"optimizer output is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise OptimizerError("optimizer output must be a JSON array")
    return [item for item in data if isinstance(item, dict)]


def _parse_patches(items: list) -> list[Patch]:
    patches: list[Patch] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            operation = PatchOperation(str(item.get("operation") or "").upper())
        except ValueError:
            continue  # 未知操作：预算校验语义上已非法；直接丢弃该补丁
        patches.append(
            Patch(
                operation=operation,
                path=str(item.get("path") or ""),
                old_value=item.get("old_value"),
                new_value=item.get("new_value"),
            )
        )
    return patches


def resolve_optimizer_name(task: EvolutionTask, strategy_type: StrategyType) -> str:
    """解析优化器名：策略显式指定优先，否则按诊断映射表（spec section 24）。"""
    if task.strategy.optimizer:
        return task.strategy.optimizer
    diagnosis_type = str((task.diagnosis or {}).get("type") or "")
    mapped = DIAGNOSIS_OPTIMIZER_MAP.get(diagnosis_type)
    if mapped:
        return mapped
    # 目标类型兜底：文本类目标用同构优化器。
    return "skill_optimizer"


def default_optimizers(model_fn: ModelFn | None = None) -> list[EvolutionOptimizer]:
    """组合根默认优化器集：V1 提供 skill/prompt 两个文本优化器。

    注册表由 EvolutionService 显式持有（与 Evaluation 的 EvaluatorRegistry
    同构），不使用模块级全局状态。
    """
    return [SkillOptimizer(model_fn), PromptOptimizer(model_fn)]
