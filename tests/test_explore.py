"""Unit tests for explore mode — assembly, orchestrator, and CLI."""

from unittest.mock import MagicMock, patch

import pytest

from pointy_rag.llms_txt import (
    _ancestor_chain,
    _build_child_to_parent,
    _heading_hashes,
    _level_label,
    _node_role,
    _resolve_doc_title,
    _snippet,
    assemble_explore,
    assemble_explore_contents,
    assemble_explore_llms_txt,
    assemble_explore_overview,
)
from pointy_rag.models import (
    Chunk,
    DisclosureDoc,
    Document,
    DocumentFormat,
    ExploreResult,
    SearchResult,
)

# ---------------------------------------------------------------------------
# Test helpers (same pattern as test_llms_txt.py)
# ---------------------------------------------------------------------------


def _ddoc(
    node_id: str, title: str, content: str, level: int = 1, doc_id: str = "doc-1"
) -> DisclosureDoc:
    return DisclosureDoc(
        id=node_id, document_id=doc_id, level=level, title=title, content=content
    )


def _doc(doc_id: str, title: str) -> Document:
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


def _search_result(chunk_id: str = "c1") -> SearchResult:
    return SearchResult(
        chunk=Chunk(id=chunk_id, disclosure_doc_id="dd-1", content="chunk text"),
        score=0.9,
    )


def _subgraph(nodes, matches, hierarchy, edges=None):
    return {
        "nodes": nodes,
        "matches": matches,
        "hierarchy": hierarchy,
        "edges": edges or [],
    }


# ---------------------------------------------------------------------------
# _snippet
# ---------------------------------------------------------------------------


def test_snippet_short_passthrough():
    assert _snippet("hello world") == "hello world"


def test_snippet_truncation():
    text = "a" * 100
    result = _snippet(text, max_len=60)
    assert len(result) == 63  # 60 + "..."
    assert result.endswith("...")


def test_snippet_newline_collapse():
    assert _snippet("line1\nline2\nline3") == "line1 line2 line3"


def test_snippet_empty_string():
    assert _snippet("") == ""


def test_snippet_none_like_empty():
    assert _snippet("") == ""


def test_snippet_whitespace_collapse():
    assert _snippet("  lots   of   spaces  ") == "lots of spaces"


# ---------------------------------------------------------------------------
# _level_label
# ---------------------------------------------------------------------------


def test_level_label_l0():
    assert _level_label(0) == "L0 library_catalog"


def test_level_label_l1():
    assert _level_label(1) == "L1 resource_index"


def test_level_label_l2():
    assert _level_label(2) == "L2 section_summary"


def test_level_label_l3():
    assert _level_label(3) == "L3 detailed_passage"


def test_level_label_none():
    assert _level_label(None) == "chunk"


def test_level_label_unknown_int():
    assert _level_label(99) == "L99"


# ---------------------------------------------------------------------------
# _node_role
# ---------------------------------------------------------------------------


def test_node_role_match():
    assert _node_role("n1", {"n1"}, set()) == "match"


def test_node_role_related():
    assert _node_role("n1", set(), {"n1"}) == "related"


def test_node_role_context():
    assert _node_role("n1", set(), set()) == "context"


def test_node_role_match_takes_priority():
    # If somehow in both sets, match wins
    assert _node_role("n1", {"n1"}, {"n1"}) == "match"


# ---------------------------------------------------------------------------
# _build_child_to_parent / _ancestor_chain
# ---------------------------------------------------------------------------


def test_build_child_to_parent():
    hierarchy = {"p1": ["c1", "c2"], "c1": ["gc1"]}
    result = _build_child_to_parent(hierarchy)
    assert result == {"c1": "p1", "c2": "p1", "gc1": "c1"}


def test_ancestor_chain_simple():
    hierarchy = {"root": ["mid"], "mid": ["leaf"]}
    c2p = _build_child_to_parent(hierarchy)
    nodes = {
        "root": _node("root", "Root", 0),
        "mid": _node("mid", "Mid", 1),
        "leaf": _node("leaf", "Leaf", 2),
    }
    chain = _ancestor_chain("leaf", c2p, nodes)
    assert chain == ["root", "mid"]


def test_ancestor_chain_no_parents():
    c2p: dict[str, str] = {}
    nodes = {"orphan": _node("orphan", "Orphan", 0)}
    chain = _ancestor_chain("orphan", c2p, nodes)
    assert chain == []


