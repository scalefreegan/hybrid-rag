"""Unit tests for pointy_rag.graph — no live database required."""

from unittest.mock import MagicMock, patch

from pointy_rag.graph import (
    GRAPH_NAME,
    _escape_cypher,
    _parse_agtype_int,
    create_chunk_node,
    create_contains_edge,
    create_disclosure_node,
    create_similar_to_edges,
    delete_document_graph_data,
    ensure_graph,
    get_graph_stats,
    merge_contains_edge,
    node_exists,
)
from pointy_rag.models import Chunk, DisclosureDoc, DisclosureLevel

# ---------------------------------------------------------------------------
# ensure_graph
# ---------------------------------------------------------------------------


def test_ensure_graph_issues_extension_and_load(mock_conn):
    """ensure_graph should CREATE EXTENSION, LOAD age, and attempt create_graph."""
    mock_conn.execute.return_value = MagicMock()
    with patch("pointy_rag.graph.GRAPH_NAME", GRAPH_NAME):
        ensure_graph(mock_conn)

    sql_fragments = [
        c.args[0] if c.args else "" for c in mock_conn.execute.call_args_list
    ]
    assert any("CREATE EXTENSION IF NOT EXISTS age" in s for s in sql_fragments)
    assert any("LOAD 'age'" in s for s in sql_fragments)


def test_ensure_graph_idempotent_on_duplicate(mock_conn):
    """ensure_graph should swallow DuplicateSchema when graph already exists."""
    import psycopg.errors

    def side_effect(sql, *args, **kwargs):
        if "create_graph" in sql:
            raise psycopg.errors.DuplicateSchema("graph already exists")
        return MagicMock()

    mock_conn.execute.side_effect = side_effect
    # Should not raise
    ensure_graph(mock_conn)
    mock_conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# create_disclosure_node
# ---------------------------------------------------------------------------


def test_create_disclosure_node(mock_conn):
    ddoc = DisclosureDoc(
        id="ddoc-1",
        document_id="doc-1",
        level=DisclosureLevel.section_summary,
        title="Chapter 1",
        content="Some content.",
    )
    create_disclosure_node(ddoc, mock_conn)
    mock_conn.execute.assert_called_once()
    sql, params = mock_conn.execute.call_args[0]
    assert "ag_catalog.cypher" in sql
    assert GRAPH_NAME in params
    assert "DisclosureNode" in sql
    assert "ddoc-1" in sql
    assert "doc-1" in sql
    assert "Chapter 1" in sql


def test_create_disclosure_node_escapes_quotes(mock_conn):
    ddoc = DisclosureDoc(
        id="ddoc-2",
        document_id="doc-2",
        level=DisclosureLevel.detailed_passage,
        title="It's a title",
        content="Content.",
    )
    create_disclosure_node(ddoc, mock_conn)
    sql, _ = mock_conn.execute.call_args[0]
    # Single quote in title should be doubled (Cypher style), not backslash-escaped
    assert "It''s a title" in sql


# ---------------------------------------------------------------------------
# create_chunk_node
# ---------------------------------------------------------------------------


def test_create_chunk_node(mock_conn):
    chunk = Chunk(id="chunk-1", disclosure_doc_id="ddoc-1", content="text")
    create_chunk_node(chunk, "doc-1", mock_conn)
    mock_conn.execute.assert_called_once()
    sql, params = mock_conn.execute.call_args[0]
    assert "ag_catalog.cypher" in sql
    assert "ChunkNode" in sql
    assert "chunk-1" in sql
    assert "ddoc-1" in sql
    assert "doc-1" in sql


# ---------------------------------------------------------------------------
# create_contains_edge
# ---------------------------------------------------------------------------


def test_create_contains_edge(mock_conn):
    create_contains_edge("parent-1", "child-1", 3, mock_conn)
    mock_conn.execute.assert_called_once()
    sql, params = mock_conn.execute.call_args[0]
    assert "ag_catalog.cypher" in sql
    assert "CONTAINS" in sql
    assert "parent-1" in sql
    assert "child-1" in sql
    assert "3" in sql


# ---------------------------------------------------------------------------
# delete_document_graph_data
# ---------------------------------------------------------------------------


def test_delete_document_graph_data(mock_conn):
    delete_document_graph_data("doc-42", mock_conn)
    mock_conn.execute.assert_called_once()
    sql, params = mock_conn.execute.call_args[0]
    assert "ag_catalog.cypher" in sql
    assert "DETACH DELETE" in sql
    assert "doc-42" in sql


# ---------------------------------------------------------------------------
# get_graph_stats
# ---------------------------------------------------------------------------


def test_get_graph_stats(mock_conn):
    mock_conn.execute.return_value.fetchone.return_value = ("42::bigint",)
    stats = get_graph_stats(mock_conn)
    assert stats["node_count"] == 42
    assert stats["edge_count"] == 42
    assert stats["similar_to_count"] == 42
    assert stats["contains_count"] == 42
    assert mock_conn.execute.call_count == 4


def test_get_graph_stats_empty_graph(mock_conn):
    mock_conn.execute.return_value.fetchone.return_value = None
    stats = get_graph_stats(mock_conn)
    assert stats["node_count"] == 0
    assert stats["edge_count"] == 0


# ---------------------------------------------------------------------------
# merge_contains_edge
# ---------------------------------------------------------------------------


def test_merge_contains_edge(mock_conn):
    merge_contains_edge("parent-1", "child-1", 2, mock_conn)
    mock_conn.execute.assert_called_once()
    sql, params = mock_conn.execute.call_args[0]
    assert "ag_catalog.cypher" in sql
    assert "MERGE" in sql
    assert "CONTAINS" in sql
    assert "parent-1" in sql
    assert "child-1" in sql
    assert "2" in sql
    assert GRAPH_NAME in params


