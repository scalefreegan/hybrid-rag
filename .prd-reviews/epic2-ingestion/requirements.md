# Requirements Analysis: Epic 2 — Ingestion Components

**Reviewer:** Requirements Analyst
**PRD:** `.prd-reviews/epic2-ingestion/prd-draft.md`
**Date:** 2026-03-14

---

## Summary

The PRD is well-scoped with clear non-goals and sensible user stories. The four
components are independently implementable, which is good. However, the PRD has
notable gaps in measurable acceptance criteria, error handling contracts, and
output shape specifications. Several implicit requirements surface when cross-
referencing the Epic 1 models (`models.py`). The most critical gaps are in
component-level "done" definitions and error handling philosophy — both of which
are partially acknowledged in Open Question 5 but left unresolved.

---

## Findings by Category

---

### 1. Success Criteria / Definition of Done

#### 1.1 — No component-level acceptance criteria
**Status: GAP | Severity: must-fix**

Goal 5 states "each component has pytest unit tests using mocks," but this is a
process criterion, not a success criterion. There is no statement of what
observable behavior must hold for each component to be considered complete. The
user stories are helpful scenarios but are written as informal Given/When/Then
— they are not tied to any test assertions or acceptance gate.

**Suggested improvement:** Add a "Done when" checklist for each component. Example
for the chunker:
- `chunk_markdown` returns a non-empty list for any non-empty input.
- No single chunk exceeds `target_size * 1.5` tokens (the 1.5x tolerance must
  be stated).
- Overlap tokens appear verbatim at the start of chunk N+1.
- All four test files pass with `pytest -x`.
- `mypy --strict` reports no errors on the four new modules.

#### 1.2 — No type-annotation / linting acceptance criteria
**Status: GAP | Severity: should-fix**

Epic 1 models use Pydantic and type annotations throughout. The PRD does not
state whether the new modules must pass `mypy`, `ruff`, or any linter. The
existing `pyproject.toml` may already configure these; if so, the PRD should
reference that enforcement gate as a hard requirement.

**Suggested improvement:** State explicitly: "All new modules pass the project's
existing mypy and ruff configurations with no new ignores."

---

### 2. Agent Wrapper (`claude_agent.py`)

#### 2.1 — Return type of `run_agent` not specified
**Status: GAP | Severity: must-fix**

US-5 says "I get a parsed dict result." The Goals section says "structured JSON
output extraction." Neither specifies:
- Is the return type `dict[str, Any]`? Or a typed model?
- What is the return value when JSON extraction fails after all three passes?
- Is the return value always a dict, or can it be a string fallback?

This ambiguity means two implementors could produce incompatible signatures. The
function signature must be specified before implementation begins.

**Suggested improvement:** State the full signature, e.g.:
```python
async def run_agent(
    prompt: str,
    system_prompt: str = "",
    timeout: float = 300.0,
) -> dict[str, Any]:
    ...
```
And state: "Raises `ValueError` if JSON cannot be extracted after all three
passes" (or whichever contract is chosen).

#### 2.2 — Three-pass JSON extraction: failure contract
**Status: PARTIAL | Severity: must-fix**

The Key Design Decisions section describes the three-pass extraction strategy
but does not specify what happens when all three passes fail. US-5 says "I get
a parsed dict result" — implying success is assumed. US-6 only covers the
timeout path. No story covers the malformed-output path.

**Suggested improvement:** Add a story or constraint: "If Claude output cannot be
parsed as JSON after all three passes, `run_agent` raises `ValueError` with the
raw output included in the message for debugging."

#### 2.3 — Subprocess stdout/stderr handling not specified
**Status: GAP | Severity: should-fix**

The PRD does not specify:
- Where does Claude's stderr go? Suppressed? Logged? Included in the exception?
- Is stdout buffered in memory? What is the practical size limit?
- Is there a max output size guard to prevent OOM on runaway agent output?

**Suggested improvement:** Add a constraint: "Claude subprocess stdout is
captured in memory. Stderr is discarded (or logged at DEBUG level). No max
output size guard is required for this epic, but the implementation must not
block on unbounded buffering."

#### 2.4 — `--allowedTools` / MCP config: open question blocks implementation
**Status: GAP | Severity: must-fix**

Open Questions 1 and 2 directly concern the agent wrapper's invocation
contract. If the answer affects the CLI flags passed to `claude`, it will change
the function signature and the test mocks. These questions must be resolved
before implementation begins, not left open in the delivered PRD.

**Suggested improvement:** Resolve both questions or explicitly state interim
decisions: e.g., "For this epic, invoke `claude` with `--no-mcp` and pass file
content via stdin. MCP tool access is deferred to Epic 3."

