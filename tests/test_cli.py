"""Stub tests for CLI scaffolding."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from pointy_rag.cli import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output
    assert "ingest" in result.output
    assert "search" in result.output
    assert "drill" in result.output
    assert "ls" in result.output
    assert "graph-backfill" in result.output


def test_workspace_flag_accepted(tmp_path):
    """--workspace flag is parsed without error (even with no marker)."""
    result = runner.invoke(app, ["--workspace", str(tmp_path / "nope"), "--help"])
    assert result.exit_code == 0


def test_init_creates_marker(tmp_path):
    """init <path> creates directory + marker file (DB calls mocked)."""
    ws_dir = tmp_path / "my_workspace"
    with (
        patch("pointy_rag.db.ensure_database"),
        patch("pointy_rag.db.create_tables"),
    ):
        result = runner.invoke(app, ["init", str(ws_dir)], input="y\n")
    assert result.exit_code == 0
    assert (ws_dir / ".pointy-rag.toml").exists()


def test_init_cwd_no_path(tmp_path, monkeypatch):
    """init with no path argument uses cwd."""
    monkeypatch.chdir(tmp_path)
    with (
        patch("pointy_rag.db.ensure_database"),
        patch("pointy_rag.db.create_tables"),
    ):
        result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / ".pointy-rag.toml").exists()


def _make_conn_ctx(mock_conn):
    """Build a MagicMock that acts as a context-manager returning mock_conn."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_conn)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def test_graph_backfill_aborts_when_kg_disabled():
    """graph-backfill exits with error if KG is disabled."""
    from pointy_rag.config import Settings

    # Imports inside graph_backfill are from their source modules
    with patch(
        "pointy_rag.config.get_settings",
        return_value=Settings(kg_enabled=False),
    ):
        result = runner.invoke(app, ["graph-backfill"])
    assert result.exit_code != 0
    assert "disabled" in result.output.lower()


def test_graph_backfill_no_documents():
    """graph-backfill exits cleanly with a message when no documents exist."""
    mock_conn = MagicMock()

    from pointy_rag.config import Settings

    with (
        patch("pointy_rag.config.get_settings", return_value=Settings(kg_enabled=True)),
        patch("pointy_rag.db.get_connection", return_value=_make_conn_ctx(mock_conn)),
        patch("pointy_rag.graph.ensure_graph"),
        patch("pointy_rag.db.list_documents", return_value=[]),
    ):
        result = runner.invoke(app, ["graph-backfill"])
    assert result.exit_code == 0
    assert "nothing to backfill" in result.output.lower()


