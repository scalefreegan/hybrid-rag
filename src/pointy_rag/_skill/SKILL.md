---
name: pointy-rag
description: "ingest documents, set up pointy-rag, search my documents, initialize the database, how do I use pointy-rag, hybrid RAG CLI, explore mode, knowledge graph, graph search, explore query"
---

# pointy-rag CLI Guide

Pointy-rag is a hybrid RAG CLI that converts PDF/EPUB documents into a searchable vector store with a multi-level disclosure hierarchy. An optional knowledge graph (Apache AGE) adds cross-document semantic linking, and explore mode produces structured three-layer packages for AI agent consumption. This skill provides quick reference for all commands.

## Prerequisites check

Before using pointy-rag, verify your environment:

```bash
# Python 3.11+ required
python3 --version

# UV package manager
uv --version

# PostgreSQL with pgvector extension running
psql -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
```

Ensure your `.env` file (or environment) has:

```
VOYAGE_API_KEY=your-voyage-ai-api-key
POINTY_DATABASE_URL=postgresql://localhost:5432/pointy_rag
```

If `POINTY_DATABASE_URL` is not set, it defaults to `postgresql://localhost:5432/pointy_rag`.

## Database setup

Initialize the database with tables and indexes:

```bash
pointy-rag init
```

To use a specific database URL instead of the env var:

```bash
pointy-rag init --database-url "postgresql://user:pass@host:5432/dbname"
```

This creates:
- **`documents`** table — ingested document metadata (title, format, source path)
- **`disclosure_docs`** table — hierarchical disclosure documents with parent/child relationships and levels 0-3
- **`chunks`** table — text chunks with 1024-dim Voyage AI embeddings
- **pgvector HNSW index** on the chunks embedding column (cosine similarity, m=16, ef_construction=64)
- The `vector` PostgreSQL extension (if not already installed)

## Ingesting documents

Ingest PDF or EPUB files into the vector store:

```bash
pointy-rag ingest document.pdf another.epub
```

Options:
- `--output-dir`, `-o` — Directory for converted markdown files (default: `./converted`)
- `--no-agent` — Skip the Claude agent for conversion and disclosure generation (fallback mode, no disclosure hierarchy)

```bash
# Save markdown output to a specific directory
pointy-rag ingest report.pdf --output-dir ./markdown_output

# Ingest without Claude agent (faster, but no disclosure hierarchy)
pointy-rag ingest report.pdf --no-agent
```

What happens during ingestion:
1. **Convert** — PDF/EPUB is converted to markdown (uses Claude agent by default)
2. **Chunk** — Markdown is split into text chunks
3. **Embed** — Chunks are embedded using Voyage AI (1024-dim vectors)
4. **Store** — Document record is saved to the database
5. **Disclosure hierarchy** — Claude agent generates a multi-level disclosure tree (skipped with `--no-agent`)
6. **Map** — Chunks are mapped to disclosure documents
7. **Library catalog** — The L0 library catalog is regenerated to include the new document
8. **Knowledge graph** — Nodes and similarity edges are created (if KG enabled)

Re-ingesting a file with the same source path deletes existing data before re-inserting.

## Searching

Search the vector store with pointer-based retrieval:

```bash
pointy-rag search "your query here"
```

Options:
- `--limit`, `-n` — Number of results (default: 10, max: 100)
- `--threshold`, `-t` — Minimum similarity score (default: 0.7)
- `--level`, `-l` — Filter by disclosure level 0-3 (see levels reference below)
- `--content`, `-c` — Show chunk text content in results
- `--graph`, `-g` — Expand results via knowledge graph (same as `graph-search`)

```bash
# Top 5 results with content preview
pointy-rag search "machine learning basics" --limit 5 --content

# Only section summaries (level 2)
pointy-rag search "neural networks" --level 2

# Lower threshold for broader results
pointy-rag search "transformer architecture" --threshold 0.5

# Search with knowledge graph enrichment
pointy-rag search "attention mechanisms" --graph
```

Results show: similarity score, document title, disclosure level, section title, and child count. The child count tells you if there's deeper content to explore with `drill`.

## Drilling down

Navigate the disclosure hierarchy by drilling into a disclosure doc from search results:

```bash
pointy-rag drill <disclosure-doc-id>
```

Options:
- `--content`, `-c` — Show full text content of child documents

```bash
# See children of a disclosure doc
pointy-rag drill abc123def456

# Include content preview for each child
pointy-rag drill abc123def456 --content
```

The breadcrumb trail shows the ancestry path from the library catalog down to the current node. Children are listed with their ID (use the ID to drill deeper), level, and title.

## Listing documents

Check what's been ingested:

```bash
pointy-rag ls
```

Shows a table with: document ID, title, format (pdf/epub), chunk count, disclosure doc count, and ingestion date.

## Knowledge graph

