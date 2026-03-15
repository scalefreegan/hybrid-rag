# Technical Feasibility Analysis: Epic 2 — Ingestion Components

**Reviewer role:** Technical Feasibility Analyst
**Date:** 2026-03-14
**Subject:** PRD at `.prd-reviews/epic2-ingestion/prd-draft.md`

---

## Summary Verdict

The overall architecture is sound. The four components are genuinely independent, the dependency choices are reasonable, and the async/subprocess approach for the agent wrapper is the correct pattern for spawning Claude Code headlessly. However, there are several concrete technical problems that will cause silent failures or runtime errors if not addressed before implementation begins. Two of these are blockers.

---

## Concerns

---

### F-01: `Chunk` model is incompatible with `chunk_markdown` signature

**Classification:** must-fix

**Risk:** The `Chunk` model in `models.py` has a required field `disclosure_doc_id: str = Field(min_length=1)` with no default. The PRD specifies that `chunk_markdown(text, target_size=1500, overlap=200)` returns `list[Chunk]`, but the function signature carries no `disclosure_doc_id` parameter. A `disclosure_doc_id` is a database foreign key assigned by the pipeline layer (Epic 4), not by a text-processing function. Attempting to construct `Chunk(disclosure_doc_id=???)` inside the chunker will raise a `ValidationError` with no value to supply.

**Likelihood:** Certain. This is a type-system contradiction. The chunker cannot construct valid `Chunk` objects with the current model.

**Suggested mitigation (pick one):**
1. Make `disclosure_doc_id` optional in the model: `disclosure_doc_id: str | None = None`. This is consistent with the `embedding: list[float] | None = None` pattern already present.
2. Have `chunk_markdown` accept `disclosure_doc_id: str | None = None` and pass it through.
3. Return `list[str]` (raw content) from `chunk_markdown` and let Epic 4 construct `Chunk` objects. This is the cleanest separation of concerns.

Option 3 is the strongest: a text chunker has no business knowing about database IDs. The PRD should be clarified on which approach is intended.

---

### F-02: `pymupdf` import name changed at version 1.24

**Classification:** must-fix

**Risk:** The PRD specifies `pymupdf>=1.24.0` as the dependency. However, prior to 1.24, the package was imported as `import fitz`. Starting with 1.24, the package supports `import pymupdf` directly. If the fallback extractor code is written with `import pymupdf` and then pinned or installed at 1.23.x (or if an older system install shadows the venv), the import will fail at runtime. Conversely, if the code uses `import fitz` (the old name), it will still work on 1.24+ because `fitz` remains a compatibility shim — but it is not guaranteed to remain one forever.

**Likelihood:** Moderate. The version pin `>=1.24.0` is correct and the venv Python is 3.12 which will resolve to 1.24+. The risk materializes if someone tests against a system Python or a pre-existing venv with 1.23.

**Suggested mitigation:** Use `import pymupdf` (not `import fitz`) in all code since the pin is `>=1.24.0`. Add a comment documenting that this requires 1.24+. The version constraint already handles this correctly if always installed fresh.

---

### F-03: Voyage AI batch limit is 1000, not 128 — the constraint in the PRD is stale

**Classification:** should-fix

**Risk:** The PRD states "Voyage API batch limit: 128 texts per request (API hard limit)" and designs the embedding client around batches of 128. As of 2026, the `voyage-4-lite` model accepts up to 1,000 texts per request (with a 1-million-token total cap per batch). Batching at 128 is overly conservative and will make 500-chunk ingestion jobs 7x slower than necessary, but it will not cause failures.

**Likelihood:** The 128 figure may be inherited from an older model's limit (voyage-2 era) or from a reference implementation targeting a different model. It is incorrect for `voyage-4-lite`.

**Suggested mitigation:** Update the constraint to reflect the actual `voyage-4-lite` limit of 1000 texts per request. Use a configurable `batch_size` with a sensible default of 128 if conservative batching is desired for cost control, but do not document 128 as an API hard limit. The implementation should enforce `batch_size <= 1000` not `batch_size <= 128`.

---

### F-04: `ebooklib` depends on `six` — a Python 2/3 compatibility shim

**Classification:** should-fix

