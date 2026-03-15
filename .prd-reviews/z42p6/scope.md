# Scope Analysis

## Summary

The PRD defines four independent library components for document ingestion and is well-bounded
at the epic level: no CLI commands, no pipeline orchestration, no database writes. The
Non-Goals section is explicit and reasonable. However, several scope leaks are present or
imminent: the `Chunk` model (from Epic 1) has a required `disclosure_doc_id` field that
the chunker cannot populate without Epic 3 involvement, the converter's output file lifecycle
is partially scoped, and three open questions (Q2, Q3, Q5) bleed into the interface contracts
that downstream epics depend on. The PRD also has MVP-shrinkable surface: the agent wrapper
and converter are substantially more complex than the chunker and embedding client; splitting
them into separate milestones would reduce parallel risk.

The biggest scope risk is not feature creep from the outside — it's the implicit API contracts
between these four components and their Epic 3/4 consumers that the PRD treats as implementation
details. Those contracts will become rigid the moment polecats start writing tests against them.

## Findings

### Critical Gaps / Questions

**1. `Chunk.disclosure_doc_id` is required in the model but the chunker cannot populate it**

- The `Chunk` model (Epic 1, `models.py`) has `disclosure_doc_id: str = Field(min_length=1)`,
  meaning no `Chunk` can be created without a `DisclosureDoc` parent.
- The PRD says the chunker returns "a list of Chunk objects" but makes no mention of
  `disclosure_doc_id`. The chunker is a pure text-splitting component — it has no knowledge
  of `DisclosureDoc` records.
- Why this matters: Either (a) the Chunk model must be changed to make `disclosure_doc_id`
  optional for the chunker's output (a breaking schema change), or (b) the chunker doesn't
  return `Chunk` objects at all (returns plain dicts or a different type), or (c) callers
  must fill in `disclosure_doc_id` after chunking — in which case this should be explicit
  in the PRD.
- Clarifying question: Does the chunker return `Chunk` objects from `models.py`, or an
  intermediate type? If the former, who provides `disclosure_doc_id` and when?

**2. The converter's output interface overlaps with `Document` model creation in an unscoped way**

- The converter returns `(markdown_text, output_path)`. The `Document` model (Epic 1)
  represents a processed document with `source_path` and `format`. There is no statement
  about when a `Document` record is created — is that the converter's job, the pipeline's
  job (Epic 4), or neither?
- Why this matters: If polecats building Epic 3/4 assume the converter creates `Document`
  records, they will build on a non-existent side effect. If they assume it doesn't, they
  will duplicate that logic themselves.
- Clarifying question: After `convert_to_markdown()` runs, is a `Document` row written to
  the database? If not, at what point in the pipeline is that responsibility assigned?

**3. Open Question 3 (heading context in chunks) must be resolved before any chunk consumers are written**

- The PRD defers this as an open question, but the `Chunk.metadata` field is the only
  hook for heading context if the chunker doesn't store it explicitly.
- Why this matters: If Epic 3 (progressive disclosure) needs heading hierarchy to build
  its disclosure tree, and the chunker doesn't emit it, Epic 3 either (a) must re-parse
  the markdown, (b) requires a breaking change to `Chunk`, or (c) silently produces lower-
  quality disclosures. This is a scope seam between Epic 2 and Epic 3 that will calcify
  the moment both are implemented in parallel.
- Clarifying question: Will Epic 3's disclosure tree builder require `parent_heading_chain`
  or equivalent metadata in each `Chunk`? If yes, this must be in Epic 2 scope, not deferred.

### Important Considerations

**4. The agent wrapper and converter are a distinct complexity tier from chunker + embedder**

- The chunker and embedding client are stateless library functions with deterministic or
  mockable behavior. They can be built and tested without any external dependencies beyond
  the Voyage API (which can be mocked).
- The agent wrapper introduces subprocess management, process group lifecycle, signal
  handling, and a runtime dependency on Claude CLI being installed. The converter's
  primary path depends on the agent wrapper.
- Why this matters: If the agent wrapper hits unexpected complexity (process group
  behavior varies across OS, Claude CLI version drift, etc.), it blocks the converter.
  Meanwhile the chunker and embedder are unblocked.
- Suggested: Clarify whether all four components must ship together or if chunker+embedder
  could be delivered and used while agent wrapper+converter are still in progress.

