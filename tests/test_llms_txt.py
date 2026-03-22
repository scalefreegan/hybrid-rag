"""Unit tests for pointy_rag.llms_txt — pure logic, no live database."""

from unittest.mock import MagicMock, patch

from pointy_rag.llms_txt import _blockquote, _fetch_node_content, assemble_reference
from pointy_rag.models import DisclosureDoc, Document

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ddoc(
    node_id: str, title: str, content: str, level: int = 1, doc_id: str = "doc-1"
) -> DisclosureDoc:
    return DisclosureDoc(
        id=node_id,
        document_id=doc_id,
        level=level,
        title=title,
        content=content,
    )


def _doc(doc_id: str, title: str) -> Document:
    from pointy_rag.models import DocumentFormat

    return Document(
        id=doc_id,
        title=title,
        format=DocumentFormat.pdf,
        source_path=f"/tmp/{doc_id}.pdf",  # noqa: S108
    )


def _node(
    node_id: str,
    title: str,
    level: int,
    doc_id: str = "doc-1",
    node_type: str = "disclosure",
) -> dict:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "level": level,
        "title": title,
        "document_id": doc_id,
    }


def _subgraph(nodes, matches, hierarchy, edges=None):
    return {
        "nodes": nodes,
        "matches": matches,
        "hierarchy": hierarchy,
        "edges": edges or [],
    }


# ---------------------------------------------------------------------------
# _blockquote
# ---------------------------------------------------------------------------


def test_blockquote_single_line():
    assert _blockquote("hello") == "> hello"


def test_blockquote_multiline():
    result = _blockquote("line1\nline2")
    assert result == "> line1\n> line2"


def test_blockquote_empty_line_in_middle():
    result = _blockquote("first\n\nthird")
    assert result == "> first\n>\n> third"


# ---------------------------------------------------------------------------
# _fetch_node_content
# ---------------------------------------------------------------------------


def test_fetch_node_content_disclosure(mock_conn):
    ddoc = _ddoc("n1", "Title", "disclosure content")
    with patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc):
        result = _fetch_node_content("n1", "disclosure", mock_conn)
    assert result == "disclosure content"


def test_fetch_node_content_disclosure_missing(mock_conn):
    with patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=None):
        result = _fetch_node_content("n1", "disclosure", mock_conn)
    assert result == ""


def test_fetch_node_content_chunk(mock_conn):
    mock_cursor = MagicMock()
    mock_cursor.execute.return_value = mock_cursor
    mock_cursor.fetchone.return_value = {"content": "chunk content"}
    mock_conn.cursor.return_value = mock_cursor

    result = _fetch_node_content("c1", "chunk", mock_conn)
    assert result == "chunk content"


def test_fetch_node_content_chunk_missing(mock_conn):
    mock_cursor = MagicMock()
    mock_cursor.execute.return_value = mock_cursor
    mock_cursor.fetchone.return_value = None
    mock_conn.cursor.return_value = mock_cursor

    result = _fetch_node_content("c1", "chunk", mock_conn)
    assert result == ""


# ---------------------------------------------------------------------------
# assemble_reference — heading structure
# ---------------------------------------------------------------------------


def test_heading_depth_matches_node_level(mock_conn):
    """Node at level N should produce N+1 hashes."""
    ancestor = _node("anc-1", "Root Section", level=0)
    match_node = _node("match-1", "Match Node", level=1)
    sg = _subgraph(
        nodes=[ancestor, match_node],
        matches=["match-1"],
        hierarchy={"anc-1": ["match-1"]},
    )
    ddocs = {
        "anc-1": _ddoc("anc-1", "Root Section", "root content", level=0),
        "match-1": _ddoc("match-1", "Match Node", "match content", level=1),
    }

    with patch(
        "pointy_rag.llms_txt.db.get_disclosure_doc",
        side_effect=lambda nid, _: ddocs.get(nid),
    ):
        result = assemble_reference(sg, mock_conn)

    assert "# Root Section [ref:anc-1]" in result
    assert "## Match: Match Node [ref:match-1]" in result


def test_heading_l0_is_single_hash(mock_conn):
    match_node = _node("m1", "Top Doc", level=0)
    sg = _subgraph(nodes=[match_node], matches=["m1"], hierarchy={"m1": []})
    ddoc = _ddoc("m1", "Top Doc", "content", level=0)

    with patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc):
        result = assemble_reference(sg, mock_conn)

    assert "# Match: Top Doc [ref:m1]" in result


# ---------------------------------------------------------------------------
# assemble_reference — [ref:] pointers
# ---------------------------------------------------------------------------


