"""Pointer-based vector search for pointy-rag."""

import psycopg
import psycopg.rows

from pointy_rag.db import get_disclosure_doc
from pointy_rag.embeddings import embed_query
from pointy_rag.models import (
    Chunk,
    DisclosureDoc,
    Document,
    GraphSearchResult,
    SearchResult,
)


def search(
    query: str,
    conn: psycopg.Connection,
    limit: int = 10,
    threshold: float = 0.7,
) -> list[SearchResult]:
    """Embed query, run pgvector cosine similarity, return disclosure pointers.

    Args:
        query: The search query text.
        conn: Active database connection.
        limit: Maximum number of results.
        threshold: Minimum cosine similarity score (0-1).

    Returns:
        SearchResult list sorted by descending similarity score.
    """
    query_embedding = embed_query(query)

    cursor = conn.cursor(row_factory=psycopg.rows.dict_row)
    rows = cursor.execute(
        """
        WITH scored AS (
            SELECT
                c.id AS chunk_id, c.disclosure_doc_id, c.content AS chunk_content,
                c.metadata AS chunk_metadata,
                1 - (c.embedding <=> %s::vector) AS score
            FROM chunks c
            WHERE c.embedding IS NOT NULL
        )
        SELECT
            s.chunk_id, s.disclosure_doc_id, s.chunk_content, s.chunk_metadata,
            s.score,
            dd.id AS dd_id, dd.document_id AS dd_document_id,
            dd.parent_id AS dd_parent_id, dd.level AS dd_level,
            dd.title AS dd_title, dd.content AS dd_content,
            dd.ordering AS dd_ordering,
            doc.id AS doc_id, doc.title AS doc_title, doc.format AS doc_format,
            doc.source_path AS doc_source_path, doc.metadata AS doc_metadata,
            doc.created_at AS doc_created_at
        FROM scored s
        JOIN disclosure_docs dd ON dd.id = s.disclosure_doc_id
        JOIN documents doc ON doc.id = dd.document_id
        WHERE s.score >= %s
        ORDER BY s.score DESC
        LIMIT %s
        """,
        (query_embedding, threshold, limit),
    ).fetchall()

    results: list[SearchResult] = []
    for row in rows:
        chunk = Chunk(
            id=row["chunk_id"],
            disclosure_doc_id=row["disclosure_doc_id"],
            content=row["chunk_content"],
            embedding=None,  # Don't return embeddings in search results.
            metadata=row["chunk_metadata"],
        )

        ddoc = DisclosureDoc(
            id=row["dd_id"],
            document_id=row["dd_document_id"],
            parent_id=row["dd_parent_id"],
            level=row["dd_level"],
            title=row["dd_title"],
            content=row["dd_content"],
            ordering=row["dd_ordering"],
        )

        doc = Document(
            id=row["doc_id"],
            title=row["doc_title"],
            format=row["doc_format"],
            source_path=row["doc_source_path"],
            metadata=row["doc_metadata"],
            created_at=row["doc_created_at"],
        )

        results.append(
            SearchResult(
                chunk=chunk,
                score=row["score"],
                document=doc,
                disclosure_doc=ddoc,
            )
        )

    return results


def batch_children_counts(
    disclosure_doc_ids: list[str],
    conn: psycopg.Connection,
) -> dict[str, int]:
    """Get children counts for multiple disclosure docs in one query.

    Returns:
        Dict mapping disclosure_doc_id -> children count.
    """
    if not disclosure_doc_ids:
        return {}
    cursor = conn.cursor(row_factory=psycopg.rows.dict_row)
    # Use ANY(%s) for parameterized IN-list.
    rows = cursor.execute(
        """
        SELECT parent_id, COUNT(*) AS cnt
        FROM disclosure_docs
        WHERE parent_id = ANY(%s)
        GROUP BY parent_id
        """,
        (disclosure_doc_ids,),
    ).fetchall()
    return {row["parent_id"]: row["cnt"] for row in rows}


def get_disclosure_content(
    disclosure_doc_id: str,
    conn: psycopg.Connection,
) -> str | None:
    """Get the content of a disclosure doc for drill-down."""
    ddoc = get_disclosure_doc(disclosure_doc_id, conn)
    return ddoc.content if ddoc else None


def get_children(
    disclosure_doc_id: str,
    conn: psycopg.Connection,
) -> list[dict]:
    """Get child disclosure docs for navigating deeper.

    Returns:
        List of dicts with id, title, level, ordering.
    """
    cursor = conn.cursor(row_factory=psycopg.rows.dict_row)
    return cursor.execute(
        """
        SELECT id, title, level, ordering, document_id
        FROM disclosure_docs
        WHERE parent_id = %s
        ORDER BY ordering
        """,
        (disclosure_doc_id,),
    ).fetchall()


def graph_search(
    query: str,
    conn: psycopg.Connection,
    limit: int = 10,
    threshold: float = 0.7,
    hierarchy_levels_up: int = 1,
    include_similar: bool = True,
) -> GraphSearchResult:
    """Run vector search then expand results via the knowledge graph.

    Args:
        query: The search query text.
        conn: Active database connection.
        limit: Maximum number of vector results.
        threshold: Minimum cosine similarity score (0-1).
        hierarchy_levels_up: How many CONTAINS levels to walk up per match.
        include_similar: Whether to traverse SIMILAR_TO edges.

    Returns:
        GraphSearchResult with vector matches, assembled reference markdown,
        and subgraph statistics.  Falls back to empty reference_document if KG
        is disabled or graph traversal fails.
    """
    from pointy_rag import graph_query, llms_txt
    from pointy_rag.config import get_settings

    results = search(query, conn, limit=limit, threshold=threshold)

    if not get_settings().kg_enabled or not results:
        return GraphSearchResult(
            vector_results=results,
            reference_document="",
            node_count=0,
            edge_count=0,
        )

    node_ids = [r.chunk.id for r in results]
    try:
        subgraph = graph_query.build_context_subgraph(
            node_ids, conn, hierarchy_levels_up, include_similar
        )
        reference_document = llms_txt.assemble_reference(subgraph, conn)
    except Exception:
        return GraphSearchResult(
            vector_results=results,
            reference_document="",
            node_count=0,
            edge_count=0,
        )

    return GraphSearchResult(
        vector_results=results,
        reference_document=reference_document,
        node_count=len(subgraph["nodes"]),
        edge_count=len(subgraph["edges"]),
    )


def get_parent_chain(
    disclosure_doc_id: str,
    conn: psycopg.Connection,
) -> list[DisclosureDoc]:
    """Get ancestor chain for breadcrumb context.

    Returns:
        List of DisclosureDoc from root (Level 0) to immediate parent.
    """
    cursor = conn.cursor(row_factory=psycopg.rows.dict_row)
    rows = cursor.execute(
        """
        WITH RECURSIVE ancestors AS (
            SELECT id, document_id, parent_id, level, title, content,
                   ordering, 0 AS depth
            FROM disclosure_docs
            WHERE id = %s
            UNION ALL
            SELECT d.id, d.document_id, d.parent_id, d.level, d.title,
                   d.content, d.ordering, a.depth + 1
            FROM disclosure_docs d
            JOIN ancestors a ON d.id = a.parent_id
            WHERE a.depth < 10
        )
        SELECT id, document_id, parent_id, level, title, content, ordering
        FROM ancestors
        WHERE id != %s
        ORDER BY level
        """,
        (disclosure_doc_id, disclosure_doc_id),
    ).fetchall()

    return [
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
