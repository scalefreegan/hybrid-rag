# Stakeholder Analysis: Epic 2 — Ingestion Components

## Summary

The PRD identifies a single stakeholder class — "Developers building and operating the
pointy-rag system" — and writes all five user stories from that perspective. This
identification is not wrong, but it is dangerously incomplete. The four components
being designed here are not standalone tools; they are the shared API surface that
Epic 3 and Epic 4 will build on top of. Those downstream teams are unnamed co-designers
masquerading as future consumers. Several open questions in the PRD (Q2, Q3, Q5) are
not implementation ambiguities — they are cross-epic contract decisions that belong to
the Epic 3 and Epic 4 designers to answer first.

Beyond the downstream epic teams, three additional stakeholder groups have real but
unaddressed requirements: the ops/infrastructure owner who will run these components
(and needs observability), a security function that should govern what permissions the
Claude subprocess receives, and Voyage AI as an external dependency with quota, rate,
and availability constraints that affect pipeline reliability.

The conflict most worth flagging: Epic 2 polecats are encouraged to build in parallel
with "component independence," but the unresolved open questions mean each polecat
will silently make a different API contract decision. The independence is real for
implementation but does not hold for the shared surface (error types, JSON schema,
chunk metadata shape) that Epic 3 and Epic 4 depend on.

---

## Stakeholder Findings

### Epic 3 Team (Progressive Disclosure Builder)

