"""Progressive disclosure hierarchy generator for pointy-rag."""

import asyncio
import logging

import psycopg
import psycopg.rows

from pointy_rag.chunker import count_tokens, split_into_sections
from pointy_rag.claude_agent import run_agent
from pointy_rag.db import (
    delete_disclosure_docs_by_level,
    insert_disclosure_doc,
    update_disclosure_doc_parent,
)
from pointy_rag.models import DisclosureDoc, DisclosureLevel

# Max concurrent agent calls for Level 2 generation.
_AGENT_CONCURRENCY = 5

# Safety bound: refuse absurdly large text to avoid runaway agent costs.
MAX_DISCLOSURE_TEXT_LENGTH = 500_000

# Sections under this token count skip the agent and use content as-is.
_L2_SKIP_THRESHOLD = 500  # tokens

# Number of sections to batch together for L2 summarization.
_L2_BATCH_SIZE = 15

_LEVEL_INSTRUCTIONS = {
    0: "Produce a library-wide catalog summarizing all documents.",
    1: "Produce a resource index (table of contents) for the document.",
    2: "Produce a concise section summary.",
}

_log = logging.getLogger(__name__)


async def run_disclosure_agent(
    *,
    text: str,
    title: str,
    level: int,
    timeout: int | None = None,
) -> str:
    """Build a disclosure prompt and call the Claude agent.

    Args:
        text: Source text to summarize.
        title: Document or section title.
        level: Disclosure level (0-2).
        timeout: Optional wall-clock timeout in seconds.

    Returns:
        The agent's summary text.
    """
    if len(text) > MAX_DISCLOSURE_TEXT_LENGTH:
        msg = (
            f"Input text ({len(text)} chars) is too large for disclosure agent "
            f"(max {MAX_DISCLOSURE_TEXT_LENGTH})"
        )
        raise ValueError(msg)

    # Escape closing tags to prevent prompt injection.
    safe_text = text.replace("</document>", "&lt;/document&gt;")
    instruction = _LEVEL_INSTRUCTIONS.get(level, "Summarize the following text.")

    prompt = f"{instruction}\n\nTitle: {title}\n\n<document>\n{safe_text}\n</document>"

    return await run_agent(prompt, timeout=timeout)


