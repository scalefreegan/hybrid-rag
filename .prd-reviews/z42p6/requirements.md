# Requirements Completeness

## Summary

The PRD is well-structured for a library epic: it names four components, provides user stories with concrete function signatures, and lists explicit constraints (timeouts, batch sizes, dimension counts). The rough approach section adds useful implementation intent. However, the PRD conflates *scenarios* with *acceptance criteria* — the "Given/When/Then" blocks describe happy paths for individual functions but do not define what constitutes a passing or failing implementation at the epic level. Several critical behavioral contracts are left implicit or ambiguous, and five open questions remain unresolved, two of which materially affect the component API surface.

The PRD is implementable for the core happy paths, but a QA engineer or a second polecat picking up this work would need to fill in multiple gaps through inference or ad-hoc decisions. That inconsistency risk is the core problem.

## Findings

### Critical Gaps / Questions

**1. Open Question 5 (error handling philosophy) is a blocking API contract decision**
- The PRD acknowledges the components have inconsistent error handling: "chunker is total (never raises), wrapper raises RuntimeError, embedding client raises Exception."
- Why this matters: Callers in Epic 3/4 will either wrap all four components in try/except or assume they're safe to call without one. An inconsistent contract forces callers to read each component's source to understand failure behavior.
- Clarifying question: Should all four components share a single error handling contract (e.g., raise a typed `IngestionError` subclass), or is the inconsistency intentional (e.g., chunker is always safe, I/O components raise)?

**2. "No orphan processes remain" is stated as a requirement but is untestable as written**
- US-6 asserts that after timeout, no orphan processes remain. There is no specified mechanism to verify this in the test suite.
- Why this matters: A test that launches a real subprocess and checks `os.getpgid()` is an integration test, not a unit test. The PRD says "no live services required for testing" but doesn't address whether real subprocesses are allowed.
- Clarifying question: Are subprocess-level tests in scope for `test_claude_agent.py`, or must process group kill behavior be tested via mocks only? If mocks, what observable side effect proves the kill happened?

**3. The chunk variance bound is unspecified**
- US-3 says "each ~1500 tokens" with no acceptable tolerance. "~" is not testable.
- Why this matters: A chunker that returns chunks of 800–3000 tokens when `target_size=1500` technically satisfies "~1500" by some reading, but would silently break downstream embedding batch size assumptions.
- Clarifying question: What is the acceptable upper bound on chunk size relative to `target_size`? Is there a hard maximum (e.g., `target_size * 1.5`)?

**4. JSON output schema for agent wrapper is undefined**
- US-5 says "I get a parsed dict result." The PRD's rough approach mentions three-pass JSON extraction but does not specify what keys/structure the dict must contain.
- Why this matters: Epic 3 (progressive disclosure) is the primary consumer of the agent wrapper. If the dict schema is implicit, Epic 3 will either define it ad hoc or break when the agent's output format changes.
- Clarifying question: Is the JSON schema caller-defined (the agent returns whatever Claude emits), or does `run_agent` validate/normalize the result to a known shape?

**5. Open Question 2 (allowedTools for conversion agent) affects security posture**
- If the conversion agent has `Write` tool access, it can modify arbitrary files. If it only receives file content via stdin, it cannot overwrite the source document.
- Why this matters: This is both a correctness constraint (can the agent write the output file itself?) and a security constraint (what can a malicious/hallucinating agent do?).
- Clarifying question: Does the conversion agent write the output markdown file itself, or does `claude_agent.py` capture stdout/JSON and write it?

### Important Considerations

**6. Conversion quality has no minimum threshold**
- US-2 says the fallback extractor produces "possibly lower quality" output. No quality floor is defined.
- A fallback that strips all content and returns `# Document` technically satisfies "usable markdown." The lack of a minimum quality definition means there's no regression test possible for the fallback path.
- Suggested: Define at minimum that fallback output must contain the same number of paragraphs as source sections, or some other measurable proxy for content preservation.

**7. Partial failure semantics are unspecified for the embedding client**
- The PRD specifies per-batch retry but not what happens after all retries are exhausted. Does `embed_texts` raise on the first failed batch? Partial-return the successful batches? Return an error sentinel for failed batch positions?
- A 500-item list batched into 4 groups where batch 2 fails permanently has no specified outcome.

**8. `convert_to_markdown` output file lifecycle is unspecified**
- US-1 says the function returns `(markdown_text, output_path)`, implying it writes a file. If the conversion fails mid-way, is the partial file cleaned up? Is `output_dir` created if it doesn't exist?
- This affects whether callers need to wrap the call in cleanup logic.

**9. Thread safety of the lazy singleton embedding client is unspecified**
- The "lazy singleton" pattern is race-prone in multithreaded contexts. The constraint says the Voyage client is sync with `time.sleep`, but the agent wrapper is async. If these components are ever composed in an async context with threadpool executors, the singleton initialization is a potential race.
- No thread-safety guarantee (or explicit disclaimer) is stated.

**10. Open Question 3 (heading context in chunk metadata) affects Epic 3 utility**
- The PRD acknowledges this is an open question but defers it. Since the chunker's output directly feeds the progressive disclosure layer (Epic 3), this decision should be resolved before Epic 2 is frozen — retrofitting heading metadata into `Chunk` objects after Epic 3 depends on them is a breaking change.

### Observations

**11. The five open questions are not all equal weight**
- Q1 (MCP config) and Q4 (credential source) are implementation details that individual polecats can decide locally.
- Q3 (heading context) should be resolved before Epic 2 closes to avoid a breaking change in Epic 3.
- Q2, Q5 need a decision before the component APIs are considered stable.

**12. "Preserves heading hierarchy and all content" (US-1) is subjective**
- Tables, code blocks, images (as alt text?), and footnotes are all "content." The PRD doesn't define which content types are in scope for preservation.
- For PDF especially, mathematical formulas and figures are commonly lost in text extraction. Whether this is acceptable is unstated.

**13. The `DocumentFormat` dependency on Epic 1 is implicit**
- The rough approach says the converter imports `DocumentFormat` from `models.py`. There's no explicit statement that Epic 1 must be complete and merged before Epic 2 can be tested. The current state ("Epic 1 is complete and passing") is stated in prose, not as a formal dependency. This is fine for now but should be tracked.

**14. Backoff parameters for the embedding client are implementation detail, not requirement**
- The constraint says "Exponential backoff: 1s, 2s, 4s (3 retries)." These specific values appear in the Constraints section, making them testable requirements rather than implementation hints. Verify this is intentional — if these are meant to be configurable, they shouldn't be in Constraints.

## Confidence Assessment

**Medium.** The PRD is implementable for the happy path of all four components. The user stories provide enough detail to write the core implementations and most unit tests. However, three of the five open questions affect observable API behavior (error handling, output schema, allowed tools), and two testability gaps (orphan-process verification, chunk variance bound) will cause ambiguity when polecats write test assertions. A QA engineer would flag at minimum items 1, 2, 3, and 4 above as "untestable as written." The PRD should resolve Q2, Q5, and Q3 before dispatch, and clarify the chunk variance bound and JSON output contract.
