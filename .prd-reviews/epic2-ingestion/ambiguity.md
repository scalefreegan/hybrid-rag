# Ambiguity Analysis: Epic 2 — Ingestion Components

**Reviewer role:** Ambiguity Analyst
**Date:** 2026-03-14
**PRD:** `.prd-reviews/epic2-ingestion/prd-draft.md`

---

## Summary

The PRD is generally well-structured and specific in places, but contains several
ambiguities that could cause implementors to produce incompatible components or
waste effort on the wrong behavior. Eight must-fix issues and four should-fix
issues are identified below.

---

## Must-Fix Ambiguities

---

### MF-1: "chunks" vs. "Chunk objects" — input type to `embed_texts` is unspecified

**Problematic text (US-4):**
> "Given 500 text chunks / When I call embed_texts(chunks)"

**Problematic text (Goals §4):**
> "Generate 1024-dimensional vector embeddings via Voyage AI"

**Problem:** The user story passes `chunks` to `embed_texts`, but does not say
whether `chunks` is a `list[str]` (raw text) or a `list[Chunk]` (model objects
from `models.py`). The `Chunk` model has a `content: str` field and an
`embedding: list[float] | None` field. If the function accepts `Chunk` objects,
it could return the same objects with `.embedding` populated (mutating or
copying). If it accepts `list[str]`, it returns `list[list[float]]` and the
caller is responsible for attaching embeddings to `Chunk` objects.

These are meaningfully different APIs. The US-4 acceptance criterion says "I get
500 embedding vectors, each 1024 floats," which implies `list[list[float]]` is
returned — suggesting the input might be `list[str]`. But calling the parameter
`chunks` when you mean strings is misleading.

**Interpretations:**
1. `embed_texts(texts: list[str]) -> list[list[float]]` — pure text in, raw
   vectors out; caller assembles Chunk objects.
2. `embed_texts(chunks: list[Chunk]) -> list[Chunk]` — model objects in,
   populated objects out (mutating or new).
3. `embed_texts(chunks: list[Chunk]) -> list[list[float]]` — model objects in,
   raw vectors out (confusing).

**Suggested fix:** Replace "500 text chunks" with the exact Python type. Specify
whether the function is a pure text utility or a Chunk-aware operation. Given
the "component independence" principle (no imports between components), option 1
(`list[str] -> list[list[float]]`) is most consistent with the design — but the
PRD should say so explicitly.

---

### MF-2: `convert_to_markdown` signature — `output_dir` vs. `output_path`

**Problematic text (US-1):**
> "When I call convert_to_markdown("/docs/annual-report.pdf", "/tmp/output/")"
> "Then I get a tuple (markdown_text, output_path)"

**Problematic text (US-2):**
> "When I call convert_to_markdown(path, output_dir, use_agent=False)"

**Problem:** The second positional argument is called `output_dir` in the
function signature shown in US-2, but the user story implies the function
returns an `output_path` (a specific file path, not a directory). It is unclear
whether the implementor is expected to:

1. Accept a directory and choose a filename derived from the input (e.g.,
   `annual-report.md`), returning the final resolved path.
2. Accept an exact output file path, using it as-is.
3. Accept an optional output destination (could be None to skip writing).

This matters because: if the agent writes the file itself, the function might not
need `output_dir` at all. If the fallback extractor writes the file, the function
needs to know where. Whether writing is mandatory or optional is also unspecified.

**Suggested fix:** Define the full signature: parameter name, type, whether
optional, and whether the function always writes to disk or can return
markdown-only. Clarify the filename derivation rule.

---

### MF-3: "Claude CLI is unavailable" vs. `use_agent=False` — two different trigger conditions

**Problematic text (Goals §2):**
> "agent-powered conversion as primary path and deterministic fallback extractors
> (pymupdf/ebooklib) when Claude CLI is unavailable"

**Problematic text (US-2):**
> "When I call convert_to_markdown(path, output_dir, use_agent=False)"

**Problem:** The Goals section says the fallback fires when Claude CLI is
unavailable (an environmental condition, detected at runtime). US-2 says the
fallback fires when the caller passes `use_agent=False` (an explicit caller
choice). These are two different things:

- Auto-detection: The function probes for `claude` on PATH and falls back
  silently.
