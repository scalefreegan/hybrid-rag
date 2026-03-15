# Scope Analysis: Epic 2 — Ingestion Components

**Reviewed by:** Scope Analyst
**Date:** 2026-03-14
**PRD:** `.prd-reviews/epic2-ingestion/prd-draft.md`

---

## Overall Assessment

The MVP boundary is reasonably well-drawn. Four independent components, no
pipeline wiring, no new CLI surface — that framing is disciplined. However,
several areas carry meaningful scope risk: the agent wrapper has unclear
boundaries around MCP configuration, the converter's dual-path design adds
hidden complexity, and three open questions directly touch the core data model
in ways that could force rework if left unresolved. The non-goals are good but
incomplete in one important area.

---

## Scope Concerns

---

### SC-01: Open Question on Heading Context in Chunk Metadata (OQ-3)

**Classification:** must-fix
**Recommendation:** DECIDE NOW (resolve before implementation, do not defer)

Open question 3 asks whether each `Chunk` should carry its parent heading chain.
This is not a cosmetic question — it changes the shape of the data model and
therefore every downstream consumer (Epic 3 disclosure builder, Epic 4 pipeline,
eventual retrieval queries). If a polecat builds the chunker without heading
context and the answer later comes back "yes, we need it," the chunker, the
`Chunk` model, the DB schema, and the embedding pipeline all need to change.

The cost of adding a nullable `heading_path: list[str] | None` field now is
near-zero. The cost of retrofitting it after Epic 3 is in progress is high.
This must be decided and locked in the PRD before implementation starts.

---

### SC-02: Agent Wrapper MCP Config Scope (OQ-1 and OQ-2)

**Classification:** must-fix
**Recommendation:** CUT MCP config from this epic; SIMPLIFY to plain prompt + file args

Open question 1 asks whether the conversion agent needs MCP tools. Open
question 2 asks which `--allowedTools` flags to pass. These questions are
sitting inside an open question box, which means the scope of the agent wrapper
is not actually defined.

The reference implementation mentioned in the PRD "builds an MCP config for
agent-trade tools." If that path is followed for document conversion, the agent
wrapper grows to include MCP config construction, tool whitelisting logic, and
the associated test surface. That is a significant scope expansion for a
component whose stated purpose is a thin subprocess wrapper.

The PRD should resolve this explicitly: for document conversion, a plain prompt
with file content passed via stdin or `--file` argument is sufficient. MCP tool
access for the conversion agent should be cut. If the disclosure builder (Epic 3)
needs MCP tools, that configuration belongs in Epic 3, not in the generic wrapper.

---

### SC-03: Error Handling Standardization (OQ-5)

**Classification:** should-fix
**Recommendation:** DECIDE NOW; policy can be simple, but it must be uniform

Open question 5 notes that the reference implementations have three inconsistent
error philosophies across four components. Leaving this open means each polecat
building a component will make their own call, and the result will be
inconsistent error behavior that every caller has to handle differently.

This is a design decision, not an implementation task. It takes one sentence to
resolve: e.g., "All components raise typed exceptions derived from a base
`PointyRagError`; none return sentinel values or silently swallow errors." The
PRD should close this before polecats start.

The "total function" approach (chunker never raises) is particularly problematic
— a chunker that silently returns garbage on malformed input will produce
confusing downstream failures in the embedding and storage stages.

---

### SC-04: Three-Pass JSON Extraction in Agent Wrapper

**Classification:** should-fix
**Recommendation:** SIMPLIFY

The PRD specifies "three-pass JSON extraction: direct parse → fenced block →
first `{` scan." The third pass (scan for first `{`) is fragile and
gold-plating-adjacent. If Claude's output does not contain a fenced JSON block
and is not directly parseable, scanning for a raw `{` character is likely to
produce partially-parsed noise rather than a valid result.

