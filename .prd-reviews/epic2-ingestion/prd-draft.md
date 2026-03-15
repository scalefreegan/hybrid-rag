# PRD: Epic 2 — Ingestion Components

## Problem Statement

The hybrid RAG system has its data layer (Epic 1: models, DB, config, CLI skeleton)
but no way to actually get documents into it. We need four independent processing
components that transform raw documents (PDF/EPUB) into embedded, searchable chunks
stored in pgvector. These components are the building blocks for the ingestion
pipeline (Epic 3/4) and the progressive disclosure intelligence layer.

**Who:** Developers building and operating the pointy-rag system.
**Why now:** Epic 1 is complete and passing. Epic 3 (intelligence layer) and
Epic 4 (pipeline orchestration) are both blocked on these components.

## Goals

1. **Agent wrapper:** Provide a reusable async interface to spawn headless Claude Code
   as a subprocess, with proper process group management, timeout handling, and
   structured JSON output extraction.

2. **Document converter:** Convert PDF and EPUB files to well-structured markdown,
   with agent-powered conversion as primary path and deterministic fallback
   extractors (pymupdf/ebooklib) when Claude CLI is unavailable.

3. **Markdown chunker:** Split markdown text into semantically meaningful chunks
   respecting heading boundaries, with configurable target size and overlap,
   using a fast heuristic token counter.

4. **Embedding client:** Generate 1024-dimensional vector embeddings via Voyage AI
   with batched requests, exponential backoff retry, and a lazy singleton client.

5. **All components tested:** Each component has pytest unit tests using mocks
   (no live services required for testing).

## Non-Goals

- **Pipeline orchestration:** Wiring these components into an end-to-end ingestion
  flow is Epic 4, not this epic.
- **Progressive disclosure generation:** The disclosure tree builder that uses the
  agent wrapper is Epic 3.
- **Collection/namespace support:** The `--collection` CLI flag is deferred (hr-12w).
- **Async embedding client:** The Voyage client uses synchronous `time.sleep` for
  backoff. An async version is a future optimization.
- **Advanced chunking strategies:** Semantic chunking, sentence-level splitting, or
  LLM-powered chunk boundary detection are out of scope. We use line-boundary
  sliding window with heading-aware splitting.
- **Multi-model embedding support:** We target `voyage-4-lite` only. Model
  configurability is a future concern.

## User Stories / Scenarios

### US-1: Developer converts a PDF to markdown
```
Given a PDF file at /docs/annual-report.pdf
When I call convert_to_markdown("/docs/annual-report.pdf", "/tmp/output/")
Then I get a tuple (markdown_text, output_path)
And the markdown preserves heading hierarchy and all content
```

### US-2: Developer converts without Claude CLI installed
```
Given Claude CLI is not on PATH
When I call convert_to_markdown(path, output_dir, use_agent=False)
Then the fallback extractor (pymupdf for PDF, ebooklib for EPUB) is used
And I still get usable markdown (possibly lower quality)
```

### US-3: Developer chunks a markdown document
```
Given a 10,000-token markdown document with ## and ### headings
When I call chunk_markdown(text, target_size=1500, overlap=200)
Then I get a list of TextChunk dataclass objects (content, token_count, chunk_index)
And each chunk is ~1500 tokens (tolerance: 1000–2000)
And chunks split primarily on heading boundaries
And overlap provides context continuity between chunks
```
Note: The chunker does NOT return `models.Chunk` objects. `Chunk` construction
(with `disclosure_doc_id`) happens in Epic 4's pipeline layer.

### US-4: Developer embeds a batch of texts
```
Given 500 text strings
When I call embed_texts(texts: list[str])
Then I get 500 embedding vectors, each 1024 floats
And the client batches into groups of 128
And transient API failures are retried with exponential backoff
And permanent failures (auth, invalid model) raise immediately without retry
```

### US-5: Developer runs the agent wrapper
```
Given a prompt and system prompt
When I call await run_agent(prompt, system_prompt="Convert this document...")
Then Claude runs as a subprocess with JSON output
And if it exceeds timeout, the entire process group is killed cleanly
And I get a parsed dict result
```

### US-6: Agent wrapper timeout handling
```
Given a prompt that causes Claude to run for > 300 seconds
When the timeout fires
Then SIGKILL is sent to the process group (not just the subprocess)
And TimeoutError is raised
And no orphan processes remain
```

## Constraints

- **Python >= 3.11** (project requirement from pyproject.toml)
- **Conversion agent timeout:** 300 seconds
- **Disclosure agent timeout:** 180 seconds (for Epic 3 use, but the wrapper should support configurable timeouts)
- **Fallback extractors must work without Claude CLI** — pymupdf and ebooklib are
  deterministic, no network or subprocess required