**Risk:** `ebooklib>=0.18` has a transitive dependency on `six`, the Python 2/3 compatibility library. The `six` library is unmaintained and exists solely to support code that must run on both Python 2 and 3. Including it in a Python 3.11+ project is harmless but signals that `ebooklib`'s internals use patterns that may not be actively maintained. More concretely, `ebooklib` also pulls in `lxml`, a C extension that requires `libxml2` and `libxslt` system libraries.

**Likelihood of failure:** Low on macOS (Xcode command line tools and Homebrew typically provide these). Moderate in CI or minimal Linux containers that lack `libxml2-dev` and `libxslt1-dev`.

**Suggested mitigation:** Pin `ebooklib>=0.18,<0.21` to avoid picking up a hypothetical future breaking release. Document that CI images must have `libxml2-dev libxslt1-dev` installed, or add a note in the project README. The `six` dependency is not worth eliminating — the project is pure-Python and `six` introduces no incompatibilities.

---

### F-05: `voyageai>=0.3.0` is underspecified — patch releases have changed the API

**Classification:** should-fix

**Risk:** The PyPI history shows `voyageai` went through releases `0.3.0` through `0.3.7` in a short period. The 0.3.x line introduced the `voyageai.Client` class replacing the older module-level function API. If the implementation targets `voyageai.Client(api_key=...).embed(...)` as in 0.3.x, pinning `>=0.3.0` is correct. However, if there is a future 0.4.x release that removes or renames `Client`, the `>=0.3.0` floor gives no upper bound protection.

**Likelihood:** Low risk today, moderate risk over the project lifetime without an upper bound.

**Suggested mitigation:** Pin `voyageai>=0.3.0,<1.0` to capture the current stable API surface. Add a comment in `pyproject.toml` noting the specific API used (`voyageai.Client.embed`). Verify with `uv lock` that the pinned version produces consistent behavior across environments.

---

### F-06: Sync `time.sleep` in embedding client will block the event loop if called from async code

**Classification:** should-fix

**Risk:** The PRD specifies that the embedding client uses synchronous `time.sleep` for exponential backoff (1s, 2s, 4s) and explicitly defers an async version. This is a valid deferral, but the agent wrapper (`claude_agent.py`) is async, and Epic 3/4 will orchestrate async pipelines. If `embed_texts()` is called from inside an `async def` function without wrapping it in `asyncio.to_thread()`, the event loop will be blocked for up to 7 seconds per failed batch. This will make timeout handling in the agent wrapper unreliable during concurrent ingestion.

**Likelihood:** Moderate. Epic 4 (pipeline orchestration) will likely call `embed_texts` from an async context. If no one adds `asyncio.to_thread` at the call site, the block will be silent but will degrade throughput and interfere with timeout timers.

**Suggested mitigation:** Add a module-level docstring to `embeddings.py` explicitly stating: "This client uses `time.sleep` and is blocking. Call from async code via `await asyncio.to_thread(embed_texts, chunks)`." Do not require Epic 4 to discover this on its own.

---

### F-07: Process group kill with `os.killpg` / `start_new_session=True` is not portable to Windows but is correct for macOS/Linux

**Classification:** should-fix (note only — no action needed for this project)

**Risk:** The PRD's process group approach (`start_new_session=True` + `os.killpg(os.getpgid(proc.pid), signal.SIGKILL)`) is the correct POSIX pattern. On macOS and Linux, `start_new_session=True` calls `setsid()` internally, which creates a new session with the child as process group leader. `os.killpg` then sends SIGKILL to every process in that group, which is exactly the right behavior for killing Claude's MCP server subprocesses.

However, `os.killpg` does not exist on Windows. Since `pyproject.toml` carries no `platform` restriction and `requires-python = ">=3.11"` says nothing about OS, there is a latent portability gap.

**Likelihood:** Near zero for this project given the Claude CLI is only available on macOS/Linux. But the code should guard explicitly.

**Suggested mitigation:** Add a guard at the top of `claude_agent.py`:
```python
import sys
if sys.platform == "win32":
    raise NotImplementedError("claude_agent requires POSIX (macOS or Linux)")
```
Or simply document the platform requirement in the module docstring. Do not attempt a Windows `TerminateProcess` fallback — it is out of scope and the Claude CLI does not run on Windows.

---