The MVP contract should be: Claude outputs a fenced JSON block. If it does not,
the wrapper raises a `ParseError`. The three-pass fallback obscures failure
modes that should be surfaced. Cut pass three; keep passes one and two.

---

### SC-05: Configurable Timeouts vs. Hardcoded Values

**Classification:** should-fix
**Recommendation:** SIMPLIFY

The Constraints section lists two specific timeout values: 300 seconds for
conversion and 180 seconds for disclosure (Epic 3). It also says "the wrapper
should support configurable timeouts," which is correct. However, noting the
Epic 3 disclosure timeout in an Epic 2 PRD bleeds scope — it implies the wrapper
must be designed with Epic 3's requirements in mind now, before Epic 3 is
specced.

Simplify the constraint: the wrapper accepts a `timeout` parameter with a
default of 300 seconds. The 180-second value for disclosure is an Epic 3
concern and should not appear in this PRD.

---

### SC-06: Fallback Extractor Markdown Quality Guarantee

**Classification:** should-fix
**Recommendation:** SIMPLIFY the acceptance criterion

US-2 says the fallback path produces "usable markdown (possibly lower quality)."
US-1 says the primary path "preserves heading hierarchy and all content." These
are asymmetric quality expectations, which is reasonable — but the PRD does not
define what "usable" means for the fallback path.

Without a minimum bar, the fallback implementation could produce a flat blob of
text with no structure and still satisfy the story. If downstream chunking relies
on heading markers (`##`, `###`) to split meaningfully, a fallback that strips
all structure defeats the chunker's primary splitting strategy.

The PRD should specify a minimum: "fallback output preserves paragraph breaks;
heading detection is best-effort." This prevents the fallback from being
implemented so minimally that it undermines the chunker.

---

### SC-07: EPUB HTML Parsing Scope (beautifulsoup4 dependency)

**Classification:** should-fix
**Recommendation:** DEFER or explicitly scope-limit

The dependency list includes `beautifulsoup4>=4.12.0` for HTML parsing of EPUB
content. EPUB files are ZIP archives of XHTML; ebooklib exposes the raw HTML.
Parsing that HTML into clean markdown is a non-trivial transformation: handling
tables, nested lists, inline formatting, image alt text, footnotes.

The PRD does not describe how much of this transformation the fallback path is
expected to handle. If the acceptance criterion is "strip tags, preserve
paragraph breaks," that is a small task. If the expectation is "produce
structured markdown with working heading hierarchy from arbitrary EPUB HTML,"
that is a substantial chunk of work.

This should be scoped explicitly. Recommended position: EPUB fallback strips
tags, preserves paragraph text, makes no guarantee about heading structure.
BeautifulSoup is then a thin utility call, not a full conversion pipeline.

---

### SC-08: Non-Goals Missing — Incremental / Idempotent Ingestion

**Classification:** should-fix
**Recommendation:** ADD to non-goals

The non-goals do not address whether re-ingesting the same document should be
idempotent (skip if already present, replace, or append). This question will
arise during Epic 4 pipeline work, and someone will look back at this PRD for
guidance. The components built in Epic 2 will influence whether idempotency is
easy or hard to add.

Add to non-goals: "Idempotency / deduplication: detecting or skipping
previously-ingested documents is Epic 4 pipeline logic, not the responsibility
of these components."

---

### SC-09: Non-Goals Missing — Structured Logging / Observability

**Classification:** should-fix
**Recommendation:** ADD to non-goals

None of the component descriptions mention logging. Long-running agent
subprocess calls (up to 300s) with no progress signal are operationally opaque.
There is a risk polecats will add verbose logging, progress callbacks, or
structured event emission to make the wrapper "production-ready."

Add to non-goals: "Structured logging, progress callbacks, and observability
instrumentation are deferred. Components may emit plain `print()` or `logging`
debug output, but no structured event system is in scope."

---

### SC-10: Voyage API Credential Validation on Startup

**Classification:** should-fix
**Recommendation:** CUT