- **Token counting is heuristic:** ~4 chars/token. No tokenizer dependency.
- **Voyage API batch limit:** 128 texts per request (API hard limit)
- **Voyage API key from env:** `VOYAGE_API_KEY` environment variable, loaded via
  `get_settings()` from config.py
- **Embedding dimension:** 1024 floats (voyage-4-lite output)
- **No new CLI commands in this epic** — these are library components only
- **All imports lazy where expensive** — don't import pymupdf/ebooklib/voyageai at
  module level (they're heavy)
- **pymupdf import name:** Use `import pymupdf` (not `import fitz`) — the package
  renamed at v1.24
- **Agent stderr:** Capture stderr from Claude subprocess and include in
  RuntimeError message on failure. Do not let it leak to caller's stdout/stderr.
- **Fallback quality floor:** Fallback extractors must produce non-empty text with
  at least paragraph separation. EPUB fallback must strip HTML tags (use
  BeautifulSoup `.get_text(separator='\n\n')`). If extraction produces empty
  string, raise ValueError.
- **Process group kill safety:** Catch `ProcessLookupError` when calling
  `os.killpg()` (process may have already exited between timeout and kill)

## Resolved Questions (from PRD Review)

1. **Agent wrapper: MCP config?** → **No MCP.** No MCP config needed. The agent
   wrapper spawns `claude -p` with `--allowedTools` for Read+Write but no MCP
   server configuration.

2. **Converter: What `--allowedTools` for Claude?** → **Read + Write, no MCP.**
   The conversion agent can read source files and write output files directly.
   No MCP tools. `--allowedTools Read,Write` on the subprocess invocation.

3. **Chunker: heading context in metadata?** → **No.** The chunker produces plain
   text chunks. Heading context / disclosure tree is Epic 3's concern.

4. **Embedding client: Credential source?** → **`get_settings().voyage_api_key`**
   from config.py. Validated at singleton init time (raise if empty).

5. **Error handling philosophy:** → **Raise exceptions everywhere.** All four
   components raise on failure. Callers handle errors. Specific exception types:
   - `RuntimeError` for agent wrapper (non-zero exit, JSON parse failure)
   - `TimeoutError` for agent wrapper timeouts
   - `FileNotFoundError` / `ValueError` for converter (missing file, unsupported format)
   - `ValueError` for chunker (empty input)
   - `voyageai` exceptions bubble up from embedding client; `ValueError` if API key empty

6. **Chunker return type:** → **`list[TextChunk]`** where `TextChunk` is a lightweight
   dataclass (`content: str, token_count: int, chunk_index: int`). NOT `models.Chunk`.
   `Chunk` objects with `disclosure_doc_id` are constructed in Epic 4's pipeline.

7. **`embed_texts` input type:** → **`list[str]`**. Pure text→vector function.
   The embedding client has no knowledge of `Chunk` or any model objects.

## Rough Approach

### File Layout
```
src/pointy_rag/
├── claude_agent.py    # Headless Claude subprocess wrapper
├── converter.py       # PDF/EPUB → markdown converter
├── chunker.py         # Markdown-aware text chunker
├── embeddings.py      # Voyage AI embedding client
tests/
├── test_claude_agent.py
├── test_converter.py
├── test_chunker.py
├── test_embeddings.py
```

### Component Independence
All four components are independent — no imports between them. They share only
the models from `models.py` (DocumentFormat for converter) and config from
`config.py` (Settings for embeddings). This means all four can be built in
parallel by separate polecats.

### Dependencies to Add (pyproject.toml)
```
pymupdf>=1.24.0        # PDF text extraction fallback
ebooklib>=0.18          # EPUB text extraction fallback
beautifulsoup4>=4.12.0  # HTML parsing for EPUB content
voyageai>=0.3.0         # Voyage AI embedding API
```

### Key Design Decisions

1. **Agent wrapper is async** — matches reference pattern and allows concurrent
   agent invocations in future pipeline work.
2. **Process group kill** — `start_new_session=True` + `os.killpg` to avoid orphan
   processes from Claude's MCP servers.
3. **Three-pass JSON extraction** — direct parse → fenced block → first `{` scan.
4. **Chunker uses line-boundary sliding window** — split on headings first, then
   subdivide large sections with line-level overlap.
5. **Embedding client uses lazy singleton** — initialized on first use, credential
   loaded from `get_settings()`.
6. **Batched with per-batch retry** — failure in batch N doesn't retry batches 0..N-1.
   Exponential backoff: 1s, 2s, 4s (3 retries).
