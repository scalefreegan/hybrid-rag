"""Unit tests for pointy_rag.db (uses mocking — no live database required)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from pointy_rag.db import (
    get_document,
    insert_chunk,
    insert_disclosure_doc,
    insert_document,
)
from pointy_rag.models import Chunk, DisclosureDoc, DisclosureLevel, Document


@pytest.fixture
def mock_conn():
    """Return a mock psycopg connection."""
    conn = MagicMock()
    conn.execute.return_value = conn
    return conn


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
    mock_conn.execute.return_value.fetchone.return_value = (
        "doc-abc",
        "Test Title",
        "epub",
        "/path/to/file.epub",
        {"key": "val"},
        now,
    )
    doc = get_document("doc-abc", mock_conn)
    assert doc is not None
    assert doc.id == "doc-abc"
    assert doc.title == "Test Title"
    assert doc.format == "epub"
    assert doc.metadata == {"key": "val"}
    assert doc.created_at == now


def test_get_document_not_found(mock_conn):
    mock_conn.execute.return_value.fetchone.return_value = None
    doc = get_document("nonexistent", mock_conn)
    assert doc is None


def test_get_database_url_default():
    with patch.dict("os.environ", {}, clear=True):
        # Remove POINTY_DATABASE_URL if set
        import os

        from pointy_rag.db import get_database_url
        os.environ.pop("POINTY_DATABASE_URL", None)
        url = get_database_url()
        assert url == "postgresql://localhost:5432/pointy_rag"


def test_get_database_url_from_env():
    with patch.dict("os.environ", {"POINTY_DATABASE_URL": "postgresql://myhost:5432/mydb"}):
        from pointy_rag.db import get_database_url

        url = get_database_url()
        assert url == "postgresql://myhost:5432/mydb"
