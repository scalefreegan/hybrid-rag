# Technical Feasibility

## Summary

The pointy-rag PRD describes a hybrid RAG system built around a "progressive disclosure"
document hierarchy (4-level llms.txt-inspired structure) with pgvector-backed pointer
search. The foundation (Epic 1) is complete and structurally sound. The remaining
three epics (ingestion components, intelligence layer, integration) are feasible
but carry several hard technical problems that need resolution before implementation.

The most significant concern is a fundamental coupling conflict in the data model:
the DB schema requires chunks to reference a `disclosure_doc_id` (NOT NULL FK), but
the pipeline generates disclosure docs *after* chunks. Either the pipeline order or
the schema constraint must change — this needs an explicit design decision before
Epic 2 work begins. Beyond that, the "hybrid" label is misleading (the search
design is vector-only, no BM25), and the Level 0 library catalog regeneration
scales poorly.

---

## Findings

### Critical Gaps / Questions

**1. Chunk ↔ DisclosureDoc FK coupling: chicken-and-egg**

The DB schema has `disclosure_doc_id TEXT NOT NULL REFERENCES disclosure_docs(id)` on
the `chunks` table. But the Epic 4 pipeline order is:
`chunk → embed → store document → generate disclosure hierarchy → map chunk pointers → update chunks`

Chunks are chunked and embedded *before* disclosure docs exist. You cannot INSERT
chunks into the DB before their `disclosure_doc_id` is known — the FK constraint
will reject it.

- **Why this matters**: Without resolving this, the ingestion pipeline cannot
  complete a round-trip into the database. It is a hard correctness bug in the design.
- **Clarifying question**: Should the pipeline order change (generate disclosure docs
  from chunks *before* storing chunks), or should `disclosure_doc_id` be nullable at
  insert time with a mandatory backfill after pointer mapping?
- **Option A**: Generate Level 3 disclosure docs from chunked markdown structurally
  (no Claude needed at Level 3 per Epic 3), then insert disclosure docs, then assign
  `disclosure_doc_id` to each chunk before storage.
- **Option B**: Make `disclosure_doc_id` nullable on INSERT; enforce NOT NULL only
  after the pointer-mapping step.

**2. "Hybrid" RAG: is there a BM25/keyword component?**

The project is named "Hybrid RAG with Voyage AI embeddings and pgvector" and the
README echoes this. But the search design (Epic 3, Sub-bead 6) describes *only*
pgvector cosine similarity. There is no BM25, full-text search, or reciprocal rank
fusion in the PRD.

- **Why this matters**: If stakeholders expect traditional hybrid search (vector +
  keyword), the current design doesn't deliver it. If "hybrid" refers only to the
  progressive disclosure pointer approach (not vector+keyword fusion), that needs to
  be stated explicitly.
- **Clarifying question**: Does "hybrid" mean vector + BM25 fusion (requires
  pg_trgm or ts_vector in schema), or does it mean vector search + navigable pointer
  hierarchy (current design)? If the former, the schema and Epic 3 scope are missing
  substantial work.

**3. Level 0 library catalog: O(n) regeneration on every ingestion**

Level 0 (library_catalog) "re-generated after each new ingestion." At Level 0,
the agent reads ALL Level 1 resource index docs across the entire DB to produce
a single library overview. As the library grows, this:
- Scales linearly with document count in Claude API calls
- Could exceed context window limits with a large library (no budget cap mentioned)
- Adds significant latency to every ingestion

- **Why this matters**: This is a system design constraint that could make ingestion
  prohibitively slow for libraries > ~50-100 documents, or fail entirely when the
  concatenated Level 1 docs exceed the model's context window.
- **Clarifying question**: What is the expected library size? Is Level 0 regeneration
  acceptable at 10 docs? 100? 1000? Is there a plan for incremental updates or
  summarization of summaries?

**4. Claude Code CLI as subprocess: interface stability risk**

Epics 2 and 3 depend on spawning `claude -p` with specific flags (`--output-format`,
`--max-turns`, `--allowedTools`, `--append-system-prompt`). This is a tight coupling
to the current claude CLI interface.

- **Why this matters**: If the claude CLI changes flag names, output format, or
  process behavior (e.g., changes to `--output-format json` schema), all conversion
  and disclosure generation silently fails or crashes. There is no abstraction layer
  between the subprocess invocation and the business logic.
- **Clarifying question**: Is there a version-pinned `claude` CLI, or a documented
  interface contract? Should the claude_agent.py wrapper validate output schema
  versions?

---

### Important Considerations

**5. Missing dependencies in pyproject.toml**

The current `pyproject.toml` includes only the foundation dependencies. Epics 2-4
require packages not yet declared:
- `voyageai>=0.3.0` (embedding client — Epic 2)
- `pymupdf>=1.24.0` (PDF fallback extraction — Epic 2)
- `ebooklib>=0.18` (EPUB fallback extraction — Epic 2)
- `beautifulsoup4>=4.12.0` (HTML parsing from ebooklib — Epic 2)

Until these are declared, `uv run pointy-rag` won't have access to them. This is
expected (Epics not started) but must happen at Epic 2 kickoff.