**5. The Claude CLI runtime dependency is not in scope for test infrastructure setup**

- The PRD states "no live services required for testing" but the agent wrapper's primary
  path requires Claude CLI to be on PATH. Unit tests can mock `subprocess.run`, but the
  integration behavior (process group kill, JSON extraction from real Claude output) cannot
  be verified without it.
- The constraints don't define which test level verifies process group cleanup. If it's
  only an integration test, it needs an environment with Claude CLI — which is out of scope.
- Clarifying question: Is there a CI environment with Claude CLI available for Epic 2 tests?
  If not, what is the test coverage strategy for `run_agent`'s kill path?

**6. The embedding client's credential validation timing creates a hidden scope requirement**

- The "lazy singleton" pattern means `get_settings().voyage_api_key` is not validated until
  first use. An empty key will cause a Voyage API call to fail, not an initialization error.
- If `Settings.voyage_api_key` defaults to `""` (which it does in `config.py`), a missing
  key produces a late-stage API error, not a clear "credentials not configured" message.
- This creates an implicit requirement: the embedding client should either (a) validate
  credentials at singleton initialization or (b) emit a clear error when the key is empty
  before making any API call.
- This is borderline in-scope for Epic 2 (it's about the embedding client's behavior) but
  the PRD is silent on it. If not addressed, Epic 3/4 will have confusing failures.

**7. Deduplication of documents is entirely unscoped and will be the first "day after launch" ask**

- The PRD does not mention what happens when `convert_to_markdown` is called twice on the
  same file, or when `embed_texts` is called with chunks that already have embeddings in
  the database.
- Why this matters: The moment anyone uses this system for real ingestion, they will ask
  "can I re-run this without duplicating embeddings?" The absence of a `content_hash` or
  deduplication key on `Chunk` means this will require a model change later.
- Suggested: Explicitly call out deduplication as deferred (e.g., "deduplication of
  re-ingested documents is Epic 5" or "not in scope for any current epic"), or add a
  `content_hash` field to `Chunk` now while the model is being established.

### Observations

**8. "No new CLI commands in this epic" could be violated by developer ergonomics pressure**

- The constraint says these are library-only components. But the CLI already exists
  (`pointy-rag` via Typer). Developers testing these components will want to run
  `pointy-rag convert <file>` or `pointy-rag embed <file>` interactively.
- This is a predictable scope creep vector. The PRD should explicitly state that CLI
  commands for these components are Epic 4 scope, not a gap in Epic 2.

**9. The four components are scoped as independent but share a silent integration assumption**

- The rough approach states all four components are independent with no imports between
  them. This is true for imports but not for data flow: the pipeline is
  `converter → chunker → embedder → (db insert)`.
- The `(markdown_text, output_path)` return of the converter feeds directly into
  `chunk_markdown(text, ...)`. The `Chunk` objects from the chunker feed into `embed_texts`.
  There's no integration test scoped in Epic 2 that verifies this composition works.
- This is fine if Epic 4 owns integration testing, but that should be explicit.

**10. `voyage-4-lite` model name is a scope constraint, not just an implementation detail**

- The constraint fixes the embedding model to `voyage-4-lite`. Model names change;
  `voyage-4-lite` may be deprecated by the time Epic 3/4 are complete.
- No fallback or model configurability is mentioned (explicitly deferred as Non-Goal).
- Suggested: The constraint should note the model version pin is intentional and
  document the known limitation that this will require a code change (not configuration)
  to update.

**11. The `tests/` directory naming in the rough approach conflicts with `pyproject.toml` config**

- The `pyproject.toml` specifies `testpaths = ["tests"]`. The rough approach lists four
  test files in `tests/`. No conflict here — this is already aligned. (Observation only,
  no action needed.)

## Confidence Assessment

**Medium-High.** The scope boundaries for what Epic 2 builds are clear: four library modules,
four test files, no CLI, no pipeline orchestration. The Non-Goals section is specific and
reasonable. The scope risk is concentrated at two seams: (1) the `Chunk.disclosure_doc_id`
constraint that creates a dependency on Epic 3 data (Critical finding #1), and (2) the
Open Question 3 deferral that will force either a breaking change or a workaround when Epic 3
is built (Critical finding #3). Resolving these two questions before parallel dispatch would
significantly reduce integration risk. The remaining findings are important but do not
block individual component implementation.