async def generate_disclosure_hierarchy(
    document_id: str,
    markdown: str,
    title: str,
    conn: psycopg.Connection,
) -> list[DisclosureDoc]:
    """Generate a 4-level disclosure hierarchy for a document.

    Bottom-up generation: L3 (structural) -> L2 (agent) -> L1 (agent).
    Does NOT generate L0 — call regenerate_library_catalog() after ingestion.

    Args:
        document_id: The document's ID in the database.
        markdown: The full markdown text of the document.
        title: The document title.
        conn: Active database connection.

    Returns:
        All generated DisclosureDoc instances (L3, L2, L1).
    """
    if not markdown.strip():
        return []

    # --- Level 3: structural extraction (no agent) ---
    sections = split_into_sections(markdown)
    level3_docs: list[DisclosureDoc] = []
    for idx, (heading, body) in enumerate(sections):
        if not body:
            continue
        section_title = heading.lstrip("#").strip() if heading else ""
        if not section_title:
            section_title = f"Section {idx + 1}"
        ddoc = DisclosureDoc(
            document_id=document_id,
            level=DisclosureLevel.detailed_passage,
            title=section_title,
            content=body,
            ordering=idx,
        )
        level3_docs.append(ddoc)

    if not level3_docs:
        return []

    # --- Level 2: agent summarizes each L3 doc ---
    # Short sections skip the agent entirely.
    short_l3: list[DisclosureDoc] = []
    needs_agent_l3: list[DisclosureDoc] = []
    for l3 in level3_docs:
        if count_tokens(l3.content) < _L2_SKIP_THRESHOLD:
            short_l3.append(l3)
        else:
            needs_agent_l3.append(l3)

    level2_docs: list[DisclosureDoc] = []
    successful_l3_docs: list[DisclosureDoc] = []

    # Promote short sections directly (content as-is).
    for l3 in short_l3:
        level2_docs.append(
            DisclosureDoc(
                document_id=document_id,
                level=DisclosureLevel.section_summary,
                title=l3.title,
                content=l3.content,
                ordering=l3.ordering,
            )
        )
        successful_l3_docs.append(l3)

    # Batch remaining sections for agent summarization.
    sem = asyncio.Semaphore(_AGENT_CONCURRENCY)

    async def _summarize_batch(
        batch: list[DisclosureDoc],
    ) -> list[tuple[DisclosureDoc, str]]:
        combined = "\n\n".join(
            f"[SECTION {i+1}] {l3.title}\n{l3.content}" for i, l3 in enumerate(batch)
        )
        async with sem:
            try:
                prompt = (
                    f"Produce a concise summary for EACH of the {len(batch)} "
                    f"numbered sections below. Format your response as:\n\n"
                    f"[SUMMARY 1]\nYour summary here\n\n"
                    f"[SUMMARY 2]\nYour summary here\n\n"
                    f"...and so on for all {len(batch)} sections.\n\n"
                    f"<document>\n{combined}\n</document>"
                )
                response = await run_agent(prompt, timeout=300, model="haiku")
                # Parse numbered summaries
                import re as _re
                parts = _re.split(r"\[SUMMARY \d+\]\s*", response)
                summaries = [s.strip() for s in parts if s.strip()]
                if len(summaries) != len(batch):
                    _log.warning(
                        "Batch summary count mismatch (%d vs %d sections), "
                        "using raw content fallback",
                        len(summaries),
                        len(batch),
                    )
                    summaries = [l3.content[:500] for l3 in batch]
            except Exception:
                _log.warning(
                    "L2 batch summarization failed for document %s, "
                    "falling back to raw content",
                    document_id,
                    exc_info=True,
                )
                summaries = [l3.content[:500] for l3 in batch]
        return list(zip(batch, summaries, strict=True))

    # Create batches of _L2_BATCH_SIZE
    batches: list[list[DisclosureDoc]] = []
    for i in range(0, len(needs_agent_l3), _L2_BATCH_SIZE):
        batches.append(needs_agent_l3[i : i + _L2_BATCH_SIZE])

    if batches:
        batch_results = await asyncio.gather(
            *[_summarize_batch(b) for b in batches],
            return_exceptions=True,
        )
        for batch, result in zip(batches, batch_results, strict=True):
            if isinstance(result, BaseException):
                _log.warning(
                    "L2 batch failed for document %s, using raw content: %s",
                    document_id,
                    result,
                )
                for l3 in batch:
                    level2_docs.append(
                        DisclosureDoc(
                            document_id=document_id,
                            level=DisclosureLevel.section_summary,
                            title=l3.title,
                            content=l3.content[:500],
                            ordering=l3.ordering,
                        )
                    )
                    successful_l3_docs.append(l3)
            else:
                for l3, summary in result:
                    level2_docs.append(
                        DisclosureDoc(
                            document_id=document_id,
                            level=DisclosureLevel.section_summary,
                            title=l3.title,
                            content=summary,
                            ordering=l3.ordering,
                        )
                    )
                    successful_l3_docs.append(l3)

    if not level2_docs:
        return []

    # --- Level 1: agent produces resource index from all L2 summaries ---
    combined_l2 = "\n\n".join(f"## {d.title}\n{d.content}" for d in level2_docs)
    level1_doc: DisclosureDoc | None = None
    try:
        resource_index_text = await run_disclosure_agent(
            text=combined_l2,
            title=title,
            level=1,
        )
        level1_doc = DisclosureDoc(
            document_id=document_id,
            level=DisclosureLevel.resource_index,
            title=title,
            content=resource_index_text,
            ordering=0,
        )
    except Exception:
        _log.warning(
            "L1 resource index failed for document %s, persisting L2+L3 only",
            document_id,
            exc_info=True,
        )

    # --- Set parent_id links ---
    for l2, l3 in zip(level2_docs, successful_l3_docs, strict=True):
        l3.parent_id = l2.id
        if level1_doc is not None:
            l2.parent_id = level1_doc.id

    # --- Persist to database ---
    if level1_doc is not None:
        insert_disclosure_doc(level1_doc, conn)
    for l2 in level2_docs:
        insert_disclosure_doc(l2, conn)
    for l3 in successful_l3_docs:
        insert_disclosure_doc(l3, conn)
    conn.commit()

    all_docs: list[DisclosureDoc] = []
    if level1_doc is not None:
        all_docs.append(level1_doc)
    all_docs.extend(level2_docs)
    all_docs.extend(successful_l3_docs)
    return all_docs


async def regenerate_library_catalog(
    conn: psycopg.Connection,
) -> DisclosureDoc | None:
    """Regenerate the single Level 0 library catalog from all Level 1 docs.

    Idempotent: deletes existing L0 docs, creates a new one, re-parents L1 docs.

    Returns:
        The new Level 0 DisclosureDoc, or None if no documents exist.
    """
    # Gather all Level 1 docs across all documents.
    row_factory = psycopg.rows.dict_row
    rows = (
        conn.cursor(row_factory=row_factory)
        .execute(
            "SELECT id, document_id, parent_id, level, title, content, ordering "
            "FROM disclosure_docs WHERE level = %s ORDER BY title",
            (int(DisclosureLevel.resource_index),),
        )
        .fetchall()
    )

    if not rows:
        return None

    level1_docs = [
        DisclosureDoc(
            id=r["id"],
            document_id=r["document_id"],
            parent_id=r["parent_id"],
            level=r["level"],
            title=r["title"],
            content=r["content"],
            ordering=r["ordering"],
        )
        for r in rows
    ]

    combined = "\n\n".join(f"## {d.title}\n{d.content}" for d in level1_docs)

    catalog_text = await run_disclosure_agent(
        text=combined,
        title="Library Catalog",
        level=0,
    )

    # Clear L1 parent_ids before deleting L0 (avoids FK violation).
    conn.execute(
        "UPDATE disclosure_docs SET parent_id = NULL WHERE level = %s",
        (int(DisclosureLevel.resource_index),),
    )
    delete_disclosure_docs_by_level(DisclosureLevel.library_catalog, conn)

    # Create new L0 doc. Use the first L1's document_id as a convention.
    catalog_doc = DisclosureDoc(
        document_id=level1_docs[0].document_id,
        level=DisclosureLevel.library_catalog,
        title="Library Catalog",
        content=catalog_text,
        ordering=0,
    )
    insert_disclosure_doc(catalog_doc, conn)

    # Re-parent all L1 docs to the new L0.
    for l1 in level1_docs:
        update_disclosure_doc_parent(l1.id, catalog_doc.id, conn)

    conn.commit()
    return catalog_doc