def test_ancestor_chain_breaks_on_cycle():
    """Cycle in hierarchy should not cause infinite loop."""
    child_to_parent = {"a": "b", "b": "c", "c": "a"}  # cycle: a->b->c->a
    nodes_index = {"a": {}, "b": {}, "c": {}}
    result = _ancestor_chain("a", child_to_parent, nodes_index)
    # Should terminate, not hang. Exact result depends on implementation.
    assert isinstance(result, list)
    assert len(result) <= 3  # bounded by number of nodes


# ---------------------------------------------------------------------------
# assemble_explore_overview
# ---------------------------------------------------------------------------


def test_overview_stats_line(mock_conn):
    sg = _subgraph(
        nodes=[_node("m1", "Match", 2)],
        matches=["m1"],
        hierarchy={"m1": []},
    )
    ddoc = _ddoc("m1", "Match", "content", level=2)
    with (
        patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        result = assemble_explore_overview(sg, mock_conn, "test query")

    assert "1 matches | 1 nodes | 1 documents" in result


def test_overview_has_footer(mock_conn):
    sg = _subgraph(nodes=[], matches=[], hierarchy={})
    result = assemble_explore_overview(sg, mock_conn, "test")
    assert "Detail: llms.txt" in result
    assert "contents/{node_id}.md" in result


def test_overview_match_badge(mock_conn):
    sg = _subgraph(
        nodes=[_node("anc", "Ancestor", 0), _node("m1", "Matched", 1)],
        matches=["m1"],
        hierarchy={"anc": ["m1"]},
    )
    ddocs = {
        "anc": _ddoc("anc", "Ancestor", "anc content", level=0),
        "m1": _ddoc("m1", "Matched", "match content", level=1),
    }
    with (
        patch(
            "pointy_rag.llms_txt.db.get_disclosure_doc",
            side_effect=lambda nid, _: ddocs.get(nid),
        ),
        patch(
            "pointy_rag.llms_txt.db.get_document",
            return_value=_doc("doc-1", "Test Doc"),
        ),
    ):
        result = assemble_explore_overview(sg, mock_conn, "test")

    assert "[match]" in result


def test_overview_related_badge(mock_conn):
    match_node = _node("m1", "Match", 1)
    sim_node = _node("s1", "Similar", 2, doc_id="doc-2")
    # Put s1 under m1 so it renders as a list item with badge
    sg = _subgraph(
        nodes=[match_node, sim_node],
        matches=["m1"],
        hierarchy={"m1": ["s1"]},
        edges=[{"type": "SIMILAR_TO", "source": "m1", "target": "s1", "score": 0.9}],
    )
    ddocs = {
        "m1": _ddoc("m1", "Match", "match content", level=1),
        "s1": _ddoc("s1", "Similar", "sim content", level=2, doc_id="doc-2"),
    }
    with (
        patch(
            "pointy_rag.llms_txt.db.get_disclosure_doc",
            side_effect=lambda nid, _: ddocs.get(nid),
        ),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        result = assemble_explore_overview(sg, mock_conn, "test")

    assert "[related]" in result


def test_overview_no_full_content(mock_conn):
    """Overview should have snippets, not full content bodies."""
    long_content = "x" * 200
    root = _node("root", "Root", 0)
    child = _node("m1", "Match", 1)
    # Put m1 under root so it renders as a list item with snippet
    sg = _subgraph(
        nodes=[root, child],
        matches=["m1"],
        hierarchy={"root": ["m1"]},
    )
    ddocs = {
        "root": _ddoc("root", "Root", "root content", level=0),
        "m1": _ddoc("m1", "Match", long_content, level=1),
    }
    with (
        patch(
            "pointy_rag.llms_txt.db.get_disclosure_doc",
            side_effect=lambda nid, _: ddocs.get(nid),
        ),
        patch(
            "pointy_rag.llms_txt.db.get_document",
            return_value=_doc("doc-1", "Test Doc"),
        ),
    ):
        result = assemble_explore_overview(sg, mock_conn, "test")

    # Full 200-char content should NOT appear; snippet should be truncated
    assert long_content not in result
    assert "..." in result


# ---------------------------------------------------------------------------
# assemble_explore_llms_txt
# ---------------------------------------------------------------------------


def test_llms_txt_has_ref_pointers(mock_conn):
    sg = _subgraph(
        nodes=[_node("m1", "Match", 1)],
        matches=["m1"],
        hierarchy={"m1": []},
    )
    ddoc = _ddoc("m1", "Match", "content", level=1)
    with (
        patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        result = assemble_explore_llms_txt(sg, mock_conn, "test")

    assert "[ref:m1]" in result


def test_llms_txt_has_file_links(mock_conn):
    sg = _subgraph(
        nodes=[_node("m1", "Match", 1)],
        matches=["m1"],
        hierarchy={"m1": []},
    )
    ddoc = _ddoc("m1", "Match", "content", level=1)
    with (
        patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        result = assemble_explore_llms_txt(sg, mock_conn, "test")

    assert "contents/m1.md" in result


def test_llms_txt_match_prefix(mock_conn):
    sg = _subgraph(
        nodes=[_node("m1", "Found It", 2)],
        matches=["m1"],
        hierarchy={"m1": []},
    )
    ddoc = _ddoc("m1", "Found It", "content", level=2)
    with (
        patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        result = assemble_explore_llms_txt(sg, mock_conn, "test")

    assert "Match: Found It" in result


def test_llms_txt_related_prefix_and_attribution(mock_conn):
    match_node = _node("m1", "Match", 1)
    sim_node = _node("s1", "Related Section", 2, doc_id="doc-2")
    sg = _subgraph(
        nodes=[match_node, sim_node],
        matches=["m1"],
        hierarchy={"m1": [], "s1": []},
        edges=[{"type": "SIMILAR_TO", "source": "m1", "target": "s1", "score": 0.9}],
    )
    ddocs = {
        "m1": _ddoc("m1", "Match", "match content", level=1),
        "s1": _ddoc("s1", "Related Section", "sim content", level=2, doc_id="doc-2"),
    }
    with (
        patch(
            "pointy_rag.llms_txt.db.get_disclosure_doc",
            side_effect=lambda nid, _: ddocs.get(nid),
        ),
        patch(
            "pointy_rag.llms_txt.db.get_document",
            return_value=_doc("doc-2", "Other Book"),
        ),
    ):
        result = assemble_explore_llms_txt(sg, mock_conn, "test")

    assert "Related: Related Section" in result
    assert "From: Other Book" in result


def test_llms_txt_level_labels(mock_conn):
    sg = _subgraph(
        nodes=[_node("m1", "Match", 3)],
        matches=["m1"],
        hierarchy={"m1": []},
    )
    ddoc = _ddoc("m1", "Match", "content", level=3)
    with (
        patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        result = assemble_explore_llms_txt(sg, mock_conn, "test")

    assert "L3 detailed_passage" in result


def test_llms_txt_heading_depth(mock_conn):
    ancestor = _node("anc", "Root", level=0)
    match_node = _node("m1", "Child", level=1)
    sg = _subgraph(
        nodes=[ancestor, match_node],
        matches=["m1"],
        hierarchy={"anc": ["m1"]},
    )
    ddocs = {
        "anc": _ddoc("anc", "Root", "root content", level=0),
        "m1": _ddoc("m1", "Child", "child content", level=1),
    }
    with (
        patch(
            "pointy_rag.llms_txt.db.get_disclosure_doc",
            side_effect=lambda nid, _: ddocs.get(nid),
        ),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        result = assemble_explore_llms_txt(sg, mock_conn, "test")

    assert "# Root [ref:anc]" in result
    assert "## Match: Child [ref:m1]" in result


def test_llms_txt_content_truncated(mock_conn):
    long_content = "word " * 100  # 500 chars
    sg = _subgraph(
        nodes=[_node("m1", "Match", 1)],
        matches=["m1"],
        hierarchy={"m1": []},
    )
    ddoc = _ddoc("m1", "Match", long_content, level=1)
    with (
        patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        result = assemble_explore_llms_txt(sg, mock_conn, "test")

    # Full content should NOT appear
    assert long_content.strip() not in result
    assert "..." in result


# ---------------------------------------------------------------------------
# assemble_explore_contents
# ---------------------------------------------------------------------------


def test_contents_has_yaml_frontmatter(mock_conn):
    sg = _subgraph(
        nodes=[_node("m1", "Match Node", 2)],
        matches=["m1"],
        hierarchy={"m1": []},
    )
    ddoc = _ddoc("m1", "Match Node", "full match content", level=2)
    with (
        patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc),
        patch(
            "pointy_rag.llms_txt.db.get_document",
            return_value=_doc("doc-1", "My Document"),
        ),
    ):
        result = assemble_explore_contents(sg, mock_conn)

    assert "m1" in result
    content = result["m1"]
    assert content.startswith("---\n")
    assert "node_id: m1" in content
    assert 'title: "Match Node"' in content
    assert "level: L2 section_summary" in content
    assert 'document: "My Document"' in content
    assert "role: match" in content


def test_contents_full_unblockquoted(mock_conn):
    full_text = "This is the full original content\nwith multiple lines\nand details."
    sg = _subgraph(
        nodes=[_node("m1", "Match", 1)],
        matches=["m1"],
        hierarchy={"m1": []},
    )
    ddoc = _ddoc("m1", "Match", full_text, level=1)
    with (
        patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        result = assemble_explore_contents(sg, mock_conn)

    content = result["m1"]
    # Full text should be present
    assert full_text in content
    # No blockquoting in the main body (after frontmatter)
    body = content.split("---\n", 2)[2]
    body_lines = body.strip().split("\n")
    # Skip heading lines when checking for blockquotes
    non_heading_lines = [ln for ln in body_lines if not ln.startswith("#")]
    for line in non_heading_lines:
        assert not line.startswith("> "), f"Found blockquoted line: {line}"


def test_contents_includes_ancestor_context(mock_conn):
    """Content files must include ancestor hierarchy content."""
    root = _node("root", "Root Section", 0)
    mid = _node("mid", "Middle Section", 1)
    leaf = _node("leaf", "Leaf Match", 2)
    sg = _subgraph(
        nodes=[root, mid, leaf],
        matches=["leaf"],
        hierarchy={"root": ["mid"], "mid": ["leaf"]},
    )
    ddocs = {
        "root": _ddoc("root", "Root Section", "root content here", level=0),
        "mid": _ddoc("mid", "Middle Section", "middle content here", level=1),
        "leaf": _ddoc("leaf", "Leaf Match", "leaf content here", level=2),
    }
    with (
        patch(
            "pointy_rag.llms_txt.db.get_disclosure_doc",
            side_effect=lambda nid, _: ddocs.get(nid),
        ),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        result = assemble_explore_contents(sg, mock_conn)

    leaf_content = result["leaf"]
    # Ancestor content should appear before leaf's own content
    assert "root content here" in leaf_content
    assert "middle content here" in leaf_content
    assert "leaf content here" in leaf_content
    # Root should come first
    root_pos = leaf_content.index("root content here")
    mid_pos = leaf_content.index("middle content here")
    leaf_pos = leaf_content.index("leaf content here")
    assert root_pos < mid_pos < leaf_pos


def test_contents_related_attribution(mock_conn):
    match_node = _node("m1", "Match", 1)
    sim_node = _node("s1", "Similar", 2, doc_id="doc-2")
    sg = _subgraph(
        nodes=[match_node, sim_node],
        matches=["m1"],
        hierarchy={"m1": [], "s1": []},
        edges=[{"type": "SIMILAR_TO", "source": "m1", "target": "s1", "score": 0.9}],
    )
    ddocs = {
        "m1": _ddoc("m1", "Match", "match text", level=1),
        "s1": _ddoc("s1", "Similar", "similar text", level=2, doc_id="doc-2"),
    }
    with (
        patch(
            "pointy_rag.llms_txt.db.get_disclosure_doc",
            side_effect=lambda nid, _: ddocs.get(nid),
        ),
        patch(
            "pointy_rag.llms_txt.db.get_document",
            return_value=_doc("doc-2", "Other Book"),
        ),
    ):
        result = assemble_explore_contents(sg, mock_conn)

    assert "s1" in result
    s1_content = result["s1"]
    assert "role: related" in s1_content
    assert "> From: Other Book" in s1_content


def test_contents_context_role(mock_conn):
    """Ancestor-only nodes should have role: context."""
    root = _node("root", "Root", 0)
    match_node = _node("m1", "Match", 1)
    sg = _subgraph(
        nodes=[root, match_node],
        matches=["m1"],
        hierarchy={"root": ["m1"]},
    )
    ddocs = {
        "root": _ddoc("root", "Root", "root content", level=0),
        "m1": _ddoc("m1", "Match", "match content", level=1),
    }
    with (
        patch(
            "pointy_rag.llms_txt.db.get_disclosure_doc",
            side_effect=lambda nid, _: ddocs.get(nid),
        ),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        result = assemble_explore_contents(sg, mock_conn)

    assert "root" in result
    assert "role: context" in result["root"]
    assert "role: match" in result["m1"]


# ---------------------------------------------------------------------------
# assemble_explore (orchestrator)
# ---------------------------------------------------------------------------


def test_assemble_explore_returns_triple(mock_conn):
    sg = _subgraph(
        nodes=[_node("m1", "Match", 1)],
        matches=["m1"],
        hierarchy={"m1": []},
    )
    ddoc = _ddoc("m1", "Match", "content", level=1)
    with (
        patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        overview, llms_txt, contents = assemble_explore(sg, mock_conn, "test query")

    assert isinstance(overview, str)
    assert isinstance(llms_txt, str)
    assert isinstance(contents, dict)
    assert len(overview) > 0
    assert len(llms_txt) > 0
    assert "m1" in contents


# ---------------------------------------------------------------------------
# explore() in search.py
# ---------------------------------------------------------------------------


def test_explore_kg_disabled_fallback(mock_conn):
    from pointy_rag.search import explore as explore_fn

    with (
        patch("pointy_rag.search.search", return_value=[]),
        patch(
            "pointy_rag.config.get_settings",
            return_value=MagicMock(kg_enabled=False),
        ),
    ):
        result = explore_fn("test", mock_conn)

    assert isinstance(result, ExploreResult)
    assert result.overview is None
    assert result.llms_txt is None
    assert result.contents == {}


def test_explore_no_results_fallback(mock_conn):
    from pointy_rag.search import explore as explore_fn

    with (
        patch("pointy_rag.search.search", return_value=[]),
        patch(
            "pointy_rag.config.get_settings",
            return_value=MagicMock(kg_enabled=True),
        ),
    ):
        result = explore_fn("test", mock_conn)

    assert result.overview is None
    assert result.contents == {}


def test_explore_exception_fallback(mock_conn):
    import psycopg

    from pointy_rag.search import explore as explore_fn

    with (
        patch("pointy_rag.search.search", return_value=[_search_result()]),
        patch(
            "pointy_rag.config.get_settings",
            return_value=MagicMock(kg_enabled=True),
        ),
        patch(
            "pointy_rag.graph_query.build_context_subgraph",
            side_effect=psycopg.Error("boom"),
        ),
    ):
        result = explore_fn("test", mock_conn)

    assert isinstance(result, ExploreResult)
    assert result.overview is None
    assert result.node_count == 0


def test_explore_passes_deeper_defaults(mock_conn):
    """Verify explore calls build_context_subgraph with deeper defaults."""
    from pointy_rag.search import explore as explore_fn

    mock_subgraph = {
        "nodes": [
            {
                "node_id": "c1",
                "node_type": "chunk",
                "level": None,
                "title": "C1",
                "document_id": "d1",
            }
        ],
        "edges": [],
        "matches": ["c1"],
        "hierarchy": {"c1": []},
    }

    sr = _search_result("c1")
    with (
        patch("pointy_rag.search.search", return_value=[sr]),
        patch(
            "pointy_rag.config.get_settings",
            return_value=MagicMock(kg_enabled=True),
        ),
        patch(
            "pointy_rag.graph_query.build_context_subgraph",
            return_value=mock_subgraph,
        ) as mock_build,
        patch(
            "pointy_rag.llms_txt.assemble_explore",
            return_value=("ov", "llms", {"c1": "content"}),
        ),
    ):
        explore_fn("test", mock_conn)

    mock_build.assert_called_once_with(["c1"], mock_conn, 3, True, 2)


# ---------------------------------------------------------------------------
# Empty subgraph edge cases
# ---------------------------------------------------------------------------


def test_overview_empty_subgraph(mock_conn):
    sg = _subgraph(nodes=[], matches=[], hierarchy={})
    result = assemble_explore_overview(sg, mock_conn, "empty query")
    assert "# Context Overview" in result
    assert "0 matches" in result


def test_llms_txt_empty_subgraph(mock_conn):
    sg = _subgraph(nodes=[], matches=[], hierarchy={})
    result = assemble_explore_llms_txt(sg, mock_conn, "empty query")
    assert "# Explore:" in result


def test_contents_empty_subgraph(mock_conn):
    sg = _subgraph(nodes=[], matches=[], hierarchy={})
    result = assemble_explore_contents(sg, mock_conn)
    assert result == {}


# ---------------------------------------------------------------------------
# _heading_hashes edge cases
# ---------------------------------------------------------------------------


def test_heading_hashes_level_0():
    assert _heading_hashes(0) == "#"


def test_heading_hashes_level_3():
    assert _heading_hashes(3) == "####"


def test_heading_hashes_none():
    assert _heading_hashes(None) == "#"


def test_heading_hashes_clamps_max_at_6():
    assert _heading_hashes(10) == "######"


def test_heading_hashes_negative_clamps_to_1():
    """Negative levels must still produce at least '#'."""
    assert _heading_hashes(-5) == "#"


# ---------------------------------------------------------------------------
# _resolve_doc_title
# ---------------------------------------------------------------------------


def test_resolve_doc_title_found(mock_conn):
    doc = _doc("doc-1", "My Book")
    with patch("pointy_rag.llms_txt.db.get_document", return_value=doc):
        assert _resolve_doc_title("doc-1", mock_conn) == "My Book"


def test_resolve_doc_title_not_found_returns_id(mock_conn):
    with patch("pointy_rag.llms_txt.db.get_document", return_value=None):
        assert _resolve_doc_title("doc-missing", mock_conn) == "doc-missing"


def test_resolve_doc_title_empty_passthrough(mock_conn):
    assert _resolve_doc_title("", mock_conn) == ""


# ---------------------------------------------------------------------------
# ExploreResult model validator
# ---------------------------------------------------------------------------


def test_explore_result_both_none_valid():
    r = ExploreResult(
        vector_results=[], overview=None, llms_txt=None, node_count=0, edge_count=0
    )
    assert r.overview is None
    assert r.llms_txt is None


def test_explore_result_both_set_valid():
    r = ExploreResult(
        vector_results=[],
        overview="ov",
        llms_txt="llms",
        node_count=0,
        edge_count=0,
    )
    assert r.overview == "ov"


def test_explore_result_overview_only_invalid():
    with pytest.raises(ValueError, match="overview and llms_txt must both"):
        ExploreResult(
            vector_results=[],
            overview="ov",
            llms_txt=None,
            node_count=0,
            edge_count=0,
        )


def test_explore_result_llms_txt_only_invalid():
    with pytest.raises(ValueError, match="overview and llms_txt must both"):
        ExploreResult(
            vector_results=[],
            overview=None,
            llms_txt="llms",
            node_count=0,
            edge_count=0,
        )


# ---------------------------------------------------------------------------
# YAML frontmatter quote escaping
# ---------------------------------------------------------------------------


def test_contents_escapes_quotes_in_title(mock_conn):
    """Titles with double quotes must not break YAML frontmatter."""
    sg = _subgraph(
        nodes=[_node("m1", 'He said "hello"', 1)],
        matches=["m1"],
        hierarchy={"m1": []},
    )
    ddoc = _ddoc("m1", 'He said "hello"', "content", level=1)
    with (
        patch("pointy_rag.llms_txt.db.get_disclosure_doc", return_value=ddoc),
        patch("pointy_rag.llms_txt.db.get_document", return_value=None),
    ):
        result = assemble_explore_contents(sg, mock_conn)

    fm = result["m1"]
    assert 'title: "He said \\"hello\\""' in fm


# ---------------------------------------------------------------------------
# _prepare_subgraph filters None node_ids
# ---------------------------------------------------------------------------


def test_prepare_subgraph_skips_none_node_id(mock_conn):
    """Nodes with node_id=None should be silently filtered out."""
    from pointy_rag.llms_txt import _prepare_subgraph

    sg = _subgraph(
        nodes=[
            _node("m1", "Valid", 1),
            {"node_id": None, "node_type": "disclosure", "level": 0, "title": "Bad"},
        ],
        matches=["m1"],
        hierarchy={"m1": []},
    )
    with patch("pointy_rag.llms_txt.db.get_document", return_value=None):
        prepared = _prepare_subgraph(sg, mock_conn)

    assert "m1" in prepared.nodes_index
    assert None not in prepared.nodes_index