def test_ref_pointers_present_for_all_nodes(mock_conn):
    ancestor = _node("anc-1", "Ancestor", level=0)
    match_node = _node("m1", "Match", level=1)
    sg = _subgraph(
        nodes=[ancestor, match_node],
        matches=["m1"],
        hierarchy={"anc-1": ["m1"]},
    )
    ddocs = {
        "anc-1": _ddoc("anc-1", "Ancestor", "anc content", level=0),
        "m1": _ddoc("m1", "Match", "match content", level=1),
    }

    with patch(
        "pointy_rag.llms_txt.db.get_disclosure_doc",
        side_effect=lambda nid, _: ddocs.get(nid),
    ):
        result = assemble_reference(sg, mock_conn)

    assert "[ref:anc-1]" in result
    assert "[ref:m1]" in result


def test_ref_pointer_includes_node_id_exactly(mock_conn):
    node = _node("unique-id-42", "Node", level=1)
    sg = _subgraph(
        nodes=[node], matches=["unique-id-42"], hierarchy={"unique-id-42": []}
    )
    ddoc = _ddoc("unique-id-42", "Node", "content", level=1)

    with patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc):
        result = assemble_reference(sg, mock_conn)

    assert "[ref:unique-id-42]" in result


# ---------------------------------------------------------------------------
# assemble_reference — match prefix
# ---------------------------------------------------------------------------


def test_match_nodes_have_match_prefix(mock_conn):
    match_node = _node("m1", "Result Section", level=2)
    sg = _subgraph(nodes=[match_node], matches=["m1"], hierarchy={"m1": []})
    ddoc = _ddoc("m1", "Result Section", "content", level=2)

    with patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc):
        result = assemble_reference(sg, mock_conn)

    assert "Match: Result Section" in result


def test_ancestor_nodes_do_not_have_match_prefix(mock_conn):
    ancestor = _node("anc-1", "Parent", level=0)
    match_node = _node("m1", "Child", level=1)
    sg = _subgraph(
        nodes=[ancestor, match_node],
        matches=["m1"],
        hierarchy={"anc-1": ["m1"]},
    )
    ddocs = {
        "anc-1": _ddoc("anc-1", "Parent", "anc content", level=0),
        "m1": _ddoc("m1", "Child", "match content", level=1),
    }

    with patch(
        "pointy_rag.llms_txt.db.get_disclosure_doc",
        side_effect=lambda nid, _: ddocs.get(nid),
    ):
        result = assemble_reference(sg, mock_conn)

    assert "Match: Parent" not in result
    assert "Match: Child" in result


# ---------------------------------------------------------------------------
# assemble_reference — related/similar prefix with doc attribution
# ---------------------------------------------------------------------------


def test_similar_nodes_have_related_prefix(mock_conn):
    sim_node = _node("sim-1", "Similar Section", level=1, doc_id="doc-2")
    sg = _subgraph(
        nodes=[sim_node],
        matches=["m1"],
        hierarchy={"sim-1": []},
        edges=[{"type": "SIMILAR_TO", "source": "m1", "target": "sim-1", "score": 0.9}],
    )
    # match-1 not in nodes — resolve via PG
    match_ddoc = _ddoc("m1", "Match", "match content", level=1, doc_id="doc-1")
    sim_ddoc = _ddoc("sim-1", "Similar Section", "sim content", level=1, doc_id="doc-2")

    def fake_get_ddoc(nid, _conn):
        return {"m1": match_ddoc, "sim-1": sim_ddoc}.get(nid)

    doc2 = _doc("doc-2", "Other Book")

    with (
        patch("pointy_rag.llms_txt.db.get_disclosure_doc", side_effect=fake_get_ddoc),
        patch("pointy_rag.llms_txt.db.get_document", return_value=doc2),
    ):
        result = assemble_reference(sg, mock_conn)

    assert "Related: Similar Section" in result


def test_similar_nodes_have_from_attribution(mock_conn):
    sim_node = _node("sim-1", "Similar Section", level=1, doc_id="doc-2")
    sg = _subgraph(
        nodes=[sim_node],
        matches=["m1"],
        hierarchy={"sim-1": []},
        edges=[{"type": "SIMILAR_TO", "source": "m1", "target": "sim-1", "score": 0.9}],
    )
    match_ddoc = _ddoc("m1", "Match", "match content", level=1, doc_id="doc-1")
    sim_ddoc = _ddoc("sim-1", "Similar Section", "sim content", level=1, doc_id="doc-2")

    def fake_get_ddoc(nid, _conn):
        return {"m1": match_ddoc, "sim-1": sim_ddoc}.get(nid)

    doc2 = _doc("doc-2", "Other Textbook")

    with (
        patch("pointy_rag.llms_txt.db.get_disclosure_doc", side_effect=fake_get_ddoc),
        patch("pointy_rag.llms_txt.db.get_document", return_value=doc2),
    ):
        result = assemble_reference(sg, mock_conn)

    assert "> From: Other Textbook" in result


