"""Apache AGE graph layer for pointy-rag knowledge graph."""

import logging
from datetime import UTC, datetime

import psycopg

from pointy_rag.models import Chunk, DisclosureDoc

logger = logging.getLogger(__name__)

GRAPH_NAME = "pointy_rag_kg"


def _cypher_sql(cypher: str) -> str:
    """Return a SQL string that runs a Cypher query via AGE."""
    return f"SELECT * FROM ag_catalog.cypher(%s, $$ {cypher} $$) AS (v agtype)"  # noqa: S608


def _parse_agtype_int(val: object) -> int:
    """Parse an AGE agtype integer value.

    AGE returns count() results as agtype strings like "42::bigint".
    Strip the type annotation before converting to int.
    """
    if val is None:
        return 0
    s = str(val).split("::")[0].strip()
    return int(s)


def _escape_cypher(s: str) -> str:
    """Escape a string for use in a Cypher string literal.

    Backslashes are doubled first, then single quotes are doubled.
    Cypher uses doubled single quotes (not backslash-escaped).
    """
    return s.replace("\\", "\\\\").replace("'", "''")


# Backward-compatible alias used by graph_query.py
_esc = _escape_cypher


def ensure_graph(conn: psycopg.Connection) -> None:
    """Create the AGE extension and graph if they don't already exist. Idempotent."""
    conn.execute("CREATE EXTENSION IF NOT EXISTS age")
    conn.execute("LOAD 'age'")
    conn.execute("SET search_path = ag_catalog, '$user', public")
    try:
        conn.execute(
            "SELECT ag_catalog.create_graph(%s)",
            (GRAPH_NAME,),
        )
    except psycopg.errors.DuplicateSchema:
        # Graph already exists — rollback and restore AGE session state
        conn.rollback()
        conn.execute("LOAD 'age'")
        conn.execute("SET search_path = ag_catalog, '$user', public")


def create_disclosure_node(ddoc: DisclosureDoc, conn: psycopg.Connection) -> None:
    """Create a :DisclosureNode vertex for a DisclosureDoc."""
    cypher = (
        f"MERGE (n:DisclosureNode {{node_id: '{_esc(ddoc.id)}'}}) "
        f"SET n.document_id = '{_esc(ddoc.document_id)}', "
        f"n.level = {int(ddoc.level)}, "
        f"n.title = '{_esc(ddoc.title)}', "
        f"n.node_type = 'disclosure'"
    )
    conn.execute(_cypher_sql(cypher), (GRAPH_NAME,))  # noqa: S608


def create_chunk_node(chunk: Chunk, document_id: str, conn: psycopg.Connection) -> None:
    """Create a :ChunkNode vertex for a Chunk."""
    cypher = (
        f"MERGE (n:ChunkNode {{node_id: '{_esc(chunk.id)}'}}) "
        f"SET n.disclosure_doc_id = '{_esc(chunk.disclosure_doc_id)}', "
        f"n.document_id = '{_esc(document_id)}', "
        f"n.node_type = 'chunk'"
    )
    conn.execute(_cypher_sql(cypher), (GRAPH_NAME,))  # noqa: S608


def create_contains_edge(
    parent_id: str, child_id: str, ordering: int, conn: psycopg.Connection
) -> None:
    """Create a :CONTAINS edge between two nodes matched by node_id."""
    cypher = (
        f"MATCH (parent {{node_id: '{_esc(parent_id)}'}}),"
        f" (child {{node_id: '{_esc(child_id)}'}}) "
        f"CREATE (parent)-[:CONTAINS {{ordering: {ordering}}}]->(child)"
    )
    conn.execute(_cypher_sql(cypher), (GRAPH_NAME,))  # noqa: S608


def merge_contains_edge(
    parent_id: str, child_id: str, ordering: int, conn: psycopg.Connection
) -> None:
    """Idempotent CONTAINS edge creation using MERGE (safe for backfill re-runs)."""
    cypher = (
        f"MATCH (parent {{node_id: '{_esc(parent_id)}'}}), "
        f"(child {{node_id: '{_esc(child_id)}'}}) "
        f"MERGE (parent)-[r:CONTAINS]->(child) "
        f"ON CREATE SET r.ordering = {ordering}"
    )
    conn.execute(_cypher_sql(cypher), (GRAPH_NAME,))  # noqa: S608


def node_exists(node_id: str, conn: psycopg.Connection) -> bool:
    """Return True if a node with the given node_id already exists in the graph."""
    row = conn.execute(
        _cypher_sql(f"MATCH (n {{node_id: '{_esc(node_id)}'}}) RETURN count(n)"),
        (GRAPH_NAME,),
    ).fetchone()
    return bool(row and _parse_agtype_int(row[0]) > 0)


def create_similar_to_edges(
    chunk: Chunk,
    conn: psycopg.Connection,
    threshold: float | None = None,
    max_neighbors: int = 20,
) -> int:
    """Create bidirectional SIMILAR_TO edges from chunk to its nearest neighbors.

    Uses pgvector KNN to find similar existing chunks, filters by threshold,
    and creates edges in both directions. Returns the number of edges created.
    """
    from pointy_rag.config import get_settings

    if threshold is None:
        threshold = get_settings().kg_similarity_threshold

    rows = conn.execute(
        """
        SELECT id, 1 - (embedding <=> %s::vector) AS score
        FROM chunks
        WHERE embedding IS NOT NULL AND id != %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (chunk.embedding, chunk.id, chunk.embedding, max_neighbors),
    ).fetchall()

    created = 0
    now = datetime.now(UTC).isoformat()
    for row in rows:
        candidate_id, score = row[0], row[1]
        if score < threshold:
            break
        cypher = (
            f"MATCH (a {{node_id: '{_esc(chunk.id)}'}}), "
            f"(b {{node_id: '{_esc(candidate_id)}'}}) "
            f"CREATE (a)-[:SIMILAR_TO {{score: {score}, created_at: '{now}'}}]->(b)"
        )
        conn.execute(_cypher_sql(cypher), (GRAPH_NAME,))
        created += 1
    return created


def delete_document_graph_data(doc_id: str, conn: psycopg.Connection) -> None:
    """Delete all graph nodes (and edges) for a document. Used for re-ingestion."""
    cypher = f"MATCH (n {{document_id: '{_esc(doc_id)}'}}) DETACH DELETE n"
    conn.execute(_cypher_sql(cypher), (GRAPH_NAME,))  # noqa: S608


def get_graph_stats(conn: psycopg.Connection) -> dict:
    """Return counts of nodes and edges in the knowledge graph."""

    def _fetch_count(cypher: str) -> int:
        row = conn.execute(_cypher_sql(cypher), (GRAPH_NAME,)).fetchone()  # noqa: S608
        return _parse_agtype_int(row[0]) if row else 0

    return {
        "node_count": _fetch_count("MATCH (n) RETURN count(n)"),
        "edge_count": _fetch_count("MATCH ()-[e]->() RETURN count(e)"),
        "similar_to_count": _fetch_count("MATCH ()-[e:SIMILAR_TO]->() RETURN count(e)"),
        "contains_count": _fetch_count("MATCH ()-[e:CONTAINS]->() RETURN count(e)"),
    }