- Explicit override: The function always tries the agent unless told not to.

A developer who reads only the Goals section will implement automatic fallback
with no `use_agent` parameter. A developer who reads only US-2 will implement
an explicit flag that does not auto-detect anything. It is also unclear whether
auto-detection and the `use_agent` flag can both exist — what happens if
`use_agent=True` but Claude CLI is not on PATH? Should it raise an error or
still fall back?

**Suggested fix:** Decide and state: (a) is auto-detection supported, (b) is
`use_agent` a flag that overrides detection, and (c) what exception (if any) is
raised when `use_agent=True` but Claude CLI is not found.

---

### MF-4: "chunks split primarily on heading boundaries" — heading levels are unspecified

**Problematic text (US-3):**
> "chunks split primarily on heading boundaries"

**Problematic text (Non-Goals):**
> "We use line-boundary sliding window with heading-aware splitting."

**Problematic text (Rough Approach §4):**
> "split on headings first, then subdivide large sections with line-level overlap"

**Problem:** The PRD mentions headings throughout but never specifies which
heading levels trigger splits. `## Section` (H2) and `### Subsection` (H3) are
mentioned in US-3 as example content, but the chunker behavior at H1, H4, H5,
H6 is not defined. Two legitimate interpretations:

1. All heading levels (`#` through `######`) are split boundaries.
2. Only major headings (H1 and H2) are split boundaries; lower levels are
   treated as body text.

This is not trivial — for a long book, splitting on every H4/H5 would produce
thousands of tiny chunks that ignore the target size entirely.

Additionally, "heading-aware splitting" appears in Non-Goals to mean the
algorithm is NOT full semantic chunking, but in the body it is used as a feature
name. The term is used in two opposing contexts.

**Suggested fix:** State explicitly which heading levels act as mandatory split
boundaries vs. soft hints. State what happens when a single section exceeds
`target_size` with no sub-headings.

---

### MF-5: Overlap semantics — token count or line count?

**Problematic text (US-3):**
> "overlap provides context continuity between chunks"

**Problematic text (Non-Goals):**
> "We use line-boundary sliding window with heading-aware splitting"

**Problematic text (Rough Approach §4):**
> "subdivide large sections with line-level overlap"

**Problem:** The `overlap` parameter in `chunk_markdown(text, target_size=1500,
overlap=200)` is shown with a numeric value of 200. The token counter is
described as "~4 chars/token." But the approach says "line-level overlap." These
are irreconcilable unless clarified:

- If overlap is measured in tokens (heuristic), 200 overlap tokens means ~800
  characters of repeated content at chunk boundaries.
- If overlap is measured in lines, a value of 200 would mean 200 lines of
  repeated content — likely unintended and inconsistently named.
- "Line-boundary sliding window" could mean the window slides by lines (not
  tokens), which would make the `overlap` parameter a line count, not a token
  count.

The mismatch between the parameter name/value (looks like tokens) and the
algorithm description (line-level) is a direct contradiction.

**Suggested fix:** State the unit for `target_size` and `overlap` unambiguously.
If both are token estimates, say so. If the algorithm uses lines internally but
exposes a token-count API, explain the conversion.

---

### MF-6: Agent wrapper return type — "parsed dict result" vs. structured output

**Problematic text (US-5):**
> "And I get a parsed dict result"

**Problematic text (Rough Approach §3):**
> "Three-pass JSON extraction — direct parse → fenced block → first `{` scan"

**Problem:** The return type of `run_agent()` is described as "a parsed dict
result." But the three-pass JSON extraction is described as a design decision,
implying Claude's stdout is expected to contain JSON. This leaves undefined:

1. What happens when all three JSON extraction passes fail? Does `run_agent`
   raise? Return `None`? Return the raw string?
2. What schema does the returned dict have? The converter uses the agent to
   produce markdown — is the expected JSON `{"markdown": "..."}` or something
   else? Without a schema, callers cannot write correct code.
3. Does the agent wrapper enforce a JSON output contract (e.g., by injecting
   "respond only in JSON" into the prompt), or is JSON extraction opportunistic?

