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


def _heading_level(heading: str) -> int:
    """Return the heading level (number of leading '#' chars)."""
    match = re.match(r"^(#{1,6})", heading)
    return len(match.group(1)) if match else 0


def _context_heading(heading_stack: list[tuple[int, str]]) -> str | None:
    """Build a contextual heading from the heading hierarchy.

    Returns something like "## Cascade > ### Characteristics".
    """
    if not heading_stack:
        return None
    return " > ".join(h for _, h in heading_stack)


def _force_split_text(text: str, max_tokens: int) -> list[str]:
    """Split text that exceeds max_tokens at sentence, then word, then char boundaries."""
    if count_tokens(text) <= max_tokens:
        return [text]

    # Try splitting at sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) > 1:
        chunks: list[str] = []
        current: list[str] = []
        current_tokens = 0
        for sentence in sentences:
            stokens = count_tokens(sentence)
            if current_tokens + stokens > max_tokens and current:
                chunks.append(" ".join(current))
                current = []
                current_tokens = 0
            current.append(sentence)
            current_tokens += stokens
        if current:
            chunks.append(" ".join(current))
        # Recursively force-split any chunk still too large
        result: list[str] = []
        for chunk in chunks:
            if count_tokens(chunk) > max_tokens:
                result.extend(_force_split_text(chunk, max_tokens))
            else:
                result.append(chunk)
        return result

    # Fall back to word splitting
    words = text.split()
    if len(words) > 1:
        chunks = []
        current = []
        current_tokens = 0
        for word in words:
            wtokens = count_tokens(word)
            if current_tokens + wtokens > max_tokens and current:
                chunks.append(" ".join(current))
                current = []
                current_tokens = 0
            current.append(word)
            current_tokens += wtokens
        if current:
            chunks.append(" ".join(current))
        return chunks

    # Last resort: split at character boundaries
    max_chars = max_tokens * 4
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _split_by_paragraphs(
    body: str,
    heading: str | None,
    max_tokens: int,
    start_index: int,
) -> list[TextChunk]:
    """Split body text at paragraph boundaries (\\n\\n).

    If a single paragraph exceeds max_tokens, force-split it at sentences
    then words.
    """
    paragraphs = re.split(r"\n\n+", body)
    chunks: list[TextChunk] = []
    current_parts: list[str] = []
    current_tokens = 0
    chunk_index = start_index

    for para in paragraphs:
        para_tokens = count_tokens(para)

        # If a single paragraph is too large, force-split it
        if para_tokens > max_tokens:
            # Flush what we have first
            if current_parts:
                content = "\n\n".join(current_parts)
                chunks.append(
                    TextChunk(
                        content=content,
                        token_count=count_tokens(content),
                        chunk_index=chunk_index,
                        heading=heading,
                    )
                )
                chunk_index += 1
                current_parts = []
                current_tokens = 0

            # Force-split the oversized paragraph
            for piece in _force_split_text(para, max_tokens):
                chunks.append(
                    TextChunk(
                        content=piece,
                        token_count=count_tokens(piece),
                        chunk_index=chunk_index,
                        heading=heading,
                    )
                )
                chunk_index += 1
            continue

        if current_tokens + para_tokens > max_tokens and current_parts:
            # Emit current chunk
            content = "\n\n".join(current_parts)
            chunks.append(
                TextChunk(
                    content=content,
                    token_count=count_tokens(content),
                    chunk_index=chunk_index,
                    heading=heading,
                )
            )
            chunk_index += 1
            current_parts = []
            current_tokens = 0

        current_parts.append(para)
        current_tokens += para_tokens

    # Emit final chunk
    if current_parts:
        content = "\n\n".join(current_parts)
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
) -> list[TextChunk]:
    """Split markdown text into chunks respecting heading boundaries.

    Strategy:
    1. Split on markdown headings (##, ###, etc.) first
    2. Track heading hierarchy so sub-sections carry parent context
    3. If a section exceeds target_size, split at paragraph boundaries
    4. Force-split oversized paragraphs at sentences then words

    Args:
        text: Markdown text to chunk
        target_size: Target token count per chunk

    Returns:
        List of TextChunk instances with sequential chunk_index values
    """
    if not text.strip():
        return []

    sections = split_into_sections(text)
    chunks: list[TextChunk] = []
    chunk_index = 0

    # Track heading hierarchy: list of (level, heading_text)
    heading_stack: list[tuple[int, str]] = []

    for heading, body in sections:
        # Update heading stack based on current heading
        if heading is not None:
            level = _heading_level(heading)
            # Pop headings at same or deeper level
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading))

        # Skip sections with no body content
        if not body:
            continue

        # Build contextual heading from hierarchy
        ctx_heading = _context_heading(heading_stack)

        body_tokens = count_tokens(body)

        if body_tokens <= target_size:
            # Fits in a single chunk
            chunks.append(
                TextChunk(
                    content=body,
                    token_count=body_tokens,
                    chunk_index=chunk_index,
                    heading=ctx_heading,
                )
            )
            chunk_index += 1
        else:
            # Split at paragraph boundaries
            sub_chunks = _split_by_paragraphs(
                body, ctx_heading, target_size, chunk_index
            )
            chunks.extend(sub_chunks)
            chunk_index += len(sub_chunks)

    return chunks
