"""Tests for the pointer-based vector search module."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from pointy_rag.models import (
    DisclosureLevel,
)
from pointy_rag.search import (
    batch_children_counts,
    get_children,
    get_disclosure_content,
    get_parent_chain,
    graph_search,
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


def _make_joined_row(
    chunk_id="chunk1",
    disclosure_doc_id="dd1",
    chunk_content="Matched text",
    chunk_metadata=None,
    score=0.85,
    dd_id="dd1",
    dd_document_id="doc1",
    dd_parent_id=None,
    dd_level=3,
    dd_title="Section 1",
    dd_content="Content",
    dd_ordering=0,
    doc_id="doc1",
    doc_title="Test Doc",
    doc_format="pdf",
    doc_source_path="/test.pdf",
    doc_metadata=None,
    doc_created_at=None,
):
    return {
        "chunk_id": chunk_id,
        "disclosure_doc_id": disclosure_doc_id,
        "chunk_content": chunk_content,
        "chunk_metadata": chunk_metadata or {},
        "score": score,
        "dd_id": dd_id,
        "dd_document_id": dd_document_id,
        "dd_parent_id": dd_parent_id,
        "dd_level": dd_level,
        "dd_title": dd_title,
        "dd_content": dd_content,
        "dd_ordering": dd_ordering,
        "doc_id": doc_id,
        "doc_title": doc_title,
        "doc_format": doc_format,
        "doc_source_path": doc_source_path,
        "doc_metadata": doc_metadata or {},
        "doc_created_at": doc_created_at or datetime.now(UTC),
    }


class TestSearch:
    @patch("pointy_rag.search.embed_query")
    def test_returns_results(self, mock_embed, mock_conn, sample_embedding):
        mock_embed.return_value = sample_embedding

        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = [_make_joined_row()]
        mock_conn.cursor.return_value = cursor

        results = search("test query", mock_conn)

        assert len(results) == 1
        assert results[0].score == 0.85
        assert results[0].chunk.id == "chunk1"
        assert results[0].chunk.embedding is None  # Stripped
        assert results[0].disclosure_doc.title == "Section 1"
        assert results[0].document.title == "Test Doc"

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
    def test_respects_limit_and_threshold(
        self, mock_embed, mock_conn, sample_embedding
    ):
        mock_embed.return_value = sample_embedding

        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = cursor

        search("query", mock_conn, limit=5, threshold=0.9)

        # Check the SQL params include our limit and threshold
        call_args = cursor.execute.call_args
        params = call_args[0][1]
        # CTE-based query: (embedding, threshold, limit)
        assert params[1] == 0.9  # threshold
        assert params[2] == 5  # limit


class TestGetDisclosureContent:
    @patch("pointy_rag.search.get_disclosure_doc")
    def test_returns_content(self, mock_get, mock_conn):
        from pointy_rag.models import DisclosureDoc

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
                "id": "c1",
                "title": "Child 1",
                "level": 3,
                "ordering": 0,
                "document_id": "doc1",
            },
            {
                "id": "c2",
                "title": "Child 2",
                "level": 3,
                "ordering": 1,
                "document_id": "doc1",
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


class TestBatchChildrenCounts:
    def test_returns_counts(self, mock_conn):
        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = [
            {"parent_id": "p1", "cnt": 3},
            {"parent_id": "p2", "cnt": 1},
        ]
        mock_conn.cursor.return_value = cursor

        result = batch_children_counts(["p1", "p2", "p3"], mock_conn)
        assert result == {"p1": 3, "p2": 1}

    def test_empty_ids(self, mock_conn):
        assert batch_children_counts([], mock_conn) == {}


class TestGraphSearch:
    @patch("pointy_rag.search.embed_query")
    @patch("pointy_rag.llms_txt.assemble_reference")
    @patch("pointy_rag.graph_query.build_context_subgraph")
    @patch("pointy_rag.config.get_settings")
    def test_returns_graph_result_when_kg_enabled(
        self,
        mock_settings,
        mock_build,
        mock_assemble,
        mock_embed,
        mock_conn,
        sample_embedding,
    ):
        mock_embed.return_value = sample_embedding
        mock_settings.return_value.kg_enabled = True

        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = [_make_joined_row()]
        mock_conn.cursor.return_value = cursor

        mock_build.return_value = {
            "nodes": [{"node_id": "n1"}],
            "edges": [{"type": "SIMILAR_TO"}],
            "matches": ["chunk1"],
            "hierarchy": {},
        }
        mock_assemble.return_value = "# Reference\n> Content"

        result = graph_search("test query", mock_conn)

        assert len(result.vector_results) == 1
        assert result.reference_document == "# Reference\n> Content"
        assert result.node_count == 1
        assert result.edge_count == 1

    @patch("pointy_rag.search.embed_query")
    @patch("pointy_rag.config.get_settings")
    def test_falls_back_when_kg_disabled(
        self, mock_settings, mock_embed, mock_conn, sample_embedding
    ):
        mock_embed.return_value = sample_embedding
        mock_settings.return_value.kg_enabled = False

        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = [_make_joined_row()]
        mock_conn.cursor.return_value = cursor

        result = graph_search("test query", mock_conn)

        assert len(result.vector_results) == 1
        assert result.reference_document == ""
        assert result.node_count == 0
        assert result.edge_count == 0

    @patch("pointy_rag.search.embed_query")
    @patch("pointy_rag.config.get_settings")
    def test_falls_back_on_empty_results(
        self, mock_settings, mock_embed, mock_conn, sample_embedding
    ):
        mock_embed.return_value = sample_embedding
        mock_settings.return_value.kg_enabled = True

        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = cursor

        result = graph_search("test query", mock_conn)

        assert result.vector_results == []
        assert result.reference_document == ""
        assert result.node_count == 0

    @patch("pointy_rag.search.embed_query")
    @patch("pointy_rag.graph_query.build_context_subgraph")
    @patch("pointy_rag.config.get_settings")
    def test_falls_back_on_graph_exception(
        self, mock_settings, mock_build, mock_embed, mock_conn, sample_embedding
    ):
        mock_embed.return_value = sample_embedding
        mock_settings.return_value.kg_enabled = True
        mock_build.side_effect = RuntimeError("AGE unavailable")

        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = [_make_joined_row()]
        mock_conn.cursor.return_value = cursor

        result = graph_search("test query", mock_conn)

        assert len(result.vector_results) == 1
        assert result.reference_document == "[Graph expansion failed]"
        assert result.node_count == 0


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