The knowledge graph uses [Apache AGE](https://age.apache.org/) to discover cross-document relationships. When enabled, ingestion creates graph nodes for disclosure docs and chunks, then links semantically similar content via `SIMILAR_TO` edges.

### Graph search

Search and expand results via the knowledge graph, producing an llms.txt-style reference document with hierarchical context:

```bash
pointy-rag graph-search "attention mechanisms"
```

Options:
- `--limit`, `-n` — Number of vector results (default: 10)
- `--threshold`, `-t` — Minimum similarity score (default: 0.7)
- `--levels-up` — Hierarchy levels to walk up per match (default: 1)
- `--no-similar` — Skip SIMILAR_TO edge traversal

```bash
# Deeper hierarchy traversal
pointy-rag graph-search "neural networks" --levels-up 3

# Skip cross-document similarity edges
pointy-rag graph-search "transformers" --no-similar
```

### Graph status

Show knowledge graph statistics (node/edge counts):

```bash
pointy-rag graph-status
```

### Graph backfill

Migrate existing PostgreSQL data into the knowledge graph (one-time, for data ingested before KG was enabled):

```bash
pointy-rag graph-backfill
```

## Explore mode

Explore mode produces a **three-layer progressive disclosure package** — ideal for AI agents that need structured context with drill-down capability. It uses deeper traversal defaults than `graph-search` (3 hierarchy levels up, 2 similarity hops).

```bash
pointy-rag explore "transformer architecture"
```

Options:
- `--limit`, `-n` — Number of vector results (default: 10)
- `--threshold`, `-t` — Minimum similarity score (default: 0.6)
- `--levels-up` — Hierarchy levels to walk up per match (default: 3)
- `--no-similar` — Skip SIMILAR_TO edge traversal
- `--output`, `-o` — Output directory (default: `./explore-<timestamp>`)

```bash
# Explore with custom output directory
pointy-rag explore "attention mechanisms" --output ./my-explore

# Broader results with lower threshold
pointy-rag explore "machine learning" --threshold 0.4 --limit 20
```

### Output structure

```
explore-output/
├── overview.md          # Layer 1: ultra-compact index
├── llms.txt             # Layer 2: navigational TOC
└── contents/            # Layer 3: full content per node
    ├── <node-id>.md
    └── ...
```

| Layer | File | Purpose |
|-------|------|---------|
| 1 | `overview.md` | Minimal-token structured index — stats, hierarchy tree, match/related badges |
| 2 | `llms.txt` | Navigational TOC with heading depths, level labels, content snippets, and `contents/{id}.md` links |
| 3 | `contents/{id}.md` | Full node content with YAML frontmatter (node_id, title, level, document, role) and ancestor context |

Each `contents/*.md` file includes ancestor content inlined above the node's own content, so agents get full hierarchical context without fetching parent files.

## Disclosure levels reference

| Level | Name | Description |
|-------|------|-------------|
| 0 | `library_catalog` | Top-level overview of all ingested documents |
| 1 | `resource_index` | Per-document table of contents / index |
| 2 | `section_summary` | Summary of a document section |
| 3 | `detailed_passage` | Fine-grained passage with full detail |

Lower levels are broader; higher levels are more specific. Search results include the level so you know where you are in the hierarchy.

## Common workflows

### Start from scratch

```bash
# 1. Initialize the database
pointy-rag init

# 2. Ingest your documents
pointy-rag ingest paper.pdf textbook.epub

# 3. Search
pointy-rag search "your topic"
```

### Add more documents

```bash
# 1. Ingest additional files (existing data is preserved)
pointy-rag ingest new_paper.pdf

# 2. Verify it was added
pointy-rag ls
```

### Drill into search results

```bash
# 1. Search and note disclosure doc IDs from the Section column
pointy-rag search "quantum computing" --content

# 2. Drill into an interesting result (use the ID from search output)
pointy-rag drill <doc-id>

# 3. Keep drilling into children for more detail
pointy-rag drill <child-id> --content
```

### Explore a topic for an AI agent

```bash
# 1. Generate a three-layer explore package
pointy-rag explore "quantum computing" --output ./quantum-context

# 2. The agent reads overview.md first (minimal tokens)
# 3. If it needs more detail, it reads llms.txt
# 4. For full content on specific nodes, it reads contents/<node-id>.md
```

### Enable knowledge graph on existing data

```bash
# 1. Set environment variable
export POINTY_KG_ENABLED=true

# 2. Backfill existing documents into the graph
pointy-rag graph-backfill

# 3. Verify graph was populated
pointy-rag graph-status

# 4. Use graph-enriched search
pointy-rag graph-search "your query"
```

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VOYAGE_API_KEY` | Yes | _(none)_ | Voyage AI API key for embedding generation |
| `POINTY_DATABASE_URL` | No | `postgresql://localhost:5432/pointy_rag` | PostgreSQL connection string with pgvector |
| `POINTY_KG_ENABLED` | No | `true` | Enable/disable knowledge graph features |
| `POINTY_KG_SIMILARITY_THRESHOLD` | No | `0.85` | Minimum cosine similarity for SIMILAR_TO edges |
| `POINTY_KG_MAX_NEIGHBORS` | No | `20` | Maximum similarity edges per node |
| `POINTY_KG_HIERARCHY_LEVELS_UP` | No | `1` | Default hierarchy levels to walk up in graph-search |
| `POINTY_KG_SIMILAR_HOPS` | No | `1` | Default SIMILAR_TO traversal depth |