---

### 3. Document Converter (`converter.py`)

#### 3.1 — Return type of `convert_to_markdown` not specified
**Status: GAP | Severity: must-fix**

US-1 says "I get a tuple (markdown_text, output_path)." The PRD does not
specify:
- Is the tuple `tuple[str, Path]` or `tuple[str, str]`?
- Is `output_path` the written file path, or is it `None` when `output_dir` is
  not provided?
- Is the markdown file always written, or is writing optional?

The `output_dir` parameter in the user story implies writing is always done, but
this is not stated explicitly.

**Suggested improvement:** Specify the full function signature:
```python
def convert_to_markdown(
    path: str | Path,
    output_dir: str | Path | None = None,
    use_agent: bool = True,
) -> tuple[str, Path | None]:
    ...
```
And state: "If `output_dir` is provided, the markdown is written to
`output_dir/<stem>.md` and the path is returned. If `output_dir` is `None`, no
file is written and the path element is `None`."

#### 3.2 — Agent conversion failure fallback path not specified
**Status: GAP | Severity: must-fix**

The PRD defines two paths: agent (primary) and fallback (deterministic). It does
not specify:
- Does the converter silently fall back if the agent fails (non-zero exit,
  timeout, JSON parse failure)?
- Or does it raise, letting the caller decide whether to retry with fallback?
- Is there a log/warning emitted when fallback is used?

US-2 only covers the case where `use_agent=False` is explicitly passed. There is
no story for "agent is available but fails mid-conversion."

**Suggested improvement:** Add a constraint: "If the agent subprocess fails
(non-zero exit code or `TimeoutError`), the converter automatically retries with
the deterministic fallback and emits a `logging.warning`. It does not raise."
Or alternatively: "The converter does not silently fall back. A failed agent
raises `RuntimeError`. The caller is responsible for retrying with
`use_agent=False`." Either contract is acceptable, but one must be chosen.

#### 3.3 — Markdown quality criteria for fallback not testable
**Status: PARTIAL | Severity: should-fix**

US-2 says "I still get usable markdown (possibly lower quality)." The phrase
"possibly lower quality" is not testable. There is no minimum bar for what the
fallback must preserve.

**Suggested improvement:** State a minimum floor: "The fallback extractor must
preserve all extractable text content. Loss of formatting is acceptable. An
empty string result is not acceptable — if the extractor returns empty output,
raise `ValueError`."

#### 3.4 — EPUB with no spine or broken structure: behavior unspecified
**Status: GAP | Severity: should-fix**

The PRD targets PDF and EPUB but does not specify behavior for:
- Corrupt or password-protected PDFs.
- EPUBs with no `<spine>` or no readable item content.
- Files with unsupported MIME type passed to `convert_to_markdown`.

**Suggested improvement:** Add a constraint: "Unsupported file formats raise
`ValueError`. Corrupt files that cause the extractor to throw propagate the
extractor's exception without wrapping."

---

### 4. Markdown Chunker (`chunker.py`)

#### 4.1 — `chunk_markdown` return type and Chunk model population
**Status: PARTIAL | Severity: must-fix**

US-3 says "I get a list of Chunk objects." `Chunk` is defined in `models.py`
and has required fields: `disclosure_doc_id` (min_length=1), `content`, `id`,
`embedding`, `metadata`. The chunker is described as a library component that
does not interact with the DB. But `Chunk.disclosure_doc_id` is required — who
populates it?

If the chunker returns `Chunk` objects, the caller must supply
`disclosure_doc_id` somehow. If the chunker returns something else (a plain
dataclass, a NamedTuple with just `content` and `token_count`), the PRD should
specify that intermediate type rather than reusing the DB-linked model.

This is a significant implicit coupling to the Epic 1 models that is not
addressed.

**Suggested improvement:** Explicitly state: "The chunker returns a list of
plain dataclasses or TypedDicts with fields `content: str` and
`token_count: int`. Conversion to the `Chunk` model is the pipeline's
responsibility (Epic 4)." Or alternatively: "The chunker returns `Chunk`
objects with `disclosure_doc_id` set to empty string; the pipeline must
backfill this field before persisting."

#### 4.2 — Token count tolerance / chunk size contract
**Status: PARTIAL | Severity: must-fix**

US-3 says "each ~1500 tokens." The tilde is doing heavy lifting here. The
implementation must know the actual tolerance to decide when to subdivide a
section. Without a stated tolerance, test assertions cannot be written.

**Suggested improvement:** State: "A chunk may exceed `target_size` when a
single paragraph or code block cannot be split at a line boundary. The hard
maximum is `target_size * 2`. Chunks below `target_size * 0.25` are permitted
only at document boundaries (first or last chunk)."

