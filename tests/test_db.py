"""Unit tests for pointy_rag.db (uses mocking — no live database required)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from pointy_rag.db import (
    _split_ddl,
    create_tables,
    get_chunks_by_document,
    get_document,
    insert_chunk,
    insert_disclosure_doc,
    insert_document,
)
from pointy_rag.models import Chunk, DisclosureDoc, DisclosureLevel, Document


def test_insert_document(mock_conn):
    doc = Document(title="My Book", format="pdf", source_path="/books/mybook.pdf")
    insert_document(doc, mock_conn)
    mock_conn.execute.assert_called_once()
    call_args = mock_conn.execute.call_args
    assert doc.id in call_args[0][1]
    assert "My Book" in call_args[0][1]


def test_insert_disclosure_doc(mock_conn):
    ddoc = DisclosureDoc(
        document_id="doc-1",
        level=DisclosureLevel.section_summary,
        title="Chapter 1",
        content="Some section content.",
    )
    insert_disclosure_doc(ddoc, mock_conn)
    mock_conn.execute.assert_called_once()
    call_args = mock_conn.execute.call_args
    params = call_args[0][1]
    assert ddoc.id in params
    assert "doc-1" in params
    assert int(DisclosureLevel.section_summary) in params


def test_insert_chunk(mock_conn):
    chunk = Chunk(
        disclosure_doc_id="ddoc-1",
        content="This is a chunk.",
        embedding=[0.5] * 1024,
    )
    insert_chunk(chunk, mock_conn)
    mock_conn.execute.assert_called_once()
    call_args = mock_conn.execute.call_args
    params = call_args[0][1]
    assert chunk.id in params
    assert "ddoc-1" in params
    assert [0.5] * 1024 in params


def test_get_document_found(mock_conn):
    now = datetime.now(UTC)
    mock_cursor = MagicMock()
    mock_cursor.execute.return_value.fetchone.return_value = {
        "id": "doc-abc",
        "title": "Test Title",
        "format": "epub",
        "source_path": "/path/to/file.epub",
        "metadata": {"key": "val"},
        "created_at": now,
    }
    mock_conn.cursor.return_value = mock_cursor
    doc = get_document("doc-abc", mock_conn)
    assert doc is not None
    assert doc.id == "doc-abc"
    assert doc.title == "Test Title"
    assert doc.format == "epub"
    assert doc.metadata == {"key": "val"}
    assert doc.created_at == now


def test_get_document_not_found(mock_conn):
    mock_cursor = MagicMock()
    mock_cursor.execute.return_value.fetchone.return_value = None
    mock_conn.cursor.return_value = mock_cursor
    doc = get_document("nonexistent", mock_conn)
    assert doc is None


def test_get_chunks_by_document(mock_conn):
    mock_cursor = MagicMock()
    mock_cursor.execute.return_value.fetchall.return_value = [
        {
            "id": "chunk-1",
            "disclosure_doc_id": "ddoc-1",
            "content": "First chunk.",
            "embedding": [0.1] * 1024,
            "metadata": {},
        },
        {
            "id": "chunk-2",
            "disclosure_doc_id": "ddoc-1",
            "content": "Second chunk.",
            "embedding": None,
            "metadata": {"key": "val"},
        },
    ]
    mock_conn.cursor.return_value = mock_cursor
    chunks = get_chunks_by_document("doc-1", mock_conn)
    assert len(chunks) == 2
    assert chunks[0].id == "chunk-1"
    assert chunks[0].disclosure_doc_id == "ddoc-1"
    assert chunks[0].content == "First chunk."
    assert chunks[1].id == "chunk-2"
    assert chunks[1].embedding is None
    # Verify the SQL JOIN through disclosure_docs
    sql = mock_cursor.execute.call_args[0][0]
    assert "disclosure_docs" in sql
    assert "document_id" in sql


def test_get_chunks_by_document_empty(mock_conn):
    mock_cursor = MagicMock()
    mock_cursor.execute.return_value.fetchall.return_value = []
    mock_conn.cursor.return_value = mock_cursor
    chunks = get_chunks_by_document("doc-empty", mock_conn)
    assert chunks == []


def test_create_tables():
    """Verify create_tables executes each DDL statement individually."""
    mock_conn = MagicMock()
    with (
        patch("pointy_rag.db.psycopg.connect") as mock_connect,
        patch("pointy_rag.db.register_vector"),
    ):
        mock_connect.return_value.__enter__ = lambda _: mock_conn
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        create_tables("postgresql://localhost/test")

    calls = mock_conn.execute.call_args_list
    # First call: CREATE EXTENSION
    assert "CREATE EXTENSION" in calls[0][0][0]
    # Remaining calls: individual DDL statements (3 tables + 3 indexes = 6)
    ddl_calls = calls[1:]
    assert len(ddl_calls) >= 6
    # Verify tables
    ddl_sql = " ".join(c[0][0] for c in ddl_calls)
    assert "CREATE TABLE IF NOT EXISTS documents" in ddl_sql
    assert "CREATE TABLE IF NOT EXISTS disclosure_docs" in ddl_sql
    assert "CREATE TABLE IF NOT EXISTS chunks" in ddl_sql
    # Verify HNSW index (not IVFFlat)
    assert "hnsw" in ddl_sql
    assert "ivfflat" not in ddl_sql
    # Verify commit called (extension commit + DDL commit)
    assert mock_conn.commit.call_count == 2


def test_split_ddl():
    """Verify DDL splitting produces correct individual statements."""
    from pointy_rag.db import DDL

    statements = _split_ddl(DDL)
    assert len(statements) == 6  # 3 tables + 3 indexes
    for stmt in statements:
        assert stmt.endswith(";")
        assert not stmt.endswith(";;"), f"Double semicolon: {stmt!r}"


def test_resolve_database_url_used_for_connection():
    """Verify get_connection uses resolve_database_url when no URL provided."""
    with (
        patch(
            "pointy_rag.workspace.resolve_database_url",
            return_value="postgresql://from-settings/db",
        ),
        patch("pointy_rag.db.psycopg.connect") as mock_connect,
    ):
        mock_ctx = MagicMock()
        mock_connect.return_value.__enter__ = lambda _: mock_ctx
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        with patch("pointy_rag.db.register_vector"):
            from pointy_rag.db import get_connection

            with get_connection() as _conn:
                pass
        mock_connect.assert_called_once_with("postgresql://from-settings/db")


def test_ensure_database_creates_when_missing():
    """ensure_database issues CREATE DATABASE when DB doesn't exist."""
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None
    with patch("pointy_rag.db.psycopg.connect") as mock_connect:
        mock_connect.return_value.__enter__ = lambda _: mock_conn
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        from pointy_rag.db import ensure_database

        ensure_database("postgresql://localhost:5432/new_db")
    # Should have called execute twice: SELECT + CREATE DATABASE
    assert mock_conn.execute.call_count == 2
    create_call = mock_conn.execute.call_args_list[1]
    composed = create_call[0][0]
    # Verify it's a psycopg.sql.Composed object containing the DB name
    import psycopg.sql

    assert isinstance(composed, psycopg.sql.Composed)
    # Check that SELECT used the right db name
    select_call = mock_conn.execute.call_args_list[0]
    assert select_call[0][1] == ("new_db",)


def test_ensure_database_noop_when_exists():
    """ensure_database skips CREATE when database already exists."""
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (1,)
    with patch("pointy_rag.db.psycopg.connect") as mock_connect:
        mock_connect.return_value.__enter__ = lambda _: mock_conn
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        from pointy_rag.db import ensure_database

        ensure_database("postgresql://localhost:5432/existing_db")
    # Only the SELECT check — no CREATE
    assert mock_conn.execute.call_count == 1
