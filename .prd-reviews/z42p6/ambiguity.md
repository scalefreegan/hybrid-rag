# Ambiguity Analysis

## Summary

The Epic 2 PRD is concrete in the places it needs to be — function signatures, explicit numeric constraints, and five acknowledged open questions. However, it is ambiguous in the _behavioral contracts_ between those specifics: what the functions do at boundaries, what counts as success, and what exactly the technical terms mean. A well-intentioned engineer reading this PRD would need to make approximately a dozen judgment calls that different engineers would resolve differently, producing components that individually pass their own tests but fail integration because their authors held different mental models of the same sentence.

The most systemic ambiguity is "agent-powered conversion as primary path and deterministic fallback" — the PRD never specifies whether the fallback is automatic (triggered by detection) or manual (controlled by caller parameter), which affects the `convert_to_markdown` API contract. Several other terms are used as if they have obvious meanings ("well-structured markdown," "semantically meaningful chunks," "context continuity") that will be resolved differently by different implementors.

## Findings

### Critical Gaps / Questions

**1. Fallback triggering semantics are undefined: automatic detection vs. explicit parameter**
- US-2 shows `convert_to_markdown(path, output_dir, use_agent=False)` — an explicit caller-controlled parameter. But Goal 2 says the agent path is "primary" with "deterministic fallback extractors when Claude CLI is unavailable" — suggesting automatic detection.
- These are two different behaviors: (a) auto-detect CLI absence and silently fall back; (b) caller explicitly selects the path.
- Why this matters: If automatic, callers in Epic 3/4 never need to handle `use_agent=False`. If explicit, callers must anticipate Claude CLI absence themselves and pass the flag. The Epic 3 progressive disclosure caller likely wants auto-detection; the Epic 4 pipeline may want explicit control.
- Clarifying question: Is `use_agent` a genuine override flag (caller explicitly selects fallback), or just a test-convenience parameter? And is silent auto-fallback also supported when Claude CLI is absent, regardless of `use_agent`?

**2. "Heading boundaries" is undefined — which headings trigger a split?**
- US-3 says chunks "split primarily on heading boundaries" for a document with `##` and `###` headings. The rough approach says "split on headings first, then subdivide large sections."
- Ambiguity: Does the chunker split on ALL ATX heading levels (H1–H6)? Only H1–H2? Only H1–H3? Does setext-style (underline) markdown headings get treated the same?
- Why this matters: Two engineers will produce incompatible implementations. A document with many H4–H6 subheadings either produces tiny chunks (if all levels split) or large under-split sections (if only H2 splits).
- Clarifying question: What heading levels trigger a split boundary? Is there a minimum heading level, or are all `#`-prefixed lines treated equally?

**3. "Three-pass JSON extraction" — what happens when all three passes fail?**
- The rough approach defines the extraction strategy (direct parse → fenced block → first `{` scan) but does not specify the error behavior when all three passes fail.
- Two reasonable interpretations: (a) raise `RuntimeError("no JSON found in agent output")`; (b) return empty dict `{}`; (c) return the raw string wrapped in a dict.
- Why this matters: Epic 3 callers will either wrap `run_agent` in try/except expecting a RuntimeError, or check for empty/malformed returns. These require different calling code. The error handling philosophy question (PRD Open Q5) is directly downstream of this.
- Clarifying question: When the agent produces output with no parseable JSON, does `run_agent` raise or return? If it raises, what exception type?

**4. The `output_path` in `convert_to_markdown`'s return value is ambiguous**
- US-1 says the return is `(markdown_text, output_path)` and the function signature is `convert_to_markdown(path, output_dir)`. The output_path is presumably derived from `output_dir` + some filename — but the derivation rule is unspecified.
- Questions: Is the output filename `source_stem + ".md"` (i.e., `annual-report.pdf` → `annual-report.md`)? What if a file with that name already exists in `output_dir`? Does it overwrite silently, raise, or generate a unique name?
- Why this matters: Callers building pipelines that process many documents need predictable output paths for downstream file handling.
- Clarifying question: How is the output filename derived from the input path? What is the overwrite policy?

**5. "Configurable timeouts" vs. specific constraint values — which is authoritative?**
- Constraints section states: "Conversion agent timeout: 300 seconds" and "Disclosure agent timeout: 180 seconds (for Epic 3 use, but the wrapper should support configurable timeouts)."
- The parenthetical "for Epic 3 use" is ambiguous: does 180s appear in the wrapper's API as a second named constant? Is it a default? Is it only set by Epic 3 callers?
- The 300s and 180s values in the Constraints section makes them testable requirements — but they're described as if they're hints about how callers will use the wrapper.
- Why this matters: If a polecat writes `DEFAULT_TIMEOUT = 300` and tests assert this value, and another polecat writes `DEFAULT_TIMEOUT = 180`, they're both following the PRD.
- Clarifying question: What is `run_agent`'s default timeout — 300s, 180s, or is `timeout` a required argument? Should both values appear as named constants in the module?

### Important Considerations

