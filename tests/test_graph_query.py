"""Unit tests for pointy_rag.graph_query — no live database required."""

from unittest.mock import MagicMock, patch

from pointy_rag.graph_query import (
    _edge_score_from,
    _node_props,
    _parse_agtype,
    build_context_subgraph,
    get_neighbors,
    walk_hierarchy_up,
)

# ---------------------------------------------------------------------------
# Test helpers: mock AGE agtype structures
# ---------------------------------------------------------------------------


def _make_node(
    node_id: str,
    node_type: str = "disclosure",
    level: int = 2,
    title: str = "Test Node",
    document_id: str = "doc-1",
) -> dict:
    """Return a dict simulating a parsed AGE vertex with properties."""
    return {
        "properties": {
            "node_id": node_id,
            "node_type": node_type,
            "level": level,
            "title": title,
            "document_id": document_id,
        }
    }


def _make_edge(label: str = "SIMILAR_TO", score: float | None = None) -> dict:
    """Return a dict simulating a parsed AGE edge with optional score."""
    props: dict = {}
    if score is not None:
        props["score"] = score
    return {"label": label, "properties": props}


# ---------------------------------------------------------------------------
# _parse_agtype
# ---------------------------------------------------------------------------


def test_parse_agtype_dict_passthrough():
    data = {"properties": {"node_id": "x"}}
    assert _parse_agtype(data) == data


def test_parse_agtype_list_passthrough():
    data = [1, 2, 3]
    assert _parse_agtype(data) == data


def test_parse_agtype_none_returns_none():
    assert _parse_agtype(None) is None


def test_parse_agtype_json_string():
    s = '{"properties": {"node_id": "abc"}}'
    assert _parse_agtype(s) == {"properties": {"node_id": "abc"}}


def test_parse_agtype_strips_vertex_annotation():
    s = '{"properties": {"node_id": "abc"}}::vertex'
    result = _parse_agtype(s)
    assert result == {"properties": {"node_id": "abc"}}


def test_parse_agtype_invalid_returns_none():
    assert _parse_agtype("not valid json") is None


# ---------------------------------------------------------------------------
# _node_props
# ---------------------------------------------------------------------------


def test_node_props_extracts_all_fields():
    val = _make_node("n1", "disclosure", 2, "Chapter 1", "doc-1")
    result = _node_props(val)
    assert result == {
        "node_id": "n1",
        "node_type": "disclosure",
        "level": 2,
        "title": "Chapter 1",
        "document_id": "doc-1",
    }


def test_node_props_handles_none():
    assert _node_props(None) == {}


def test_node_props_handles_non_dict():
    assert _node_props("not a dict") == {}


# ---------------------------------------------------------------------------
# _edge_score_from
# ---------------------------------------------------------------------------


def test_edge_score_from_single_edge():
    edge = _make_edge("SIMILAR_TO", 0.87)
    assert _edge_score_from(edge) == 0.87


def test_edge_score_from_edge_list():
    """Variable-length paths return a list of edges — score taken from first match."""
    edges = [_make_edge("SIMILAR_TO", 0.75)]
    assert _edge_score_from(edges) == 0.75


def test_edge_score_from_edge_without_score():
    edge = _make_edge("CONTAINS")
    assert _edge_score_from(edge) is None


def test_edge_score_from_none():
    assert _edge_score_from(None) is None


# ---------------------------------------------------------------------------
# get_neighbors
# ---------------------------------------------------------------------------


def test_get_neighbors_returns_correct_neighbor_set(mock_conn):
    node_a = _make_node("node-a")
    node_b = _make_node("node-b")
    mock_conn.fetchall.return_value = [(node_a, {}), (node_b, {})]

    results = get_neighbors("node-start", mock_conn)

    node_ids = {r["node_id"] for r in results}
    assert node_ids == {"node-a", "node-b"}


def test_get_neighbors_includes_edge_score_for_similar_to(mock_conn):
    node_a = _make_node("node-a")
    edge_a = _make_edge("SIMILAR_TO", 0.87)
    mock_conn.fetchall.return_value = [(node_a, edge_a)]

    results = get_neighbors("node-start", mock_conn, edge_type="SIMILAR_TO")

    assert results[0]["edge_score"] == 0.87


def test_get_neighbors_no_edge_score_for_contains(mock_conn):
    node_a = _make_node("node-a")
    edge_a = _make_edge("CONTAINS")
    mock_conn.fetchall.return_value = [(node_a, edge_a)]

    results = get_neighbors("node-start", mock_conn, edge_type="CONTAINS")

    assert "edge_score" not in results[0]


