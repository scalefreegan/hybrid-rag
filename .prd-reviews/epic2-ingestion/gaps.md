# Gap Analysis: Epic 2 — Ingestion Components

Reviewer: Gap Analyst
Date: 2026-03-14
PRD: `.prd-reviews/epic2-ingestion/prd-draft.md`

---

## Summary

The PRD covers the happy path for all four components reasonably well. The main
weaknesses are: (1) undefined return contracts that will create integration
friction with Epics 3/4, (2) missing failure semantics that make the components
unreliable to compose, (3) absent operational surface, and (4) under-specified
behaviour around file system assumptions. Most gaps are in the converter and
agent wrapper, which have the most external dependencies.

---

## Gaps

### GAP-01: `convert_to_markdown` return contract is ambiguous

**Classification: must-fix**

**Missing requirement:** The user story shows `(markdown_text, output_path)` as the
return type, but the PRD never specifies what `output_path` is when the caller
does not supply an `output_dir`, or when the fallback extractor is used and no
file is written to disk. It is also unclear whether `markdown_text` is always the
full document content or only a partial result when extraction partially fails.
The `Chunk` model in `models.py` stores `content: str`, so downstream code needs
a guaranteed non-empty string, not `None` or an empty string.

**Impact:** Callers in Epic 4 will write defensive guards that differ between
contributors if the contract is not pinned. The fallback path especially risks
returning an empty string from a failed pymupdf call with no indication of
degradation.

**Suggested addition:**
- Specify the exact return type annotation: `tuple[str, Path | None]` where the
  second element is `None` when no output file is written.
- Require that `markdown_text` is always a non-empty `str`; if extraction produces
  no text, raise `ValueError("Extracted empty document: {path}")` rather than
  returning silently.
- State whether `output_dir` is created automatically if it does not exist, or
  whether a `FileNotFoundError` is raised.

---

### GAP-02: Agent wrapper stdout/stderr handling is unspecified

**Classification: must-fix**

**Missing requirement:** The PRD describes three-pass JSON extraction from stdout
but never addresses what happens to stderr. Claude Code writes warnings,
deprecation notices, and debug lines to stderr. The wrapper must decide: swallow
stderr, capture it for logging, or surface it on failure. This also affects the
subprocess's inherited file descriptors — if the wrapper does not redirect stderr,
it leaks into the calling process's stderr, which is wrong for a library component.

**Impact:** Operators will see unsuppressible output from spawned Claude processes
mixed into their own application logs. Debugging failed conversions is impossible
without captured stderr. Race conditions exist if stderr and stdout both contain
partial JSON when a timeout fires.

**Suggested addition:**
- Specify that `stderr=asyncio.subprocess.PIPE` is used and stderr is captured.
- On success, discard stderr unless the caller opts into it via a `capture_stderr`
  flag.
- On failure (non-zero exit code or RuntimeError), include the first N bytes of
  stderr in the exception message to aid debugging.
- Document the subprocess's working directory and inherited environment variables
  explicitly.

---

### GAP-03: Agent wrapper environment variable inheritance is unspecified

**Classification: must-fix**

**Missing requirement:** The subprocess spawns Claude Code, which will attempt to
load `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_API_KEY`) from the environment.
The PRD says nothing about which environment variables the subprocess inherits,
whether `VOYAGE_API_KEY` is passed through (it should not need to be), or how
credentials are supplied when running inside a CI environment where env vars are
restricted to specific processes.

**Impact:** The conversion agent will silently fail in environments where the
parent process has credentials but subprocess env is stripped (common in Docker,
GitHub Actions with OIDC, or `sudo`-escalated processes). The error will
manifest as a non-zero exit code with an opaque message.

**Suggested addition:**
- State that the subprocess inherits the full parent environment by default
  (`env=None`).
- Document that the Claude Code CLI must find its API key via the inherited
  environment; no special injection is performed.
- Add a constraint: if `ANTHROPIC_API_KEY` is not set and `use_agent=True`, the
  converter should detect this and either raise early with a clear message or fall
  back automatically to the deterministic extractor.

---

### GAP-04: Auto-fallback trigger conditions are undefined

