"""Tests for the markdown-aware chunker."""


from pointy_rag.chunker import TextChunk, chunk_markdown, count_tokens

# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------


def test_count_tokens():
    """Basic heuristic: len(text) // 4."""
    assert count_tokens("") == 0
    assert count_tokens("abcd") == 1  # 4 chars → 1 token
    assert count_tokens("a" * 400) == 100


# ---------------------------------------------------------------------------
# chunk_markdown — edge cases
# ---------------------------------------------------------------------------


def test_empty_text():
    assert chunk_markdown("") == []


def test_whitespace_only():
    assert chunk_markdown("   \n\n  ") == []


# ---------------------------------------------------------------------------
# Basic heading behaviour
# ---------------------------------------------------------------------------


def test_single_small_section():
    text = "## Introduction\n\nThis is a short intro."
    chunks = chunk_markdown(text)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.heading == "## Introduction"
    assert "short intro" in chunk.content
    assert chunk.chunk_index == 0


def test_multiple_headings():
    text = (
        "## Section One\n\nContent of section one.\n\n"
        "## Section Two\n\nContent of section two."
    )
    chunks = chunk_markdown(text)
    assert len(chunks) == 2
    assert chunks[0].heading == "## Section One"
    assert chunks[1].heading == "## Section Two"


def test_nested_headings():
    """Both ## and ### headings are recognised."""
    text = "## Chapter\n\nIntro text.\n\n### Sub-section\n\nSub content."
    chunks = chunk_markdown(text)
    assert len(chunks) == 2
    headings = {c.heading for c in chunks}
    assert "## Chapter" in headings
    assert "### Sub-section" in headings


# ---------------------------------------------------------------------------
# Splitting oversized sections
# ---------------------------------------------------------------------------


def test_large_section_splits():
    """A section larger than target_size must produce multiple chunks."""
    # Each line is 40 chars → 10 tokens. 200 lines = 2000 tokens >> target_size=500
    lines = [f"Line {i:03d}: " + "x" * 32 for i in range(200)]
    body = "\n".join(lines)
    text = f"## Big Section\n\n{body}"

    chunks = chunk_markdown(text, target_size=500, overlap=50)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.heading == "## Big Section"


def test_overlap_content():
    """Consecutive chunks within a large section share overlap lines."""
    # Build a body where each line has exactly 10 tokens (40 chars)
    line = "word " * 8  # 40 chars ≈ 10 tokens
    lines = [line.strip() for _ in range(60)]  # 60 × 10 = 600 tokens
    body = "\n".join(lines)
    text = f"## Section\n\n{body}"

    chunks = chunk_markdown(text, target_size=200, overlap=50)
    assert len(chunks) >= 2

    # The last few lines of chunk N should appear at the start of chunk N+1
    for i in range(len(chunks) - 1):
        tail_lines = chunks[i].content.split("\n")[-5:]
        head_lines = chunks[i + 1].content.split("\n")[:5]
        # At least one overlap line must be shared
        assert any(line in head_lines for line in tail_lines), (
            f"No overlap between chunk {i} and chunk {i + 1}"
        )


# ---------------------------------------------------------------------------
# No headings
# ---------------------------------------------------------------------------


def test_no_headings():
    """Text without any heading should chunk with heading=None."""
    text = "Just a plain paragraph.\n\nAnother paragraph here."
    chunks = chunk_markdown(text)
    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.heading is None


def test_no_headings_large():
    """Plain text larger than target_size splits with heading=None."""
    line = "word " * 8
    body = "\n".join([line.strip()] * 80)  # 800 tokens
    chunks = chunk_markdown(body, target_size=200, overlap=50)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.heading is None


# ---------------------------------------------------------------------------
# Sequential chunk indices
# ---------------------------------------------------------------------------


def test_chunk_index_sequential():
    """chunk_index must be 0, 1, 2, … across all chunks."""
    line = "word " * 8
    lines = [line.strip()] * 100
    sections = [
        "## Alpha\n\n" + "\n".join(lines[:30]),
        "## Beta\n\n" + "\n".join(lines[:50]),
        "## Gamma\n\nShort.",
    ]
    text = "\n\n".join(sections)
    chunks = chunk_markdown(text, target_size=200, overlap=50)
    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(len(chunks)))


# ---------------------------------------------------------------------------
# Heading-only document
# ---------------------------------------------------------------------------


def test_heading_only_no_body():
    """A document with only headings (no body text) returns no chunks."""
    text = "## Heading One\n\n## Heading Two\n\n## Heading Three"
    chunks = chunk_markdown(text)
    assert chunks == []


# ---------------------------------------------------------------------------
# Import smoke-test
# ---------------------------------------------------------------------------


def test_public_api_importable():
    """Verify the public API surface is importable without errors."""
    # If the imports at the top of this file succeeded, we're good.
    assert callable(chunk_markdown)
    assert callable(count_tokens)
    assert TextChunk  # class exists