**Suggested fix:** Define the failure behavior explicitly (raise `ValueError`?
return `None`?). Either specify the dict schema or clarify that the wrapper is
schema-agnostic and callers interpret the dict. Document whether JSON output is
enforced via prompt engineering.

---

### MF-7: "Claude Code as a subprocess" — which `claude` binary?

**Problematic text (Goals §1):**
> "spawn headless Claude Code as a subprocess"

**Problematic text (Open Questions §2):**
> "Converter: What `--allowedTools` for Claude?"

**Problem:** The PRD refers to "Claude Code" (the IDE/CLI product) and "Claude
CLI" interchangeably, but these may refer to different executables depending on
installation method. The questions about `--allowedTools` suggest the binary is
`claude` (the Claude Code CLI), not a generic API client. However:

1. "Claude CLI" (`claude`) is the Claude Code command-line interface.
2. A different binary might be expected in CI/headless environments.

The command string actually invoked is never stated. This matters because the
`PATH` detection logic (see MF-3) and the `--allowedTools` flag both depend on
knowing which binary is called. An implementor who assumes `claude` could be
wrong if the project uses `claude-code` or a wrapper script.

**Suggested fix:** State the exact binary name (`claude`), the minimum version
required, and the base command template (e.g.,
`claude --output-format json --print <prompt>`).

---

### MF-8: "component independence" contradicted by Chunk model use in chunker

**Problematic text (Rough Approach, Component Independence):**
> "All four components are independent — no imports between them. They share only
> the models from `models.py` (DocumentFormat for converter) and config from
> `config.py` (Settings for embeddings)."

**Problematic text (US-3):**
> "Then I get a list of Chunk objects, each ~1500 tokens"

**Problem:** The independence statement lists only `DocumentFormat` (from
`models.py`) as the model the converter uses. But US-3 says the chunker returns
`list[Chunk]` — meaning the chunker also imports from `models.py`. The
independence statement omits this dependency. Furthermore, if `embed_texts`
accepts `Chunk` objects (see MF-1), then the embeddings component also depends
on `models.py`. The statement "they share only ... DocumentFormat for converter"
is provably incomplete and may mislead an implementor into not importing `Chunk`
from `models.py` for the chunker.

**Suggested fix:** Enumerate all `models.py` symbols each component uses:
converter uses `DocumentFormat`, chunker uses `Chunk`, embeddings component
uses whatever is decided in MF-1.

---

## Should-Fix Ambiguities

---

### SF-1: Fallback quality — "possibly lower quality" is not testable

**Problematic text (US-2):**
> "And I still get usable markdown (possibly lower quality)"

**Problem:** "Usable" and "lower quality" are subjective. The acceptance
criterion cannot be evaluated in a pytest test. There is no definition of what
constitutes a failed conversion (empty output? no headings detected? below some
character count?) vs. a low-quality-but-acceptable result. This affects error
handling design: should the fallback raise if it produces output below a quality
threshold, or always return whatever it extracted?

**Suggested fix:** Replace with an objective criterion: "returns non-empty
markdown string" or "returns markdown with at least one paragraph of text."

---

### SF-2: "Batched with per-batch retry" — ambiguous retry scope

**Problematic text (Rough Approach §6):**
> "Batched with per-batch retry — failure in batch N doesn't retry batches
> 0..N-1. Exponential backoff: 1s, 2s, 4s (3 retries)."

**Problem:** "3 retries" with delays "1s, 2s, 4s" describes the backoff
sequence, but it is ambiguous whether:

1. The client retries the same batch up to 3 times before raising (total 4
   attempts per batch).
2. The client retries the same batch exactly 3 times, with the 3rd failure
   propagating immediately (no 4th attempt).

Also, "failure in batch N doesn't retry batches 0..N-1" is clear about not
re-doing past work, but it does not say what happens to batches N+1..end. Does a
single non-retryable failure abort all remaining batches, or does it continue and
return partial results?

