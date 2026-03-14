"""Unit tests for pointy_rag.models."""


from pointy_rag.models import (
    Chunk,
    DisclosureDoc,
    DisclosureLevel,
    Document,
    SearchResult,
)


def test_disclosure_level_values():
    assert DisclosureLevel.library_catalog == 0
    assert DisclosureLevel.resource_index == 1
    assert DisclosureLevel.section_summary == 2
    assert DisclosureLevel.detailed_passage == 3


def test_document_defaults():
    doc = Document(title="Test Doc", format="pdf", source_path="/path/to/file.pdf")
    assert doc.id  # auto-generated UUID
    assert doc.metadata == {}
    assert doc.created_at is not None
    assert doc.format == "pdf"


def test_document_custom_id():
    doc = Document(id="my-id", title="T", format="epub", source_path="/x.epub")
    assert doc.id == "my-id"


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


def test_chunk_defaults():
    chunk = Chunk(disclosure_doc_id="ddoc-1", content="A chunk of text.")
    assert chunk.id
    assert chunk.embedding is None
    assert chunk.metadata == {}


def test_chunk_with_embedding():
    embedding = [0.1] * 1024
    chunk = Chunk(disclosure_doc_id="ddoc-1", content="text", embedding=embedding)
    assert len(chunk.embedding) == 1024


def test_search_result():
    chunk = Chunk(disclosure_doc_id="ddoc-1", content="result text")
    result = SearchResult(chunk=chunk, score=0.95)
    assert result.score == 0.95
    assert result.document is None
    assert result.disclosure_doc is None
