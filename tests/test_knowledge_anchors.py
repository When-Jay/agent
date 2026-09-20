"""Pure knowledge tests: anchor parsing, chunking, incremental diff
(plan 070 section 12, tests 1-3). No I/O, no provider."""

from agent_platform.knowledge.anchors import parse_anchors, preamble_key
from agent_platform.knowledge.chunking import split_children
from agent_platform.knowledge.domain import (
    StoredAnchor,
    sha256_text,
    validate_anchor_levels,
    validate_chunk_params,
)
from agent_platform.knowledge.incremental import diff_anchors

import pytest


DOC = (
    "# Top title\n"
    "intro under h1\n"
    "\n"
    "## One\n"
    "one body\n"
    "\n"
    "### One-A\n"
    "sub body\n"
    "\n"
    "## Two\n"
    "two body\n"
    "\n"
    "#### Deep\n"
    "h4 body\n"
)


# -- parsing -------------------------------------------------------------------


def test_default_levels_boundaries():
    sections = parse_anchors(DOC, [2, 3])
    # h1 and h4 are body text; h2/h3 anchor and close.
    assert [s.display_path for s in sections] == [
        "(preamble)",
        "One",
        "One > One-A",
        "Two",
    ]
    assert sections[0].heading == "" and sections[0].level == 0
    assert sections[3].content == "## Two\ntwo body\n\n#### Deep\nh4 body"
    # h1 intro is preamble; h4 belongs to the enclosing section.
    assert "intro under h1" in sections[0].content
    assert "h4 body" in sections[3].content


def test_preamble_present_and_whitespace_only_absent():
    assert parse_anchors(DOC, [2, 3])[0].key == preamble_key()
    assert [s.display_path for s in parse_anchors("\n\n## One\nbody\n", [2, 3])] == ["One"]
    assert parse_anchors("   \n\t\n", [2, 3]) == []
    assert parse_anchors("", [2, 3]) == []


def test_fence_suppresses_headings():
    fence = "`" * 3
    doc = (
        "## Real\n"
        "before\n"
        "\n"
        + fence
        + "markdown\n"
        + "## Not An Anchor\n"
        + fence
        + "\n"
        "after\n"
    )
    sections = parse_anchors(doc, [2, 3])
    assert len(sections) == 1
    assert sections[0].heading == "Real"
    assert "## Not An Anchor" in sections[0].content
    assert "after" in sections[0].content


def test_tilde_fence_and_closing_requires_same_marker():
    doc = "## A\n~~~\n## Hidden\n~~~\ntail\n"
    sections = parse_anchors(doc, [2, 3])
    assert len(sections) == 1
    assert "## Hidden" in sections[0].content


def test_duplicate_paths_get_occurrence_suffixes():
    doc = "## Dup\nfirst\n\n## Dup\nsecond\n\n## Dup\nthird\n"
    sections = parse_anchors(doc, [2, 3])
    assert [s.display_path for s in sections] == ["Dup", "Dup [2]", "Dup [3]"]
    assert len({s.key for s in sections}) == 3
    assert sections[0].key == sha256_text("Dup")
    assert sections[1].key == sha256_text("Dup [2]")


def test_non_anchor_headings_excluded_from_path_and_keys():
    base = "## A\n\n#### x\n\n### B\nb body\n"
    v1 = parse_anchors(base, [2, 3])
    v2 = parse_anchors(base.replace("#### x", "#### renamed"), [2, 3])
    assert [s.key for s in v1] == [s.key for s in v2]
    assert [s.display_path for s in v1] == ["A", "A > B"]


def test_spans_and_content_hashes_match_source():
    doc = "## A\nbody line\n\n## B\nb\n"
    sections = parse_anchors(doc, [2, 3])
    for section in sections:
        assert doc[section.start : section.end] == section.content
        assert section.content_hash == sha256_text(section.content)
    assert sections[0].content == "## A\nbody line"


def test_custom_levels_single_and_lone():
    # [1]: only h1 anchors; later non-anchor headings stay in its body.
    doc = "# Top\n\n## Second\nbody\n"
    sections = parse_anchors(doc, [1])
    assert [s.display_path for s in sections] == ["Top"]
    assert sections[0].content == "# Top\n\n## Second\nbody"

    # [3]: h2 text becomes the preamble; h3 anchors do not nest under h2.
    doc2 = "## A\n\n### B\nb body\n\n### C\nc body\n"
    sections2 = parse_anchors(doc2, [3])
    assert [s.display_path for s in sections2] == ["(preamble)", "B", "C"]


