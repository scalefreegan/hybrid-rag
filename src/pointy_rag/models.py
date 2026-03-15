"""Pydantic data models for pointy-rag."""

from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class DisclosureLevel(IntEnum):
    library_catalog = 0
    resource_index = 1
    section_summary = 2
    detailed_passage = 3


class DocumentFormat(StrEnum):
    pdf = "pdf"
    epub = "epub"


class Document(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = Field(min_length=1)
    format: DocumentFormat
    source_path: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DisclosureDoc(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    document_id: str = Field(min_length=1)
    parent_id: str | None = None
    level: DisclosureLevel
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    ordering: int = 0


class Chunk(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    disclosure_doc_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    embedding: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    chunk: Chunk
    score: float
    document: Document | None = None
    disclosure_doc: DisclosureDoc | None = None
