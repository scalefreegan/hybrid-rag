"""Tests for the pointer-based vector search module."""

from unittest.mock import MagicMock, patch

import pytest

from pointy_rag.models import (
    DisclosureDoc,
    DisclosureLevel,
    Document,
    DocumentFormat,
)
from pointy_rag.search import (
    get_children,
    get_disclosure_content,
    get_parent_chain,
    search,
)


@pytest.fixture
def mock_conn():
    conn = MagicMock()
    conn.execute.return_value = conn
    return conn


@pytest.fixture
def sample_embedding():
    return [0.1] * 1024


class TestSearch:
    @patch("pointy_rag.search.embed_query")
    @patch("pointy_rag.search.get_disclosure_doc")
    @patch("pointy_rag.search.get_document")
    def test_returns_results(
        self, mock_get_doc, mock_get_ddoc, mock_embed, mock_conn, sample_embedding
    ):
        mock_embed.return_value = sample_embedding
        mock_get_ddoc.return_value = DisclosureDoc(
            id="dd1",
            document_id="doc1",
            level=DisclosureLevel.detailed_passage,
            title="Section 1",
            content="Content",
        )
        mock_get_doc.return_value = Document(
            id="doc1",
            title="Test Doc",
            format=DocumentFormat.pdf,
            source_path="/test.pdf",
        )

        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = [
            {
                "id": "chunk1",
                "disclosure_doc_id": "dd1",
                "content": "Matched text",
                "metadata": {},
                "score": 0.85,
            },
        ]
        mock_conn.cursor.return_value = cursor

        results = search("test query", mock_conn)

        assert len(results) == 1
        assert results[0].score == 0.85
        assert results[0].chunk.id == "chunk1"
        assert results[0].chunk.embedding is None  # Stripped
        assert results[0].disclosure_doc.title == "Section 1"

    @patch("pointy_rag.search.embed_query")
    def test_no_results(self, mock_embed, mock_conn, sample_embedding):
        mock_embed.return_value = sample_embedding

        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = cursor

        results = search("obscure query", mock_conn)
        assert results == []

    @patch("pointy_rag.search.embed_query")
    @patch("pointy_rag.search.get_disclosure_doc")
    @patch("pointy_rag.search.get_document")
    def test_respects_limit_and_threshold(
        self, mock_get_doc, mock_get_ddoc, mock_embed, mock_conn, sample_embedding
    ):
        mock_embed.return_value = sample_embedding
        mock_get_ddoc.return_value = MagicMock(document_id="doc1")
        mock_get_doc.return_value = MagicMock(title="Doc")

        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = cursor

        search("query", mock_conn, limit=5, threshold=0.9)

        # Check the SQL params include our limit and threshold
        call_args = cursor.execute.call_args
        params = call_args[0][1]
        assert params[2] == 0.9  # threshold
        assert params[3] == 5  # limit


class TestGetDisclosureContent:
    @patch("pointy_rag.search.get_disclosure_doc")
    def test_returns_content(self, mock_get, mock_conn):
        mock_get.return_value = DisclosureDoc(
            id="dd1",
            document_id="doc1",
            level=DisclosureLevel.section_summary,
            title="Test",
            content="The content here",
        )
        assert get_disclosure_content("dd1", mock_conn) == "The content here"

    @patch("pointy_rag.search.get_disclosure_doc")
    def test_returns_none_for_missing(self, mock_get, mock_conn):
        mock_get.return_value = None
        assert get_disclosure_content("nonexistent", mock_conn) is None


class TestGetChildren:
    def test_returns_children(self, mock_conn):
        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = [
            {
                "id": "c1", "title": "Child 1",
                "level": 3, "ordering": 0, "document_id": "doc1",
            },
            {
                "id": "c2", "title": "Child 2",
                "level": 3, "ordering": 1, "document_id": "doc1",
            },
        ]
        mock_conn.cursor.return_value = cursor

        result = get_children("parent1", mock_conn)
        assert len(result) == 2
        assert result[0]["title"] == "Child 1"

    def test_returns_empty_for_leaf(self, mock_conn):
        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = cursor

        assert get_children("leaf1", mock_conn) == []


class TestGetParentChain:
    def test_returns_ancestors(self, mock_conn):
        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = [
            {
                "id": "l0",
                "document_id": "doc1",
                "parent_id": None,
                "level": 0,
                "title": "Library",
                "content": "Catalog",
                "ordering": 0,
            },
            {
                "id": "l1",
                "document_id": "doc1",
                "parent_id": "l0",
                "level": 1,
                "title": "Doc Index",
                "content": "Index",
                "ordering": 0,
            },
        ]
        mock_conn.cursor.return_value = cursor

        result = get_parent_chain("l2-child", mock_conn)
        assert len(result) == 2
        assert result[0].level == DisclosureLevel.library_catalog
        assert result[1].level == DisclosureLevel.resource_index

    def test_returns_empty_for_root(self, mock_conn):
        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = cursor

        assert get_parent_chain("root-id", mock_conn) == []
