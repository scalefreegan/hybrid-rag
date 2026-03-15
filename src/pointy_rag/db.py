"""PostgreSQL + pgvector database layer for pointy-rag."""

import re
from collections.abc import Generator
from contextlib import contextmanager

import psycopg
import psycopg.rows
from pgvector.psycopg import register_vector

from pointy_rag.config import get_settings
from pointy_rag.models import Chunk, DisclosureDoc, Document

DDL = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    format TEXT NOT NULL,
    source_path TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS disclosure_docs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    parent_id TEXT REFERENCES disclosure_docs(id),
    level INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    ordering INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_disclosure_docs_parent
    ON disclosure_docs(parent_id);

CREATE INDEX IF NOT EXISTS idx_disclosure_docs_doc_level
    ON disclosure_docs(document_id, level);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    disclosure_doc_id TEXT NOT NULL REFERENCES disclosure_docs(id),
    content TEXT NOT NULL,
    embedding vector(1024),
    metadata JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
"""


def _split_ddl(ddl: str) -> list[str]:
    """Split a DDL string into individual SQL statements."""
    statements = re.split(r";\s*\n", ddl.strip())
    return [s.strip().rstrip(";") + ";" for s in statements if s.strip()]


@contextmanager
def get_connection(
    database_url: str | None = None,
) -> Generator[psycopg.Connection, None, None]:
    url = database_url or get_settings().database_url
    with psycopg.connect(url) as conn:
        register_vector(conn)
        yield conn


def create_tables(database_url: str | None = None) -> None:
    """Create all tables and indexes idempotently."""
    with get_connection(database_url) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        for stmt in _split_ddl(DDL):
            conn.execute(stmt)
        conn.commit()


def insert_document(doc: Document, conn: psycopg.Connection) -> None:
    conn.execute(
        """
        INSERT INTO documents (id, title, format, source_path, metadata, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            title = EXCLUDED.title,
            format = EXCLUDED.format,
            source_path = EXCLUDED.source_path,
            metadata = EXCLUDED.metadata
        """,
        (
            doc.id,
            doc.title,
            doc.format.value,
            doc.source_path,
            doc.metadata,
            doc.created_at,
        ),
    )


def insert_disclosure_doc(ddoc: DisclosureDoc, conn: psycopg.Connection) -> None:
    conn.execute(
        """
        INSERT INTO disclosure_docs
            (id, document_id, parent_id, level, title, content, ordering)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            ddoc.id,
            ddoc.document_id,
            ddoc.parent_id,
            int(ddoc.level),
            ddoc.title,
            ddoc.content,
            ddoc.ordering,
        ),
    )


def insert_chunk(chunk: Chunk, conn: psycopg.Connection) -> None:
    conn.execute(
        """
        INSERT INTO chunks (id, disclosure_doc_id, content, embedding, metadata)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            chunk.id,
            chunk.disclosure_doc_id,
            chunk.content,
            chunk.embedding,
            chunk.metadata,
        ),
    )


def get_document(doc_id: str, conn: psycopg.Connection) -> Document | None:
    row = conn.cursor(row_factory=psycopg.rows.dict_row).execute(
        "SELECT id, title, format, source_path, metadata, created_at"
        " FROM documents WHERE id = %s",
        (doc_id,),
    ).fetchone()
    if row is None:
        return None
    return Document(
        id=row["id"],
        title=row["title"],
        format=row["format"],
        source_path=row["source_path"],
        metadata=row["metadata"],
        created_at=row["created_at"],
    )