**Classification: must-fix**

**Missing requirement:** The PRD states that the deterministic fallback is used
"when Claude CLI is unavailable" and when `use_agent=False`. It does not specify
what other conditions trigger an automatic fallback:
- Claude CLI exits with a non-zero code
- Claude CLI times out (the `TimeoutError` from the wrapper)
- Claude CLI produces output that fails all three JSON extraction passes
- Claude CLI is found on PATH but returns a response with no meaningful content

Should all of these silently fall back, or should they raise? The PRD's Open
Question #5 acknowledges the inconsistency between components but defers it.

**Impact:** Epic 4 (pipeline orchestration) will have to defensively handle both
exception and empty-string returns unless one contract is chosen. Mixing silent
fallback with raising creates non-deterministic pipeline behavior.

**Suggested addition:**
- Define a `ConversionError` exception class exported from `converter.py`.
- Specify that `use_agent=True` + agent failure raises `ConversionError` by
  default, with a `fallback_on_agent_failure: bool = False` parameter to opt
  into automatic degradation.
- Resolve Open Question #5 here: `converter.py` and `claude_agent.py` raise on
  failure; `chunker.py` is total (never raises). The embedding client raises
  after exhausting retries.

---

### GAP-05: Duplicate document ingestion and idempotency are unaddressed

**Classification: must-fix**

**Missing requirement:** `db.py` already shows that `insert_document` uses
`ON CONFLICT (id) DO UPDATE` while `insert_chunk` uses `ON CONFLICT (id) DO
NOTHING`. If the same PDF is converted twice, the Document row is updated but
the old Chunk rows are orphaned (no parent cascade, no cleanup). The components
PRD says nothing about how converters or chunkers should behave when re-processing
an already-ingested document.

**Impact:** Re-running ingestion on a changed document leaves stale embeddings in
the database. Search results will mix old and new chunks with no way to tell them
apart. This is a data-correctness issue that compounds silently.