def test_merge_contains_edge_uses_merge_not_create(mock_conn):
    merge_contains_edge("p", "c", 0, mock_conn)
    sql, _ = mock_conn.execute.call_args[0]
    # MERGE pattern, not a bare CREATE
    assert "MERGE" in sql
    assert "CREATE (p" not in sql  # no bare CREATE edge statement


# ---------------------------------------------------------------------------
# node_exists
# ---------------------------------------------------------------------------


def test_node_exists_true(mock_conn):
    mock_conn.execute.return_value.fetchone.return_value = ("1::bigint",)
    assert node_exists("chunk-1", mock_conn) is True
    sql, params = mock_conn.execute.call_args[0]
    assert "chunk-1" in sql
    assert GRAPH_NAME in params


def test_node_exists_false_zero(mock_conn):
    mock_conn.execute.return_value.fetchone.return_value = ("0::bigint",)
    assert node_exists("chunk-99", mock_conn) is False


def test_node_exists_false_none(mock_conn):
    mock_conn.execute.return_value.fetchone.return_value = None
    assert node_exists("chunk-99", mock_conn) is False


def test_node_exists_agtype_string(mock_conn):
    """node_exists must handle agtype strings like '1::bigint'."""
    mock_conn.execute.return_value.fetchone.return_value = ("1::bigint",)
    assert node_exists("chunk-1", mock_conn) is True


# ---------------------------------------------------------------------------
# _escape_cypher
# ---------------------------------------------------------------------------


def test_escape_cypher_single_quote():
    assert _escape_cypher("it's") == "it''s"


def test_escape_cypher_backslash():
    assert _escape_cypher("a\\b") == "a\\\\b"


def test_escape_cypher_backslash_and_quote():
    """Backslash must be doubled before quote is doubled."""
    assert _escape_cypher("\\'") == "\\\\''", "backslash-then-quote must escape both"


def test_escape_cypher_plain():
    assert _escape_cypher("hello") == "hello"


# ---------------------------------------------------------------------------
# _parse_agtype_int
# ---------------------------------------------------------------------------


def test_parse_agtype_int_plain():
    assert _parse_agtype_int("42") == 42


def test_parse_agtype_int_with_annotation():
    assert _parse_agtype_int("42::bigint") == 42


def test_parse_agtype_int_zero_annotation():
    assert _parse_agtype_int("0::bigint") == 0


def test_parse_agtype_int_none():
    assert _parse_agtype_int(None) == 0


def test_parse_agtype_int_integer():
    assert _parse_agtype_int(7) == 7


# ---------------------------------------------------------------------------
# get_graph_stats agtype
# ---------------------------------------------------------------------------


def test_get_graph_stats_agtype_strings(mock_conn):
    """get_graph_stats must parse agtype strings returned by AGE."""
    mock_conn.execute.return_value.fetchone.return_value = ("100::bigint",)
    stats = get_graph_stats(mock_conn)
    assert stats["node_count"] == 100
    assert stats["edge_count"] == 100
    assert stats["similar_to_count"] == 100
    assert stats["contains_count"] == 100


# ---------------------------------------------------------------------------
# create_similar_to_edges
# ---------------------------------------------------------------------------


def test_create_similar_to_edges_above_threshold(mock_conn):
    """Edges are created for neighbors with score >= threshold."""
    from pointy_rag.models import Chunk

    chunk = Chunk(id="chunk-1", disclosure_doc_id="ddoc-1", content="text")
    chunk.embedding = [0.1, 0.2]

    # conftest sets execute.return_value = conn, so fetchall() resolves on conn directly
    mock_conn.fetchall.return_value = [
        ("neighbor-1", 0.9),
        ("neighbor-2", 0.7),
    ]

    # Pass threshold explicitly so get_settings is never called
    result = create_similar_to_edges(chunk, mock_conn, threshold=0.5)
    assert result == 2


def test_create_similar_to_edges_below_threshold(mock_conn):
    """No edges are created when all neighbors are below threshold."""
    from pointy_rag.models import Chunk

    chunk = Chunk(id="chunk-1", disclosure_doc_id="ddoc-1", content="text")
    chunk.embedding = [0.1, 0.2]

    mock_conn.fetchall.return_value = [
        ("neighbor-1", 0.3),
    ]

    result = create_similar_to_edges(chunk, mock_conn, threshold=0.5)
    assert result == 0


def test_create_similar_to_edges_empty_neighbors(mock_conn):
    """Returns 0 when there are no candidate neighbors."""
    from pointy_rag.models import Chunk

    chunk = Chunk(id="chunk-1", disclosure_doc_id="ddoc-1", content="text")
    chunk.embedding = [0.1, 0.2]

    mock_conn.fetchall.return_value = []

    result = create_similar_to_edges(chunk, mock_conn, threshold=0.5)
    assert result == 0


def test_create_similar_to_edges_all_above_threshold(mock_conn):
    """All neighbors above threshold results in count equal to number of rows."""
    from pointy_rag.models import Chunk

    chunk = Chunk(id="chunk-x", disclosure_doc_id="ddoc-1", content="text")
    chunk.embedding = [0.5, 0.5]

    mock_conn.fetchall.return_value = [
        ("nbr-a", 0.95),
        ("nbr-b", 0.85),
        ("nbr-c", 0.75),
    ]

    result = create_similar_to_edges(chunk, mock_conn, threshold=0.5)
    assert result == 3