#### 4.3 — Overlap behavior at heading boundaries
**Status: GAP | Severity: should-fix**

The PRD states overlap provides "context continuity between chunks" but does not
specify:
- When a chunk boundary falls on a heading, does the heading appear in the
  overlap of the next chunk?
- If a section is smaller than `overlap`, is the entire section repeated?
- Is `overlap=0` a valid input?

**Suggested improvement:** Add: "When a chunk boundary falls immediately after a
heading, the heading is included in the overlap prefix of the next chunk. Overlap
is capped at the actual size of the preceding chunk. `overlap=0` disables
overlap entirely."

#### 4.4 — Heading context in chunk metadata: open question blocks design
**Status: GAP | Severity: should-fix**

Open Question 3 asks whether each chunk should carry its parent heading chain.
This directly affects the `Chunk.metadata` shape, which affects downstream Epic
3 and Epic 4 work. Leaving this open means the chunker implementor will make an
arbitrary decision that downstream epics will have to work around.

**Suggested improvement:** Resolve the question. Recommended decision: "Store
heading context as `metadata['headings']: list[str]`, e.g.
`['Chapter 1', 'Section 1.2']`. This is low cost now and expensive to add
later."

---

### 5. Embedding Client (`embeddings.py`)

#### 5.1 — `embed_texts` return type not specified
**Status: GAP | Severity: must-fix**

US-4 says "I get 500 embedding vectors, each 1024 floats." The PRD does not
specify the return type:
- `list[list[float]]`?
- `list[np.ndarray]`?
- A typed model?

Given that `Chunk.embedding` in `models.py` is typed `list[float] | None`, the
return type should be `list[list[float]]` for direct compatibility. This should
be stated explicitly.

**Suggested improvement:** State: "`embed_texts(texts: list[str]) ->
list[list[float]]`. The outer list is parallel to the input list. Each inner
list has exactly 1024 elements."

#### 5.2 — Partial batch failure behavior
**Status: PARTIAL | Severity: must-fix**

The PRD states: "failure in batch N doesn't retry batches 0..N-1." This implies
that on permanent failure of batch N (after 3 retries), the entire call raises.
But what does the caller receive? Are the successfully-embedded batches 0..N-1
returned, or is the entire result discarded on any single batch failure?

"Per-batch retry" without specifying the failure propagation contract means
implementations will diverge.

**Suggested improvement:** State explicitly: "If any batch fails permanently
after all retries, `embed_texts` raises `RuntimeError` and no partial results
are returned. The caller must re-submit the entire input after resolving the
failure."

#### 5.3 — Lazy singleton: thread safety and test isolation
**Status: GAP | Severity: should-fix**

The "lazy singleton" design pattern is described without addressing:
- Is it safe to call from multiple threads (synchronous context) or multiple
  asyncio tasks?
- How does the test suite reset the singleton between test cases? A module-level
  singleton that persists between tests will cause test isolation failures.

**Suggested improvement:** Add: "The singleton is a module-level variable
initialized to `None`. Tests reset it via `embeddings._client = None` in their
teardown, or via a provided `reset_client()` function."

#### 5.4 — Missing API key: error contract
**Status: GAP | Severity: must-fix**

`config.py` sets `voyage_api_key: str = ""` as the default. The PRD does not
specify what happens when `embed_texts` is called with an empty API key:
- Does it fail immediately at client initialization (lazy singleton creation)?
- Does it fail when the first API call is made?
- Does it raise `ValueError`, `RuntimeError`, or a voyageai-specific exception?

**Suggested improvement:** Add a constraint: "If `get_settings().voyage_api_key`
is empty at client initialization time, `embed_texts` raises `ValueError:
'VOYAGE_API_KEY not configured'` before making any API call."

#### 5.5 — Exponential backoff parameters
**Status: COVERED**

The PRD specifies the backoff schedule (1s, 2s, 4s, 3 retries). This is
testable and specific. No gap.

---

### 6. Performance Requirements

#### 6.1 — No throughput requirements for any component
**Status: GAP | Severity: should-fix**

The PRD specifies only the agent timeout (300s/180s) and the Voyage batch limit
(128). There are no performance requirements for:
- Chunker throughput (tokens/second for splitting a large document).
- Fallback extractor latency (how long is acceptable for a 500-page PDF via
  pymupdf?).
- Embedding client throughput (how many embeddings/second is acceptable?).

Without these, there is no basis for a performance regression test.

**Suggested improvement:** Add a "Performance" section in Constraints, even if
only order-of-magnitude bounds: "The chunker must process 100,000 tokens in
under 1 second on commodity hardware. The fallback PDF extractor must complete
in under 30 seconds for a 300-page document."

#### 6.2 — Agent timeout values not tied to success criteria
**Status: PARTIAL | Severity: should-fix**

The 300s and 180s timeouts are stated as facts but not as requirements with a
rationale. If a conversion agent regularly takes 280s, is that acceptable? Is
there a p95 latency expectation?

**Suggested improvement:** Clarify whether these are maximum acceptable latencies
or just sentinel values for the timeout guard. E.g.: "300s is a safety ceiling,
not a performance target. Typical agent conversion is expected to complete in
under 60s."

---

### 7. Error Handling

#### 7.1 — No standardized error contract across components
**Status: GAP | Severity: must-fix**

Open Question 5 acknowledges the inconsistency but leaves it unresolved. The
current state is:
- Reference chunker: never raises (total function).
- Agent wrapper: raises `RuntimeError`.
- Embedding client: raises `Exception` (untyped).

This inconsistency will directly affect Epic 4 pipeline error handling. The
pipeline must be able to distinguish retriable errors from fatal errors. Without
a standardized hierarchy, Epic 4 will have to special-case each component.

**Suggested improvement:** Resolve Open Question 5 before implementation begins.
Recommended approach: define a small exception hierarchy in a `exceptions.py`
module (or inline in each file):
```
IngestionError(Exception)          # base
  TransientError(IngestionError)   # safe to retry
  PermanentError(IngestionError)   # do not retry
