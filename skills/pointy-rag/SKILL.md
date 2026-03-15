---
name: pointy-rag
description: "ingest documents, set up pointy-rag, search my documents, initialize the database, how do I use pointy-rag, hybrid RAG CLI"
---

# pointy-rag CLI Guide

Pointy-rag is a hybrid RAG CLI that converts PDF/EPUB documents into a searchable vector store with a multi-level disclosure hierarchy. This skill provides quick reference for database setup, document ingestion, and pointer-based search.

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

```bash
# Top 5 results with content preview
pointy-rag search "machine learning basics" --limit 5 --content

# Only section summaries (level 2)
pointy-rag search "neural networks" --level 2

# Lower threshold for broader results
pointy-rag search "transformer architecture" --threshold 0.5
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

### Explore search results

```bash
# 1. Search and note disclosure doc IDs from the Section column
pointy-rag search "quantum computing" --content

# 2. Drill into an interesting result (use the ID from search output)
pointy-rag drill <doc-id>

# 3. Keep drilling into children for more detail
pointy-rag drill <child-id> --content
```

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VOYAGE_API_KEY` | Yes | _(none)_ | Voyage AI API key for embedding generation |
| `POINTY_DATABASE_URL` | No | `postgresql://localhost:5432/pointy_rag` | PostgreSQL connection string with pgvector |
