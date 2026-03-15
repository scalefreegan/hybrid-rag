"""Progressive disclosure hierarchy generator for pointy-rag."""

import asyncio
import logging

import psycopg
import psycopg.rows

from pointy_rag.chunker import split_into_sections
from pointy_rag.claude_agent import run_disclosure_agent
from pointy_rag.db import (
    delete_disclosure_docs_by_level,
    insert_disclosure_doc,
    update_disclosure_doc_parent,
)
from pointy_rag.models import DisclosureDoc, DisclosureLevel

# Max concurrent agent calls for Level 2 generation.
_AGENT_CONCURRENCY = 3

_log = logging.getLogger(__name__)


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
        section_title = (
            heading.lstrip("#").strip() if heading else ""
        )
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
    sem = asyncio.Semaphore(_AGENT_CONCURRENCY)

    async def _summarize_section(l3: DisclosureDoc) -> DisclosureDoc:
        async with sem:
            summary = await run_disclosure_agent(
                text=l3.content,
                title=l3.title,
                level=2,
            )
        return DisclosureDoc(
            document_id=document_id,
            level=DisclosureLevel.section_summary,
            title=l3.title,
            content=summary,
            ordering=l3.ordering,
        )

    gather_results = await asyncio.gather(
        *[_summarize_section(l3) for l3 in level3_docs],
        return_exceptions=True,
    )

    level2_docs: list[DisclosureDoc] = []
    successful_l3_docs: list[DisclosureDoc] = []
    for l3, result in zip(level3_docs, gather_results):
        if isinstance(result, BaseException):
            _log.warning(
                "L2 summary failed for section %r in document %s, skipping: %s",
                l3.title,
                document_id,
                result,
            )
        else:
            level2_docs.append(result)
            successful_l3_docs.append(l3)

    if not level2_docs:
        return []

    # --- Level 1: agent produces resource index from all L2 summaries ---
    combined_l2 = "\n\n".join(
        f"## {d.title}\n{d.content}" for d in level2_docs
    )
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

    # --- Set parent_id links ---
    for l2, l3 in zip(level2_docs, successful_l3_docs, strict=True):
        l3.parent_id = l2.id
        l2.parent_id = level1_doc.id

    # --- Persist to database ---
    insert_disclosure_doc(level1_doc, conn)
    for l2 in level2_docs:
        insert_disclosure_doc(l2, conn)
    for l3 in successful_l3_docs:
        insert_disclosure_doc(l3, conn)
    conn.commit()

    return [level1_doc, *level2_docs, *successful_l3_docs]


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
    rows = conn.cursor(row_factory=row_factory).execute(
        "SELECT id, document_id, parent_id, level, title, content, ordering "
        "FROM disclosure_docs WHERE level = %s ORDER BY title",
        (int(DisclosureLevel.resource_index),),
    ).fetchall()

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

    combined = "\n\n".join(
        f"## {d.title}\n{d.content}" for d in level1_docs
    )

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
