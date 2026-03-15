# Epic 3: Intelligence Layer — Design

## Architecture Overview

Three new modules, plus DB query additions and CLI wiring:

```
src/pointy_rag/
├── disclosure.py       # NEW: Progressive disclosure hierarchy generator
├── pointer_mapper.py   # NEW: Map TextChunks → Chunk models with disclosure_doc_id
├── search.py           # NEW: Pointer-based vector search + navigation
├── db.py               # EXTEND: Add query functions for search/navigation
└── cli.py              # EXTEND: Wire search + drill commands
```

## Module 1: disclosure.py (Sub-bead 5a)

### Hierarchy Structure (per document with N sections)

```
Level 0: library_catalog (1 per library, shared across all docs)
  └─ Level 1: resource_index (1 per document)
       └─ Level 2: section_summary (N per document, 1:1 with Level 3)
            └─ Level 3: detailed_passage (N per document, 1 per section)
```

### Public API

```python
async def generate_disclosure_hierarchy(
    document_id: str,
    markdown: str,
    title: str,
    conn: psycopg.Connection,
) -> list[DisclosureDoc]:
    """Generate 4-level disclosure hierarchy for a document.

    Bottom-up generation: L3 (structural) → L2 (agent) → L1 (agent).
    Does NOT generate L0 — call regenerate_library_catalog() after.
    """

async def regenerate_library_catalog(conn: psycopg.Connection) -> DisclosureDoc | None:
    """Regenerate the single Level 0 library catalog from all Level 1 docs.

    Idempotent: deletes existing L0 docs, creates new one, re-parents L1 docs.
    Returns None if no documents exist.
    """
```

### Generation Flow

1. **Level 3** (pure parsing, no agent): Split markdown by top-level headings using `chunker._split_into_sections()`. One DisclosureDoc per section with heading as title, body as content. `ordering` = sequential index.

2. **Level 2** (agent per section): For each Level 3 doc, call `run_disclosure_agent(text=l3.content, title=l3.title, level=2)`. Create DisclosureDoc with parent_id = Level 1 doc (set after L1 is created). Use `asyncio.gather` with concurrency limit (sem=3) to parallelize agent calls.

3. **Level 1** (agent, one per doc): Concatenate all Level 2 summaries. Call `run_disclosure_agent(text=combined, title=title, level=1)`. Create DisclosureDoc.

4. **Post-generation**: Set parent_id links:
   - L3 docs → parent = corresponding L2 doc
   - L2 docs → parent = L1 doc
   - L1 doc → parent = L0 doc (if exists)

5. **Level 0** (separate function): Query all L1 docs, concatenate, call agent. Delete old L0, insert new, update all L1 parent_ids.

### Edge Cases
- **Empty document**: Return empty list
- **Single section**: Still produces full L3→L2→L1 chain
- **Agent timeout**: Let TimeoutError propagate — caller decides retry strategy
- **Text too large for agent**: Level 3 extracts raw sections; Level 2 agent has 200k char limit. If a section exceeds this, truncate with warning in metadata.

## Module 2: pointer_mapper.py (Sub-bead 5b)

### Public API

```python
def map_chunks_to_disclosure(
    chunks: list[TextChunk],
    disclosure_docs: list[DisclosureDoc],
) -> list[Chunk]:
    """Map TextChunks to Chunk models with disclosure_doc_id assigned.

    Primary: match chunk.heading to Level 3 doc title.
    Fallback: content overlap scoring (Jaccard on word sets).
    """
```

### Matching Strategy

1. Build index: `{normalize(title): ddoc_id}` for all Level 3 disclosure docs
2. For each TextChunk:
   - If chunk.heading matches a Level 3 title → assign that disclosure_doc_id
   - Else: compute word-set Jaccard similarity against all Level 3 docs, pick best above 0.3 threshold
   - If still no match: assign to first Level 3 doc (ordering=0) and set `metadata["unmapped"] = True`