### F-08: `asyncio.timeout()` is available in Python 3.11+ — use it over `asyncio.wait_for()`

**Classification:** should-fix

**Risk:** Python 3.11 introduced `asyncio.timeout()` as a context manager (PEP 654 / `asyncio.timeout`). The older `asyncio.wait_for()` wraps a coroutine and raises `asyncio.TimeoutError` (which is `TimeoutError` in 3.11+). Both work correctly. However, `asyncio.timeout()` composes better with `try/except` blocks and cleanup logic (e.g., process group kill after timeout). Using `wait_for()` makes it harder to run cleanup code because the coroutine is cancelled, not just timed out.

The venv is Python 3.12. The project floor is Python 3.11. `asyncio.timeout()` is available on both.

**Likelihood:** Using `wait_for()` will work, but the process-kill cleanup in the `except TimeoutError` block must be written carefully with `wait_for()` since the subprocess coroutine is cancelled mid-read.

**Suggested mitigation:** Prefer `asyncio.timeout()` pattern:
```python
async with asyncio.timeout(timeout_seconds):
    stdout, stderr = await proc.communicate()
```
catching `TimeoutError` outside the context manager. This is cleaner than `wait_for()` for subprocess management and is the idiomatic Python 3.11+ pattern.

---

### F-09: Claude CLI `--output-format json` wraps output — three-pass extraction may be unnecessary

**Classification:** should-fix

**Risk:** The PRD describes a "three-pass JSON extraction" strategy: direct parse, then fenced block scan, then first-`{` scan. This is the correct defensive approach when Claude outputs free-form text. However, when invoking `claude -p --output-format json`, the CLI outputs a structured JSON envelope (`{"type": "result", "subtype": "success", "result": "...", ...}`). The actual text response is in the `result` field, not the raw stdout. If the agent wrapper uses `--output-format json`, it should parse the envelope first, extract `result`, and then parse the inner JSON — not apply the three-pass fallback to the raw envelope.

If the agent wrapper does NOT use `--output-format json` (i.e., uses `--output-format text`), then the three-pass extraction is appropriate and correct.

**Likelihood:** This is an implementation ambiguity. The PRD does not specify which `--output-format` the wrapper will use. If a developer picks `--output-format json` expecting to simplify parsing but applies the three-pass extractor to the envelope, the outer `{` scan will extract the envelope JSON, not the document JSON.

**Suggested mitigation:** The PRD's open question #1 (MCP config) touches on this. Specify explicitly in the PRD which Claude CLI invocation pattern is used:
- `--print --output-format text` + three-pass extraction on raw stdout: works, handles Claude's occasional non-JSON preamble
- `--print --output-format json` + envelope unwrap + inner JSON parse: more reliable, single code path

Recommend `--output-format json` plus envelope unwrap: `data = json.loads(stdout); inner = json.loads(data["result"])`.

---

### F-10: `voyageai.Client` construction with empty API key silently defers failure

**Classification:** should-fix

**Risk:** `config.py` returns `Settings(voyage_api_key="")` when `VOYAGE_API_KEY` is not set. The embedding client uses a lazy singleton initialized on first call. If `voyageai.Client("")` raises at construction time (rather than at the first API call), the singleton initialization will fail with an unhelpful error about an invalid API key rather than a missing environment variable. If it does not raise at construction, the error surfaces only at the first `embed()` call, possibly deep inside a batch loop.

**Likelihood:** Moderate. The `voyageai` SDK does accept an empty string at `Client("")` without raising (it validates lazily), so the error will be delayed until the first network call. This is an experience problem, not a correctness problem, but it will be confusing to debug.

**Suggested mitigation:** Add an explicit guard in the embedding client's `get_client()` or lazy initialization:
```python
settings = get_settings()
if not settings.voyage_api_key:
    raise RuntimeError("VOYAGE_API_KEY is not set. Add it to .env or the environment.")
```
This surfaces the configuration error at initialization time with a clear message.

---

### F-11: `pymupdf` 1.24+ and the `fitz` shim layer — lazy import note

**Classification:** should-fix