OQ-4 asks about the credential source and confirms `get_settings().voyage_api_key`
is the right approach. The lazy singleton pattern means credentials are not
validated until first use. There is a risk someone adds startup validation
(check the key is non-empty, make a test API call) to "fail fast." That is scope
creep.

The PRD should explicitly say: credential validation is not in scope. The client
raises `ValueError` if the key is empty when the singleton is initialized; it
does not make a live validation call.

---

### SC-11: Batch Retry Granularity (Per-Batch vs. Per-Item)

**Classification:** should-fix
**Recommendation:** SIMPLIFY; lock the decision explicitly

The PRD states "failure in batch N doesn't retry batches 0..N-1." This is the
right call. However, it is silent on per-item retry within a failed batch. If a
single text in a batch of 128 causes a 400 error, does the whole batch fail? Is
the caller expected to bisect?

For MVP: yes, the whole batch fails and raises. Per-item retry or bisection is a
future optimization. This should be stated explicitly to prevent a polecat from
implementing bisection logic as a "nice to have."

---

## Summary Table

| ID    | Area                              | Recommendation          | Classification |
|-------|-----------------------------------|-------------------------|----------------|
| SC-01 | Heading context in chunk metadata | DECIDE NOW              | must-fix       |
| SC-02 | Agent wrapper MCP config scope    | CUT MCP from this epic  | must-fix       |
| SC-03 | Error handling standardization    | DECIDE NOW              | must-fix       |
| SC-04 | Three-pass JSON extraction        | SIMPLIFY (cut pass 3)   | should-fix     |
| SC-05 | Epic 3 timeout in Epic 2 PRD      | SIMPLIFY                | should-fix     |
| SC-06 | Fallback markdown quality bar     | SIMPLIFY                | should-fix     |
| SC-07 | EPUB HTML parsing scope           | SIMPLIFY or DEFER       | should-fix     |
| SC-08 | Non-goal: idempotency             | ADD to non-goals        | should-fix     |
| SC-09 | Non-goal: observability           | ADD to non-goals        | should-fix     |
| SC-10 | Credential validation on startup  | CUT                     | should-fix     |
| SC-11 | Per-batch vs. per-item retry      | SIMPLIFY; state clearly | should-fix     |

---

## What Could Be Deferred Without Impacting Core Functionality

The following are in-scope per the PRD but would not block any Epic 3 or Epic 4
integration if removed from this epic:

- **EPUB fallback path** — If the primary use case is PDFs, EPUB fallback
  (and the ebooklib + beautifulsoup4 dependencies) could move to Epic 4 or a
  separate ticket. The converter would accept only PDF in the fallback path.
- **Three-pass JSON extraction (pass 3)** — As noted in SC-04, this is
  fragile scope. The cost of cutting it is low; the risk of keeping it is subtle.
- **Exponential backoff in embedding client** — A simpler immediate-retry-once
  policy covers most transient failures. Full exponential backoff with 3 retries
  is a production concern that could be added in Epic 4 polish.

---

## Gold-Plating Risk Areas

1. **Agent wrapper process management** — Process group kill + session isolation
   is the right call, but there is a risk of over-engineering signal handling
   (SIGTERM before SIGKILL grace period, cleanup callbacks, etc.). The PRD
   should state: send SIGKILL directly, no grace period, no cleanup hooks.

2. **Chunker overlap semantics** — "Overlap provides context continuity" is
   vague. Someone may implement overlapping at the token level with exact
   alignment logic. The PRD should specify: overlap is approximate (line
   boundary), computed by the heuristic token counter. Exact token-boundary
   overlap is not required.

3. **Embedding client singleton thread safety** — The lazy singleton will
   attract thread-safety concerns (double-checked locking, etc.) in a
   hypothetically concurrent context. The PRD should state: thread safety of
   the singleton is out of scope for this epic; callers are assumed
   single-threaded or responsible for their own locking.
