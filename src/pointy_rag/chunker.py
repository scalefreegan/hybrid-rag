"""Markdown-aware text chunking for pointy_rag ingestion."""

import re
from dataclasses import dataclass


@dataclass
class TextChunk:
    """A chunk of text for embedding."""

    content: str
    token_count: int
    chunk_index: int
    heading: str | None  # The markdown heading this chunk falls under


def count_tokens(text: str) -> int:
    """Approximate token count (~4 chars per token heuristic)."""
    return len(text) // 4


def split_into_sections(text: str) -> list[tuple[str | None, str]]:
    """Split markdown text into (heading, body) sections.

    Returns a list of (heading_text, body_text) tuples. The first section
    may have heading=None if text starts before any heading.
    """
    # Match lines that start with 1-6 # characters followed by a space
    heading_pattern = re.compile(r"^(#{1,6}\s+.+)$", re.MULTILINE)

    sections = []
    current_heading: str | None = None
    last_end = 0

    for match in heading_pattern.finditer(text):
        # Save everything before this heading as the current section's body
        body = text[last_end : match.start()]
        body_stripped = body.strip()

        # Only emit a section if there's body content or we have a heading to flush
        if body_stripped or current_heading is not None:
            sections.append((current_heading, body_stripped))

        current_heading = match.group(1).strip()
        last_end = match.end()
        # Skip the newline immediately after the heading line
        if last_end < len(text) and text[last_end] == "\n":
            last_end += 1

    # Flush the final section
    remaining = text[last_end:].strip()
    if remaining or current_heading is not None:
        sections.append((current_heading, remaining))

    return sections


def _split_body_with_overlap(
    body: str,
    heading: str | None,
    target_size: int,
    overlap: int,
    start_index: int,
) -> list[TextChunk]:
    """Split a body text into overlapping line-based chunks."""
    chunks: list[TextChunk] = []
    lines = body.split("\n")
    current_lines: list[str] = []
    current_tokens = 0
    chunk_index = start_index

    for line in lines:
        line_tokens = count_tokens(line)

        if current_tokens + line_tokens > target_size and current_lines:
            # Emit current chunk
            content = "\n".join(current_lines)
            chunks.append(
                TextChunk(
                    content=content,
                    token_count=count_tokens(content),
                    chunk_index=chunk_index,
                    heading=heading,
                )
            )
            chunk_index += 1

            # Build overlap from tail of current chunk
            overlap_lines: list[str] = []
            overlap_tokens = 0
            for prev_line in reversed(current_lines):
                prev_tokens = count_tokens(prev_line)
                if overlap_tokens + prev_tokens <= overlap:
                    overlap_lines.insert(0, prev_line)
                    overlap_tokens += prev_tokens
                else:
                    break

            current_lines = overlap_lines
            current_tokens = overlap_tokens

        current_lines.append(line)
        current_tokens += line_tokens

    # Emit final chunk
    if current_lines:
        content = "\n".join(current_lines)
        chunks.append(
            TextChunk(
                content=content,
                token_count=count_tokens(content),
                chunk_index=chunk_index,
                heading=heading,
            )
        )

    return chunks


def chunk_markdown(
    text: str,
    target_size: int = 1500,
    overlap: int = 200,
) -> list[TextChunk]:
    """Split markdown text into chunks respecting heading boundaries.

    Strategy:
    1. Split on markdown headings (##, ###, etc.) first
    2. If a section exceeds target_size, further split with line-based overlap
    3. Each chunk carries its parent heading for context

    Args:
        text: Markdown text to chunk
        target_size: Target token count per chunk
        overlap: Token overlap between consecutive chunks within a section

    Returns:
        List of TextChunk instances with sequential chunk_index values
    """
    if not text.strip():
        return []

    sections = split_into_sections(text)
    chunks: list[TextChunk] = []
    chunk_index = 0

    for heading, body in sections:
        # Skip sections with no body content (e.g. heading-only lines)
        if not body:
            continue

        body_tokens = count_tokens(body)

        if body_tokens <= target_size:
            # Fits in a single chunk
            chunks.append(
                TextChunk(
                    content=body,
                    token_count=body_tokens,
                    chunk_index=chunk_index,
                    heading=heading,
                )
            )
            chunk_index += 1
        else:
            # Split with overlap
            sub_chunks = _split_body_with_overlap(
                body, heading, target_size, overlap, chunk_index
            )
            chunks.extend(sub_chunks)
            chunk_index += len(sub_chunks)

    return chunks
