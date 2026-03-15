# Design Document: Epic 2 — Ingestion Components

## Overview

Four independent library components that transform raw documents (PDF/EPUB)
into embedded, searchable chunks. These are building blocks for Epic 3
(intelligence layer) and Epic 4 (pipeline orchestration).

**PRD:** `.prd-reviews/epic2-ingestion/prd-draft.md`

## File Layout

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

## Component Dependency Graph

```
models.py (DocumentFormat) ──► converter.py
config.py (get_settings)  ──► embeddings.py
claude_agent.py (run_agent) ──► converter.py   # agent conversion path
(none)                     ──► claude_agent.py
(none)                     ──► chunker.py
```

Three of the four components are fully independent (`claude_agent`, `chunker`,
`embeddings`). `converter` depends on `claude_agent` for the agent conversion
path. In practice, `converter` can be built in parallel with `claude_agent`
since it only needs the `run_agent` function signature (not the implementation)
and has a standalone fallback path.

## Data Flow

```
Path (PDF/EPUB)
    │
    ▼
converter.convert_to_markdown(path, output_dir)
    → tuple[str, Path]       # (markdown_text, output_file)
    │
    ▼
chunker.chunk_markdown(text, target_size=1500, overlap=200)
    → list[TextChunk]        # frozen dataclass: content, token_count, chunk_index
    │
    ▼  [c.content for c in chunks]
embeddings.embed_texts(texts)
    → list[list[float]]      # 1024-dim vectors
    │
    ▼  (Epic 4 — out of scope)
models.Chunk(disclosure_doc_id=..., content=tc.content, embedding=emb)
```

---

## 1. claude_agent.py

### Constants

```python
DEFAULT_TIMEOUT: float = 300.0
DISCLOSURE_TIMEOUT: float = 180.0   # For Epic 3 callers
```

### Public API

```python
async def run_agent(
    prompt: str,
    *,
    system_prompt: str = "",
    timeout: float = DEFAULT_TIMEOUT,
    allowed_tools: tuple[str, ...] = ("Read", "Write"),
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Run headless Claude Code subprocess and return parsed JSON output."""
```

### Subprocess Construction

```python
proc = await asyncio.create_subprocess_exec(
    "claude", "--output-format", "json",
    "--allowedTools", ",".join(allowed_tools),
    *(["--system-prompt", system_prompt] if system_prompt else []),
    "-p", prompt,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    cwd=output_dir,
    start_new_session=True,        # process group isolation
)
```

### Critical: Use `proc.communicate()` to Avoid Pipe Deadlock

```python
try:
    stdout, stderr = await asyncio.wait_for(
        proc.communicate(), timeout=timeout
    )
except asyncio.TimeoutError:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass  # process already exited
    raise TimeoutError(f"Claude agent timed out after {timeout}s")
```

Do NOT use `proc.stdout.read()` + `proc.wait()` — risks deadlock on large
outputs when OS pipe buffer fills.

### JSON Extraction — Three-Pass Strategy

```python
def _extract_json(stdout: str, stderr: str = "") -> dict:
    """Extract JSON from Claude output. Raises RuntimeError on failure."""
    text = stdout.strip()

    # Pass 1: Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Pass 2: Fenced block (```json ... ```)
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Pass 3: First-brace scan
    brace_index = text.find("{")
    if brace_index != -1:
        try:
            return json.loads(text[brace_index:])
        except json.JSONDecodeError:
            pass

    raise RuntimeError(
        f"Could not extract JSON from agent output. "
        f"stdout (first 500 chars): {text[:500]!r}"
        f"{f' stderr: {stderr[:500]!r}' if stderr else ''}"
    )
```

### Exceptions

| Exception | Condition |
|-----------|-----------|
| `TimeoutError` | Subprocess exceeds `timeout`; process group killed first |
| `RuntimeError` | Non-zero exit; message includes stderr for diagnostics |
| `RuntimeError` | All three JSON extraction passes fail |

### Exit Code Handling

```python
if proc.returncode != 0:
    raise RuntimeError(
        f"Claude agent exited with code {proc.returncode}. "
        f"stderr: {stderr.decode(errors='replace')[:1000]}"
    )
```

---

## 2. converter.py

### Constants