**Actor:** The team or polecat implementing Epic 3 ("progressive disclosure
intelligence layer," per the PRD problem statement).

**Their need:** Epic 3 directly invokes `run_agent` from `claude_agent.py` and
consumes `Chunk` objects from `chunker.py`. The agent wrapper's JSON output schema
and the chunk metadata shape are both Epic 3 design inputs, not Epic 2 implementation
details. Epic 3 will parse specific keys from the structured JSON returned by
`run_agent`; it will use chunk heading context (Open Question 3) to build disclosure
trees. Both of these need to be specified before Epic 3 can start, not after.

**Does the PRD address it?** No. The PRD lists Epic 3 only as a downstream consumer
that is "blocked." The Epic 3 team is not named as a reviewer or sign-off for the
`run_agent` output schema, the `--allowedTools` decision (Q2), or the chunk metadata
question (Q3).

**Gaps:**
- Open Question 3 (heading hierarchy in chunk metadata) is classified as a detail for
  Epic 2 polecats to resolve. It is not: it is a breaking-change constraint for Epic 3.
  If Epic 3 requires `parent_headings: list[str]` on each `Chunk` and Epic 2 ships
  without it, retrofitting requires changing the `Chunk` model (Epic 1 scope) and
  all existing chunks in the DB.
- The agent wrapper returns "a parsed dict" (US-5). Epic 3 will write code against
  specific keys in that dict. There is no schema. Any polecat who decides the dict
  shape without consulting Epic 3 creates a breaking change before Epic 3 starts.
- The disclosure agent timeout (180s) is mentioned in the Constraints section as
  "for Epic 3 use." This confirms Epic 3 has operational requirements on the wrapper,
  but Epic 3 has no voice in the PRD.

**Classification:** Must-fix. The Epic 3 designer must be consulted on Q2, Q3, and
the agent wrapper output schema before Epic 2 components are frozen.

---

### Epic 4 Team (Pipeline Orchestration)

**Actor:** The team or polecat implementing Epic 4 ("pipeline orchestration"), which
will wire all four Epic 2 components into an end-to-end ingestion flow.

**Their need:** Epic 4 needs a predictable, uniform error contract from the four
components it composes. If the embedding client raises bare `Exception`, the chunker
never raises, and the agent wrapper raises `RuntimeError`, the pipeline must contain
four different error-handling patterns around four sequential calls. That is not a
minor inconvenience — it is a structural hazard that will cause missed error
propagation.

Epic 4 also needs to reason about partial failure: what happens when batch 3 of 5
embedding batches fails permanently after all retries? The PRD specifies per-batch
retry with backoff but does not specify whether `embed_texts` raises immediately, or
returns partial results, or returns an error sentinel. The pipeline cannot be designed
without this answer.

**Does the PRD address it?** Partially. Open Question 5 acknowledges the error
contract inconsistency but defers it as a question. The PRD does not name Epic 4's
designer as someone who must answer it.

**Gaps:**
- Open Question 5 is an Epic 4 usability decision, not an Epic 2 implementation
  detail. The four independent polecats are likely to resolve it four different ways.
- Partial failure semantics for `embed_texts` (what happens after all retries for
  batch N are exhausted) are unspecified. This determines whether Epic 4 can treat
  the embedding step as atomic.
- The `converter.py` fallback path silently degrades quality with no signal to the
  caller. Epic 4 may need to know whether conversion used the agent or the fallback,
  in order to apply different quality thresholds or trigger a re-ingest queue.

**Classification:** Must-fix (error contract and partial failure semantics);
should-fix (fallback signal to caller).

---

### Developer / Polecat (Implementation DX Stakeholder)

**Actor:** The four polecats who will build `claude_agent.py`, `converter.py`,
`chunker.py`, and `embeddings.py` in parallel.

**Their need:** Unambiguous, resolved specifications so that parallel builds do not
produce incompatible API surfaces. The PRD intentionally enables parallel work via
component independence, which is sound for implementation. However, component
independence does not extend to the shared API surface: error types, exception
hierarchies, JSON output schema, and chunk metadata shape are shared contracts.
Polecats building independently will make independent choices on these.

**Does the PRD address it?** No. The PRD lists five open questions and does not
assign resolution ownership to any specific person or role.

**Gaps:**
- Q2 (allowedTools), Q3 (chunk heading metadata), and Q5 (error handling philosophy)
  each affect observable API behavior. Any polecat who "resolves locally" creates a
  constraint that the other polecats did not agree to.
- Q1 (MCP config) and Q4 (credential source) are genuine implementation details that
  polecats can decide independently without risk of cross-polecat conflict.
- There is no artifact specified to capture the resolved API contracts. A typed stub
  file or interface spec in `src/pointy_rag/` would formalize the contracts before
  polecats start and serve as the Epic 3/4 handoff artifact.

**Classification:** Must-fix for Q2, Q3, Q5 (resolve before dispatch);
should-fix for a formal API contract artifact.

---

### Operations / Infrastructure Owner

**Actor:** The person or team responsible for running the pointy-rag system in a
deployed environment (development, staging, production, or CI).

**Their need:** Observability into failures, resource controls on subprocesses and
heavy dependencies, and the ability to audit conversion quality over time.

**Does the PRD address it?** No. The PRD has no logging requirements, no metrics
requirements, and no mention of deployment environment, containerization, or resource
limits.

**Gaps:**
- The agent wrapper spawns Claude Code as a subprocess using `start_new_session=True`
  and kills via `os.killpg`. In a containerized environment (Docker, Kubernetes),
  process group semantics differ from a bare VM. Whether `os.killpg` works as intended
  inside a container's PID namespace is unspecified.
- PyMuPDF and ebooklib are "heavy" imports (per the Constraints: "all imports lazy
  where expensive"). Large PDF files can spike memory substantially. No memory or CPU
  limits are specified, and no upper bound on input file size is defined.
- The fallback path in `converter.py` is silent: a caller receives `(markdown_text,
  output_path)` with no indication whether the agent or the fallback produced it.
  An operator cannot audit how many documents were converted with degraded quality
  without log-level instrumentation, which is not required by the PRD.
- The embedding client has exponential backoff with 3 retries but no circuit-breaker
  pattern. If Voyage AI is down for an extended period, every `embed_texts` call will
  block for 1+2+4 = 7 seconds before failing. Under load, this means many threads
  blocking simultaneously.
- There are no structured logging requirements anywhere in the PRD. The ops owner
  cannot query "how many documents used the fallback converter last week" or "which
  batches triggered embedding retries" without this.

**Classification:** Should-fix (logging and fallback-path signal); should-fix
(subprocess PID namespace behavior in containers); observation (resource limits,
circuit-breaker).

---

### Security Function

**Actor:** The person or role responsible for security review of this system, including
its subprocess invocations and external API calls.

**Their need:** Understanding of the security boundary around the Claude subprocess
and approval of the `--allowedTools` configuration.

**Does the PRD address it?** No. Open Question 2 acknowledges the tools question but
frames it as a developer convenience decision, not a security decision.

**Gaps:**
- The `--allowedTools` choice is a security boundary, not a UX preference. If the
  conversion agent has `Write` access, a hallucinating or adversarially-prompted Claude
  invocation can overwrite arbitrary files in the subprocess's working directory.
  Documents processed from untrusted sources (user uploads, web scrapers) are a
  meaningful threat surface.
- The PRD does not specify the working directory the subprocess inherits. If it
  inherits the process's working directory and has `Write` access, the blast radius
  of a bad invocation includes the project source code.
- Embedding generation sends raw document text to the Voyage AI external API. If
  documents contain PII or confidential information, this is a data exfiltration
  surface. No data classification requirement is stated.
- There is no mention of sandboxing, read-only filesystem mounts, or network
  isolation for the Claude subprocess. These may all be acceptable tradeoffs for a
  developer tool, but they should be explicitly decided, not silently assumed.

**Classification:** Must-fix (the `--allowedTools` decision needs a security owner
and explicit rationale); should-fix (document the subprocess working directory and
data-at-rest scope).

---

### Voyage AI (External Dependency)

**Actor:** Voyage AI's `voyage-4-lite` embedding model, accessed via their external
API.

**Their need (as a dependency constraint):** The PRD must specify how the system
behaves when Voyage AI is unavailable beyond the 3-retry window, and how quota and
rate limits are managed.

**Does the PRD address it?** Partially. The PRD specifies the batch limit (128) and
retry parameters (1s, 2s, 4s, 3 retries) but nothing beyond that.

**Gaps:**
- Rate limits: Voyage AI imposes rate limits per API key. The PRD does not specify
  the expected throughput (documents per hour) or whether the embedding client needs
  to honor rate-limit response codes (HTTP 429) differently from other errors.
- Sustained outage: 3 retries over ~7 seconds handles transient errors. A sustained
  Voyage API outage means every `embed_texts` call will fail permanently after 7
  seconds. The pipeline has no fallback embedding model or graceful degradation path.
  Whether this is acceptable must be an explicit decision, not a gap.
- Account/quota ownership: No one is named as owner of the Voyage AI account. In
  production, quota exhaustion would silently halt all ingestion. There is no
  cost-monitoring or alert requirement stated.
- The embedding dimension is hardcoded at 1024 (matching `voyage-4-lite`). The DB
  schema (`vector(1024)`) cannot be changed without a migration. If Voyage AI
  deprecates `voyage-4-lite` or changes its output dimension, the migration path
  is not discussed.

**Classification:** Should-fix (rate limit handling and sustained-outage behavior
must be explicitly decided); observation (quota ownership, model deprecation path).

---

### Anthropic / Claude Code (External Dependency)

**Actor:** Claude Code CLI invoked as a subprocess by the agent wrapper.

**Their need (as a dependency constraint):** The PRD must specify how Claude Code
availability, rate limits, and cost are managed.

**Does the PRD address it?** No. Claude Code CLI availability is treated as a binary
installed/not-installed condition.

**Gaps:**
- The PRD specifies the fallback for `convert_to_markdown` when Claude CLI is absent
  (`use_agent=False`). But it does not specify what happens if the Claude CLI is
  present but returns a non-zero exit code for a non-timeout reason (API error,
  authentication failure, context limit exceeded).
- Claude Code invocations have cost. Document conversion is the highest-cost step —
  one agent invocation per document. For a system that may ingest thousands of
  documents, there is no budget or cost-monitoring requirement.
- Claude Code may enforce per-user or per-account rate limits. If the pipeline
  saturates the rate limit, the subprocess will return an auth/rate error, not a
  timeout. The error handling requirements in the PRD (specifically what
  `run_agent` raises for a non-zero exit code) are undefined beyond the timeout case.

**Classification:** Should-fix (non-timeout exit code handling for `run_agent`);
observation (cost and rate limit budget).

---

### End Users of the RAG System

**Actor:** The people who will ultimately query the RAG system that these ingestion
components feed.

**Their need:** High-quality chunking and conversion that produces retrievable,
contextually coherent chunks. Poor conversion or chunking means poor retrieval, which
means poor answers from the RAG layer.

**Does the PRD address it?** No. End users are entirely absent. The PRD's "Who"
statement is exclusively about the developer/operator building the system.

**Gaps:**
- The PRD permits fallback output that is "possibly lower quality" with no floor
  definition. "Usable markdown" is not a quality bar; it is an empty qualifier.
- There are no acceptance criteria for conversion fidelity (e.g., heading hierarchy
  preserved, tables represented, code blocks delimited) that map to retrieval quality.
- The chunker's `~4 chars/token` heuristic can produce significant variance from
  actual token counts for code-heavy documents. No downstream system in Epic 3 or
  Epic 4 has defined acceptable chunk size variance from the target. End-user answer
  quality is sensitive to this.

**Classification:** Should-fix (define minimum quality floor for fallback conversion,
even if proxied by a structural check); observation (token heuristic variance for
code-heavy documents).

---

## Conflict Summary

| Conflict | Stakeholders Involved | Nature |
|---|---|---|
| Open Q3 (chunk metadata) decided by polecat vs. needed by Epic 3 | Epic 2 polecats vs. Epic 3 team | Breaking-change risk |
| Open Q5 (error handling) decided independently by 4 polecats | Epic 2 polecats vs. Epic 4 team | Inconsistent API contract |
| `--allowedTools` framed as developer choice vs. security decision | Epic 2 polecats vs. security function | Security posture |
| Fallback path is silent vs. ops needs audit trail | `converter.py` design vs. ops owner | Observability gap |
| Parallel polecat builds vs. shared API surface | Epic 2 polecats (internal) | Inconsistency risk |

---

## Who Needs to Be Consulted During Implementation

Before Epic 2 polecats are dispatched:
1. **Epic 3 designer** — resolve Q3 (chunk heading metadata) and define the
   `run_agent` output JSON schema.
2. **Epic 4 designer** — resolve Q5 (error handling philosophy) and specify
   partial failure semantics for `embed_texts`.
3. **Security owner** — resolve Q2 (`--allowedTools` for the conversion agent)
   with explicit rationale and threat model.

Before Epic 2 closes (not blockers for dispatch, but before Epic 3/4 start):
4. **Ops/infrastructure owner** — define logging requirements (at minimum: conversion
   method used, fallback triggered, embedding retry count).
5. **Voyage AI account owner** — confirm rate limit handling strategy and quota
   monitoring plan.

---

## Gap Classification Index

| # | Gap | Stakeholder | Classification |
|---|---|---|---|
| 1 | Epic 3 not consulted on `run_agent` JSON schema | Epic 3 team | Must-fix |
| 2 | Epic 3 not consulted on chunk heading metadata (Q3) | Epic 3 team | Must-fix |
| 3 | Epic 4 not consulted on error handling contract (Q5) | Epic 4 team | Must-fix |
| 4 | `embed_texts` partial failure semantics undefined | Epic 4 team | Must-fix |
| 5 | `--allowedTools` decided without security review (Q2) | Security function | Must-fix |
| 6 | Fallback converter emits no signal to caller | Ops owner / Epic 4 | Should-fix |
| 7 | No structured logging requirements | Ops owner | Should-fix |
| 8 | Non-timeout exit code behavior of `run_agent` undefined | Epic 3, Epic 4 | Should-fix |
| 9 | Voyage AI rate limit and sustained-outage behavior | Ops owner, Voyage AI | Should-fix |
| 10 | No formal API contract artifact for Epic 3/4 handoff | Epic 3, Epic 4 polecats | Should-fix |
| 11 | Subprocess PID namespace behavior in containers | Ops owner | Observation |
| 12 | Minimum conversion quality floor undefined | End users | Observation |
| 13 | Claude Code cost and rate limit budget absent | Ops owner, Anthropic | Observation |
| 14 | Voyage AI model deprecation / dimension change path | Ops owner | Observation |
| 15 | Compliance/data classification for external API calls | Security / legal | Observation |
