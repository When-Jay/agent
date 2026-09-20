"""Markdown anchor parsing (knowledge-rag-spec.md section 4, normative).

Pure module: no LangChain, no I/O. A single line scan with fence
tracking and an anchor-heading stack produces AnchorSection records
with exact content, content hashes and [start, end) char spans.
"""

import re
from dataclasses import replace

from agent_platform.knowledge.domain import AnchorSection, sha256_text

# ATX headings only (A2). Leading whitespace is NOT allowed (stricter than
# CommonMark, per plan section 4.2); closing hashes are stripped.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")

_PREAMBLE_DISPLAY = "(preamble)"


def preamble_key() -> str:
    """Constant key of the synthetic preamble anchor (A6)."""
    return sha256_text(_PREAMBLE_DISPLAY)


def _fence_marker(line: str) -> tuple[str, int] | None:
    """Return (marker_char, length) if the line carries a fence delimiter
    (``` or ~~~, 3+ markers, indented at most 3 spaces), else None."""
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return None
    match = _FENCE_RE.match(stripped)
    if match is None:
        return None
    return match.group(1)[0], len(match.group(1))


def _is_closing_fence(line: str, char: str, min_length: int) -> bool:
    """A closing fence uses the same char, at least the opening length,
    with no trailing content."""
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return False
    run = 0
    while run < len(stripped) and stripped[run] == char:
        run += 1
    if run < min_length:
        return False
    return stripped[run:].strip() == ""


def _display_path(path: tuple[str, ...], level: int) -> str:
    if level == 0:
        return _PREAMBLE_DISPLAY
    return " > ".join(path)


def parse_anchors(content: str, anchor_levels: list[int] | tuple[int, ...]) -> list[AnchorSection]:
    """Parse markdown content into anchor sections (spec A1-A9).

    Never raises on valid str input. Whitespace-only regions (including
    the whole document) yield no anchors. Duplicate display paths get
    " [n]" occurrence suffixes in document order.
    """
    levels = set(anchor_levels)
    lines = content.split("\n")
    # Char offset of every line start (lines are joined back with "\n").
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line) + 1

    raw: list[dict] = []  # anchor headings in document order
    stack: list[tuple[int, str]] = []  # open anchor chain (A4)
    in_fence = False
    fence_char = ""
    fence_len = 0

    for index, line in enumerate(lines):
        if in_fence:
            if _is_closing_fence(line, fence_char, fence_len):
                in_fence = False
            continue
        marker = _fence_marker(line)
        if marker is not None:
            in_fence = True
            fence_char, fence_len = marker
            continue
        heading = _HEADING_RE.match(line)
        if heading is not None and len(heading.group(1)) in levels:
            if raw:
                raw[-1]["end_line"] = index  # A3: any anchor heading closes
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2)))
            raw.append(
                {
                    "path": [text for _, text in stack],
                    "level": level,
                    "heading": heading.group(2),
                    "start_line": index,
                    "end_line": len(lines),
                }
            )

    sections: list[AnchorSection] = []

    def emit(path: list[str], level: int, heading: str, start: int, raw_end: int) -> None:
        text = content[start:raw_end].rstrip()
        if not text.strip():
            return  # A5: whitespace-only region yields no anchor
        sections.append(
            AnchorSection(
                path=tuple(path),
                key="",  # assigned after dedup below
                display_path=_display_path(tuple(path), level),
                level=level,
                heading=heading,
                content=text,
                content_hash=sha256_text(text),
                start=start,
                end=start + len(text),
            )
        )

    if raw:
        first_start = raw[0]["start_line"]
        if first_start > 0:
            emit([], 0, "", 0, offsets[first_start])  # A5 preamble
    elif content.strip():
        emit([], 0, "", 0, len(content))  # no anchor headings at all

    for section in raw:
        start = offsets[section["start_line"]]
        end_line = section["end_line"]
        raw_end = offsets[end_line] if end_line < len(lines) else len(content)
        emit(section["path"], section["level"], section["heading"], start, raw_end)

    # A6: dedup display paths with occurrence suffixes, then key them.
    seen: dict[str, int] = {}
    result: list[AnchorSection] = []
    for section in sections:
        count = seen.get(section.display_path, 0) + 1
        seen[section.display_path] = count
        display = section.display_path if count == 1 else f"{section.display_path} [{count}]"
        key = preamble_key() if section.level == 0 else sha256_text(display)
        result.append(replace(section, display_path=display, key=key))
    return result