**Suggested addition:**
- Specify that these four components are stateless with respect to the DB (they
  do not touch the DB — that is Epic 4's job). Make this explicit to avoid
  contributors adding DB calls to the converter.
- Add a note in the Rough Approach that Epic 4 is responsible for idempotency
  (e.g., delete-before-insert or content-hash deduplication).
- Alternatively, if these components should be self-contained, specify the
  idempotency strategy here and update the DB insert functions accordingly.

---

### GAP-06: File size and memory bounds are absent for the converter

**Classification: must-fix**

**Missing requirement:** The converter passes file content to the Claude agent.
There is no upper bound on file size. A 500 MB PDF passed to the agent wrapper
will either: exhaust the subprocess's context window and return garbage, OOM the
host process, or silently truncate. Similarly, pymupdf loads the entire PDF into
memory; the PRD sets no constraint on input file size.

**Impact:** The system will fail in non-obvious ways on large academic PDFs (many
exceed 50 MB; some exceed 200 MB). The failure mode depends on which path is
taken, making it hard to diagnose.

**Suggested addition:**
- Add a `max_file_size_mb` constraint (e.g., 50 MB for agent path, 500 MB for
  fallback path) with a clear `ValueError` when exceeded.
- Specify whether content is passed to the agent via `--prompt` (limited by shell
  arg length), via a temp file that the agent reads, or via stdin. This determines
  the practical size limit.
- Note that EPUB extraction via ebooklib already operates on in-memory XML;
  large EPUBs (>10 MB) should be noted as a known risk.

---

### GAP-07: Chunk `disclosure_doc_id` linkage is unspecified

**Classification: must-fix**

**Missing requirement:** The `Chunk` model in `models.py` requires
`disclosure_doc_id: str` (non-empty, min_length=1). The chunker's user story
returns `list[Chunk]`, but the chunker has no knowledge of `disclosure_doc_id`
because that ID is assigned when the `DisclosureDoc` is persisted (Epic 3/4).
The PRD does not say how the chunker populates this required field.

**Impact:** Either the chunker must accept a `disclosure_doc_id` parameter and
populate it, or the Chunk model must be relaxed to allow an empty/placeholder ID
before persistence, or a different intermediate type must be used. Without
clarification, every implementer will make a different choice.

**Suggested addition:**
- Define the chunker's return type explicitly. If it returns `list[Chunk]`, specify
  that callers must pass a `disclosure_doc_id: str` parameter and it is applied to
  all returned chunks.
- Or introduce a `ChunkDraft` type (without the `disclosure_doc_id` constraint)
  that is promoted to `Chunk` when the DisclosureDoc ID is known.
- This is a real interface design decision that must be resolved before parallel
  implementation begins.

---

### GAP-08: Embedding dimension mismatch detection is absent

**Classification: must-fix**

**Missing requirement:** The DB schema pins embeddings at `vector(1024)`. The
embedding client targets `voyage-4-lite` which outputs 1024 dimensions. But the
PRD makes no provision for detecting or handling a dimension mismatch: what
happens if the Voyage API returns vectors of a different size (e.g., API change,
wrong model name, or future model update)? `pgvector` will raise a cryptic
dimension mismatch error at insert time, far from the embedding call site.

**Impact:** Dimension errors surface at DB insert time with a generic psycopg
exception rather than at the embedding step. Debugging requires knowing the
pgvector constraint, which is not obvious to contributors who only work on the
embedding client.

**Suggested addition:**
- Specify that `embed_texts` validates that every returned vector has exactly 1024
  elements before returning, raising `ValueError("Expected 1024-dim embedding,
  got {n}")` on mismatch.
- This adds one assertion per batch and is cheap relative to the API call.

---

### GAP-09: Retry scope for the embedding client is under-specified

**Classification: should-fix**

**Missing requirement:** The PRD says "failure in batch N doesn't retry batches
0..N-1" and describes 3 retries with exponential backoff. It does not specify:
- Which exception types trigger a retry (all `Exception`? HTTP 429 only? HTTP 5xx?
  Network timeouts? `voyageai.error.RateLimitError`?)
- Whether HTTP 429 responses include a `Retry-After` header that should override
  the backoff schedule.
- What happens after all retries are exhausted — does the function raise, return
  partial results, or return empty vectors for failed batches?

**Impact:** Indiscriminately retrying on all exceptions (including
`voyageai.error.AuthenticationError`) wastes 7 seconds of backoff before failing.
Returning partial results silently would corrupt the embedding list alignment with
input chunks.

**Suggested addition:**
- Enumerate retryable exceptions explicitly: network errors and HTTP 5xx/429
  responses. Raise immediately on 4xx (except 429).
- Specify that after exhausting retries the function raises `EmbeddingError`
  (a custom exception class), not a bare `Exception`.
- Specify that partial results are never returned; the function either returns all
  N vectors or raises.
- Note that Voyage AI's Python client may surface `voyageai.error.RateLimitError`
  specifically; handle it as a retryable case.

---

### GAP-10: Chunk size edge cases are unhandled

**Classification: should-fix**

**Missing requirement:** The chunker description says "split on heading boundaries,
then subdivide large sections with line-level overlap." It does not specify:
- What happens when a single section with no sub-headings exceeds `target_size`
  by more than 2x or 10x (e.g., a 50,000-token chapter with no headings).
- What happens when `overlap >= target_size` (a configuration error that produces
  infinite or nonsensical chunks).
- Whether a chunk can be returned with zero content (empty section between two
  headings).
- Whether the last chunk in a document can be smaller than `overlap` tokens.

**Impact:** The chunker is described as "total (never raises)" but these edge
cases would produce either an infinite loop, zero-length chunks (failing
`Chunk.content` validation with `min_length=1`), or silently truncated output.

**Suggested addition:**
- Add precondition: raise `ValueError` if `overlap >= target_size`.
- Add postcondition assertion in tests: all returned chunks have `len(content) > 0`.
- Specify minimum chunk size (e.g., discard chunks with fewer than 50 tokens;
  or merge them with the previous chunk).
- Specify the subdivision strategy for heading-free content explicitly: sliding
  window with stride = `target_size - overlap`.

---

### GAP-11: Heuristic token counter accuracy is not bounded

**Classification: should-fix**

**Missing requirement:** The PRD states the counter uses ~4 chars/token and
explicitly accepts heuristic inaccuracy. It does not state what the acceptable
error margin is or how this interacts with the Voyage API input limit. Voyage AI
`voyage-4-lite` has a token limit per request; if the heuristic underestimates
token count, a batch could exceed the API's per-text token limit.

**Impact:** Over-long chunks passed to the embedding API will receive a 400 error.
Because this is not a retryable error, the current retry logic will fail the whole
batch, losing all embeddings for that batch.

**Suggested addition:**
- Document the known error range of the heuristic (e.g., "accurate within 20% for
  English prose; may undercount code blocks with short identifiers").
- Add a safety margin: target_size should not exceed 80% of the Voyage per-text
  token limit to accommodate heuristic error.
- Specify the Voyage per-text limit (`voyage-4-lite` allows 32,000 tokens/text)
  and the recommended `target_size` ceiling (e.g., 2000 tokens).

---

### GAP-12: Process group kill on Windows / non-POSIX platforms

**Classification: should-fix**

**Missing requirement:** The "Key Design Decisions" section specifies
`start_new_session=True` + `os.killpg` for orphan prevention. `os.killpg` is
POSIX-only and does not exist on Windows. The PRD states Python >= 3.11 but does
not state platform requirements.

**Impact:** If a developer runs this on Windows (not unlikely for a library
component), importing `claude_agent` will succeed but calling `run_agent` will
raise `AttributeError: module 'os' has no attribute 'killpg'` only when a timeout
fires — an obscure failure mode.

**Suggested addition:**
- Add an explicit platform constraint: "Linux/macOS only" for the agent wrapper
  process group kill.
- Or specify the Windows fallback: use `proc.kill()` on the subprocess only, and
  document that MCP server orphans are a known risk on Windows.
- At minimum, add a module-level guard that raises `NotImplementedError` on
  non-POSIX platforms at import time, so the failure is immediate and clear.

---

### GAP-13: No logging surface is specified for any component

**Classification: should-fix**

**Missing requirement:** None of the four components are specified to emit any
log output. There is no mention of Python's `logging` module, structured logging,
or debug output. The agent wrapper in particular performs significant work
(subprocess management, timeout handling, JSON extraction) where debug logging
would be essential for production diagnosis.

**Impact:** When a conversion fails in production, operators have no way to
reconstruct what happened without re-running with a debugger. The only signal is
the raised exception, which loses subprocess context (exit code, partial stdout,
stderr content, elapsed time).

**Suggested addition:**
- Specify that each component uses `logging.getLogger(__name__)` at `DEBUG` level
  for non-error events and `WARNING` level for degradation (e.g., fallback
  activated, retry attempt N of 3).
- For the agent wrapper specifically, log: subprocess start (pid, timeout,
  truncated prompt length), successful exit (elapsed_ms, output_bytes), and
  failure (exit_code, stderr excerpt, elapsed_ms).
- Do not add any `print()` calls; all output goes through the logging framework
  so callers can control verbosity.

---

### GAP-14: EPUB extraction via ebooklib produces HTML, not plain text

**Classification: should-fix**

**Missing requirement:** The PRD lists `beautifulsoup4>=4.12.0` as a dependency
(correctly), indicating awareness that ebooklib produces HTML. However, the user
story for the fallback path says it produces "usable markdown." The conversion
from HTML to markdown is not specified: which HTML tags map to which markdown
constructs, how tables are handled, whether images are skipped or produce alt-text
references, and how deeply nested HTML is handled.

**Impact:** Without a specification, implementers will make different choices about
how faithful the HTML-to-markdown conversion is. BeautifulSoup's `get_text()`
strips all structure; a proper conversion requires more work. The quality gap
between the two paths may be larger than expected.

**Suggested addition:**
- Specify the EPUB fallback explicitly: use `ebooklib` to extract HTML item bodies,
  then use `beautifulsoup4` with `get_text(separator="\n")` for a plain-text
  approximation, with a comment that this loses all heading structure.
- OR specify use of `markdownify` (an additional dependency) for structure-
  preserving HTML-to-markdown. If so, add it to the dependencies list.
- Either way, document the known quality floor so Epic 4 can log a warning when
  the fallback is used.

---

### GAP-15: `conftest.py` and test fixtures are not specified

**Classification: should-fix**

**Missing requirement:** The existing `tests/conftest.py` has a `mock_conn`
fixture. The PRD specifies that tests use mocks "no live services required" but
does not specify shared fixtures that the four new test files should use:
- A small real PDF fixture file (needed to test pymupdf integration at all)
- A small real EPUB fixture file
- A `mock_voyage_client` fixture
- A `mock_subprocess` fixture for the agent wrapper
- Sample markdown strings with known chunk counts for the chunker

Without specified fixtures, the four parallel implementers will create
incompatible or duplicated test infrastructure.

**Suggested addition:**
- Add a "Test Fixtures" subsection to the Rough Approach specifying shared
  fixtures in `conftest.py`, a `tests/fixtures/` directory for binary test files,
  and conventions for mock naming.
- Specify that real PDF/EPUB fixture files are minimal (< 10 KB) and committed to
  the repository under `tests/fixtures/`.

---

### GAP-16: The `voyage-4-lite` model identifier is not verified

**Classification: should-fix**

**Missing requirement:** The PRD specifies `voyage-4-lite` as the embedding model
and 1024 as the output dimension. As of the project's knowledge horizon, the
standard Voyage model family uses names like `voyage-large-2-instruct`,
`voyage-3-lite`, etc. The name `voyage-4-lite` may be a prospective name for a
future model. If it does not exist in the voyageai SDK at the time of
implementation, the embedding client will fail with an opaque API error.

**Impact:** Implementers may silently substitute a different model name or
dimension, breaking the DB schema constraint (`vector(1024)`).

**Suggested addition:**
- Confirm the exact model identifier with the Voyage AI API documentation or SDK
  and update the PRD with the verified string.
- Add a note to the embedding client spec: if the model name changes, the DB
  schema `vector(1024)` must be verified to still match the new model's output
  dimension before deployment.

---

### GAP-17: No specification for what "well-structured markdown" means

**Classification: should-fix**

**Missing requirement:** Goal #2 says the converter produces "well-structured
markdown." This is used as the quality bar for both the agent path and the
fallback path. US-1 says "preserves heading hierarchy and all content." But there
is no formal definition: Does "well-structured" mean ATX headings (`#`) vs setext?
Are code blocks fenced with triple backticks? Are tables preserved as GFM tables?
Is front matter emitted?

The chunker depends on this output format because it "splits on heading
boundaries." If the converter sometimes produces setext headings (`===` underlines)
and sometimes ATX headings, the chunker's heading-detection regex must handle
both, but this is not specified.

**Impact:** The chunker and converter are specified as independent components built
in parallel. Without an agreed-upon markdown format contract between them, the
chunker tests will pass with synthetic inputs but fail with real converter output.

**Suggested addition:**
- Define the markdown output format contract: ATX headings only (`#`, `##`, etc.),
  GFM fenced code blocks, no front matter, no HTML passthrough.
- State that the agent prompt for conversion should include this format
  specification.
- Specify that the fallback extractors must also emit ATX headings (pymupdf can
  detect font size as a heading proxy; ebooklib has `<h1>`-`<h6>` tags).

---

## Open Questions Disposition

The PRD lists 5 open questions. Gap analysis suggests resolution priority:

| # | Question | Recommendation |
|---|----------|----------------|
| 1 | MCP config for conversion agent? | **Resolve before impl.** Plain prompt + file path in prompt is simpler and sufficient for conversion. No MCP needed. |
| 2 | `--allowedTools` for Claude? | **Resolve before impl.** Minimal: `Read` only. The agent reads the source file; it should not write. |
| 3 | Heading context in chunk metadata? | **Resolve before impl.** Yes, include heading chain — Epic 3 needs it for disclosure tree construction. Deferring forces a re-chunking pass later. |
| 4 | Credential source for Voyage? | **Already answered** by config.py: `get_settings().voyage_api_key`. Close this question. |
| 5 | Error handling philosophy? | **Resolve before impl** (see GAP-04). Inconsistent error contracts block Epic 4. |