**6. "Well-structured markdown" is subjective and unstated**
- US-1 says conversion "preserves heading hierarchy and all content." "All content" is underspecified: tables, inline code, block code, images (as alt text?), footnotes, mathematical formulas, superscripts.
- For PDFs especially: figures typically become alt-text or are lost; tables vary by extractor quality. "All content" cannot literally mean all content for all document types.
- This creates a false pass/fail signal: a test that checks "heading hierarchy is preserved" passes while silently dropping all tables.
- Suggested: Enumerate which content types must be preserved and which are "best effort."

**7. "Overlap provides context continuity" — the semantics of overlap are unspecified**
- US-3 says `overlap=200` and "overlap provides context continuity between chunks." The rough approach says "subdivide large sections with line-level overlap."
- Overlap could mean: (a) last 200 tokens of chunk N are also the first 200 tokens of chunk N+1; (b) both chunks share a 200-token window around the split point; (c) metadata links to adjacent chunk.
- Why this matters: The embedding + retrieval layer in Epic 4 will deduplicate or merge retrieved chunks. If overlap is implemented differently across two polecats, the deduplication logic breaks.
- Clarifying question: Does a chunk's content physically contain the last N tokens of the previous chunk, or does "overlap" mean something else here?

**8. "No live services required for testing" conflicts with "headless Claude as subprocess"**
- The constraint says tests require no live services. But `test_claude_agent.py` tests the Claude subprocess wrapper. Mocking subprocess at the `subprocess.Popen` level is feasible but the PRD says "structured JSON output extraction" — does the test verify the extraction logic against a mock that returns valid/invalid JSON, or does it mock at a higher level?
- This creates a fork: some polecats will mock `Popen`, others will mock `asyncio.create_subprocess_exec`, others will test only the JSON extraction logic.
- Clarifying question: What is the mock boundary for agent wrapper tests? Is there a reference mock pattern or fixture expected?

**9. "Lazy singleton" — singleton scope is undefined**
- The embedding client is described as a "lazy singleton." In Python this typically means a module-level global initialized on first call. But "singleton" could also mean: per-`Settings` instance, per-`VOYAGE_API_KEY` value, or per-process.
- In tests, module-level singletons cause state pollution between test cases unless explicitly reset.
- Suggested: Specify whether the singleton is module-scoped (standard) and whether the test fixture should explicitly reset it between tests (e.g., via `importlib.reload` or a reset function).

**10. "Should" vs. "must" in Goal 5**
- Goal 5: "All components tested: Each component has pytest unit tests using mocks (no live services required for testing)." This uses "has" (present tense declarative), not "must have."
- More importantly: no coverage threshold, no specification of what behaviors need tests (happy path only? all error branches?), no requirement for test file naming beyond the layout in the rough approach.
- The five user stories each describe one happy path. Error paths (malformed input, mid-document failure, malformed API response) have no test scenarios specified.

### Observations

**11. "All imports lazy where expensive" — "expensive" is not defined**
- The constraint says lazy imports for `pymupdf`, `ebooklib`, and `voyageai`. But `subprocess`, `asyncio`, and `json` are standard library and presumably not lazy. The boundary between "expensive" and "not expensive" is left to the implementor's judgment.
- This is a low-stakes ambiguity for the named packages, but could cause confusion for `beautifulsoup4` (listed as a dependency but not mentioned in the lazy-imports constraint).

**12. The rough approach's component independence claim has an unstated precondition**
- "All four components are independent — no imports between them." True, but they all depend on `models.py` and `config.py` from Epic 1. The PRD says "Epic 1 is complete and passing," treating this as a fact — but if Epic 1's API changes post-Epic 2 dispatch, all four components break.
- There is no freeze or import-lock mechanism mentioned. This is an observation about inter-epic coordination risk, not a blocker.

**13. "Batched with per-batch retry" — the retry granularity creates ambiguity**
- "Failure in batch N doesn't retry batches 0..N-1." This is a design decision stated as fact, but raises a question: if batch 2 of 4 fails permanently, does `embed_texts` raise immediately (discarding batches 3 and 4), or run batches 3 and 4 first and then raise? The principle of least surprise suggests "fail fast," but the "per-batch" framing implies batches are independent and could suggest "collect errors and raise at end."

**14. Voyage API `voyage-4-lite` — model name stability is assumed**
- The PRD hardcodes `voyage-4-lite` in the Constraints. Voyage model names have changed before. Whether this value should be a constant, a config setting, or a hardcoded string literal is not specified.
- The Non-Goals section defers "multi-model embedding support," which is fine — but even single-model support benefits from having the model name in a named constant rather than scattered as literals.

## Confidence Assessment

**Medium-Low.** The core implementation of each component can be written — the function signatures, numeric constants, and external dependencies are clear. But approximately 5–6 of the 14 findings above represent places where two reasonable engineers reading the same PRD would produce incompatible implementations. The most systemic risk is the fallback triggering semantics (Finding 1), which affects the `convert_to_markdown` public API. The three-pass extraction failure behavior (Finding 3) and the overlap semantics (Finding 7) are the next most likely sources of inter-component incompatibility when Epic 3/4 integration happens. The PRD should resolve these before parallel polecat dispatch, otherwise the integration epic will absorb the ambiguity as debugging time.