```python
CONVERSION_TIMEOUT: float = 300.0
```

### Public API

```python
def convert_to_markdown(
    source_path: str | Path,
    output_dir: str | Path,
    *,
    use_agent: bool = True,
    timeout: float = CONVERSION_TIMEOUT,
) -> tuple[str, Path]:
    """Convert PDF or EPUB to markdown. Returns (text, output_path)."""
```

### Logic Flow

```
1. Validate source_path exists           → FileNotFoundError
2. Validate suffix in {.pdf, .epub}      → ValueError
3. Detect format via _EXTENSION_MAP      → DocumentFormat
4. os.makedirs(output_dir, exist_ok=True)
5. If use_agent=True:
     try _agent_convert()               → (text, path)
     except (TimeoutError, RuntimeError, FileNotFoundError):
         log warning "Agent failed, using fallback extractor"
         fall through to step 6
     # FileNotFoundError: `claude` binary not on PATH
     # Note: agent failure with use_agent=True is a graceful degradation,
     # not a silent failure — the warning is logged.
6. Deterministic fallback:
     PDF  → _extract_pdf() via pymupdf  (lazy import)
     EPUB → _extract_epub() via ebooklib + BeautifulSoup (lazy import)
7. If text.strip() empty                 → raise ValueError BEFORE writing file
8. Write markdown, return (text, path)
```

### Extension Map

```python
_EXTENSION_MAP: dict[str, DocumentFormat] = {
    ".pdf": DocumentFormat.pdf,
    ".epub": DocumentFormat.epub,
}
```

Use `file_path.suffix.lower()` for case-insensitive matching.

### Fallback Extractors

**PDF — `_extract_pdf(source_path: Path) -> str`:**
```python
import pymupdf  # NOT import fitz — renamed at v1.24
doc = pymupdf.open(str(source_path))
text = "\n\n".join(page.get_text() for page in doc)
```

**EPUB — `_extract_epub(source_path: Path) -> str`:**
```python
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

book = epub.read_epub(str(source_path))
parts = []
for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
    soup = BeautifulSoup(item.get_content(), "html.parser")
    # Remove script/style tags before extraction
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    parts.append(soup.get_text(separator="\n\n"))
text = "\n\n---\n\n".join(parts)
```

### Agent Convert Bridge

```python
def _agent_convert(source_path, output_dir, timeout):
    """Synchronous wrapper — calls asyncio.run(run_agent(...))."""
    result = asyncio.run(run_agent(prompt, timeout=timeout))
    # Read output file or extract markdown from result
```

### Exceptions

| Exception | Condition |
|-----------|-----------|
| `FileNotFoundError` | source_path does not exist |
| `ValueError` | Unsupported file extension |
| `ValueError` | Fallback extractor produces empty text |

---

## 3. chunker.py

### Constants

```python
CHARS_PER_TOKEN: int = 4
DEFAULT_TARGET_SIZE: int = 1500  # tokens
DEFAULT_OVERLAP: int = 200      # tokens
```

### TextChunk Dataclass

```python
@dataclass(frozen=True)
class TextChunk:
    content: str
    token_count: int
    chunk_index: int
```

Frozen (immutable, hashable). Not Pydantic — lightweight internal type.
NOT `models.Chunk` (which requires `disclosure_doc_id`).

### Public API

```python
def estimate_tokens(text: str) -> int:
    """Heuristic token count: ~4 chars per token."""
    return len(text) // CHARS_PER_TOKEN


def chunk_markdown(
    text: str,
    *,
    target_size: int = DEFAULT_TARGET_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[TextChunk]:
    """Split markdown into overlapping chunks respecting heading boundaries."""
```

### Algorithm

1. Raise `ValueError` if `text.strip()` is empty
2. Split text into sections on heading boundaries (`^#{1,6}\s`)
3. Accumulate sections into buffer
4. When `estimate_tokens(buffer) >= target_size`, emit chunk; seed new buffer
   with overlap suffix (last `overlap * CHARS_PER_TOKEN` chars)
5. For sections larger than `target_size`, subdivide with line-level sliding
   window
6. Emit remaining buffer as final chunk
7. Assign `chunk_index` sequentially from 0

### Exceptions

| Exception | Condition |
|-----------|-----------|
| `ValueError` | Empty/whitespace-only input |

