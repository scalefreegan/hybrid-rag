"""Map TextChunks to Chunk models with disclosure document pointers."""

import re

from pointy_rag.chunker import TextChunk
from pointy_rag.models import Chunk, DisclosureDoc, DisclosureLevel


def _normalize(text: str) -> str:
    """Normalize heading: strip markers, lowercase, collapse ws."""
    text = re.sub(r"^#+\s*", "", text)
    return text.strip().lower()


def _jaccard_similarity(a: str, b: str) -> float:
    """Word-level Jaccard similarity between two strings."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


_JACCARD_THRESHOLD = 0.3


def map_chunks_to_disclosure(
    chunks: list[TextChunk],
    disclosure_docs: list[DisclosureDoc],
) -> list[Chunk]:
    """Map TextChunks to Chunk models with disclosure_doc_id assigned.

    Matching strategy:
    1. Build index of normalized Level 3 doc titles → doc IDs.
    2. Match each chunk's heading to a Level 3 doc title.
    3. Fallback: word-set Jaccard similarity against Level 3 doc content.
    4. If still no match: assign to first Level 3 doc and flag as unmapped.

    Args:
        chunks: TextChunk instances from the chunker.
        disclosure_docs: All disclosure docs for the document (all levels).

    Returns:
        Chunk model instances with disclosure_doc_id set, embedding=None.
    """
    level3_docs = [
        d for d in disclosure_docs
        if d.level == DisclosureLevel.detailed_passage
    ]

    if not level3_docs:
        raise ValueError("No Level 3 disclosure docs provided for mapping")

    # Sort by ordering for deterministic fallback.
    level3_docs.sort(key=lambda d: d.ordering)

    # Build title → doc index.
    title_index: dict[str, DisclosureDoc] = {}
    for ddoc in level3_docs:
        title_index[_normalize(ddoc.title)] = ddoc

    result: list[Chunk] = []
    for tc in chunks:
        matched_doc: DisclosureDoc | None = None
        metadata: dict = {}

        # Strategy 1: heading match.
        if tc.heading:
            normalized = _normalize(tc.heading)
            matched_doc = title_index.get(normalized)

        # Strategy 2: Jaccard similarity fallback.
        if matched_doc is None:
            best_score = 0.0
            best_doc: DisclosureDoc | None = None
            for ddoc in level3_docs:
                score = _jaccard_similarity(tc.content, ddoc.content)
                if score > best_score:
                    best_score = score
                    best_doc = ddoc
            if best_score >= _JACCARD_THRESHOLD and best_doc is not None:
                matched_doc = best_doc

        # Strategy 3: default to first doc.
        if matched_doc is None:
            matched_doc = level3_docs[0]
            metadata["unmapped"] = True

        chunk = Chunk(
            disclosure_doc_id=matched_doc.id,
            content=tc.content,
            metadata=metadata,
        )
        result.append(chunk)

    return result