def test_document_without_anchor_headings_is_one_preamble():
    sections = parse_anchors("plain text\nmore text\n", [2, 3])
    assert len(sections) == 1
    assert sections[0].level == 0
    assert sections[0].content == "plain text\nmore text"


def test_trailing_whitespace_is_stripped_from_sections():
    doc = "## A\nbody\n\n\n\n## B\nb\n"
    sections = parse_anchors(doc, [2, 3])
    assert sections[0].content == "## A\nbody"


# -- chunking ------------------------------------------------------------------


def test_children_spans_ordered_within_parent():
    parent = "\n\n".join(f"paragraph {i} " + "x" * 50 for i in range(6))
    children = split_children(parent, chunk_size=60, overlap=15)
    assert len(children) > 1
    starts = [c.start for c in children]
    assert starts == sorted(starts)
    for child in children:
        assert 0 <= child.start < child.end <= len(parent)
        assert parent[child.start : child.end] == child.content


def test_children_cover_parent_non_whitespace():
    parent = "\n\n".join(f"para {i}\n" + "y" * 40 for i in range(5))
    children = split_children(parent, chunk_size=50, overlap=12)
    covered: set[int] = set()
    for child in children:
        covered.update(range(child.start, child.end))
    assert all(index in covered for index, ch in enumerate(parent) if not ch.isspace())


def test_adjacent_children_overlap_by_construction():
    text = "a" * 100  # no separators -> char-level windows
    children = split_children(text, chunk_size=30, overlap=10)
    assert [c.start for c in children] == [0, 20, 40, 60, 80]
    for a, b in zip(children, children[1:]):
        assert b.start < a.end
        assert a.end - b.start >= 10  # at least the overlap survives


def test_chunking_deterministic():
    parent = "## H\n" + "sentence。 " * 30
    assert split_children(parent, 100, 20) == split_children(parent, 100, 20)


def test_heading_only_parent_single_child():
    children = split_children("## Title", 100, 20)
    assert len(children) == 1
    assert children[0].content == "## Title"
    assert children[0].start == 0


def test_whitespace_only_parent_yields_no_children():
    assert split_children("   \n  ", 100, 20) == []


# -- diff ------------------------------------------------------------------


def _stored(sections):
    return {
        s.key: StoredAnchor(
            key=s.key,
            content_hash=s.content_hash,
            display_path=s.display_path,
            anchor_level=s.level,
            heading=s.heading,
        )
        for s in sections
    }


def test_diff_classification_added_changed_removed():
    v1 = parse_anchors("## A\na\n\n## B\nb\n", [2, 3])
    old = _stored(v1)
    # A edited, B untouched, C added, B2 renamed from nothing.
    v2 = parse_anchors("## A\na EDITED\n\n## B\nb\n\n## C\nc\n", [2, 3])
    diff = diff_anchors(old, v2)
    assert [s.heading for s in diff.added] == ["C"]
    assert [s.heading for s in diff.changed] == ["A"]
    assert diff.removed == []


def test_untouched_sections_absent_from_diff():
    v1 = parse_anchors(DOC, [2, 3])
    diff = diff_anchors(_stored(v1), parse_anchors(DOC, [2, 3]))
    assert diff.added == [] and diff.changed == [] and diff.removed == []


def test_rename_is_remove_plus_add():
    v1 = parse_anchors("## A\na\n", [2, 3])
    v2 = parse_anchors("## A2\na\n", [2, 3])
    diff = diff_anchors(_stored(v1), v2)
    assert [s.heading for s in diff.added] == ["A2"]
    assert diff.removed == [v1[0].key]


def test_empty_new_tree_removes_everything():
    v1 = parse_anchors("## A\na\n\n## B\nb\n", [2, 3])
    diff = diff_anchors(_stored(v1), [])
    assert diff.added == [] and diff.changed == []
    assert set(diff.removed) == {s.key for s in v1}


# -- validation helpers -------------------------------------------------------


def test_validate_anchor_levels():
    assert validate_anchor_levels([3, 2, 2]) == (2, 3)
    for levels, message in (
        ([], "empty"),
        ([0], "zero"),
        ([7], "too deep"),
        ([2.5], "non-int"),
    ):
        with pytest.raises(ValueError):
            validate_anchor_levels(levels)


def test_validate_chunk_params():
    assert validate_chunk_params(800, 150) is None
    for size, overlap in ((99, 10), (8001, 10), (800, 800), (800, 0), (800, -1)):
        with pytest.raises(ValueError):
            validate_chunk_params(size, overlap)
