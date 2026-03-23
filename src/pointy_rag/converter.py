"""Document format detection and conversion to markdown.

Multi-stage pipeline: library extraction → agent cleanup → agent restructure.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pointy_rag.models import DocumentFormat

logger = logging.getLogger(__name__)

# Maximum file size for conversion (50 MB)
MAX_FILE_SIZE = 50 * 1024 * 1024


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class RawSegment:
    """A segment of raw extracted text with source metadata."""

    text: str
    label: str  # e.g. "Page 1" or "Chapter 2: Hops"
    index: int


# ---------------------------------------------------------------------------
# Path validation & format detection
# ---------------------------------------------------------------------------


def _validate_path(path: Path) -> Path:
    """Resolve and validate a document path for existence and size."""
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Document not found (expected a file): {path}")
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
    """Detect document format from file extension."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return DocumentFormat.pdf
    if suffix == ".epub":
        return DocumentFormat.epub
    raise ValueError(f"Unsupported document format: {suffix!r}")


# ---------------------------------------------------------------------------
# Stage 0: Library extraction
# ---------------------------------------------------------------------------


def extract_segments(path: Path, fmt: DocumentFormat) -> list[RawSegment]:
    """Extract text as a list of segments from a document.

    PDF: one segment per page.
    EPUB: one segment per spine item (chapter).
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

        segments: list[RawSegment] = []
        with fitz.open(str(path)) as doc:
            if doc.is_encrypted:
                raise ValueError(
                    f"PDF is password-protected and cannot be extracted: {path.name}"
                )
            for i, page in enumerate(doc):
                text = page.get_text()
                if text.strip():
                    segments.append(RawSegment(text=text, label=f"Page {i + 1}", index=i))
        return segments

    if fmt == DocumentFormat.epub:
        import ebooklib
        from bs4 import BeautifulSoup
        from ebooklib import epub

        book = epub.read_epub(str(path))
        segments = []
        idx = 0
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                soup = BeautifulSoup(item.get_content(), "html.parser")
                text = soup.get_text()
                if text.strip():
                    title_tag = soup.find(["h1", "h2", "h3"])
                    label = title_tag.get_text().strip() if title_tag else f"Chapter {idx + 1}"
                    segments.append(RawSegment(text=text, label=label, index=idx))
                    idx += 1
        return segments

    raise ValueError(f"No extractor for format: {fmt}")


def extract_text_fallback(path: str | Path, fmt: DocumentFormat) -> str:
    """Extract text without Claude agent — pure library extraction."""
    segments = extract_segments(Path(path), fmt)
    return "\n".join(seg.text for seg in segments)


def group_segments(
    segments: list[RawSegment],
    max_chars: int = 20_000,
) -> list[RawSegment]:
    """Merge small consecutive segments up to max_chars."""
    if not segments:
        return []

    grouped: list[RawSegment] = []
    current_texts: list[str] = []
    current_labels: list[str] = []
    current_len = 0

    for seg in segments:
        seg_len = len(seg.text)

        if current_texts and current_len + seg_len > max_chars:
            # Flush current group
            label = (
                current_labels[0]
                if len(current_labels) == 1
                else f"{current_labels[0]} – {current_labels[-1]}"
            )
            grouped.append(
                RawSegment(
                    text="\n".join(current_texts),
                    label=label,
                    index=len(grouped),
                )
            )
            current_texts = []
            current_labels = []
            current_len = 0

        current_texts.append(seg.text)
        current_labels.append(seg.label)
        current_len += seg_len

    # Flush remaining
    if current_texts:
        label = (
            current_labels[0]
            if len(current_labels) == 1
            else f"{current_labels[0]} – {current_labels[-1]}"
        )
        grouped.append(
            RawSegment(
                text="\n".join(current_texts),
                label=label,
                index=len(grouped),
            )
        )

    return grouped


# ---------------------------------------------------------------------------
# Stage 1: Agent cleanup (per-segment)
# ---------------------------------------------------------------------------

_CLEANUP_SYSTEM_PROMPT = (
    "You are a document text cleanup specialist. You receive raw text extracted "
    "from a {fmt} file. Clean it up by:\n"
    "- Removing page numbers, headers, footers, and boilerplate\n"
    "- Collapsing excessive whitespace and blank lines\n"
    "- Fixing hyphenated line breaks (re-joining split words)\n"
    "- Removing formatting artifacts (orphaned bullet characters, stray symbols)\n"
    "- Preserving ALL meaningful content — do not summarize or omit anything\n\n"
    "Output ONLY the cleaned text with no commentary, preamble, or explanation."
)


async def run_cleanup_agent(text: str, fmt: DocumentFormat, timeout: int = 120) -> str:
    """Clean up a single raw text segment via agent."""
    from pointy_rag.claude_agent import run_agent

    prompt = f"Clean up the following raw extracted text:\n\n{text}"
    system_prompt = _CLEANUP_SYSTEM_PROMPT.format(fmt=fmt.value.upper())

    return await run_agent(
        prompt=prompt,
        system_prompt=system_prompt,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Stage 2: Agent restructure (full text or windowed)
# ---------------------------------------------------------------------------

_RESTRUCTURE_SYSTEM_PROMPT = (
    "You are a markdown structuring specialist. You receive cleaned text from a "
    "document titled '{title}'. Restructure it into well-formatted markdown by:\n"
    "- Identifying logical chapters, sections, and subsections\n"
    "- Adding appropriate markdown headings (# for chapters, ## for sections, "
    "### for subsections)\n"
    "- Preserving ALL content — do not summarize or omit anything\n"
    "- Using markdown formatting (lists, emphasis, code blocks) where appropriate\n\n"
    "Output ONLY the structured markdown with no commentary, preamble, or explanation."
)

_RESTRUCTURE_WINDOW_SIZE = 60_000
_RESTRUCTURE_OVERLAP = 2_000


def _split_text_at_paragraphs(text: str, target_size: int, overlap: int) -> list[str]:
    """Split text into windows at paragraph boundaries."""
    if len(text) <= target_size:
        return [text]

    paragraphs = text.split("\n\n")
    windows: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 2  # account for \n\n
        if current_len + para_len > target_size and current_parts:
            windows.append("\n\n".join(current_parts))
            # Build overlap from tail
            overlap_parts: list[str] = []
            overlap_len = 0
            for p in reversed(current_parts):
                if overlap_len + len(p) > overlap:
                    break
                overlap_parts.insert(0, p)
                overlap_len += len(p)
            current_parts = overlap_parts
            current_len = overlap_len

        current_parts.append(para)
        current_len += para_len

    if current_parts:
        windows.append("\n\n".join(current_parts))

    return windows


async def run_restructure_agent(
    text: str, title: str, timeout: int = 120
) -> str:
    """Restructure cleaned text into well-formatted markdown via agent."""
    from pointy_rag.claude_agent import run_agent

    system_prompt = _RESTRUCTURE_SYSTEM_PROMPT.format(title=title)

    if len(text) <= _RESTRUCTURE_WINDOW_SIZE:
        prompt = f"Restructure the following text into markdown:\n\n{text}"
        return await run_agent(
            prompt=prompt,
            system_prompt=system_prompt,
            timeout=timeout,
        )

    # Windowed restructuring for large texts
    windows = _split_text_at_paragraphs(text, _RESTRUCTURE_WINDOW_SIZE // 2, _RESTRUCTURE_OVERLAP)
    results: list[str] = []
    for i, window in enumerate(windows):
        prompt = (
            f"Restructure part {i + 1} of {len(windows)} of this document into markdown. "
            f"Continue from where the previous part left off.\n\n{window}"
        )
        result = await run_agent(
            prompt=prompt,
            system_prompt=system_prompt,
            timeout=timeout,
        )
        results.append(result)

    return "\n\n".join(results)


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


async def run_conversion_pipeline(
    source_path: Path,
    fmt: DocumentFormat,
    timeout: int = 120,
    max_segment_chars: int = 20_000,
    concurrency: int = 3,
    on_progress: Callable[[str], None] | None = None,
) -> str:
    """Run the full multi-stage conversion pipeline.

    Stage 0: Library extraction → segments
    Stage 1: Agent cleanup per segment (parallel, bounded concurrency)
    Stage 2: Agent restructure on reassembled text

    No fallbacks — any stage failure raises.
    """

    def _progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    # Stage 0: Extract
    _progress("Extracting text...")
    segments = extract_segments(source_path, fmt)
    if not segments:
        raise ValueError(f"No text extracted from {source_path.name}")
    logger.info("Extracted %d segments from %s", len(segments), source_path.name)

    # Group small segments
    grouped = group_segments(segments, max_chars=max_segment_chars)
    logger.info("Grouped into %d segment(s) for cleanup", len(grouped))

    # Stage 1: Cleanup (parallel)
    sem = asyncio.Semaphore(concurrency)
    completed = 0

    async def _cleanup_one(seg: RawSegment) -> str:
        nonlocal completed
        async with sem:
            result = await run_cleanup_agent(seg.text, fmt, timeout=timeout)
            completed += 1
            _progress(f"Cleaning segment {completed}/{len(grouped)}...")
            return result

    _progress(f"Cleaning {len(grouped)} segment(s)...")
    cleaned_parts = await asyncio.gather(*[_cleanup_one(seg) for seg in grouped])
    cleaned_text = "\n\n".join(cleaned_parts)
    logger.info("Cleanup complete, %d chars", len(cleaned_text))

    # Stage 2: Restructure
    title = source_path.stem.replace("_", " ")
    _progress("Restructuring markdown...")
    markdown = await run_restructure_agent(cleaned_text, title, timeout=timeout)
    logger.info("Restructure complete, %d chars", len(markdown))

    return markdown


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def convert_to_markdown(
    source_path: str | Path,
    output_dir: str | Path | None = None,
    use_agent: bool = True,
    timeout: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[str, Path | None]:
    """Convert a document to markdown.

    When use_agent=True, runs the multi-stage pipeline (extract → cleanup → restructure).
    When use_agent=False, uses raw library extraction only.
    """
    source_path = _validate_path(Path(source_path))
    fmt = detect_format(source_path)

    if use_agent:
        from pointy_rag.config import get_settings

        settings = get_settings()
        markdown = await run_conversion_pipeline(
            source_path,
            fmt,
            timeout=timeout or settings.agent_timeout,
            max_segment_chars=settings.agent_segment_max_chars,
            concurrency=settings.agent_concurrency,
            on_progress=on_progress,
        )
    else:
        markdown = extract_text_fallback(source_path, fmt)

    output_path: Path | None = None
    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{source_path.stem}.md"
        output_path.write_text(markdown, encoding="utf-8")

    return markdown, output_path
