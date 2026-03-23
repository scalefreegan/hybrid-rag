"""Pydantic data models for pointy-rag."""

from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


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


class GraphNode(BaseModel):
    node_id: str
    node_type: str  # "disclosure" | "chunk"
    level: int | None = None
    title: str | None = None
    document_id: str | None = None


class GraphEdge(BaseModel):
    type: str  # "SIMILAR_TO" | "CONTAINS"
    source: str
    target: str
    score: float | None = None


class ContextSubgraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    matches: list[str]
    hierarchy: dict[str, list[str]] = Field(default_factory=dict)


class GraphSearchResult(BaseModel):
    vector_results: list[SearchResult]  # Original pgvector matches
    reference_document: str | None = None  # Assembled llms.txt markdown
    node_count: int = Field(ge=0)  # Nodes in context subgraph
    edge_count: int = Field(ge=0)  # Edges traversed


class ExploreResult(BaseModel):
    vector_results: list[SearchResult]  # Original pgvector matches
    overview: str | None = None  # Layer 1: compact structured index
    llms_txt: str | None = None  # Layer 2: detailed navigational TOC
    contents: dict[str, str] = Field(default_factory=dict)  # node_id -> md
    node_count: int = Field(ge=0)  # Nodes in context subgraph
    edge_count: int = Field(ge=0)  # Edges traversed

    @model_validator(mode="after")
    def _layers_consistent(self) -> "ExploreResult":
        """Ensure the three explore layers are all-or-nothing."""
        has_overview = self.overview is not None
        has_llms = self.llms_txt is not None
        if has_overview != has_llms:
            msg = "overview and llms_txt must both be None or both be set"
            raise ValueError(msg)
        return self
