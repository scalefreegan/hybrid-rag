"""Unit tests for pointy_rag.graph — no live database required."""

from unittest.mock import MagicMock, patch

from pointy_rag.graph import (
    GRAPH_NAME,
    create_chunk_node,
    create_contains_edge,
    create_disclosure_node,
    delete_document_graph_data,
    ensure_graph,
    get_graph_stats,
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
        c.args[0] if c.args else ""
        for c in mock_conn.execute.call_args_list
    ]
    assert any("CREATE EXTENSION IF NOT EXISTS age" in s for s in sql_fragments)
    assert any("LOAD 'age'" in s for s in sql_fragments)


def test_ensure_graph_idempotent_on_duplicate(mock_conn):
    """ensure_graph should swallow the exception when graph already exists."""

    def side_effect(sql, *args, **kwargs):
        if "create_graph" in sql:
            raise Exception("graph already exists")
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
    # Single quote in title should be escaped
    assert "It\\'s a title" in sql


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
    mock_conn.execute.return_value.fetchone.return_value = (42,)
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