---

## 4. embeddings.py

### Constants

```python
VOYAGE_BATCH_SIZE: int = 128     # API hard limit — not caller-overridable
VOYAGE_MODEL: str = "voyage-4-lite"
EMBEDDING_DIM: int = 1024
_MAX_ATTEMPTS: int = 4           # 1 initial + 3 retries; backoff: 1s, 2s, 4s
_RETRY_BASE_DELAY: float = 1.0
```

### Lazy Singleton

```python
_client: "voyageai.Client | None" = None

def _get_client() -> "voyageai.Client":
    global _client
    if _client is None:
        import voyageai
        api_key = get_settings().voyage_api_key
        if not api_key:
            raise ValueError(
                "VOYAGE_API_KEY is not set. "
                "Add it to .env or set the environment variable."
            )
        _client = voyageai.Client(api_key=api_key)
    return _client
```

### Public API

```python
def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts using Voyage AI. Returns one 1024-dim vector per input."""
```

Note: `VOYAGE_BATCH_SIZE` is internal (API hard limit). Not exposed as a
parameter per PRD constraint.

### Batching and Retry

```python
if not texts:
    return []

client = _get_client()
all_embeddings: list[list[float]] = []

for batch_start in range(0, len(texts), VOYAGE_BATCH_SIZE):
    batch = texts[batch_start : batch_start + VOYAGE_BATCH_SIZE]
    embeddings = _embed_batch_with_retry(client, batch)
    all_embeddings.extend(embeddings)

return all_embeddings
```

**Per-batch retry with exponential backoff:**

```python
# Non-retryable exception base classes (checked via isinstance)
_PERMANENT_ERRORS: tuple[type[Exception], ...] = ()  # populated at import time

def _embed_batch_with_retry(client, batch):
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = client.embed(batch, model=VOYAGE_MODEL)
            return response.embeddings
        except Exception as exc:
            # Auth/invalid-request errors: raise immediately, no retry
            if isinstance(exc, _PERMANENT_ERRORS):
                raise
            if attempt == _MAX_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_BASE_DELAY * (2 ** attempt))  # 1s, 2s, 4s
```

`_PERMANENT_ERRORS` is populated when `voyageai` is lazily imported inside
`_get_client()`:

```python
global _PERMANENT_ERRORS
import voyageai
_PERMANENT_ERRORS = (
    voyageai.error.AuthenticationError,
    voyageai.error.InvalidRequestError,
)
```

This uses `isinstance()` (not string matching) for reliable classification.

### Exceptions

| Exception | Condition |
|-----------|-----------|
| `ValueError` | Empty API key at singleton init |
| Auth errors | Raised immediately, no retry |
| Transient errors | Re-raised after 3 retry attempts |

---

## pyproject.toml Changes

### Runtime Dependencies (add)

```toml
"pymupdf>=1.24.0",          # PDF fallback; >=1.24 for security + import rename
"ebooklib>=0.18",            # EPUB fallback
"beautifulsoup4>=4.12.0",   # HTML parsing for EPUB
"voyageai>=0.3.0",           # Embedding API
```

### Dev Dependencies (add)

```toml
"pytest-asyncio>=0.23.0",   # For async test support
```

### Pytest Config (add)

```toml
asyncio_mode = "auto"
```

---

## Cross-Cutting Concerns

### Lazy Imports

pymupdf, ebooklib, beautifulsoup4, voyageai — import inside function bodies,
never at module level. Critical for CLI responsiveness (`pointy-rag --help`).

### Import Discipline

- `converter.py` imports `DocumentFormat` from `models.py`
- `converter.py` imports `run_agent` from `claude_agent.py` (agent path)
- `embeddings.py` imports `get_settings` from `config.py`
- `claude_agent.py` and `chunker.py` have NO project imports

### Security Notes

1. Agent subprocess uses `--allowedTools Read,Write` with `shell=False` (no injection)
2. Process group kill with `start_new_session=True` prevents orphans
3. Prompts passed as single argv elements (no shell interpolation)
4. EPUB fallback decomposes `<script>`/`<style>` tags before `get_text()`
5. API key checked non-empty at singleton init; never logged

### Error Philosophy

