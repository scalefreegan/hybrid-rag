# pointy-rag

Hybrid RAG with progressive disclosure hierarchy, Voyage AI embeddings, and pgvector.

[![CI](https://github.com/scalefreegan/hybrid-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/scalefreegan/hybrid-rag/actions/workflows/ci.yml)

## Overview

pointy-rag ingests PDF and EPUB documents, builds a 4-level progressive disclosure hierarchy, embeds chunks with Voyage AI, stores everything in PostgreSQL/pgvector, and provides semantic search with hierarchical drill-down navigation.

Instead of returning flat text chunks, search results include **pointers** into a disclosure hierarchy — letting you start with a high-level summary and drill down to detailed passages on demand.

## Architecture

```
PDF/EPUB
  │
  ▼
converter ──► markdown
  │
  ├──► chunker ──► embed (Voyage AI) ──► chunks table (pgvector)
  │
  └──► disclosure hierarchy (Claude agent)
         │
         ├── L0: library_catalog    (1 per library)
         ├── L1: resource_index     (1 per document)
         ├── L2: section_summary    (N per document)
         └── L3: detailed_passage   (N per document)
                   │
                   ▼
              pointer_mapper ──► chunks linked to disclosure docs
                                    │
                                    ▼
                              search ──► pointer-based results ──► drill navigation
```

### Module map

| Module | Role |
|--------|------|
| `cli.py` | Typer CLI with 5 commands |
| `config.py` | Settings from `.env` / environment |
| `converter.py` | PDF/EPUB to markdown (agent or fallback) |
| `chunker.py` | Markdown-aware text chunking with overlap |
| `embeddings.py` | Voyage AI embedding client (voyage-4-lite, 1024-dim) |
| `db.py` | PostgreSQL/pgvector schema, CRUD, connection management |
| `models.py` | Pydantic data models (Document, DisclosureDoc, Chunk, SearchResult) |
| `disclosure.py` | 4-level disclosure hierarchy generator |
| `claude_agent.py` | Headless Claude Code subprocess wrapper |
| `pointer_mapper.py` | Maps text chunks to disclosure docs by heading/Jaccard similarity |
| `ingest.py` | End-to-end ingestion pipeline |
| `search.py` | Vector search with disclosure pointers and drill-down |

## Prerequisites

- **Python 3.11+**
- **[UV](https://docs.astral.sh/uv/)** package manager
- **PostgreSQL 12+** with the [pgvector](https://github.com/pgvector/pgvector) extension
- **Voyage AI API key** — get one at [dash.voyageai.com](https://dash.voyageai.com)
- **Claude Code CLI** (optional) — enables agent-powered document conversion and disclosure generation. Without it, pointy-rag falls back to library-based extraction (no disclosure hierarchy).

## Quickstart

```bash
# Clone and install
git clone https://github.com/scalefreegan/hybrid-rag.git
cd hybrid-rag
uv sync --dev

# Configure
cp .env.example .env
# Edit .env — fill in VOYAGE_API_KEY and POINTY_DATABASE_URL

# Initialize database tables
uv run pointy-rag init

# Ingest documents
uv run pointy-rag ingest paper.pdf book.epub

# Search
uv run pointy-rag search "transformer attention mechanism"

# Drill into a result to see children
uv run pointy-rag drill <disclosure-doc-id>
```

## CLI Reference

| Command | Description | Key flags |
|---------|-------------|-----------|
| `init` | Create database tables and indexes | `--database-url` |
| `ingest` | Ingest PDF/EPUB files into the vector store | `--output-dir`, `--no-agent` |
| `search` | Semantic search with pointer-based results | `--limit`, `--threshold`, `--level`, `--content` |
| `drill` | Drill into a disclosure doc and view its children | `--content` |
| `ls` | List all ingested documents with chunk/disclosure counts | |

Run `uv run pointy-rag <command> --help` for full flag documentation.

### Examples

```bash
# Ingest without Claude agent (fallback extraction, no disclosure hierarchy)
uv run pointy-rag ingest --no-agent document.pdf

# Search with content preview, filtered to section summaries (L2)
uv run pointy-rag search "neural networks" --level 2 --content

# List all documents
uv run pointy-rag ls
```

## Disclosure Hierarchy

pointy-rag organizes every ingested document into a 4-level tree:

```
L0  Library Catalog          (1 per library — spans all documents)
 └─ L1  Resource Index        (1 per document — overview)
     └─ L2  Section Summary   (N per document — executive summary per section)
         └─ L3  Detailed Passage  (N per document — full section content)
```

### How levels are generated

| Level | Name | Generation method |
|-------|------|-------------------|
| L3 | `detailed_passage` | Structural extraction — split markdown on headings |
| L2 | `section_summary` | Claude agent summarizes each L3 passage |
| L1 | `resource_index` | Claude agent summarizes all L2 summaries into a document overview |
| L0 | `library_catalog` | Claude agent summarizes all L1 indexes into a library-wide catalog |

Generation is bottom-up: L3 is extracted structurally, then L2, L1, and L0 are progressively summarized by the Claude agent. The library catalog (L0) is regenerated after each ingestion to incorporate new documents.

When using `--no-agent`, chunks are stored with a placeholder disclosure doc and no hierarchy is built.

## Claude Code Skill

This repo ships a [Claude Code skill](https://code.claude.com/docs/en/skills) so Claude can guide you through CLI usage interactively. Install it with the [skills CLI](https://github.com/vercel-labs/skills):

```bash
npx skills add scalefreegan/hybrid-rag --skill pointy-rag
```

Once installed, ask Claude things like "set up pointy-rag", "ingest these documents", or "search my documents" and it will have full CLI reference available.

## Development

```bash
# Install dev dependencies
uv sync --dev

# Run tests
uv run pytest

# Lint
uv run ruff check src/ tests/

# Format check
uv run ruff format --check src/ tests/
```

CI runs lint and tests on every push and pull request to `main`.

## Project Structure

```
src/pointy_rag/
├── __init__.py          # Package init
├── cli.py               # Typer CLI entry point
├── config.py            # Settings (env vars / .env)
├── converter.py         # PDF/EPUB → markdown conversion
├── chunker.py           # Markdown-aware text chunking
├── embeddings.py        # Voyage AI embedding client
├── db.py                # PostgreSQL/pgvector database layer
├── models.py            # Pydantic data models
├── disclosure.py        # Disclosure hierarchy generator
├── claude_agent.py      # Claude Code subprocess wrapper
├── pointer_mapper.py    # Chunk → disclosure doc mapping
├── ingest.py            # End-to-end ingestion pipeline
└── search.py            # Vector search + drill-down navigation
```