**6. Non-atomic re-ingestion**

Epic 4 specifies: "Re-ingestion deletes old chunks/disclosure docs before
re-inserting." No transaction wrapping is mentioned. If ingestion fails
midway after deletion, the document is in a partially-deleted state with no
automatic rollback.

- **Mitigation**: Wrap delete + re-insert in a single DB transaction. psycopg3
  supports this. The design should explicitly require it.

**7. Vector dimension lock-in**

`embedding vector(1024)` is hardcoded in the schema for Voyage AI's 1024-dim
output. Changing to a different model with different dimensions (e.g., for cost
or quality reasons) requires an ALTER TABLE or DROP/CREATE — neither is
trivially safe on a populated table.

- **Mitigation**: Consider storing embedding dimension in a config table, or
  document explicitly that the dimension is model-locked and migrations are
  required.

**8. Async architecture not yet present**

The current codebase (Epic 1) is entirely synchronous. Epics 2+ require async
subprocess management for Claude CLI invocations. Introducing async means:
- `cli.py` commands that call async code need `asyncio.run()` wrappers
- Mixing sync psycopg and async subprocess in the same pipeline needs care
- The existing sync `get_connection()` context manager is incompatible with
  async coroutines without a separate async version

This is buildable but needs a deliberate async strategy upfront, not bolted on
later.

**9. `--collection` CLI parameter: no backing data model**

The CLI stubs have `--collection/-c` in `ingest`, `search`, `drill`, and `ls`.
Open bead `hr-12w` tracks this gap. The `documents` table has no `collection`
column; queries cannot be scoped to a collection.

- **Impact**: Either implement collections before any Epic 2+ command is wired,
  or remove `--collection` from the CLI. Currently the parameter is accepted and
  silently ignored — a deception risk for users.

**10. Chunk model fields mismatch**

Epic 2 (chunker design) describes a `Chunk` dataclass with fields: `content`,
`token_count`, `chunk_index`, `heading`, `page`. The current Pydantic `Chunk`
model in models.py has: `id`, `disclosure_doc_id`, `content`, `embedding`,
`metadata`.

The fields `token_count`, `chunk_index`, `heading`, `page` are missing from the
stored model. Either these are chunker-internal fields (not stored in DB) or the
DB model is incomplete. The pointer mapper (Sub-bead 5b) explicitly uses
`heading` metadata to match chunks to disclosure docs — so `heading` must survive
from chunker through to DB storage in `metadata` or as a dedicated column.

---

### Observations

**11. Token counting heuristic (~4 chars/token)**

The PRD specifies "~4 chars/token" for chunk size estimation. This is reasonable
for English prose but significantly underestimates token count for code, YAML,
JSON, or non-Latin scripts. A 1,500-token target chunk might actually be 2,000+
tokens for code-heavy documents, potentially exceeding Claude's context window
in disclosure generation.

**12. Epics 2-4 are pre-planning "refine into granular beads" steps**

Each of Epics 2, 3, 4 begins with "Before implementation, refine this epic into
N granular beads." The actual implementation beads don't exist yet. A planning
step is required before any polecat can be assigned implementation work. This
is expected workflow overhead, not a blocker, but worth noting for sequencing.

**13. Level 3 extraction is structural (no Claude) — good**

Level 3 detailed_passage generation is pure structural extraction from markdown
headings. This means a document can be stored and searched without any Claude
API calls if only Level 3 is needed. The degraded `--no-agent` path is well
designed and can serve as a useful test harness.

**14. Search returns Chunk with content despite "no raw chunk text" constraint**

The `SearchResult` model contains a `Chunk` object, which has a `content` field.
Epic 3 says "Search must NOT return raw chunk text — only disclosure pointers."
These two things are in tension. The intent is probably that the search *response*
surfaces disclosure doc pointers (not chunk content), but the `SearchResult` model
exposes both. The implementation should suppress `chunk.content` from API/CLI
responses, even if stored in the model.

---

## Confidence Assessment

The foundation layer (Epic 1) is well-designed and the code that exists is clean.
The system architecture is inventive and internally consistent. However, the
remaining three epics contain enough unresolved design questions that jumping
straight to implementation carries meaningful rework risk.

| Area | Confidence | Notes |
|------|-----------|-------|
| Foundation (Epic 1) | High | Schema + models are solid; known bugs tracked |
| Ingestion components (Epic 2) | Medium | Claude CLI dependency and async strategy need clarity |
| Intelligence layer (Epic 3) | Medium-Low | Chunk FK problem and Level 0 scaling unresolved |
| Integration pipeline (Epic 4) | Medium | Non-atomic re-ingest and collection gap need design work |
| Search design | Medium | Vector-only vs. claimed "hybrid"; returns chunk content |

**Overall PRD readiness: Medium.** The PRD is buildable, but two issues should
be resolved before Epic 2 implementation begins: the chunk ↔ disclosure_doc
coupling (Finding 1) and the "hybrid" clarification (Finding 2). Everything else
can be resolved during implementation with appropriate bead tracking.
