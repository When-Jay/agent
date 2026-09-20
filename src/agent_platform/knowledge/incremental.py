"""Anchor-granular incremental diff (knowledge-rag-spec.md I3, normative).

Pure set logic over anchor keys; ordering follows the new document.
"""

from dataclasses import dataclass

from agent_platform.knowledge.domain import AnchorSection, StoredAnchor


@dataclass(frozen=True)
class AnchorDiff:
    added: list[AnchorSection]  # keys(new) - keys(old)
    changed: list[AnchorSection]  # in both, content_hash differs (new versions)
    removed: list[str]  # keys(old) - keys(new)


def diff_anchors(old: dict[str, StoredAnchor], new: list[AnchorSection]) -> AnchorDiff:
    """Classify anchors between the stored inventory and a fresh parse (I3).

    A rename changes the key, so it appears as remove + add (A8).
    """
    new_keys = {section.key for section in new}
    added = [section for section in new if section.key not in old]
    changed = [
        section
        for section in new
        if section.key in old and old[section.key].content_hash != section.content_hash
    ]
    removed = [key for key in old if key not in new_keys]
    return AnchorDiff(added=added, changed=changed, removed=removed)