def test_graph_backfill_continues_on_failure():
    """graph-backfill should continue processing remaining documents when one fails."""
    from datetime import UTC, datetime

    import psycopg

    from pointy_rag.config import Settings
    from pointy_rag.models import Chunk, DisclosureDoc, DisclosureLevel

    mock_conn = MagicMock()

    doc1 = {
        "id": "doc-1",
        "title": "Good Book",
        "format": "pdf",
        "created_at": datetime.now(UTC),
    }
    doc2 = {
        "id": "doc-2",
        "title": "Bad Book",
        "format": "pdf",
        "created_at": datetime.now(UTC),
    }
    doc3 = {
        "id": "doc-3",
        "title": "Also Good",
        "format": "pdf",
        "created_at": datetime.now(UTC),
    }

    ddoc = DisclosureDoc(
        id="ddoc-1",
        document_id="doc-1",
        level=DisclosureLevel.section_summary,
        title="Chapter 1",
        content="Content.",
        parent_id=None,
    )
    ddoc3 = DisclosureDoc(
        id="ddoc-3",
        document_id="doc-3",
        level=DisclosureLevel.section_summary,
        title="Chapter 3",
        content="Content 3.",
        parent_id=None,
    )
    chunk = Chunk(
        id="chunk-1",
        disclosure_doc_id="ddoc-1",
        content="text",
        embedding=[0.1] * 4,
    )
    chunk3 = Chunk(
        id="chunk-3",
        disclosure_doc_id="ddoc-3",
        content="text3",
        embedding=[0.1] * 4,
    )

    def get_ddocs(doc_id, conn):
        if doc_id == "doc-2":
            raise psycopg.Error("AGE unavailable")
        return {"doc-1": [ddoc], "doc-3": [ddoc3]}[doc_id]

    with (
        patch("pointy_rag.config.get_settings", return_value=Settings(kg_enabled=True)),
        patch("pointy_rag.db.get_connection", return_value=_make_conn_ctx(mock_conn)),
        patch("pointy_rag.graph.ensure_graph"),
        patch("pointy_rag.db.list_documents", return_value=[doc1, doc2, doc3]),
        patch("pointy_rag.db.get_disclosure_docs_by_document", side_effect=get_ddocs),
        patch(
            "pointy_rag.db.get_chunks_by_document",
            side_effect=lambda did, c: {"doc-1": [chunk], "doc-3": [chunk3]}.get(
                did, []
            ),
        ),
        patch("pointy_rag.graph.node_exists", return_value=False),
        patch("pointy_rag.graph.create_disclosure_node"),
        patch("pointy_rag.graph.create_chunk_node"),
        patch("pointy_rag.graph.create_contains_edge"),
        patch("pointy_rag.graph.create_similar_to_edges", return_value=1),
    ):
        result = runner.invoke(app, ["graph-backfill"])

    # Should still succeed overall (exit 0)
    assert result.exit_code == 0
    # Output should mention the failure
    assert "Bad Book" in result.output or "Failed" in result.output
    # conn.commit() should have been called for the two successful documents
    assert mock_conn.commit.call_count >= 2


def test_graph_backfill_processes_documents():
    """graph-backfill creates nodes and edges for each document."""
    from datetime import UTC, datetime

    from pointy_rag.config import Settings
    from pointy_rag.models import Chunk, DisclosureDoc, DisclosureLevel

    mock_conn = MagicMock()

    doc = {
        "id": "doc-1",
        "title": "Test Book",
        "format": "pdf",
        "created_at": datetime.now(UTC),
    }
    ddoc = DisclosureDoc(
        id="ddoc-1",
        document_id="doc-1",
        level=DisclosureLevel.section_summary,
        title="Chapter 1",
        content="Content.",
        parent_id=None,
    )
    chunk = Chunk(
        id="chunk-1",
        disclosure_doc_id="ddoc-1",
        content="text",
        embedding=[0.1] * 4,
    )

    with (
        patch("pointy_rag.config.get_settings", return_value=Settings(kg_enabled=True)),
        patch("pointy_rag.db.get_connection", return_value=_make_conn_ctx(mock_conn)),
        patch("pointy_rag.graph.ensure_graph"),
        patch("pointy_rag.db.list_documents", return_value=[doc]),
        patch("pointy_rag.db.get_disclosure_docs_by_document", return_value=[ddoc]),
        patch("pointy_rag.db.get_chunks_by_document", return_value=[chunk]),
        patch("pointy_rag.graph.node_exists", return_value=False),
        patch("pointy_rag.graph.create_disclosure_node") as mock_cdn,
        patch("pointy_rag.graph.create_chunk_node") as mock_ccn,
        patch("pointy_rag.graph.create_contains_edge") as mock_cce,
        patch("pointy_rag.graph.create_similar_to_edges", return_value=3) as mock_cse,
    ):
        result = runner.invoke(app, ["graph-backfill"])

    assert result.exit_code == 0
    mock_cdn.assert_called_once_with(ddoc, mock_conn)
    mock_ccn.assert_called_once_with(chunk, "doc-1", mock_conn)
    # create_contains_edge called for disclosure->chunk (no parent_id on ddoc)
    mock_cce.assert_called_once_with("ddoc-1", "chunk-1", 0, mock_conn)
    mock_cse.assert_called_once_with(chunk, mock_conn)
    assert "1" in result.output  # documents processed
    assert "3" in result.output  # similarity edges