**Suggested fix:** State total attempt count per batch (e.g., "up to 4 attempts:
initial + 3 retries"). State the behavior on permanent batch failure: raise
immediately, or collect all successful batches and raise at the end.

---

### SF-3: `target_size` — is it a hard limit or a soft target?

**Problematic text (US-3):**
> "Then I get a list of Chunk objects, each ~1500 tokens"

**Problematic text (Goals §3):**
> "configurable target size and overlap"

**Problem:** The tilde (`~`) in "each ~1500 tokens" indicates approximate size,
but it is not stated how much deviation is acceptable. A heading-aware chunker
that refuses to split within a section could produce a chunk of 3000 tokens if
the section itself is 3000 tokens. Is this acceptable? Should the chunker always
produce chunks "close to" `target_size`, or is `target_size` strictly a maximum?
The word "target" implies best-effort, but the use case (embedding a 1024-dim
model that likely has an input token limit) may require a hard cap.

**Suggested fix:** State whether `target_size` is a maximum hard limit or a
best-effort target. If best-effort, state the acceptable overshoot (e.g., "may
exceed target_size by up to 25% at heading boundaries").

---

### SF-4: "Lazy singleton client" — singleton scope is unspecified

**Problematic text (Rough Approach §5):**
> "Embedding client uses lazy singleton — initialized on first use, credential
> loaded from `get_settings()`."

**Problem:** "Singleton" is ambiguous in Python. It could mean:

1. A module-level global variable initialized on first call (process-level
   singleton).
2. A `functools.lru_cache`-decorated factory function.
3. A class-level attribute.

The scope matters for testing: a process-level singleton will leak between tests
unless explicitly reset. If tests mock `get_settings()` to inject a test API
key, a cached singleton initialized before the mock is applied will use the
wrong credentials. The PRD says "no live services required for testing" (Goal 5),
but a carelessly-scoped singleton can make this goal impossible to satisfy
without patching module internals.

**Suggested fix:** State the singleton mechanism (e.g., "module-level `_client`
variable, reset to `None` between tests via a `reset_client()` helper or by
patching the module global") and note how tests should reset it.

---

## Cross-Cutting Contradictions

### Contradiction A: Python >= 3.11 constraint vs. `.venv` running 3.12

**PRD (Constraints):**
> "Python >= 3.11 (project requirement from pyproject.toml)"

**Observed (config.py, models.py):**
The existing codebase uses `from datetime import UTC` (added in 3.11) and union
type syntax `str | None` (3.10+). The installed `.venv` uses Python 3.12
(per venv path observed in glob output). There is no contradiction in the code,
but the PRD constraint is stated as a lower bound while the team is actually
operating on 3.12. New code that happens to use 3.12-only features (e.g.,
`typing.override`, `@dataclass(slots=True)` improvements) will not be caught by
the stated constraint. This is a latent risk, not a current error.

**Suggested fix:** Either tighten the constraint to `Python >= 3.12` to reflect
actual usage, or add a CI matrix test against 3.11 to ensure the bound is real.

---

### Contradiction B: Non-Goals say "no new CLI commands" but `cli.py` has an `ingest` stub

**PRD (Constraints):**
> "No new CLI commands in this epic — these are library components only"

**Existing code (`cli.py` line 56-68):**
An `ingest` command already exists as a stub. The constraint says this epic
should not add CLI commands, but does not say whether the existing `ingest` stub
should be wired to the new library components as part of this epic. An
implementor who interprets "library components only" strictly will leave the
stub untouched. An implementor who interprets the spirit of the epic will expect
the stub to eventually call `convert_to_markdown` + `chunk_markdown` +
`embed_texts` and wonder if that wiring belongs here or in Epic 4.

**Suggested fix:** Clarify: "The existing `ingest` CLI stub must not be wired
up in this epic. Pipeline orchestration (including wiring the CLI to these
components) is Epic 4."

---

## Open Questions That Create Ambiguity

The PRD's Open Questions section (questions 1–5) are not merely "TBD" items —
several of them gate core design decisions:

- **OQ-5 (Error handling philosophy)** directly affects whether the chunker
  raises on bad input, whether `run_agent` raises on JSON parse failure, and
  whether `embed_texts` raises or returns partial results. This is referenced
  in MF-6 and SF-2 above and must be resolved before implementation, not after.

- **OQ-3 (Heading context in chunk metadata)** affects the `Chunk` model's
  `metadata` dict usage — if heading hierarchy is stored there, tests and callers
  need to know the key names. This is referenced in MF-4 above.

These open questions should be promoted to must-resolve items gating the
implementation tickets, not left as background context.
