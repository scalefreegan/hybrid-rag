"""Tests for the ingestion pipeline."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pointy_rag.ingest import ingest_document, ingest_paths
from pointy_rag.models import DocumentFormat


@pytest.fixture
def mock_conn():
    conn = MagicMock()
    conn.execute.return_value = conn
    conn.commit.return_value = None
    return conn


@pytest.fixture
def tmp_pdf(tmp_path):
    """Create a minimal non-empty file with .pdf extension."""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content for testing")
    return pdf


class TestIngestDocument:
    @pytest.mark.asyncio
    @patch("pointy_rag.ingest.embed_texts")
    @patch("pointy_rag.ingest.convert_to_markdown", new_callable=AsyncMock)
    @patch("pointy_rag.ingest.detect_format")
    @patch("pointy_rag.ingest.get_document_by_source_path")
    @patch("pointy_rag.ingest.insert_document")
    @patch("pointy_rag.ingest.insert_chunk")
    async def test_ingest_no_agent(
        self,
        mock_insert_chunk,
        mock_insert_doc,
        mock_get_existing,
        mock_detect,
        mock_convert,
        mock_embed,
        mock_conn,
        tmp_pdf,
    ):
        mock_detect.return_value = DocumentFormat.pdf
        mock_convert.return_value = (
            "## Introduction\nSome content here for testing.",
            None,
        )
        mock_embed.return_value = [[0.1] * 1024]
        mock_get_existing.return_value = None

        doc = await ingest_document(tmp_pdf, mock_conn, use_agent=False)

        assert doc.title == "test"
        assert doc.format == DocumentFormat.pdf
        mock_insert_doc.assert_called_once()
        assert mock_insert_chunk.call_count >= 1
        assert mock_conn.commit.call_count >= 2  # doc + chunks

    @pytest.mark.asyncio
    @patch("pointy_rag.ingest.embed_texts")
    @patch("pointy_rag.ingest.convert_to_markdown", new_callable=AsyncMock)
    @patch("pointy_rag.ingest.detect_format")
    @patch("pointy_rag.ingest.get_document_by_source_path")
    @patch("pointy_rag.ingest.insert_document")
    @patch("pointy_rag.ingest.insert_chunk")
    @patch("pointy_rag.ingest.delete_document_data")
    async def test_re_ingestion_deletes_existing(
        self,
        mock_delete,
        mock_insert_chunk,
        mock_insert_doc,
        mock_get_existing,
        mock_detect,
        mock_convert,
        mock_embed,
        mock_conn,
        tmp_pdf,
    ):
        from pointy_rag.models import Document

        mock_detect.return_value = DocumentFormat.pdf
        mock_convert.return_value = ("## Section\nContent.", None)
        mock_embed.return_value = [[0.1] * 1024]

        existing_doc = Document(
            id="old-id",
            title="test",
            format=DocumentFormat.pdf,
            source_path=str(tmp_pdf.resolve()),
        )
        mock_get_existing.return_value = existing_doc

        await ingest_document(tmp_pdf, mock_conn, use_agent=False)

        mock_delete.assert_called_once_with("old-id", mock_conn)

    @pytest.mark.asyncio
    @patch("pointy_rag.ingest.embed_texts")
    @patch("pointy_rag.ingest.convert_to_markdown", new_callable=AsyncMock)
    @patch("pointy_rag.ingest.detect_format")
    @patch("pointy_rag.ingest.get_document_by_source_path")
    @patch("pointy_rag.ingest.insert_document")
    @patch("pointy_rag.ingest.insert_chunk")
    async def test_empty_chunks_raises(
        self,
        mock_insert_chunk,
        mock_insert_doc,
        mock_get_existing,
        mock_detect,
        mock_convert,
        mock_embed,
        mock_conn,
        tmp_pdf,
    ):
        mock_detect.return_value = DocumentFormat.pdf
        mock_convert.return_value = ("", None)  # empty markdown
        mock_get_existing.return_value = None

        with pytest.raises(ValueError, match="No chunks produced"):
            await ingest_document(tmp_pdf, mock_conn, use_agent=False)

    @pytest.mark.asyncio
    @patch("pointy_rag.ingest.embed_texts")
    @patch("pointy_rag.ingest.convert_to_markdown", new_callable=AsyncMock)
    @patch("pointy_rag.ingest.detect_format")
    @patch("pointy_rag.ingest.get_document_by_source_path")
    @patch("pointy_rag.ingest.insert_document")
    @patch("pointy_rag.ingest.insert_chunk")
    @patch(
        "pointy_rag.disclosure.generate_disclosure_hierarchy",
        new_callable=AsyncMock,
    )
    async def test_disclosure_failure_stores_chunks_anyway(
        self,
        mock_disclosure,
        mock_insert_chunk,
        mock_insert_doc,
        mock_get_existing,
        mock_detect,
        mock_convert,
        mock_embed,
        mock_conn,
        tmp_pdf,
    ):
        mock_detect.return_value = DocumentFormat.pdf
        mock_convert.return_value = ("## Section\nContent.", None)
        mock_embed.return_value = [[0.1] * 1024]
        mock_get_existing.return_value = None
        mock_disclosure.side_effect = RuntimeError("Agent timeout")

        doc = await ingest_document(tmp_pdf, mock_conn, use_agent=True)

        assert doc is not None
        assert mock_insert_chunk.call_count >= 1


class TestIngestPaths:
    @pytest.mark.asyncio
    @patch("pointy_rag.ingest.ingest_document", new_callable=AsyncMock)
    async def test_continues_after_failure(self, mock_ingest, mock_conn):
        from pointy_rag.models import Document

        good_doc = Document(
            title="good", format=DocumentFormat.pdf, source_path="/good.pdf"
        )
        mock_ingest.side_effect = [
            good_doc,
            RuntimeError("bad file"),
            good_doc,
        ]

        paths = [Path("/a.pdf"), Path("/b.pdf"), Path("/c.pdf")]
        succeeded, failed = await ingest_paths(paths, mock_conn, use_agent=False)

        assert len(succeeded) == 2
        assert len(failed) == 1
        assert failed[0][0] == Path("/b.pdf")

    @pytest.mark.asyncio
    @patch("pointy_rag.ingest.ingest_document", new_callable=AsyncMock)
    async def test_all_succeed(self, mock_ingest, mock_conn):
        from pointy_rag.models import Document

        doc = Document(title="doc", format=DocumentFormat.pdf, source_path="/doc.pdf")
        mock_ingest.return_value = doc

        paths = [Path("/a.pdf"), Path("/b.pdf")]
        succeeded, failed = await ingest_paths(paths, mock_conn, use_agent=False)

        assert len(succeeded) == 2
        assert len(failed) == 0


class TestGraphIntegration:
    @pytest.mark.asyncio
    @patch("pointy_rag.ingest.get_settings")
    @patch("pointy_rag.graph.create_similar_to_edges")
    @patch("pointy_rag.graph.create_contains_edge")
    @patch("pointy_rag.graph.create_chunk_node")
    @patch("pointy_rag.graph.create_disclosure_node")
    @patch("pointy_rag.ingest.embed_texts")
    @patch("pointy_rag.ingest.convert_to_markdown", new_callable=AsyncMock)
    @patch("pointy_rag.ingest.detect_format")
    @patch("pointy_rag.ingest.get_document_by_source_path")
    @patch("pointy_rag.ingest.insert_document")
    @patch("pointy_rag.ingest.insert_chunk")
    async def test_graph_populated_when_kg_enabled(
        self,
        mock_insert_chunk,
        mock_insert_doc,
        mock_get_existing,
        mock_detect,
        mock_convert,
        mock_embed,
        mock_create_disclosure_node,
        mock_create_chunk_node,
        mock_create_contains_edge,
        mock_create_similar_to_edges,
        mock_get_settings,
        mock_conn,
        tmp_pdf,
    ):
        from pointy_rag.models import DisclosureDoc, DisclosureLevel

        mock_settings = MagicMock()
        mock_settings.kg_enabled = True
        mock_get_settings.return_value = mock_settings

        mock_detect.return_value = DocumentFormat.pdf
        mock_convert.return_value = ("## Section\nContent here.", None)
        mock_embed.return_value = [[0.1] * 1024]
        mock_get_existing.return_value = None
        mock_create_similar_to_edges.return_value = 2

        ddoc = DisclosureDoc(
            document_id="doc-id",
            level=DisclosureLevel.detailed_passage,
            title="Section",
            content="Content here.",
            ordering=0,
        )

        with patch(
            "pointy_rag.disclosure.generate_disclosure_hierarchy",
            new_callable=AsyncMock,
            return_value=[ddoc],
        ):
            with patch(
                "pointy_rag.pointer_mapper.map_chunks_to_disclosure"
            ) as mock_map:
                from pointy_rag.models import Chunk

                chunk = Chunk(
                    disclosure_doc_id=ddoc.id,
                    content="Content here.",
                    embedding=[0.1] * 1024,
                    metadata={},
                )
                mock_map.return_value = [chunk]
                await ingest_document(tmp_pdf, mock_conn, use_agent=True)

        mock_create_disclosure_node.assert_called_once()
        mock_create_chunk_node.assert_called_once()
        mock_create_similar_to_edges.assert_called_once()

    @pytest.mark.asyncio
    @patch("pointy_rag.ingest.get_settings")
    @patch("pointy_rag.ingest.embed_texts")
    @patch("pointy_rag.ingest.convert_to_markdown", new_callable=AsyncMock)
    @patch("pointy_rag.ingest.detect_format")
    @patch("pointy_rag.ingest.get_document_by_source_path")
    @patch("pointy_rag.ingest.insert_document")
    @patch("pointy_rag.ingest.insert_chunk")
    async def test_graph_not_populated_when_kg_disabled(
        self,
        mock_insert_chunk,
        mock_insert_doc,
        mock_get_existing,
        mock_detect,
        mock_convert,
        mock_embed,
        mock_get_settings,
        mock_conn,
        tmp_pdf,
    ):
        mock_settings = MagicMock()
        mock_settings.kg_enabled = False
        mock_get_settings.return_value = mock_settings

        mock_detect.return_value = DocumentFormat.pdf
        mock_convert.return_value = ("## Section\nContent here.", None)
        mock_embed.return_value = [[0.1] * 1024]
        mock_get_existing.return_value = None

        with patch("pointy_rag.graph.create_chunk_node") as mock_chunk_node:
            with patch(
                "pointy_rag.graph.create_similar_to_edges"
            ) as mock_similar_to:
                await ingest_document(tmp_pdf, mock_conn, use_agent=False)
                mock_chunk_node.assert_not_called()
                mock_similar_to.assert_not_called()

    @pytest.mark.asyncio
    @patch("pointy_rag.ingest.get_settings")
    @patch("pointy_rag.graph.delete_document_graph_data")
    @patch("pointy_rag.ingest.embed_texts")
    @patch("pointy_rag.ingest.convert_to_markdown", new_callable=AsyncMock)
    @patch("pointy_rag.ingest.detect_format")
    @patch("pointy_rag.ingest.get_document_by_source_path")
    @patch("pointy_rag.ingest.insert_document")
    @patch("pointy_rag.ingest.insert_chunk")
    @patch("pointy_rag.ingest.delete_document_data")
    async def test_re_ingestion_cleans_graph_when_kg_enabled(
        self,
        mock_delete_data,
        mock_insert_chunk,
        mock_insert_doc,
        mock_get_existing,
        mock_detect,
        mock_convert,
        mock_embed,
        mock_delete_graph,
        mock_get_settings,
        mock_conn,
        tmp_pdf,
    ):
        from pointy_rag.models import Document

        mock_settings = MagicMock()
        mock_settings.kg_enabled = True
        mock_get_settings.return_value = mock_settings

        mock_detect.return_value = DocumentFormat.pdf
        mock_convert.return_value = ("## Section\nContent.", None)
        mock_embed.return_value = [[0.1] * 1024]

        existing_doc = Document(
            id="old-id",
            title="test",
            format=DocumentFormat.pdf,
            source_path=str(tmp_pdf.resolve()),
        )
        mock_get_existing.return_value = existing_doc

        await ingest_document(tmp_pdf, mock_conn, use_agent=False)

        mock_delete_graph.assert_called_once_with("old-id", mock_conn)
        mock_delete_data.assert_called_once_with("old-id", mock_conn)


class TestDbFunctions:
    def test_delete_document_data(self):
        from pointy_rag.db import delete_document_data

        conn = MagicMock()
        conn.execute.return_value = conn

        delete_document_data("doc-123", conn)

        # 4 statements: chunks, clear parents, ddocs, doc
        assert conn.execute.call_count == 4

    def test_list_documents(self):
        from pointy_rag.db import list_documents

        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = [
            {
                "id": "d1",
                "title": "Test",
                "format": "pdf",
                "source_path": "/test.pdf",
                "created_at": None,
                "disclosure_count": 5,
                "chunk_count": 10,
            },
        ]
        conn.cursor.return_value = cursor

        result = list_documents(conn)
        assert len(result) == 1
        assert result[0]["chunk_count"] == 10