# ---------------------------------------------------------------------------
# assemble_reference — ancestor content as blockquote
# ---------------------------------------------------------------------------


def test_ancestor_content_is_blockquoted(mock_conn):
    ancestor = _node("anc-1", "Chapter 1", level=0)
    match_node = _node("m1", "Section", level=1)
    sg = _subgraph(
        nodes=[ancestor, match_node],
        matches=["m1"],
        hierarchy={"anc-1": ["m1"]},
    )
    ddocs = {
        "anc-1": _ddoc("anc-1", "Chapter 1", "ancestor text", level=0),
        "m1": _ddoc("m1", "Section", "match text", level=1),
    }

    with patch(
        "pointy_rag.llms_txt.db.get_disclosure_doc",
        side_effect=lambda nid, _: ddocs.get(nid),
    ):
        result = assemble_reference(sg, mock_conn)

    assert "> ancestor text" in result


def test_match_content_is_not_blockquoted(mock_conn):
    match_node = _node("m1", "Match", level=1)
    sg = _subgraph(nodes=[match_node], matches=["m1"], hierarchy={"m1": []})
    ddoc = _ddoc("m1", "Match", "plain match content", level=1)

    with patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc):
        result = assemble_reference(sg, mock_conn)

    assert "plain match content" in result
    assert "> plain match content" not in result


# ---------------------------------------------------------------------------
# assemble_reference — deduplication
# ---------------------------------------------------------------------------


def test_shared_ancestor_rendered_once(mock_conn):
    """Two matches sharing an ancestor — ancestor appears exactly once."""
    ancestor = _node("anc-shared", "Shared Chapter", level=0)
    m1 = _node("m1", "Match One", level=1)
    m2 = _node("m2", "Match Two", level=1)
    sg = _subgraph(
        nodes=[ancestor, m1, m2],
        matches=["m1", "m2"],
        hierarchy={"anc-shared": ["m1", "m2"]},
    )
    ddocs = {
        "anc-shared": _ddoc("anc-shared", "Shared Chapter", "shared content", level=0),
        "m1": _ddoc("m1", "Match One", "content 1", level=1),
        "m2": _ddoc("m2", "Match Two", "content 2", level=1),
    }

    with patch(
        "pointy_rag.llms_txt.db.get_disclosure_doc",
        side_effect=lambda nid, _: ddocs.get(nid),
    ):
        result = assemble_reference(sg, mock_conn)

    assert result.count("[ref:anc-shared]") == 1


def test_shared_ancestor_across_two_match_paths(mock_conn):
    """Ancestor shared via two separate match chains appears only once."""
    ancestor = _node("shared", "Shared Root", level=0)
    child_a = _node("child-a", "Child A", level=1)
    child_b = _node("child-b", "Child B", level=1)
    sg = _subgraph(
        nodes=[ancestor, child_a, child_b],
        matches=["child-a", "child-b"],
        hierarchy={"shared": ["child-a", "child-b"]},
    )
    ddocs = {
        "shared": _ddoc("shared", "Shared Root", "root content", level=0),
        "child-a": _ddoc("child-a", "Child A", "a content", level=1),
        "child-b": _ddoc("child-b", "Child B", "b content", level=1),
    }

    with patch(
        "pointy_rag.llms_txt.db.get_disclosure_doc",
        side_effect=lambda nid, _: ddocs.get(nid),
    ):
        result = assemble_reference(sg, mock_conn)

    assert result.count("Shared Root") == 1


# ---------------------------------------------------------------------------
# assemble_reference — edge cases
# ---------------------------------------------------------------------------


def test_empty_subgraph_returns_empty_string(mock_conn):
    sg = _subgraph(nodes=[], matches=[], hierarchy={})
    with patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=None):
        result = assemble_reference(sg, mock_conn)
    assert result == ""


def test_match_with_no_ancestors_rendered_directly(mock_conn):
    """A match node with no hierarchy entry is rendered as a top-level section."""
    match_node = _node("m1", "Orphan Match", level=2)
    sg = _subgraph(nodes=[match_node], matches=["m1"], hierarchy={})
    ddoc = _ddoc("m1", "Orphan Match", "orphan content", level=2)

    with patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc):
        result = assemble_reference(sg, mock_conn)

    assert "Match: Orphan Match [ref:m1]" in result
    assert "orphan content" in result
