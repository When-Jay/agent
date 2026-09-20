"""Child chunking (knowledge-rag-spec.md section 5, normative).

LangChain seam: the ONLY knowledge module file besides embeddings.py
that may import LangChain (boundary-enforced). Splitting is a pure
function of (content, parameters) — deterministic per C5.
"""

from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

# CJK sentence boundaries join the default recursive separators (C2).
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", ". ", " ", ""]


@dataclass(frozen=True)
class ChildSplit:
    """One overlapping window of the parent content with its char span
    within the parent (A9). Invariant: parent[start:end] == content."""

    content: str
    start: int
    end: int


def split_children(parent_content: str, chunk_size: int, overlap: int) -> list[ChildSplit]:
    """Split parent content into overlapping children (spec C2/C3).

    Deterministic; whitespace-only windows are dropped. Span data comes
    from the splitter's start_index metadata (provenance, A9).
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        add_start_index=True,
        separators=_SEPARATORS,
    )
    children: list[ChildSplit] = []
    for doc in splitter.create_documents([parent_content]):
        content = doc.page_content
        if not content.strip():
            continue
        start = int(doc.metadata.get("start_index", 0))
        children.append(ChildSplit(content=content, start=start, end=start + len(content)))
    return children
