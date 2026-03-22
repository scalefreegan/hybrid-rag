"""Apache AGE graph layer for pointy-rag knowledge graph."""

from datetime import UTC, datetime

import psycopg

from pointy_rag.models import Chunk, DisclosureDoc

GRAPH_NAME = "pointy_rag_kg"


def _cypher_sql(cypher: str) -> str:
    """Return a SQL string that runs a Cypher query via AGE."""
    return f"SELECT * FROM ag_catalog.cypher(%s, $$ {cypher} $$) AS (v agtype)"  # noqa: S608


def _esc(s: str) -> str:
    """Escape single quotes for Cypher string literals."""
    return s.replace("'", "\\'")


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
    except Exception:
        # Graph already exists — silently ignore
        conn.rollback()


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
    return bool(row and int(row[0]) > 0)


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
        candidate_id, score = row["id"], row["score"]
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
    node_count = (
        conn.execute(
            _cypher_sql("MATCH (n) RETURN count(n)"),  # noqa: S608
            (GRAPH_NAME,),
        ).fetchone()
        or (0,)
    )[0]

    edge_count = (
        conn.execute(
            _cypher_sql("MATCH ()-[e]->() RETURN count(e)"),  # noqa: S608
            (GRAPH_NAME,),
        ).fetchone()
        or (0,)
    )[0]

    similar_to_count = (
        conn.execute(
            _cypher_sql("MATCH ()-[e:SIMILAR_TO]->() RETURN count(e)"),  # noqa: S608
            (GRAPH_NAME,),
        ).fetchone()
        or (0,)
    )[0]

    contains_count = (
        conn.execute(
            _cypher_sql("MATCH ()-[e:CONTAINS]->() RETURN count(e)"),  # noqa: S608
            (GRAPH_NAME,),
        ).fetchone()
        or (0,)
    )[0]

    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "similar_to_count": similar_to_count,
        "contains_count": contains_count,
    }