```
Require each component to raise only from this hierarchy. The chunker should be
"total" (returns empty list on empty input, never raises) except for programming
errors (wrong argument types).

#### 7.2 — No error handling requirement for process group kill edge cases
**Status: GAP | Severity: should-fix**

US-6 specifies the happy path for timeout: "SIGKILL is sent, TimeoutError is
raised, no orphan processes remain." It does not specify:
- What if `os.killpg` itself raises (e.g., the process group already exited)?
- What if the subprocess exits between the timeout check and `killpg`?
- What if `start_new_session=True` fails on the target OS?

**Suggested improvement:** Add: "If the process group no longer exists when
`killpg` is called, the `ProcessLookupError` is silently swallowed.
`TimeoutError` is still raised."

---

### 8. Implicit Requirements Not Stated

#### 8.1 — `disclosure_doc_id` population responsibility
**Status: GAP | Severity: must-fix**

The `Chunk` model (from `models.py`) has `disclosure_doc_id: str` as a required
field. The chunker is described as a standalone library component. Who sets
`disclosure_doc_id` before chunks are persisted? If the chunker doesn't, the PRD
must state this explicitly so Epic 4 knows it is responsible. Currently this is
unaddressed — see also Finding 4.1.

#### 8.2 — File encoding handling for EPUB
**Status: GAP | Severity: should-fix**

EPUB files contain HTML/XML with mixed encodings. The PRD does not state whether
the converter is responsible for normalizing encoding. `ebooklib` handles this
internally for most cases, but the PRD should state: "The converter returns
UTF-8 encoded strings. Encoding errors in source files are logged and the
affected item is skipped."

#### 8.3 — Input validation for all public APIs
**Status: GAP | Severity: should-fix**

No component specifies input validation behavior:
- What does `convert_to_markdown` do if the file does not exist?
- What does `chunk_markdown` do with an empty string input?
- What does `embed_texts` do with an empty list input?
- What does `run_agent` do with an empty prompt string?

Each of these must have a defined contract for tests to be written against them.

**Suggested improvement:** Add an "Input Validation" constraint: "All public
functions validate their primary input on entry and raise `ValueError` for
invalid inputs (empty string, non-existent path, negative `target_size`)."

#### 8.4 — Logging requirements
**Status: GAP | Severity: should-fix**

None of the components specify logging requirements. Given that the agent wrapper
and embedding client involve external calls, operational observability requires
at minimum:
- Log the agent invocation command at DEBUG.
- Log batch embedding calls (batch index, size) at DEBUG.
- Log fallback activation in the converter at WARNING.

**Suggested improvement:** Add a logging constraint: "All components use the
standard `logging` module under the `pointy_rag` logger hierarchy. No component
writes to stdout/stderr directly."

#### 8.5 — `pyproject.toml` dependency versioning for new packages
**Status: PARTIAL | Severity: should-fix**

The PRD lists four new dependencies with minimum versions. It does not specify
whether these are hard lower bounds (for security/API reasons) or soft
preferences. `pymupdf>=1.24.0` is likely security-motivated (older versions have
known vulnerabilities). This should be stated so reviewers know not to relax
the bound.

---

### 9. Test Requirements

#### 9.1 — Test coverage level not specified
**Status: GAP | Severity: should-fix**

Goal 5 states "pytest unit tests using mocks (no live services required)." The
PRD does not specify:
- What coverage percentage is required?
- Are edge cases (empty input, timeout, missing API key) required to be tested?
- Are the test files required to use the existing `conftest.py`, or can each add
  new fixtures?

**Suggested improvement:** Add: "Each test file must include tests for at least
the happy path (success) and two error paths (e.g., timeout, missing credential).
There is no numerical coverage target for this epic."

#### 9.2 — Test for three-pass JSON extraction not mentioned
**Status: GAP | Severity: should-fix**

The three-pass JSON extraction is a non-trivial piece of logic with three
distinct cases. The PRD does not mention that this logic must be tested. A
failure in pass 2 or pass 3 would be silent without a test.

**Suggested improvement:** Add to the agent wrapper test requirements: "Tests
must cover all three JSON extraction passes: (1) output is valid JSON directly,
(2) output contains a fenced JSON block, (3) output contains a bare `{...}` with
surrounding noise."

---

## Open Questions Resolution Priority

The following Open Questions block implementation and must be resolved before
work begins:

| Question | Blocks | Priority |
|---|---|---|
| OQ-2: `--allowedTools` for Claude | Agent wrapper implementation | **must-fix before start** |
| OQ-1: MCP config needed? | Agent wrapper implementation | **must-fix before start** |
| OQ-5: Error handling philosophy | All four components | **must-fix before start** |
| OQ-3: Heading context in metadata | Chunker + Epic 3/4 | should-fix before start |
| OQ-4: Credential source | Embedding client | Low — `get_settings()` is clearly correct; just confirm |

---

## Summary Table

| # | Component | Finding | Status | Severity |
|---|---|---|---|---|
| 1.1 | All | No component-level "done" criteria | GAP | must-fix |
| 1.2 | All | No mypy/linting acceptance gate | GAP | should-fix |
| 2.1 | Agent | Return type of `run_agent` not specified | GAP | must-fix |
| 2.2 | Agent | JSON extraction failure contract missing | PARTIAL | must-fix |
| 2.3 | Agent | Subprocess stderr/stdout handling unspecified | GAP | should-fix |
| 2.4 | Agent | Open Questions 1+2 block implementation | GAP | must-fix |
| 3.1 | Converter | Return type of `convert_to_markdown` not specified | GAP | must-fix |
| 3.2 | Converter | Silent vs. explicit fallback on agent failure | GAP | must-fix |
| 3.3 | Converter | Fallback quality floor not testable | PARTIAL | should-fix |
| 3.4 | Converter | Corrupt/unsupported file behavior | GAP | should-fix |
| 4.1 | Chunker | `Chunk.disclosure_doc_id` population unresolved | PARTIAL | must-fix |
| 4.2 | Chunker | Chunk size tolerance not specified | PARTIAL | must-fix |
| 4.3 | Chunker | Overlap at heading boundaries unspecified | GAP | should-fix |
| 4.4 | Chunker | Heading context metadata: open question blocks design | GAP | should-fix |
| 5.1 | Embeddings | Return type of `embed_texts` not specified | GAP | must-fix |
| 5.2 | Embeddings | Partial batch failure behavior | PARTIAL | must-fix |
| 5.3 | Embeddings | Singleton test isolation unaddressed | GAP | should-fix |
| 5.4 | Embeddings | Missing API key error contract | GAP | must-fix |
| 5.5 | Embeddings | Backoff parameters | COVERED | — |
| 6.1 | All | No throughput/latency requirements | GAP | should-fix |
| 6.2 | Agent | Timeout values not tied to success criteria | PARTIAL | should-fix |
| 7.1 | All | No standardized error contract | GAP | must-fix |
| 7.2 | Agent | `killpg` edge cases unspecified | GAP | should-fix |
| 8.1 | Chunker | `disclosure_doc_id` population responsibility | GAP | must-fix |
| 8.2 | Converter | File encoding handling for EPUB | GAP | should-fix |
| 8.3 | All | Input validation for all public APIs | GAP | should-fix |
| 8.4 | All | Logging requirements | GAP | should-fix |
| 8.5 | Build | Dependency version rationale | PARTIAL | should-fix |
| 9.1 | Tests | Coverage level not specified | GAP | should-fix |
| 9.2 | Tests | Three-pass JSON extraction not in test requirements | GAP | should-fix |

**must-fix gaps:** 12
**should-fix gaps:** 17
**COVERED:** 1
