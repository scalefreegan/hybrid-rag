"""Tests for the pointer mapper."""

import pytest

from pointy_rag.chunker import TextChunk
from pointy_rag.models import DisclosureDoc, DisclosureLevel
from pointy_rag.pointer_mapper import (
    _jaccard_similarity,
    _normalize,
    map_chunks_to_disclosure,
)


@pytest.fixture
def level3_docs():
    return [
        DisclosureDoc(
            id="dd-intro",
            document_id="doc1",
            level=DisclosureLevel.detailed_passage,
            title="Introduction",
            content="This is the introduction to the paper about machine learning.",
            ordering=0,
        ),
        DisclosureDoc(
            id="dd-methods",
            document_id="doc1",
            level=DisclosureLevel.detailed_passage,
            title="Methods",
            content="We used gradient descent and backpropagation methods.",
            ordering=1,
        ),
        DisclosureDoc(
            id="dd-results",
            document_id="doc1",
            level=DisclosureLevel.detailed_passage,
            title="Results",
            content="The results show significant improvement in accuracy.",
            ordering=2,
        ),
    ]


@pytest.fixture
def all_docs(level3_docs):
    """Mix of all levels — mapper should only use L3."""
    return [
        DisclosureDoc(
            id="dd-l1",
            document_id="doc1",
            level=DisclosureLevel.resource_index,
            title="Full Doc",
            content="Resource index",
            ordering=0,
        ),
    ] + level3_docs


class TestNormalize:
    def test_strips_heading_markers(self):
        assert _normalize("## Introduction") == "introduction"

    def test_strips_whitespace(self):
        assert _normalize("  Methods  ") == "methods"

    def test_handles_no_markers(self):
        assert _normalize("Results") == "results"


class TestJaccardSimilarity:
    def test_identical_strings(self):
        assert _jaccard_similarity("hello world", "hello world") == 1.0

    def test_no_overlap(self):
        assert _jaccard_similarity("hello world", "foo bar") == 0.0

    def test_partial_overlap(self):
        score = _jaccard_similarity("hello world foo", "hello world bar")
        assert 0.4 < score < 0.6  # 2/4 = 0.5

    def test_empty_string(self):
        assert _jaccard_similarity("", "hello") == 0.0


class TestMapChunksToDisclosure:
    def test_heading_match(self, all_docs):
        chunks = [
            TextChunk(
                content="Some intro text",
                token_count=10,
                chunk_index=0,
                heading="## Introduction",
            ),
        ]
        result = map_chunks_to_disclosure(chunks, all_docs)
        assert len(result) == 1
        assert result[0].disclosure_doc_id == "dd-intro"

    def test_heading_match_case_insensitive(self, all_docs):
        chunks = [
            TextChunk(
                content="Method details",
                token_count=10,
                chunk_index=0,
                heading="### METHODS",
            ),
        ]
        result = map_chunks_to_disclosure(chunks, all_docs)
        assert result[0].disclosure_doc_id == "dd-methods"

    def test_jaccard_fallback(self, all_docs):
        chunks = [
            TextChunk(
                content="gradient descent and backpropagation methods are used",
                token_count=10,
                chunk_index=0,
                heading=None,
            ),
        ]
        result = map_chunks_to_disclosure(chunks, all_docs)
        assert result[0].disclosure_doc_id == "dd-methods"

    def test_unmapped_chunk_defaults_to_first(self, all_docs):
        chunks = [
            TextChunk(
                content="xyzzy completely unrelated gibberish",
                token_count=10,
                chunk_index=0,
                heading=None,
            ),
        ]
        result = map_chunks_to_disclosure(chunks, all_docs)
        assert result[0].disclosure_doc_id == "dd-intro"  # first by ordering
        assert result[0].metadata.get("unmapped") is True

    def test_multiple_chunks(self, all_docs):
        chunks = [
            TextChunk(
                content="Intro text", token_count=5, chunk_index=0,
                heading="## Introduction",
            ),
            TextChunk(
                content="Method text", token_count=5, chunk_index=1,
                heading="## Methods",
            ),
            TextChunk(
                content="Result text", token_count=5, chunk_index=2,
                heading="## Results",
            ),
        ]
        result = map_chunks_to_disclosure(chunks, all_docs)
        assert len(result) == 3
        assert result[0].disclosure_doc_id == "dd-intro"
        assert result[1].disclosure_doc_id == "dd-methods"
        assert result[2].disclosure_doc_id == "dd-results"

    def test_no_level3_docs_raises(self):
        non_l3 = [
            DisclosureDoc(
                id="dd-l1",
                document_id="doc1",
                level=DisclosureLevel.resource_index,
                title="Index",
                content="Resource",
                ordering=0,
            ),
        ]
        chunks = [
            TextChunk(
                content="text", token_count=5,
                chunk_index=0, heading=None,
            ),
        ]
        with pytest.raises(ValueError, match="No Level 3"):
            map_chunks_to_disclosure(chunks, non_l3)

    def test_chunk_model_has_no_embedding(self, all_docs):
        chunks = [
            TextChunk(
                content="Some text", token_count=5, chunk_index=0,
                heading="## Introduction",
            ),
        ]
        result = map_chunks_to_disclosure(chunks, all_docs)
        assert result[0].embedding is None