All components raise on failure. No silent fallbacks or None returns.
Specific exception types per component (see tables above).

---

## Test Plan Summary

| File | Tests | Mocks | Async |
|------|-------|-------|-------|
| test_claude_agent.py | 8 | subprocess, os.killpg, os.getpgid | Yes |
| test_converter.py | 8 | run_agent, pymupdf, ebooklib, BeautifulSoup | Yes |
| test_chunker.py | 10 | None (pure logic) | No |
| test_embeddings.py | 8 | voyageai.Client, get_settings, time.sleep | No |
| **Total** | **34** | | |

### Key Test Patterns

- **Singleton reset:** `test_embeddings.py` needs `autouse` fixture resetting
  `embeddings._client = None` between tests
- **Lazy import mock targets:** Patch `pointy_rag.converter.pymupdf`, NOT
  bare `pymupdf` — lazy import binds the name in converter's module namespace
- **BS4 parser:** Always pass `"html.parser"` explicitly to avoid warnings

### Test Details by File

**test_claude_agent.py:**
- `test_run_agent_success` — mock subprocess returns valid JSON
- `test_run_agent_timeout` — mock hangs, verify TimeoutError + os.killpg called
- `test_run_agent_nonzero_exit` — exit code 1, verify RuntimeError with stderr
- `test_run_agent_invalid_json` — unparseable stdout, verify RuntimeError
- `test_run_agent_json_in_fenced_block` — ` ```json ``` ` wrapper extracted
- `test_run_agent_json_first_brace_scan` — preamble + `{...}` extracted
- `test_run_agent_process_group_kill_race` — ProcessLookupError swallowed
- `test_run_agent_command_construction` — verify argv has --output-format json, --allowedTools Read,Write

**test_converter.py:**
- `test_convert_pdf_with_agent` — agent path, verify prompt includes path
- `test_convert_epub_with_agent` — agent path for EPUB
- `test_convert_pdf_fallback` — use_agent=False, mock pymupdf
- `test_convert_epub_fallback` — use_agent=False, mock ebooklib+BS4
- `test_convert_missing_file` — FileNotFoundError
- `test_convert_unsupported_format` — .txt → ValueError
- `test_convert_fallback_empty_result` — empty extraction → ValueError (before file write)
- `test_convert_agent_failure_falls_back` — use_agent=True, agent raises RuntimeError, fallback used

**test_chunker.py:**
- `test_chunk_basic` — simple text produces valid TextChunks
- `test_chunk_heading_boundary` — splits on ## headings
- `test_chunk_overlap` — consecutive chunks share content
- `test_chunk_empty_input` — ValueError
- `test_chunk_whitespace_only` — ValueError
- `test_chunk_small_document` — single chunk returned
- `test_chunk_large_section` — section > target subdivided
- `test_chunk_index_sequential` — 0, 1, 2, ...
- `test_estimate_tokens` — len//4 heuristic
- `test_chunk_token_count_matches_estimate` — stored count matches heuristic

**test_embeddings.py:**
- `test_embed_texts_single_batch` — <=128 texts, one API call
- `test_embed_texts_multiple_batches` — 300 texts → 3 calls (128+128+44)
- `test_embed_texts_empty_list` — returns [] without API call
- `test_embed_texts_retry_on_transient_error` — backoff 1s, 2s, success
- `test_embed_texts_exhausts_retries` — all attempts fail, re-raises
- `test_embed_texts_no_retry_on_auth_error` — immediate raise, no sleep
- `test_get_client_singleton` — same object on two calls
- `test_get_client_missing_api_key` — empty key → ValueError

---

## Implementation Beads (for bead-planner)

Three components are fully independent; converter depends on claude_agent:

1. **claude_agent.py + test_claude_agent.py** — async subprocess wrapper
2. **converter.py + test_converter.py** — PDF/EPUB converter with agent + fallback
3. **chunker.py + test_chunker.py** — markdown chunker (pure logic)
4. **embeddings.py + test_embeddings.py** — Voyage AI client with batching

Plus one setup bead:
5. **pyproject.toml** — add runtime + dev dependencies, pytest-asyncio config

Bead 2 depends on bead 1 (converter calls run_agent).
Beads 3 and 4 are fully independent.
Bead 5 should be done first (dependency install).
