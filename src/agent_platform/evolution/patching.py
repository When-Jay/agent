"""Patch 引擎（plan 060 section 7 / architecture doc section 2.4 有界修改）。

V1 面向文本类目标（PROMPT/SKILL/RAG 文档）：内容按 markdown 小节
（"## 标题"）寻址，文件首段（第一个标题之前）小节标题为 ""。

PATCH -> Syntax Validation -> Schema Validation -> Edit Budget Validation
-> Permission Validation -> Candidate（候选才能进入实验）。
"""

from agent_platform.evolution.domain.candidate import Patch, PatchOperation
from agent_platform.evolution.domain.task import EditBudget

_HEADING_PREFIX = "## "


class PatchApplicationError(ValueError):
    """补丁无法作用于目标内容（语法校验阶段未覆盖的引用错误）。"""


def count_tokens(text: str | None) -> int:
    """Token 估算：空白分词（V1 简化，避免引入 tokenizer 依赖）。"""
    if not text:
        return 0
    return len(text.split())


def _split_sections(content: str) -> list[list[str]]:
    """切分为 [title, body] 列表；首元素为文件首段（title=""）。"""
    sections: list[list[str]] = []
    title = ""
    body: list[str] = []
    for line in content.splitlines():
        if line.startswith(_HEADING_PREFIX):
            sections.append([title, "\n".join(body).strip("\n")])
            title = line[len(_HEADING_PREFIX):].strip()
            body = []
        else:
            body.append(line)
    sections.append([title, "\n".join(body).strip("\n")])
    return sections


def _join_sections(sections: list[list[str]]) -> str:
    parts = []
    for title, body in sections:
        if not title:
            parts.append(body)
        else:
            parts.append(f"{_HEADING_PREFIX}{title}\n\n{body}".rstrip() + "\n")
    return "\n".join(parts).strip("\n") + ("\n" if parts else "")


def _section_index(sections: list[list[str]], path: str) -> int:
    for i, (title, _) in enumerate(sections):
        if title == path.strip():
            return i
    raise PatchApplicationError(f"section not found: {path!r}")


def apply_patches(content: str, patches: list[Patch]) -> str:
    """按顺序应用补丁，返回新内容。输入内容不被修改。"""
    sections = _split_sections(content)
    for patch in patches:
        op = patch.operation
        if op is PatchOperation.REPLACE:
            index = _section_index(sections, patch.path)
            sections[index][1] = patch.new_value or ""
        elif op is PatchOperation.DELETE:
            index = _section_index(sections, patch.path)
            if index == 0 and not sections[0][0]:
                raise PatchApplicationError("cannot DELETE the preamble section")
            del sections[index]
        elif op is PatchOperation.ADD:
            title, body = _parse_full_section(patch.new_value or "")
            sections.append([title, body])
        elif op is PatchOperation.INSERT:
            title, body = _parse_full_section(patch.new_value or "")
            if not patch.path.strip():
                sections.insert(0, [title, body])
            else:
                index = _section_index(sections, patch.path)
                sections.insert(index + 1, [title, body])
        else:  # pragma: no cover - 枚举封闭
            raise PatchApplicationError(f"unknown operation: {op}")
    return _join_sections(sections)


def _parse_full_section(text: str) -> tuple[str, str]:
    """ADD/INSERT 的 new_value 必须以 "## 标题" 开头。"""
    lines = (text or "").splitlines()
    if not lines or not lines[0].startswith(_HEADING_PREFIX):
        raise PatchApplicationError(
            "ADD/INSERT new_value must start with a '## title' heading"
        )
    title = lines[0][len(_HEADING_PREFIX):].strip()
    return title, "\n".join(lines[1:]).strip("\n")


def validate_patches(patches: list[Patch], budget: EditBudget) -> list[str]:
    """预算/权限校验（spec section 8）。返回违规列表；空列表 = 通过。

    REPLACE/DELETE 必须携带 old_value 以便计算 token 增删预算。
    """
    violations: list[str] = []
    added = 0
    removed = 0
    if len(patches) > budget.max_edits:
        violations.append(
            f"edit count {len(patches)} exceeds budget max_edits={budget.max_edits}"
        )
    for i, patch in enumerate(patches):
        label = f"patch[{i}]"
        if patch.operation not in PatchOperation:
            violations.append(f"{label}: unknown operation {patch.operation!r}")
            continue
        if patch.operation.value not in budget.allowed_operations:
            violations.append(
                f"{label}: operation {patch.operation.value} not allowed "
                f"(allowed: {budget.allowed_operations})"
            )
        section = _target_section(patch)
        if budget.allowed_sections and section not in budget.allowed_sections:
            violations.append(
                f"{label}: section {section!r} not editable "
                f"(allowed: {budget.allowed_sections})"
            )
        if patch.operation in (PatchOperation.REPLACE, PatchOperation.DELETE):
            if patch.old_value is None:
                violations.append(f"{label}: {patch.operation.value} requires old_value")
                continue
            new_tokens = count_tokens(patch.new_value) if patch.new_value else 0
            old_tokens = count_tokens(patch.old_value)
            added += max(0, new_tokens - old_tokens)
            removed += max(0, old_tokens - new_tokens)
        else:  # ADD / INSERT
            added += count_tokens(patch.new_value)
    if added > budget.max_tokens_added:
        violations.append(
            f"tokens added {added} exceeds budget max_tokens_added={budget.max_tokens_added}"
        )
    if removed > budget.max_tokens_removed:
        violations.append(
            f"tokens removed {removed} exceeds budget "
            f"max_tokens_removed={budget.max_tokens_removed}"
        )
    return violations


def _target_section(patch: Patch) -> str:
    """补丁作用的小节名：REPLACE/DELETE/INSERT 用 path；ADD 取标题行。"""
    if patch.operation is PatchOperation.ADD:
        first_line = (patch.new_value or "").splitlines()[0] if patch.new_value else ""
        return first_line[len(_HEADING_PREFIX):].strip()
    return patch.path.strip()


def summarize_edits(patches: list[Patch]) -> str:
    """人读编辑摘要（候选的 edit_summary 字段）。"""
    return "; ".join(f"{p.operation.value}:{p.path or '<preamble>'}" for p in patches)
