"""Document format detection and conversion to markdown."""

from __future__ import annotations

import logging
from pathlib import Path

from pointy_rag.models import DocumentFormat

logger = logging.getLogger(__name__)

# Maximum file size for conversion (50 MB)
MAX_FILE_SIZE = 50 * 1024 * 1024


def _validate_path(path: Path) -> Path:
    """Resolve and validate a document path for existence and size.

    Checks that the path points to an existing, non-empty file within
    the size limit. Does NOT enforce base-directory containment — callers
    exposing this to untrusted input must validate that separately.

    Raises:
        FileNotFoundError: If the path does not point to a regular file.
        ValueError: If the file is empty or exceeds MAX_FILE_SIZE.
    """
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(
            f"Document not found (expected a file): {path}"
        )
    size = resolved.stat().st_size
    if size == 0:
        raise ValueError(f"File is empty (0 bytes): {path.name}")
    if size > MAX_FILE_SIZE:
        raise ValueError(
            f"File too large ({size / 1024 / 1024:.1f} MB). "
            f"Maximum is {MAX_FILE_SIZE / 1024 / 1024:.0f} MB."
        )
    return resolved


def detect_format(path: str | Path) -> DocumentFormat:
    """Detect document format from file extension.

    Args:
        path: Path to the document file.

    Returns:
        DocumentFormat enum value.

    Raises:
        ValueError: If the file extension is not supported.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return DocumentFormat.pdf
    if suffix == ".epub":
        return DocumentFormat.epub
    raise ValueError(f"Unsupported document format: {suffix!r}")


def extract_text_fallback(path: str | Path, fmt: DocumentFormat) -> str:
    """Extract text without Claude agent — pure library extraction.

    Args:
        path: Path to the document file.
        fmt: The document format.

    Returns:
        Extracted plain text content.

    Raises:
        ValueError: If the file exceeds MAX_FILE_SIZE or the PDF is
            encrypted/password-protected.
    """
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_FILE_SIZE:
        raise ValueError(
            f"File too large ({size / 1024 / 1024:.1f} MB). "
            f"Maximum is {MAX_FILE_SIZE / 1024 / 1024:.0f} MB."
        )

    if fmt == DocumentFormat.pdf:
        import fitz  # pymupdf

        doc = fitz.open(str(path))
        if doc.is_encrypted:
            doc.close()
            raise ValueError(
                f"PDF is password-protected and cannot be extracted: {path.name}"
            )
        pages = [page.get_text() for page in doc]
        doc.close()
        return "\n".join(pages)

    if fmt == DocumentFormat.epub:
        import ebooklib
        from bs4 import BeautifulSoup
        from ebooklib import epub

        book = epub.read_epub(str(path))
        parts: list[str] = []
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                soup = BeautifulSoup(item.get_content(), "html.parser")
                parts.append(soup.get_text())
        return "\n".join(parts)

    raise ValueError(f"No fallback extractor for format: {fmt}")


async def convert_to_markdown(
    source_path: str | Path,
    output_dir: str | Path | None = None,
    use_agent: bool = True,
) -> tuple[str, Path | None]:
    """Convert a document to markdown.

    Args:
        source_path: Path to the source document.
        output_dir: Optional directory to write the markdown output file.
        use_agent: If True, attempt conversion via Claude agent first.

    Returns:
        Tuple of (markdown_text, output_path_or_None).

    Raises:
        FileNotFoundError: If the source file does not exist.
        ValueError: If the file is too large, unsupported, or encrypted.
    """
    source_path = _validate_path(Path(source_path))
    fmt = detect_format(source_path)
    markdown: str | None = None

    if use_agent:
        try:
            from pointy_rag.claude_agent import run_conversion_agent

            markdown = await run_conversion_agent(str(source_path))
        except Exception as exc:
            logger.warning("Agent conversion failed, falling back: %s", exc)
            markdown = None

    if markdown is None:
        markdown = extract_text_fallback(source_path, fmt)

    output_path: Path | None = None
    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{source_path.stem}.md"
        output_path.write_text(markdown, encoding="utf-8")

    return markdown, output_path
