# Missing Requirements

## Summary

The PRD for Epic 2 (Ingestion Components) is technically focused and well-scoped at the
component level, but it leaves several operational and API-contract questions completely
unaddressed. The most serious gaps are around error handling philosophy (explicitly noted
as an open question but left unresolved), edge case behavior for malformed or empty inputs,
and thread-safety of the lazy singleton embedding client. These gaps will cause surprise at
integration time — either through silent data loss, production incidents from unexpected
None returns, or race conditions when the pipeline eventually parallelizes.

Additionally, the PRD is written entirely from a developer-of-the-component perspective and
ignores the operator perspective entirely. There is no logging strategy, no cost visibility
into Voyage AI spend, no way to diagnose silent failures in the Claude subprocess, and no
consideration of what happens to existing embeddings if the model or chunking parameters change.
These are the questions ops will ask at launch that nobody has thought about yet.

## Findings

### Critical Gaps / Questions

**1. Error handling contract is unresolved (Open Question #5)**
- The PRD names the inconsistency (converter raises RuntimeError, chunker is total/never
  raises, embedder raises Exception) but does not resolve it.
- This matters because callers cannot write correct error handling if they don't know what
  to catch. An API contract that changes component-by-component creates silent swallowing
  of errors or overly broad except clauses in downstream pipeline code.
- *Suggested question:* Should all components follow "raise on failure" semantics, or should
  chunker and embedder return sentinel values (empty list, None) for recoverable errors?
  Pick one and document it.

**2. Empty / null input behavior is unspecified**
- What does `embed_texts([])` return? `[]` or raise? This is called with the output of the
  chunker — if chunking yields no chunks (e.g., empty document), the embedder must handle
  this gracefully.
- What does `chunk_markdown("", ...)` return? An empty list or a single empty-string chunk?
- What does `convert_to_markdown(path, output_dir)` do if the PDF has zero extractable text
  (image-only scan)?
- *Suggested question:* Define the return value for each function when given empty/degenerate
  input. This is a public API contract question and cannot be left implicit.

**3. VOYAGE_API_KEY absent or invalid at runtime**
- The PRD says the key comes from `get_settings().voyage_api_key` via env var `VOYAGE_API_KEY`.
  What happens if the key is missing? Does `get_settings()` raise at import time, at first
  call, or does it silently set `None`? Does `embed_texts` then raise with a clear message
  or silently fail?
- This is a common production incident trigger: the service starts fine, the first embedding
  call fails opaquely, and nothing in the logs explains why.
- *Suggested question:* Should `get_settings()` validate that `VOYAGE_API_KEY` is present and
  non-empty at startup? What error message should the user see?

**4. Password-protected and corrupted PDF handling**
- No mention of what happens when the PDF converter encounters a password-protected file,
  a truncated file, or a file that pymupdf/ebooklib cannot parse at all.
- Should the converter raise? Return empty markdown? Fall back further (to some other tool)?
- *Suggested question:* What is the expected behavior when the input file is unreadable?
  Define error type and message.

**5. Lazy singleton thread safety**
- The embedding client is described as a "lazy singleton — initialized on first use." If
  `embed_texts` is called concurrently (which will happen when the pipeline parallelizes),
  two threads could race to initialize the singleton. Python's GIL does not protect against
  this for I/O-bound initialization.
- The PRD defers async embedder to a future optimization, but the singleton initialization
  race exists even in synchronous code called from threads (e.g., a ThreadPoolExecutor).
- *Suggested question:* Should the singleton use a threading.Lock for initialization? Or is
  this component explicitly NOT thread-safe (must document that)?

**6. Output directory creation for converter**
- `convert_to_markdown(path, output_dir)` — what happens if `output_dir` does not exist?
  Does the function create it, raise FileNotFoundError, or leave behavior undefined?
- *Suggested question:* Should the converter create missing output directories, or require
  callers to pre-create them?

### Important Considerations

**7. Heuristic token counter breaks for CJK and non-Latin scripts**
- The "~4 chars/token" heuristic significantly underestimates token count for Chinese,
  Japanese, Korean, and Arabic text (where 1-2 chars ≈ 1 token). A 4000-char Chinese
  document would be estimated at ~1000 tokens but actually be ~3500-4000 tokens, causing
  severely undersized chunks.
- The PRD says "No tokenizer dependency" — if that constraint is firm, at least document
  the limitation and what scripts it applies to. If the system will process non-Latin
  documents, a proper tokenizer should be evaluated.
- *Suggested question:* Will this system ingest non-Latin-script documents? If yes, is the
  4 chars/token heuristic acceptable, or should a language-aware fallback be considered?

**8. No logging or observability strategy**
- None of the four components mention logging. There is no specification of what gets logged
  on success (timing, chunk count, embedding count), on recoverable error (retry attempt),
  or on failure (which file, what error).
- Without logs, debugging production failures means re-running the pipeline with a debugger.
  Support teams have no way to answer "why did document X fail to ingest?"
- *Suggested question:* Should these components emit structured log entries (e.g., via
  Python's logging module at DEBUG/INFO/WARNING/ERROR)? Define what events are logged.

**9. Voyage AI cost visibility**
- The embedder batches and retries but there is no mention of tracking API usage. In
  production, a runaway ingestion job could spend unexpectedly. There is no mention of:
  - Counting tokens or texts sent to Voyage AI
  - Surfacing per-run cost estimates
  - Any rate limiting beyond the per-batch retry
- *Suggested question:* Should the embedder return metadata (total texts embedded, total
  API calls made) alongside the vectors? Or is cost tracking deferred entirely?

**10. Batch failure partial results: atomicity vs. partial success**
- The PRD says "failure in batch N doesn't retry batches 0..N-1." This implies that if
  batch 5 of 10 fails permanently after 3 retries, batches 0-4 have already been embedded
  but the caller receives an exception, not the partial results.
- The caller then has no way to resume from batch 5. They must re-embed everything.
- *Suggested question:* Should `embed_texts` return partial results on failure (e.g., as a
  partially-filled list with None for failed batches), or is raising the correct behavior
  and callers are expected to retry the full input?

**11. Claude CLI version dependency unspecified**
- The agent wrapper spawns `claude` from PATH. There is no specification of minimum Claude
  CLI version, no version check, and no documented behavior if the installed version changes
  its output format.
- *Suggested question:* What version of Claude CLI is required? Should the agent wrapper
  emit a warning if the detected version is below the minimum?

**12. No deduplication or caching of embeddings**
- If the same document is ingested twice (e.g., a retry after pipeline failure), all four
  components run from scratch: re-convert, re-chunk, re-embed, re-store. There is no
  content hash check, no "already ingested" detection.
- This is probably fine for Epic 2 scope but will be a surprise cost when a pipeline retry
  re-embeds 10,000 documents.
- *Suggested question:* Is deduplication in scope for any epic, or is re-processing
  explicitly acceptable behavior?

**13. Max file size / memory limits**
- No mention of the largest supported PDF or EPUB file. A 500MB PDF with 10,000 pages
  could consume significant memory in the fallback extractor (pymupdf loads pages into
  memory). No streaming extraction is described.
- *Suggested question:* What is the maximum supported file size? Should there be a size
  check before attempting conversion?

### Observations

**14. JSON output schema for agent wrapper is not specified**
- The PRD says the agent wrapper performs "three-pass JSON extraction" but doesn't specify
  what JSON schema Claude should produce. The prompt engineering required to get Claude to
  reliably output structured JSON is completely absent. Without this, the converter's agent
  path is underspecified.
- Non-blocking: the converter still has the fallback path, but the agent path's reliability
  depends heavily on prompt structure that isn't part of this PRD.

**15. Document format auto-detection not addressed**
- The PRD implies the caller passes a file path and the converter determines PDF vs. EPUB by...
  extension? MIME type? The `DocumentFormat` model from Epic 1 presumably drives this, but
  the detection logic is not described. What happens with a `.pdf` extension on an EPUB file?

**16. Test coverage targets are vague**
- "All components have pytest unit tests" sets a floor (tests exist) but not a ceiling
  (adequate coverage). No coverage percentage is specified. Edge case tests (timeout
  enforcement, corrupted input, empty batch) are not explicitly required.
- The US-6 timeout scenario (verify no orphan processes) is particularly hard to test in a
  unit test with mocks and likely requires an integration test strategy.

**17. No mention of `--allowedTools` MCP config (Open Question #2)**
- Open Question #2 asks what tools the conversion agent should have access to but is not
  resolved. This is a security consideration: if the agent has Write access, it could
  write to arbitrary paths. If it has Read access, it could read sensitive files. The PRD
  should resolve this before implementation.

**18. No deprecation/migration plan for chunk schema changes**
- If chunking parameters change (e.g., target_size from 1500 to 2000, or overlap from 200
  to 300), existing chunks in the database have different characteristics than new chunks.
  Vector similarity search results would be inconsistent. No versioning or migration of
  existing chunks is mentioned.

## Confidence Assessment

**Low-Medium.** The component interfaces are well-described and the technical approach is
solid. However, the PRD leaves five significant API contract questions unresolved (error
handling, empty inputs, missing API key, thread safety, output directory creation) that
directly affect implementation decisions. Each of these will generate a code review
comment or a production bug if not answered before build. The operational gaps (logging,
cost visibility, deduplication) are lower urgency but represent real launch risks. Overall,
the PRD is about 70% complete for a greenfield component set — functional enough to start
building, but with enough open questions that two polecats could independently make
incompatible choices.
