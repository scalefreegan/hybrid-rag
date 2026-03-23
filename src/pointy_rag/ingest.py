"""End-to-end ingestion pipeline for pointy-rag."""

import logging
from collections.abc import Callable
from pathlib import Path

import psycopg

from pointy_rag.chunker import chunk_markdown
from pointy_rag.config import get_settings
from pointy_rag.converter import convert_to_markdown, detect_format
from pointy_rag.db import (
    delete_document_data,
    get_document_by_source_path,
    insert_chunk,
    insert_document,
)
from pointy_rag.embeddings import embed_texts
from pointy_rag.models import Document

logger = logging.getLogger(__name__)


async def ingest_document(
    source_path: str | Path,
    conn: psycopg.Connection,
    output_dir: str | Path | None = None,
    use_agent: bool = True,
    timeout: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Document:
    """Ingest a single document through the full pipeline.

    Pipeline stages:
    1. Detect format and convert to markdown
    2. Chunk the markdown
    3. Embed the chunks
    4. Store document in DB
    5. Generate disclosure hierarchy (if use_agent=True)
    6. Map chunks to disclosure docs
    7. Store chunks in DB

    Re-ingestion: if a document with the same source_path exists,
    deletes all existing data before re-inserting.

    Args:
        source_path: Path to the source document.
        conn: Active database connection.
        output_dir: Optional directory for converted markdown files.
        use_agent: Use Claude agent for conversion and disclosure.

    Returns:
        The stored Document model.
    """
    source_path = Path(source_path).resolve()

    # --- Stage 1: Convert to markdown ---
    fmt = detect_format(source_path)
    markdown, md_path = await convert_to_markdown(
        source_path,
        output_dir=output_dir,
        use_agent=use_agent,
        timeout=timeout,
        on_progress=on_progress,
    )
    logger.info("Converted %s to markdown (%d chars)", source_path.name, len(markdown))

    # --- Stage 2: Chunk ---
    text_chunks = chunk_markdown(markdown)
    if not text_chunks:
        raise ValueError(f"No chunks produced from {source_path.name}")
    logger.info("Chunked into %d pieces", len(text_chunks))

    # --- Stage 3: Embed ---
    chunk_texts = [tc.content for tc in text_chunks]
    embeddings = embed_texts(chunk_texts)
    logger.info("Generated %d embeddings", len(embeddings))

    # --- Stage 4: Store document (handle re-ingestion) ---
    settings = get_settings()
    existing = get_document_by_source_path(str(source_path), conn)
    if existing:
        logger.info("Re-ingesting %s — deleting existing data", source_path.name)
        if settings.kg_enabled:
            from pointy_rag.graph import delete_document_graph_data

            delete_document_graph_data(existing.id, conn)
        delete_document_data(existing.id, conn)
        conn.commit()

    doc = Document(
        title=source_path.stem,
        format=fmt,
        source_path=str(source_path),
    )
    insert_document(doc, conn)
    conn.commit()

    # --- Stage 5: Generate disclosure hierarchy ---
    disclosure_docs = []
    if use_agent:
        try:
            from pointy_rag.disclosure import generate_disclosure_hierarchy

            disclosure_docs = await generate_disclosure_hierarchy(
                doc.id, markdown, doc.title, conn
            )
            logger.info("Generated %d disclosure docs", len(disclosure_docs))
        except Exception as exc:
            logger.warning(
                "Disclosure generation failed, storing chunks without pointers: %s", exc
            )
    else:
        logger.info("Skipping disclosure generation (--no-agent)")

    # --- Stage 6: Map chunks to disclosure ---
    if disclosure_docs:
        from pointy_rag.pointer_mapper import map_chunks_to_disclosure

        mapped_chunks = map_chunks_to_disclosure(text_chunks, disclosure_docs)
        # Attach embeddings to mapped chunks.
        for chunk, embedding in zip(mapped_chunks, embeddings, strict=True):
            chunk.embedding = embedding
    else:
        # No disclosure docs — store chunks with a placeholder disclosure doc.
        from pointy_rag.db import insert_disclosure_doc
        from pointy_rag.models import DisclosureDoc, DisclosureLevel

        placeholder = DisclosureDoc(
            document_id=doc.id,
            level=DisclosureLevel.detailed_passage,
            title=doc.title,
            content=markdown[:500] if len(markdown) > 500 else markdown,
            ordering=0,
        )
        insert_disclosure_doc(placeholder, conn)
        conn.commit()

        from pointy_rag.models import Chunk

        mapped_chunks = [
            Chunk(
                disclosure_doc_id=placeholder.id,
                content=tc.content,
                embedding=emb,
                metadata={"chunk_index": tc.chunk_index},
            )
            for tc, emb in zip(text_chunks, embeddings, strict=True)
        ]

    # --- Stage 7: Store chunks ---
    for chunk in mapped_chunks:
        insert_chunk(chunk, conn)
    conn.commit()
    logger.info("Stored %d chunks for %s", len(mapped_chunks), doc.title)

    # --- Stage 8: Populate knowledge graph ---
    if settings.kg_enabled:
        try:
            from pointy_rag.graph import (
                create_chunk_node,
                create_contains_edge,
                create_disclosure_node,
                create_similar_to_edges,
            )

            conn.execute("LOAD 'age'")
            conn.execute("SET search_path = ag_catalog, '$user', public")

            for ddoc in disclosure_docs:
                create_disclosure_node(ddoc, conn)
                if ddoc.parent_id:
                    create_contains_edge(ddoc.parent_id, ddoc.id, ddoc.ordering, conn)
            for chunk in mapped_chunks:
                create_chunk_node(chunk, doc.id, conn)
                create_contains_edge(chunk.disclosure_doc_id, chunk.id, 0, conn)
            edge_count = 0
            for chunk in mapped_chunks:
                edge_count += create_similar_to_edges(chunk, conn)
            logger.info("Created %d similarity edges", edge_count)
            conn.commit()
        except psycopg.Error as exc:
            logger.warning(
                "KG population failed (document stored): %s", exc, exc_info=True
            )
            conn.rollback()

    # --- Regenerate library catalog ---
    if use_agent and disclosure_docs:
        try:
            from pointy_rag.disclosure import regenerate_library_catalog

            await regenerate_library_catalog(conn)
            logger.info("Regenerated library catalog")
        except Exception as exc:
            logger.warning(
                "Library catalog regeneration failed: %s", exc, exc_info=True
            )

    return doc


async def ingest_paths(
    paths: list[Path],
    conn: psycopg.Connection,
    output_dir: str | Path | None = None,
    use_agent: bool = True,
    timeout: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[list[Document], list[tuple[Path, Exception]]]:
    """Ingest multiple documents sequentially.

    Continues processing if one file fails.

    Returns:
        Tuple of (successful_docs, failed_list) where failed_list
        contains (path, exception) pairs.
    """
    succeeded: list[Document] = []
    failed: list[tuple[Path, Exception]] = []

    for path in paths:
        try:
            doc = await ingest_document(
                path,
                conn,
                output_dir=output_dir,
                use_agent=use_agent,
                timeout=timeout,
                on_progress=on_progress,
            )
            succeeded.append(doc)
        except Exception as exc:
            logger.error("Failed to ingest %s: %s", path, exc)
            failed.append((path, exc))

    return succeeded, failed
