"""Document format detection and conversion to markdown.

Multi-stage pipeline: library extraction → agent cleanup → agent restructure.
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pointy_rag.models import DocumentFormat

logger = logging.getLogger(__name__)

# Maximum file size for conversion (200 MB)
MAX_FILE_SIZE = 200 * 1024 * 1024


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
        # Split oversized segments at paragraph boundaries first
        if len(seg.text) > max_chars:
            paragraphs = seg.text.split("\n\n")
            sub_texts: list[str] = []
            sub_len = 0
            part_num = 0
            for para in paragraphs:
                if sub_len + len(para) > max_chars and sub_texts:
                    part_num += 1
                    grouped.append(
                        RawSegment(
                            text="\n\n".join(sub_texts),
                            label=f"{seg.label} (part {part_num})",
                            index=len(grouped),
                        )
                    )
                    sub_texts = []
                    sub_len = 0
                sub_texts.append(para)
                sub_len += len(para)
            if sub_texts:
                part_num += 1
                grouped.append(
                    RawSegment(
                        text="\n\n".join(sub_texts),
                        label=f"{seg.label} (part {part_num})" if part_num > 1 else seg.label,
                        index=len(grouped),
                    )
                )
            # Reset accumulator since we flushed directly
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
                current_texts = []
                current_labels = []
                current_len = 0
            continue

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
# Slice I/O helpers
# ---------------------------------------------------------------------------


def _write_slices(work_dir: Path, segments: list[RawSegment]) -> list[Path]:
    """Write each segment to a numbered file in *work_dir* and return paths."""
    work_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for seg in segments:
        p = work_dir / f"slice_{seg.index:04d}.md"
        p.write_text(seg.text, encoding="utf-8")
        paths.append(p)
    return paths


_DATA_TABLE_FIELDS = re.compile(
    r"(?=(?:Also Known As|Characteristics|Purpose|Alpha Acid Composition|"
    r"Beta Acid Composition|Cohumulone Composition|Country|Cone Size|"
    r"Cone Density|Seasonal Maturity|Yield Amount|Growth Rate|"
    r"Resistant to|Susceptible to|Storability|Ease of Harvest|"
    r"Total Oil|Myrcene Oil|Humulene Oil|Caryophyllene Oil|"
    r"Farnesene Oil|Substitutes|Style Guide|References))"
)


def _normalize_slices(slice_paths: list[Path], max_line_len: int = 400) -> None:
    """Break long lines in slice files so the Edit tool can process them."""
    for path in slice_paths:
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")
        normalized: list[str] = []
        for line in lines:
            if len(line) <= max_line_len:
                normalized.append(line)
                continue
            if "Also Known As" in line or "Alpha Acid Composition" in line:
                parts = _DATA_TABLE_FIELDS.split(line)
                parts = [p.strip() for p in parts if p.strip()]
                if len(parts) > 1:
                    normalized.extend(parts)
                    continue
            words = line.split(" ")
            if len(words) > 1:
                current: list[str] = []
                current_len = 0
                for word in words:
                    if current_len + len(word) > max_line_len and current:
                        normalized.append(" ".join(current))
                        current = []
                        current_len = 0
                    current.append(word)
                    current_len += len(word) + 1
                if current:
                    normalized.append(" ".join(current))
            else:
                for i in range(0, len(line), max_line_len):
                    normalized.append(line[i : i + max_line_len])
        path.write_text("\n".join(normalized), encoding="utf-8")


def _create_agent_workdir(
    work_dir: Path, slice_paths: list[Path], idx: int,
) -> Path:
    """Create an isolated directory with a single slice and adjacent context copies."""
    agent_dir = work_dir / f"agent_{idx:04d}"
    agent_dir.mkdir(parents=True, exist_ok=True)

    import shutil

    # Copy the target slice
    shutil.copy2(slice_paths[idx], agent_dir / slice_paths[idx].name)

    # Adjacent context
    if idx > 0:
        shutil.copy2(slice_paths[idx - 1], agent_dir / "context_prev.md")
    if idx < len(slice_paths) - 1:
        shutil.copy2(slice_paths[idx + 1], agent_dir / "context_next.md")

    return agent_dir


def _create_batch_workdir(
    work_dir: Path, slice_paths: list[Path], batch_indices: list[int],
) -> Path:
    """Create an isolated directory with multiple slices and boundary context."""
    batch_id = batch_indices[0]
    batch_dir = work_dir / f"batch_{batch_id:04d}"
    batch_dir.mkdir(parents=True, exist_ok=True)

    import shutil

    # Copy all slices in the batch
    for idx in batch_indices:
        shutil.copy2(slice_paths[idx], batch_dir / slice_paths[idx].name)

    # Boundary context: slice before first and after last
    first, last = batch_indices[0], batch_indices[-1]
    if first > 0:
        shutil.copy2(slice_paths[first - 1], batch_dir / "context_prev.md")
    if last < len(slice_paths) - 1:
        shutil.copy2(slice_paths[last + 1], batch_dir / "context_next.md")

    return batch_dir


# ---------------------------------------------------------------------------
# Stage 1: Agent cleanup (per-segment) — legacy single-segment interface
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
    """Clean up a single raw text segment via agent.

    On timeout, retries once with 2x the timeout.
    """
    prompt = f"Clean up the following raw extracted text:\n\n{text}"
    system_prompt = _CLEANUP_SYSTEM_PROMPT.format(fmt=fmt.value.upper())
    return await _run_agent_with_retry(prompt, system_prompt, timeout, "Cleanup")


# ---------------------------------------------------------------------------
# Stage 1b: Batch cleanup-and-structure agent
# ---------------------------------------------------------------------------

_CLEANUP_AND_STRUCTURE_BATCH_PROMPT = (
    "You are a document conversion specialist. You will process a batch of text "
    "slices extracted from a {fmt} document titled '{title}'.\n\n"
    "Your working directory contains:\n"
    "- Slice files named slice_NNNN.md (the files you must process)\n"
    "- context_prev.md (preceding context, read-only — do NOT modify)\n"
    "- context_next.md (following context, read-only — do NOT modify)\n\n"
    "For EACH slice file, in order:\n"
    "1. Read the file\n"
    "2. Clean it: remove page numbers, headers/footers, boilerplate, fix "
    "hyphenated line breaks, collapse excessive whitespace\n"
    "3. Structure it as well-formatted markdown with proper heading hierarchy:\n"
    "   - # for top-level chapters/parts\n"
    "   - ## for sections\n"
    "   - ### for subsections\n"
    "   - Use context files to understand where this slice fits in the document\n"
    "4. Write the cleaned, structured result back to the SAME file\n\n"
    "Use python3 scripts via the Bash tool for any text processing that benefits "
    "from regex or programmatic manipulation.\n\n"
    "Preserve ALL meaningful content — do not summarize or omit anything.\n"
    "Process each file sequentially and write results back in place."
)


async def run_cleanup_and_structure_batch(
    batch_dir: Path,
    fmt: DocumentFormat,
    title: str,
    timeout: int = 300,
    model: str | None = None,
    max_turns: int = 50,
) -> None:
    """Run cleanup and structuring on a batch of slices in *batch_dir*."""
    system_prompt = _CLEANUP_AND_STRUCTURE_BATCH_PROMPT.format(
        fmt=fmt.value.upper(), title=title,
    )
    slice_files = sorted(batch_dir.glob("slice_*.md"))
    file_list = ", ".join(f.name for f in slice_files)
    prompt = (
        f"Process these slice files in order: {file_list}\n"
        f"Working directory: {batch_dir}"
    )
    await _run_agent_with_retry(
        prompt,
        system_prompt,
        timeout,
        label=f"Batch {batch_dir.name}",
        model=model,
        max_turns=max_turns,
        allowed_tools=["Read", "Bash", "Write", "Glob", "Edit"],
        cwd=str(batch_dir),
    )


# ---------------------------------------------------------------------------
# Final cleanup pass
# ---------------------------------------------------------------------------

_FINAL_CLEANUP_PROMPT = (
    "You are a markdown editor. You have the final assembled markdown document "
    "for '{title}'. Do a single editorial pass:\n"
    "- Fix any duplicate headings introduced at batch boundaries\n"
    "- Ensure consistent heading hierarchy (# > ## > ### > ####)\n"
    "- Remove any residual conversion artifacts or repeated lines at boundaries\n"
    "- Ensure smooth transitions between sections\n"
    "- Do NOT remove or summarize any content\n\n"
    "Read the file, edit it in place, and write the result back."
)


async def run_final_cleanup(
    output_path: Path,
    title: str,
    timeout: int = 300,
    model: str | None = None,
    max_turns: int = 30,
) -> str:
    """Run a single agent pass on the final assembled markdown."""
    system_prompt = _FINAL_CLEANUP_PROMPT.format(title=title)
    prompt = (
        f"Edit the assembled markdown file at: {output_path}\n"
        f"Working directory: {output_path.parent}"
    )
    await _run_agent_with_retry(
        prompt,
        system_prompt,
        timeout,
        label="Final cleanup",
        model=model,
        max_turns=max_turns,
        allowed_tools=["Read", "Bash", "Write", "Glob", "Edit"],
        cwd=str(output_path.parent),
    )
    return output_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Stage 2: Agent restructure (full text or windowed) — legacy interface
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


async def _run_agent_with_retry(
    prompt: str,
    system_prompt: str,
    timeout: int,
    label: str = "agent",
    model: str = "haiku",
    max_turns: int | None = None,
    allowed_tools: list[str] | None = None,
    cwd: str | None = None,
) -> str:
    """Run an agent call with one retry at 2x timeout on TimeoutError."""
    from pointy_rag.claude_agent import run_agent

    kwargs: dict = dict(prompt=prompt, system_prompt=system_prompt, timeout=timeout)
    if model is not None:
        kwargs["model"] = model
    if max_turns is not None:
        kwargs["max_turns"] = max_turns
    if allowed_tools is not None:
        kwargs["allowed_tools"] = allowed_tools
    if cwd is not None:
        kwargs["cwd"] = cwd

    try:
        return await run_agent(**kwargs)
    except TimeoutError:
        retry_timeout = timeout * 2
        logger.warning("%s timed out after %ds, retrying with %ds", label, timeout, retry_timeout)
        kwargs["timeout"] = retry_timeout
        return await run_agent(**kwargs)


async def run_restructure_agent(
    text: str, title: str, timeout: int = 120
) -> str:
    """Restructure cleaned text into well-formatted markdown via agent.

    On timeout, retries once with 2x the timeout.
    """
    system_prompt = _RESTRUCTURE_SYSTEM_PROMPT.format(title=title)

    if len(text) <= _RESTRUCTURE_WINDOW_SIZE:
        prompt = f"Restructure the following text into markdown:\n\n{text}"
        return await _run_agent_with_retry(prompt, system_prompt, timeout, "Restructure")

    # Windowed restructuring for large texts
    windows = _split_text_at_paragraphs(text, _RESTRUCTURE_WINDOW_SIZE // 2, _RESTRUCTURE_OVERLAP)
    logger.info("Restructuring in %d windows (%d chars total)", len(windows), len(text))
    results: list[str] = []
    for i, window in enumerate(windows):
        logger.info("Restructure window %d/%d (%d chars)...", i + 1, len(windows), len(window))
        win_start = time.monotonic()
        prompt = (
            f"Restructure part {i + 1} of {len(windows)} of this document into markdown. "
            f"Continue from where the previous part left off.\n\n{window}"
        )
        result = await _run_agent_with_retry(
            prompt, system_prompt, timeout, f"Restructure window {i + 1}/{len(windows)}",
        )
        logger.info("Restructure window %d/%d done (%.1fs)", i + 1, len(windows), time.monotonic() - win_start)
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
    concurrency: int = 2,
    on_progress: Callable[[str], None] | None = None,
    batch_size: int = 10,
    model: str | None = None,
) -> str:
    """Run the full multi-stage conversion pipeline.

    Stage 0: Library extraction → segments → slices on disk
    Stage 1: Batch cleanup-and-structure (batch_size slices per agent session)
    Stage 2: Concatenation + final cleanup pass

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

    title = source_path.stem.replace("_", " ")

    # Write slices to disk
    work_dir = Path(tempfile.mkdtemp(prefix="converter_"))
    slice_paths = _write_slices(work_dir, grouped)
    logger.info("Wrote %d slices to %s", len(slice_paths), work_dir)

    # Normalize long lines so Edit tool can handle them
    _normalize_slices(slice_paths)

    # Stage 1: Batch cleanup-and-structure (parallel batches, bounded concurrency)
    sem = asyncio.Semaphore(concurrency)
    batches: list[list[int]] = []
    for i in range(0, len(slice_paths), batch_size):
        batches.append(list(range(i, min(i + batch_size, len(slice_paths)))))

    completed_batches = 0
    stage1_start = time.monotonic()

    async def _process_batch(batch_indices: list[int]) -> None:
        nonlocal completed_batches
        async with sem:
            batch_dir = _create_batch_workdir(work_dir, slice_paths, batch_indices)
            batch_start = time.monotonic()
            logger.info(
                "Batch starting: slices %d–%d (%d slices)",
                batch_indices[0], batch_indices[-1], len(batch_indices),
            )
            await run_cleanup_and_structure_batch(
                batch_dir, fmt, title,
                timeout=timeout,
                model=model,
                max_turns=50,
            )
            elapsed = time.monotonic() - batch_start
            completed_batches += 1
            logger.info(
                "Batch finished: slices %d–%d (%.1fs) [%d/%d batches]",
                batch_indices[0], batch_indices[-1], elapsed,
                completed_batches, len(batches),
            )
            _progress(f"Processed batch {completed_batches}/{len(batches)}...")

            # Copy processed slices back to main work_dir
            import shutil
            for idx in batch_indices:
                src = batch_dir / slice_paths[idx].name
                if src.exists():
                    shutil.copy2(src, slice_paths[idx])

    _progress(f"Processing {len(batches)} batch(es) of up to {batch_size} slices...")
    await asyncio.gather(*[_process_batch(b) for b in batches])

    stage1_elapsed = time.monotonic() - stage1_start
    logger.info(
        "Stage 1 (batch cleanup+structure) complete: %d batches in %.1fs",
        len(batches), stage1_elapsed,
    )

    # Concatenate processed slices
    parts = []
    for sp in slice_paths:
        parts.append(sp.read_text(encoding="utf-8"))
    markdown = "\n\n".join(parts)

    # Stage 2: Final cleanup pass
    _progress("Running final cleanup pass...")
    stage2_start = time.monotonic()
    final_path = work_dir / "final.md"
    final_path.write_text(markdown, encoding="utf-8")
    markdown = await run_final_cleanup(
        final_path, title, timeout=timeout, model=model,
    )
    stage2_elapsed = time.monotonic() - stage2_start
    logger.info(
        "Stage 2 (final cleanup) complete: %.1fs, %d chars",
        stage2_elapsed, len(markdown),
    )

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