def test_get_neighbors_deduplicates_repeated_neighbors(mock_conn):
    """Multi-hop traversal can return the same neighbor multiple times."""
    node_a = _make_node("node-a")
    mock_conn.fetchall.return_value = [(node_a, {}), (node_a, {})]

    results = get_neighbors("node-start", mock_conn)

    assert len(results) == 1


def test_get_neighbors_excludes_start_node_from_results(mock_conn):
    node_start = _make_node("node-start")
    node_other = _make_node("node-other")
    mock_conn.fetchall.return_value = [(node_start, {}), (node_other, {})]

    results = get_neighbors("node-start", mock_conn)

    node_ids = [r["node_id"] for r in results]
    assert "node-start" not in node_ids
    assert "node-other" in node_ids


def test_get_neighbors_cypher_includes_edge_type(mock_conn):
    mock_conn.fetchall.return_value = []
    get_neighbors("start-id", mock_conn, edge_type="SIMILAR_TO", max_hops=2)

    sql = mock_conn.execute.call_args[0][0]
    assert "SIMILAR_TO" in sql
    assert "*1..2" in sql


def test_get_neighbors_cypher_no_label_when_edge_type_none(mock_conn):
    mock_conn.fetchall.return_value = []
    get_neighbors("start-id", mock_conn, max_hops=1)

    sql = mock_conn.execute.call_args[0][0]
    assert "SIMILAR_TO" not in sql
    assert "CONTAINS" not in sql


def test_get_neighbors_empty_graph_returns_empty_list(mock_conn):
    mock_conn.fetchall.return_value = []

    results = get_neighbors("node-start", mock_conn, edge_type="SIMILAR_TO")

    assert results == []


# ---------------------------------------------------------------------------
# walk_hierarchy_up
# ---------------------------------------------------------------------------


def test_walk_hierarchy_up_returns_ancestors_excluding_start(mock_conn):
    root = _make_node("root", level=0)
    section = _make_node("section", level=1)
    start = _make_node("start", level=2)
    # nodes(path) returns [root, section, start]
    mock_conn.fetchall.return_value = [([root, section, start],)]

    results = walk_hierarchy_up("start", mock_conn, levels_up=2)

    node_ids = [r["node_id"] for r in results]
    assert "root" in node_ids
    assert "section" in node_ids
    assert "start" not in node_ids


def test_walk_hierarchy_up_stops_at_correct_depth(mock_conn):
    parent = _make_node("parent", level=1)
    start = _make_node("start", level=2)
    mock_conn.fetchall.return_value = [([parent, start],)]

    results = walk_hierarchy_up("start", mock_conn, levels_up=1)

    assert len(results) == 1
    assert results[0]["node_id"] == "parent"


def test_walk_hierarchy_up_empty_when_no_ancestors(mock_conn):
    mock_conn.fetchall.return_value = []

    results = walk_hierarchy_up("root-node", mock_conn)

    assert results == []


def test_walk_hierarchy_up_uses_longest_path_when_multiple_match(mock_conn):
    """With *1..N, shorter paths may also match. Use the longest."""
    root = _make_node("root", level=0)
    middle = _make_node("middle", level=1)
    start = _make_node("start", level=2)
    # Two rows: a 2-hop path and a 1-hop path
    mock_conn.fetchall.return_value = [
        ([middle, start],),
        ([root, middle, start],),
    ]

    results = walk_hierarchy_up("start", mock_conn, levels_up=2)

    node_ids = [r["node_id"] for r in results]
    assert "root" in node_ids
    assert "middle" in node_ids


def test_walk_hierarchy_up_cypher_uses_contains_edge(mock_conn):
    mock_conn.fetchall.return_value = []
    walk_hierarchy_up("node-1", mock_conn, levels_up=3)

    sql = mock_conn.execute.call_args[0][0]
    assert "CONTAINS" in sql
    assert "*1..3" in sql
    assert "node-1" in sql


# ---------------------------------------------------------------------------
# build_context_subgraph
# ---------------------------------------------------------------------------