3. Create Chunk model instances (embedding=None, to be filled later)

### Normalization
- Strip heading markers (`#`), lowercase, strip whitespace
- This handles `## Introduction` matching `"Introduction"` etc.

## Module 3: search.py (Sub-bead 6)

### Public API

```python
def search(
    query: str,
    conn: psycopg.Connection,
    limit: int = 10,
    threshold: float = 0.7,
) -> list[SearchResult]:
    """Embed query, run pgvector cosine similarity, return disclosure pointers."""

def get_disclosure_content(disclosure_doc_id: str, conn: psycopg.Connection) -> str:
    """Get the content of a disclosure doc for drill-down."""

def get_children(disclosure_doc_id: str, conn: psycopg.Connection) -> list[dict]:
    """Get child disclosure docs for navigating deeper."""

def get_parent_chain(disclosure_doc_id: str, conn: psycopg.Connection) -> list[dict]:
    """Get ancestor chain for breadcrumb context. Returns root-first order."""
```

### Search Flow

1. `embed_query(query)` → 1024-dim vector
2. SQL: `SELECT c.*, 1 - (c.embedding <=> %s) AS score FROM chunks c WHERE 1 - (c.embedding <=> %s) >= %s ORDER BY score DESC LIMIT %s`
3. For each result, join disclosure_docs and documents tables
4. Return `SearchResult` with chunk (embedding stripped for output), score, document, disclosure_doc

### Navigation

- `get_children`: `SELECT id, title, level, ordering FROM disclosure_docs WHERE parent_id = %s ORDER BY ordering`
- `get_parent_chain`: Recursive CTE walking parent_id to root
- `get_disclosure_content`: Simple `SELECT content FROM disclosure_docs WHERE id = %s`

## DB Extensions (db.py)

New functions needed:

```python
def get_disclosure_doc(ddoc_id: str, conn) -> DisclosureDoc | None
def get_disclosure_docs_by_document(doc_id: str, conn, level: int | None = None) -> list[DisclosureDoc]
def get_children_disclosure_docs(parent_id: str, conn) -> list[DisclosureDoc]
def get_parent_chain(ddoc_id: str, conn) -> list[DisclosureDoc]
def delete_disclosure_docs_by_level(level: int, conn) -> int  # returns count deleted
def search_chunks(embedding: list[float], limit: int, threshold: float, conn) -> list[dict]
def update_disclosure_doc_parent(ddoc_id: str, parent_id: str, conn) -> None
```

## CLI Extensions (cli.py)

### `pointy-rag search QUERY`
- Flags: `--limit/-n` (default 10), `--threshold/-t` (default 0.7), `--level/-l` (filter), `--content/-c` (show content)
- Output: Rich table with columns: Score | Document | Level | Section | Children

### `pointy-rag drill DOC_ID`
- Positional: disclosure doc ID
- Output: Disclosure doc content + child docs listed
- Flags: `--content/-c` (show full content of children)

## Files Changed

| File | Action | Scope |
|------|--------|-------|
| `src/pointy_rag/disclosure.py` | Create | ~150 lines |
| `src/pointy_rag/pointer_mapper.py` | Create | ~80 lines |
| `src/pointy_rag/search.py` | Create | ~100 lines |
| `src/pointy_rag/db.py` | Extend | +~100 lines (query functions) |
| `src/pointy_rag/cli.py` | Extend | +~60 lines (search/drill commands) |
| `tests/test_disclosure.py` | Create | Mocked agent tests |
| `tests/test_pointer_mapper.py` | Create | Pure logic tests |
| `tests/test_search.py` | Create | Mocked DB tests |
| `tests/test_db.py` | Extend | New query function tests |

## Constraints Respected
- Level 3 is pure structural extraction (no agent calls) ✓
- Levels 2/1/0 use Claude agent — timeouts handled ✓
- Level 0 is idempotent (delete + recreate) ✓
- Search returns disclosure pointers, NOT raw chunk text ✓