**Risk:** The PRD correctly specifies lazy imports ("don't import pymupdf/ebooklib/voyageai at module level"). For `pymupdf>=1.24`, the `import pymupdf` form loads the `pymupdf` package, which internally loads the `fitz` C extension. This is a single import regardless of which name is used. The lazy import constraint is important because `pymupdf` and `ebooklib` each have non-trivial import times (C extensions). However, note that `pymupdf` also imports `PIL` (Pillow) if available — this can cause surprising import side effects.

**Likelihood:** Low. The lazy import constraint handles this correctly as long as developers follow it.

**Suggested mitigation:** The existing constraint in the PRD is sufficient. Just confirm in code review that `converter.py` does not have a top-level `import pymupdf`.

---

## Compatibility Matrix

| Dependency | PRD Spec | Latest | Compatible with Py 3.11+ | Notes |
|---|---|---|---|---|
| `pymupdf` | `>=1.24.0` | `1.27.2` | Yes | Import as `pymupdf` not `fitz` |
| `ebooklib` | `>=0.18` | `0.20` | Yes | Pulls `lxml` (C ext), `six` (legacy) |
| `beautifulsoup4` | `>=4.12.0` | `4.14.3` | Yes | No concerns |
| `voyageai` | `>=0.3.0` | `0.3.7` | Yes | Add `<1.0` upper bound |

---

## Async/Sync Assessment

The mixing of async and sync code is intentional and sound:

- `claude_agent.py` (async): correct — subprocess I/O and timeout management benefit from async.
- `converter.py` (sync wrapper + optional async agent call): the converter function signature should be `async def convert_to_markdown(...)` if it calls `run_agent()`, or synchronous with `asyncio.run()` internally. The PRD is ambiguous here. Calling `asyncio.run()` inside a sync `convert_to_markdown()` will fail if there is already a running event loop (e.g., in a Jupyter notebook or async pipeline). This should be clarified.
- `chunker.py` (sync): correct — pure text processing has no I/O.
- `embeddings.py` (sync with blocking sleep): correct for its stated scope. See F-06 for the calling-context concern.

---

## Process Group Management Assessment

The `start_new_session=True` + `os.killpg(os.getpgid(proc.pid), signal.SIGKILL)` approach is correct and battle-tested for this use case. Confirmed behavior on macOS:

- `start_new_session=True` calls `setsid()`, making the child its own session leader and process group leader.
- `os.getpgid(proc.pid)` returns `proc.pid` itself (pgid equals pid for the session leader).
- `os.killpg(pgid, SIGKILL)` delivers SIGKILL to all processes in the group including any grandchild MCP servers.

The one edge case: if `proc.pid` has already exited by the time `os.getpgid(proc.pid)` is called (race between timeout and natural completion), `getpgid` raises `ProcessLookupError`. The implementation must catch this:
```python
try:
    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
except ProcessLookupError:
    pass  # Process already exited
```

This is a must-handle edge case, not a design flaw. **Classify as must-fix** in the implementation checklist for `claude_agent.py`.

---

## Must-Fix Summary

| ID | Issue | Location |
|---|---|---|
| F-01 | `Chunk.disclosure_doc_id` incompatible with `chunk_markdown` signature | `models.py` + PRD |
| F-02 | `pymupdf` import name must be `pymupdf` not `fitz` for `>=1.24.0` | `converter.py` |
| Process group race | `getpgid()` after process exit raises `ProcessLookupError` | `claude_agent.py` |

## Should-Fix Summary

| ID | Issue | Location |
|---|---|---|
| F-03 | Voyage batch limit is 1000, not 128 — update constraint | PRD constraint + `embeddings.py` |
| F-04 | `ebooklib` lxml C dependency needs CI environment note | CI config / docs |
| F-05 | `voyageai` needs upper bound `<1.0` | `pyproject.toml` |
| F-06 | Sync `time.sleep` blocks event loop — document for callers | `embeddings.py` docstring |
| F-07 | `os.killpg` not portable to Windows — add POSIX guard | `claude_agent.py` |
| F-08 | Prefer `asyncio.timeout()` over `wait_for()` for subprocess cleanup | `claude_agent.py` |
| F-09 | Specify `--output-format` choice and matching parse strategy | PRD + `claude_agent.py` |
| F-10 | Empty API key deferred error — add explicit guard at init | `embeddings.py` |
| F-11 | Confirm lazy import discipline for `pymupdf`/`ebooklib` in code review | `converter.py` |
