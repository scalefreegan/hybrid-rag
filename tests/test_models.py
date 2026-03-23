"""Unit tests for pointy_rag.models."""

import pytest
from pydantic import ValidationError

from pointy_rag.models import (
    Chunk,
    ContextSubgraph,
    DisclosureDoc,
    DisclosureLevel,
    Document,
    DocumentFormat,
    GraphEdge,
    GraphNode,
    SearchResult,
)


def test_disclosure_level_values():
    assert DisclosureLevel.library_catalog == 0
    assert DisclosureLevel.resource_index == 1
    assert DisclosureLevel.section_summary == 2
    assert DisclosureLevel.detailed_passage == 3


def test_document_format_is_enum():
    assert DocumentFormat.pdf == "pdf"
    assert DocumentFormat.epub == "epub"
    assert isinstance(DocumentFormat.pdf, DocumentFormat)
    assert list(DocumentFormat) == [DocumentFormat.pdf, DocumentFormat.epub]


def test_document_format_invalid():
    with pytest.raises(ValidationError):
        Document(title="T", format="docx", source_path="/t.docx")


def test_document_defaults():
    doc = Document(title="Test Doc", format="pdf", source_path="/path/to/file.pdf")
    assert doc.id  # auto-generated UUID
    assert doc.metadata == {}
    assert doc.created_at is not None
    assert doc.format == DocumentFormat.pdf


def test_document_custom_id():
    doc = Document(id="my-id", title="T", format="epub", source_path="/x.epub")
    assert doc.id == "my-id"


def test_document_empty_title_rejected():
    with pytest.raises(ValidationError):
        Document(title="", format="pdf", source_path="/f.pdf")


def test_document_empty_source_path_rejected():
    with pytest.raises(ValidationError):
        Document(title="T", format="pdf", source_path="")


def test_disclosure_doc_defaults():
    ddoc = DisclosureDoc(
        document_id="doc-1",
        level=DisclosureLevel.section_summary,
        title="Section 1",
        content="Some content here.",
    )
    assert ddoc.id
    assert ddoc.parent_id is None
    assert ddoc.ordering == 0
    assert ddoc.level == DisclosureLevel.section_summary


def test_disclosure_doc_with_parent():
    parent = DisclosureDoc(
        document_id="doc-1",
        level=DisclosureLevel.resource_index,
        title="Parent",
        content="Parent content",
    )
    child = DisclosureDoc(
        document_id="doc-1",
        parent_id=parent.id,
        level=DisclosureLevel.section_summary,
        title="Child",
        content="Child content",
        ordering=1,
    )
    assert child.parent_id == parent.id


def test_disclosure_doc_empty_fields_rejected():
    with pytest.raises(ValidationError):
        DisclosureDoc(
            document_id="",
            level=DisclosureLevel.section_summary,
            title="T",
            content="C",
        )


def test_chunk_defaults():
    chunk = Chunk(disclosure_doc_id="ddoc-1", content="A chunk of text.")
    assert chunk.id
    assert chunk.embedding is None
    assert chunk.metadata == {}


def test_chunk_with_embedding():
    embedding = [0.1] * 1024
    chunk = Chunk(disclosure_doc_id="ddoc-1", content="text", embedding=embedding)
    assert len(chunk.embedding) == 1024


def test_chunk_empty_content_rejected():
    with pytest.raises(ValidationError):
        Chunk(disclosure_doc_id="ddoc-1", content="")


def test_search_result():
    chunk = Chunk(disclosure_doc_id="ddoc-1", content="result text")
    result = SearchResult(chunk=chunk, score=0.95)
    assert result.score == 0.95
    assert result.document is None
    assert result.disclosure_doc is None


# ---------------------------------------------------------------------------
# GraphNode / GraphEdge / ContextSubgraph
# ---------------------------------------------------------------------------


def test_graph_node_minimal():
    node = GraphNode(node_id="n1", node_type="disclosure")
    assert node.node_id == "n1"
    assert node.level is None
    assert node.title is None
    assert node.document_id is None


def test_graph_node_full():
    node = GraphNode(
        node_id="n1", node_type="chunk", level=2, title="Section", document_id="doc-1"
    )
    assert node.level == 2
    assert node.title == "Section"


def test_graph_edge_minimal():
    edge = GraphEdge(type="SIMILAR_TO", source="a", target="b")
    assert edge.score is None


def test_graph_edge_with_score():
    edge = GraphEdge(type="SIMILAR_TO", source="a", target="b", score=0.9)
    assert edge.score == 0.9


def test_context_subgraph_defaults():
    sg = ContextSubgraph(nodes=[], edges=[], matches=[])
    assert sg.hierarchy == {}


def test_context_subgraph_full():
    nodes = [GraphNode(node_id="n1", node_type="disclosure")]
    edges = [GraphEdge(type="SIMILAR_TO", source="n1", target="n2", score=0.8)]
    sg = ContextSubgraph(
        nodes=nodes,
        edges=edges,
        matches=["n1"],
        hierarchy={"n1": ["n2"]},
    )
    assert len(sg.nodes) == 1
    assert sg.nodes[0].node_id == "n1"
    assert sg.edges[0].score == 0.8
