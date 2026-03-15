"""Tests for the progressive disclosure hierarchy generator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pointy_rag.disclosure import (
    generate_disclosure_hierarchy,
    regenerate_library_catalog,
)
from pointy_rag.models import DisclosureLevel


@pytest.fixture
def mock_conn():
    conn = MagicMock()
    conn.execute.return_value = conn
    conn.commit.return_value = None
    return conn


@pytest.fixture
def sample_markdown():
    return (
        "## Introduction\n"
        "This is the introduction content.\n\n"
        "## Methods\n"
        "These are the methods used.\n\n"
        "## Results\n"
        "Here are the results.\n"
    )


class TestGenerateDisclosureHierarchy:
    @pytest.mark.asyncio
    async def test_empty_markdown_returns_empty(self, mock_conn):
        result = await generate_disclosure_hierarchy("doc1", "", "Title", mock_conn)
        assert result == []

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_empty(self, mock_conn):
        result = await generate_disclosure_hierarchy(
            "doc1", "   \n  ", "Title", mock_conn
        )
        assert result == []

    @pytest.mark.asyncio
    @patch("pointy_rag.disclosure.run_disclosure_agent", new_callable=AsyncMock)
    @patch("pointy_rag.disclosure.insert_disclosure_doc")
    async def test_generates_three_levels(
        self, mock_insert, mock_agent, mock_conn, sample_markdown
    ):
        mock_agent.return_value = "Summary text"

        result = await generate_disclosure_hierarchy(
            "doc1", sample_markdown, "Test Doc", mock_conn
        )

        # Should produce: 1 L1 + 3 L2 + 3 L3 = 7 docs
        assert len(result) == 7

        levels = [d.level for d in result]
        assert levels.count(DisclosureLevel.resource_index) == 1
        assert levels.count(DisclosureLevel.section_summary) == 3
        assert levels.count(DisclosureLevel.detailed_passage) == 3

    @pytest.mark.asyncio
    @patch("pointy_rag.disclosure.run_disclosure_agent", new_callable=AsyncMock)
    @patch("pointy_rag.disclosure.insert_disclosure_doc")
    async def test_level3_is_structural_no_agent(
        self, mock_insert, mock_agent, mock_conn, sample_markdown
    ):
        mock_agent.return_value = "Summary"

        result = await generate_disclosure_hierarchy(
            "doc1", sample_markdown, "Test Doc", mock_conn
        )

        l3_docs = [d for d in result if d.level == DisclosureLevel.detailed_passage]
        assert len(l3_docs) == 3
        assert l3_docs[0].title == "Introduction"
        assert "introduction content" in l3_docs[0].content

    @pytest.mark.asyncio
    @patch("pointy_rag.disclosure.run_disclosure_agent", new_callable=AsyncMock)
    @patch("pointy_rag.disclosure.insert_disclosure_doc")
    async def test_parent_ids_set_correctly(
        self, mock_insert, mock_agent, mock_conn, sample_markdown
    ):
        mock_agent.return_value = "Summary"

        result = await generate_disclosure_hierarchy(
            "doc1", sample_markdown, "Test Doc", mock_conn
        )

        l1 = [d for d in result if d.level == DisclosureLevel.resource_index][0]
        l2_docs = [d for d in result if d.level == DisclosureLevel.section_summary]
        l3_docs = [d for d in result if d.level == DisclosureLevel.detailed_passage]

        # L2 docs should point to L1
        for l2 in l2_docs:
            assert l2.parent_id == l1.id

        # L3 docs should point to corresponding L2
        for l3, l2 in zip(l3_docs, l2_docs, strict=True):
            assert l3.parent_id == l2.id

    @pytest.mark.asyncio
    @patch("pointy_rag.disclosure.run_disclosure_agent", new_callable=AsyncMock)
    @patch("pointy_rag.disclosure.insert_disclosure_doc")
    async def test_agent_called_for_l2_and_l1(
        self, mock_insert, mock_agent, mock_conn, sample_markdown
    ):
        mock_agent.return_value = "Summary"

        await generate_disclosure_hierarchy(
            "doc1", sample_markdown, "Test Doc", mock_conn
        )

        # 3 L2 calls + 1 L1 call = 4 agent calls
        assert mock_agent.call_count == 4

        # Verify L2 calls used level=2
        l2_calls = [c for c in mock_agent.call_args_list if c.kwargs.get("level") == 2]
        assert len(l2_calls) == 3

        # Verify L1 call used level=1
        l1_calls = [c for c in mock_agent.call_args_list if c.kwargs.get("level") == 1]
        assert len(l1_calls) == 1

    @pytest.mark.asyncio
    @patch("pointy_rag.disclosure.run_disclosure_agent", new_callable=AsyncMock)
    @patch("pointy_rag.disclosure.insert_disclosure_doc")
    async def test_persists_to_database(
        self, mock_insert, mock_agent, mock_conn, sample_markdown
    ):
        mock_agent.return_value = "Summary"

        await generate_disclosure_hierarchy(
            "doc1", sample_markdown, "Test Doc", mock_conn
        )

        # 7 docs inserted: 1 L1 + 3 L2 + 3 L3
        assert mock_insert.call_count == 7
        mock_conn.commit.assert_called_once()

    @pytest.mark.asyncio
    @patch("pointy_rag.disclosure.run_disclosure_agent", new_callable=AsyncMock)
    @patch("pointy_rag.disclosure.insert_disclosure_doc")
    async def test_single_section_document(
        self, mock_insert, mock_agent, mock_conn
    ):
        markdown = "## Only Section\nJust one section here."
        mock_agent.return_value = "Summary"

        result = await generate_disclosure_hierarchy(
            "doc1", markdown, "One Section Doc", mock_conn
        )

        assert len(result) == 3  # 1 L1 + 1 L2 + 1 L3

    @pytest.mark.asyncio
    async def test_no_sections_returns_empty(self, mock_conn):
        """Markdown with only headings and no body content."""
        markdown = "## Heading One\n## Heading Two\n"
        result = await generate_disclosure_hierarchy(
            "doc1", markdown, "Empty Sections", mock_conn
        )
        assert result == []

    @pytest.mark.asyncio
    @patch("pointy_rag.disclosure.run_disclosure_agent", new_callable=AsyncMock)
    @patch("pointy_rag.disclosure.insert_disclosure_doc")
    async def test_heading_only_hash_gets_default_title(
        self, mock_insert, mock_agent, mock_conn
    ):
        markdown = "# \nSome body text here."
        mock_agent.return_value = "Summary"

        result = await generate_disclosure_hierarchy(
            "doc1", markdown, "Test", mock_conn
        )
        l3 = [
            d for d in result
            if d.level == DisclosureLevel.detailed_passage
        ]
        # Should get a fallback title, not an empty string.
        assert all(len(d.title) > 0 for d in l3)

    @pytest.mark.asyncio
    @patch("pointy_rag.disclosure.run_disclosure_agent", new_callable=AsyncMock)
    @patch("pointy_rag.disclosure.insert_disclosure_doc")
    async def test_no_headings_uses_full_text(
        self, mock_insert, mock_agent, mock_conn
    ):
        markdown = "Plain text with no headings at all."
        mock_agent.return_value = "Summary"

        result = await generate_disclosure_hierarchy(
            "doc1", markdown, "No Headings", mock_conn
        )
        l3 = [
            d for d in result
            if d.level == DisclosureLevel.detailed_passage
        ]
        assert len(l3) == 1
        assert "Plain text" in l3[0].content


class TestRegenerateLibraryCatalog:
    @pytest.mark.asyncio
    @patch("pointy_rag.disclosure.run_disclosure_agent", new_callable=AsyncMock)
    @patch("pointy_rag.disclosure.insert_disclosure_doc")
    @patch("pointy_rag.disclosure.delete_disclosure_docs_by_level")
    @patch("pointy_rag.disclosure.update_disclosure_doc_parent")
    async def test_no_documents_returns_none(
        self, mock_update, mock_delete, mock_insert, mock_agent, mock_conn
    ):
        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = cursor

        result = await regenerate_library_catalog(mock_conn)
        assert result is None
        mock_agent.assert_not_called()

    @pytest.mark.asyncio
    @patch("pointy_rag.disclosure.run_disclosure_agent", new_callable=AsyncMock)
    @patch("pointy_rag.disclosure.insert_disclosure_doc")
    @patch("pointy_rag.disclosure.delete_disclosure_docs_by_level")
    @patch("pointy_rag.disclosure.update_disclosure_doc_parent")
    async def test_generates_catalog_from_l1_docs(
        self, mock_update, mock_delete, mock_insert, mock_agent, mock_conn
    ):
        cursor = MagicMock()
        cursor.execute.return_value = cursor
        cursor.fetchall.return_value = [
            {
                "id": "l1-1",
                "document_id": "doc1",
                "parent_id": None,
                "level": 1,
                "title": "Doc One",
                "content": "Overview of doc one",
                "ordering": 0,
            },
            {
                "id": "l1-2",
                "document_id": "doc2",
                "parent_id": None,
                "level": 1,
                "title": "Doc Two",
                "content": "Overview of doc two",
                "ordering": 0,
            },
        ]
        mock_conn.cursor.return_value = cursor
        mock_agent.return_value = "Library catalog entry"

        result = await regenerate_library_catalog(mock_conn)

        assert result is not None
        assert result.level == DisclosureLevel.library_catalog
        assert result.title == "Library Catalog"
        mock_agent.assert_called_once()
        mock_delete.assert_called_once()
        # Both L1 docs re-parented
        assert mock_update.call_count == 2
        mock_conn.commit.assert_called_once()

        # Verify L1 parent_ids cleared before L0 delete (FK safety).
        # The conn.execute call clears parents before delete.
        execute_calls = mock_conn.execute.call_args_list
        clear_call = [
            c for c in execute_calls
            if "SET parent_id = NULL" in str(c)
        ]
        assert len(clear_call) >= 1