def test_build_context_subgraph_deduplicates_nodes_across_matches():
    """A node found via multiple match nodes should appear only once."""
    shared = {
        "node_id": "shared",
        "node_type": "disclosure",
        "level": 1,
        "title": "Shared",
        "document_id": "doc-1",
    }
    with (
        patch("pointy_rag.graph_query.walk_hierarchy_up") as mock_walk,
        patch("pointy_rag.graph_query.get_neighbors") as mock_neighbors,
    ):
        mock_walk.return_value = [shared]
        mock_neighbors.return_value = []

        result = build_context_subgraph(["node-a", "node-b"], MagicMock())

    node_ids = [n.node_id for n in result.nodes]
    assert node_ids.count("shared") == 1


def test_build_context_subgraph_includes_similar_neighbors_when_enabled():
    similar_node = {
        "node_id": "sim-1",
        "node_type": "disclosure",
        "level": 2,
        "title": "Similar",
        "document_id": "doc-2",
        "edge_score": 0.9,
    }
    with (
        patch("pointy_rag.graph_query.walk_hierarchy_up") as mock_walk,
        patch("pointy_rag.graph_query.get_neighbors") as mock_neighbors,
    ):
        mock_walk.return_value = []
        mock_neighbors.return_value = [similar_node]

        result = build_context_subgraph(["node-a"], MagicMock(), include_similar=True)

    node_ids = [n.node_id for n in result.nodes]
    assert "sim-1" in node_ids
    assert len(result.edges) == 1
    edge = result.edges[0]
    assert edge.type == "SIMILAR_TO"
    assert edge.source == "node-a"
    assert edge.target == "sim-1"
    assert edge.score == 0.9


def test_build_context_subgraph_excludes_similar_neighbors_when_disabled():
    with (
        patch("pointy_rag.graph_query.walk_hierarchy_up") as mock_walk,
        patch("pointy_rag.graph_query.get_neighbors") as mock_neighbors,
    ):
        mock_walk.return_value = []

        result = build_context_subgraph(["node-a"], MagicMock(), include_similar=False)

    mock_neighbors.assert_not_called()
    assert result.edges == []


def test_build_context_subgraph_preserves_match_node_ids():
    with (
        patch("pointy_rag.graph_query.walk_hierarchy_up") as mock_walk,
        patch("pointy_rag.graph_query.get_neighbors") as mock_neighbors,
    ):
        mock_walk.return_value = []
        mock_neighbors.return_value = []

        result = build_context_subgraph(["n1", "n2", "n3"], MagicMock())

    assert result.matches == ["n1", "n2", "n3"]


def test_build_context_subgraph_builds_hierarchy_from_ancestors():
    """Hierarchy map should reflect parent->child CONTAINS relationships."""
    root = {
        "node_id": "root",
        "node_type": "disclosure",
        "level": 0,
        "title": "Root",
        "document_id": "doc-1",
    }
    parent = {
        "node_id": "parent",
        "node_type": "disclosure",
        "level": 1,
        "title": "Parent",
        "document_id": "doc-1",
    }
    with (
        patch("pointy_rag.graph_query.walk_hierarchy_up") as mock_walk,
        patch("pointy_rag.graph_query.get_neighbors") as mock_neighbors,
    ):
        mock_walk.return_value = [root, parent]
        mock_neighbors.return_value = []

        result = build_context_subgraph(["child"], MagicMock(), hierarchy_levels_up=2)

    assert "root" in result.hierarchy
    assert "parent" in result.hierarchy["root"]
    assert "parent" in result.hierarchy
    assert "child" in result.hierarchy["parent"]


def test_build_context_subgraph_similar_node_ancestors_in_hierarchy():
    """Ancestors of similar nodes should be added to hierarchy too."""
    sim_parent = {
        "node_id": "sim-parent",
        "node_type": "disclosure",
        "level": 1,
        "title": "Sim Parent",
        "document_id": "doc-2",
    }
    similar_node = {
        "node_id": "sim-1",
        "node_type": "disclosure",
        "level": 2,
        "title": "Similar",
        "document_id": "doc-2",
        "edge_score": 0.8,
    }
    # walk_hierarchy_up returns [] for match node, [sim_parent] for similar node
    walk_returns = [[], [sim_parent]]
    with (
        patch("pointy_rag.graph_query.walk_hierarchy_up") as mock_walk,
        patch("pointy_rag.graph_query.get_neighbors") as mock_neighbors,
    ):
        mock_walk.side_effect = walk_returns
        mock_neighbors.return_value = [similar_node]

        result = build_context_subgraph(["node-a"], MagicMock(), include_similar=True)

    assert "sim-parent" in result.hierarchy
    assert "sim-1" in result.hierarchy["sim-parent"]
