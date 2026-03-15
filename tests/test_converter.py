"""Tests for pointy_rag.converter module."""

from __future__ import annotations

import asyncio
import io
import struct
import zlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pointy_rag.converter import (
    MAX_FILE_SIZE,
    convert_to_markdown,
    detect_format,
    extract_text_fallback,
)
from pointy_rag.models import DocumentFormat


# ---------------------------------------------------------------------------
# detect_format
# ---------------------------------------------------------------------------


def test_detect_format_pdf():
    assert detect_format("document.pdf") == DocumentFormat.pdf


def test_detect_format_epub():
    assert detect_format("book.epub") == DocumentFormat.epub


def test_detect_format_unsupported():
    with pytest.raises(ValueError, match="Unsupported document format"):
        detect_format("notes.txt")


def test_detect_format_case_insensitive(tmp_path):
    p = tmp_path / "FILE.PDF"
    assert detect_format(p) == DocumentFormat.pdf


# ---------------------------------------------------------------------------
# extract_text_fallback — PDF
# ---------------------------------------------------------------------------


def _make_minimal_pdf(tmp_path: Path) -> Path:
    """Create a minimal valid PDF containing 'Hello PDF' via pymupdf."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello PDF")
    pdf_path = tmp_path / "test.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def test_extract_text_fallback_pdf(tmp_path):
    pdf_path = _make_minimal_pdf(tmp_path)
    text = extract_text_fallback(pdf_path, DocumentFormat.pdf)
    assert "Hello PDF" in text


# ---------------------------------------------------------------------------
# extract_text_fallback — EPUB
# ---------------------------------------------------------------------------


def _make_minimal_epub(tmp_path: Path) -> Path:
    """Create a minimal valid EPUB containing 'Hello EPUB'."""
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("test-id")
    book.set_title("Test Book")
    book.set_language("en")

    chapter = epub.EpubHtml(title="Chapter 1", file_name="chap1.xhtml", lang="en")
    chapter.content = b"<html><body><p>Hello EPUB</p></body></html>"
    book.add_item(chapter)
    book.spine = ["nav", chapter]

    nav = epub.EpubNav()
    book.add_item(nav)
    book.add_item(epub.EpubNcx())

    epub_path = tmp_path / "test.epub"
    epub.write_epub(str(epub_path), book, {})
    return epub_path


def test_extract_text_fallback_epub(tmp_path):
    epub_path = _make_minimal_epub(tmp_path)
    text = extract_text_fallback(epub_path, DocumentFormat.epub)
    assert "Hello EPUB" in text


# ---------------------------------------------------------------------------
# convert_to_markdown — agent path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_convert_to_markdown_agent_success(tmp_path):
    """Agent path returns result and no output path when output_dir omitted."""
    pdf_path = _make_minimal_pdf(tmp_path)
    agent_md = "# Agent Output\n\nContent here."

    mock_module = MagicMock()
    mock_module.run_conversion_agent = AsyncMock(return_value=agent_md)

    with patch.dict("sys.modules", {"pointy_rag.claude_agent": mock_module}):
        text, path = await convert_to_markdown(pdf_path, use_agent=True)

    assert text == agent_md
    assert path is None


@pytest.mark.asyncio
async def test_convert_to_markdown_agent_success_via_mock(tmp_path):
    """Verify run_conversion_agent is called when use_agent=True and succeeds."""
    pdf_path = _make_minimal_pdf(tmp_path)
    agent_md = "# Converted by Agent\n\nSome content."

    mock_agent_module = MagicMock()
    mock_agent_module.run_conversion_agent = AsyncMock(return_value=agent_md)

    with patch.dict("sys.modules", {"pointy_rag.claude_agent": mock_agent_module}):
        text, path = await convert_to_markdown(pdf_path, use_agent=True)

    assert text == agent_md
    assert path is None
    mock_agent_module.run_conversion_agent.assert_called_once_with(str(pdf_path.resolve()))


# ---------------------------------------------------------------------------
# convert_to_markdown — fallback on agent failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_convert_to_markdown_agent_fallback(tmp_path):
    """When agent raises, fallback extraction runs and returns text."""
    pdf_path = _make_minimal_pdf(tmp_path)

    failing_agent = MagicMock()
    failing_agent.run_conversion_agent = AsyncMock(side_effect=RuntimeError("boom"))

    with patch.dict("sys.modules", {"pointy_rag.claude_agent": failing_agent}):
        text, path = await convert_to_markdown(pdf_path, use_agent=True)

    assert "Hello PDF" in text
    assert path is None


# ---------------------------------------------------------------------------
# convert_to_markdown — output file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_convert_to_markdown_output_file(tmp_path):
    """Verify markdown file is written when output_dir is provided."""
    pdf_path = _make_minimal_pdf(tmp_path)
    out_dir = tmp_path / "output"

    text, path = await convert_to_markdown(
        pdf_path, output_dir=out_dir, use_agent=False
    )

    assert path is not None
    assert path.exists()
    assert path.suffix == ".md"
    assert path.stem == pdf_path.stem
    assert path.read_text(encoding="utf-8") == text


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_convert_nonexistent_file(tmp_path):
    """Raise FileNotFoundError for missing files."""
    fake = tmp_path / "no_such_file.pdf"
    with pytest.raises(FileNotFoundError, match="Document not found"):
        await convert_to_markdown(fake, use_agent=False)


def test_extract_password_protected_pdf(tmp_path):
    """Raise ValueError for encrypted PDFs."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "secret")
    pdf_path = tmp_path / "locked.pdf"
    perm = fitz.PDF_PERM_ACCESSIBILITY
    encrypt_meth = fitz.PDF_ENCRYPT_AES_256
    doc.save(str(pdf_path), encryption=encrypt_meth, user_pw="pass", permissions=perm)
    doc.close()

    with pytest.raises(ValueError, match="password-protected"):
        extract_text_fallback(pdf_path, DocumentFormat.pdf)
