"""Graph query layer for pointy-rag knowledge graph traversal."""

from __future__ import annotations

import json
import logging
import re

import psycopg

from pointy_rag.graph import GRAPH_NAME, cypher_sql, escape_cypher
from pointy_rag.models import ContextSubgraph, GraphEdge, GraphNode

logger = logging.getLogger(__name__)


def _cypher_sql_multi(cypher: str, *col_names: str) -> str:
    """Return SQL for a Cypher query returning multiple agtype columns."""
    col_defs = ", ".join(f"{c} agtype" for c in col_names)
    return f"SELECT * FROM ag_catalog.cypher(%s, $$ {cypher} $$) AS ({col_defs})"  # noqa: S608


def _parse_agtype(val: object) -> dict | list | None:
    """Parse an AGE agtype value into a Python object.

    Handles: Python dicts/lists (pass-through), JSON strings with AGE type
    annotations (::vertex, ::edge, etc.), and None.
    """
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    s = str(val).strip()
    # Strip trailing type hint (e.g. ::vertex, ::edge, ::path, ::agtype)
    s = re.sub(r"::[a-z]+$", "", s)
    # Strip inner type hints before commas/brackets (inside list literals)
    s = re.sub(r"::[a-z]+(?=[,\]\s])", "", s)
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse agtype value: %r", val)
        return None


def _node_props(val: object) -> dict:
    """Extract standardized node properties from an agtype vertex value."""
    parsed = _parse_agtype(val)
    if not isinstance(parsed, dict):
        return {}
    props = parsed.get("properties", parsed)
    return {
        "node_id": props.get("node_id"),
        "node_type": props.get("node_type"),
        "level": props.get("level"),
        "title": props.get("title"),
        "document_id": props.get("document_id"),
    }


def _edge_score_from(val: object) -> float | None:
    """Extract a score from an agtype edge or edge list (variable-length paths)."""
    parsed = _parse_agtype(val)
    if isinstance(parsed, list):
        for edge in parsed:
            if isinstance(edge, dict):
                score = edge.get("properties", {}).get("score")
                if score is not None:
                    return score
        return None
    if isinstance(parsed, dict):
        return parsed.get("properties", {}).get("score")
    return None


def get_neighbors(
    node_id: str,
    conn: psycopg.Connection,
    edge_type: str | None = None,
    max_hops: int = 1,
) -> list[dict]:
    """Traverse edges from a node and return neighbor node information.

    Args:
        node_id: The starting node's node_id property.
        conn: Active psycopg connection with AGE loaded.
        edge_type: Edge label to filter on ("SIMILAR_TO", "CONTAINS", or None for all).
        max_hops: Maximum traversal depth (variable-length path upper bound).

    Returns:
        List of node dicts with keys: node_id, node_type, level, title, document_id.
        For SIMILAR_TO edges, each dict also includes edge_score.
    """
    rel = f"-[r:{edge_type}*1..{max_hops}]-" if edge_type else f"-[r*1..{max_hops}]-"

    cypher = (
        f"MATCH (start {{node_id: '{escape_cypher(node_id)}'}}) "
        f"{rel}(neighbor) "
        f"RETURN neighbor, r"
    )
    rows = conn.execute(
        _cypher_sql_multi(cypher, "neighbor", "r"), (GRAPH_NAME,)
    ).fetchall()

    results: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        node = _node_props(row[0])
        nid = node.get("node_id")
        if not nid or nid == node_id or nid in seen:
            continue
        seen.add(nid)
        if edge_type == "SIMILAR_TO":
            node["edge_score"] = _edge_score_from(row[1])
        results.append(node)
    return results


def walk_hierarchy_up(
    node_id: str,
    conn: psycopg.Connection,
    levels_up: int = 1,
) -> list[dict]:
    """Walk CONTAINS edges upward, returning the ancestor chain.

    Returns nodes ordered from root ancestor to immediate parent of node_id
    (the start node itself is excluded). If multiple matching paths exist,
    the longest (most ancestors) is used.

    Args:
        node_id: The starting node's node_id property.
        conn: Active psycopg connection with AGE loaded.
        levels_up: Maximum number of CONTAINS hops to walk upward.

    Returns:
        List of ancestor node dicts (node_id, node_type, level, title, document_id),
        ordered from root to immediate parent.
    """
    cypher = (
        f"MATCH path = (ancestor)-[:CONTAINS*1..{levels_up}]->"
        f"(start {{node_id: '{escape_cypher(node_id)}'}}) "
        f"RETURN nodes(path)"
    )
    rows = conn.execute(cypher_sql(cypher), (GRAPH_NAME,)).fetchall()

    # Use the longest matching path to get the fullest ancestor chain
    best_path: list = []
    for row in rows:
        parsed = _parse_agtype(row[0])
        if isinstance(parsed, list) and len(parsed) > len(best_path):
            best_path = parsed

    # Exclude the last element, which is the start node itself
    return [_node_props(n) for n in best_path[:-1]] if best_path else []


def build_context_subgraph(
    match_node_ids: list[str],
    conn: psycopg.Connection,
    hierarchy_levels_up: int = 1,
    include_similar: bool = True,
    similar_hops: int = 1,
) -> ContextSubgraph:
    """Build a context subgraph for a set of matched nodes.

    For each matched node:
    - Walks up the CONTAINS hierarchy to gather parent context.
    - Optionally traverses SIMILAR_TO edges to gather semantically related nodes.
    - For similar nodes, also walks up their hierarchy 1 level.

    All collected nodes are deduplicated by node_id. The hierarchy dict captures
    parent_id -> [child_ids] CONTAINS relationships discovered during traversal.
    Content is NOT fetched here — the llms_txt assembler retrieves it from PG tables.

    Args:
        match_node_ids: Node IDs from the initial vector/BM25 search.
        conn: Active psycopg connection with AGE loaded.
        hierarchy_levels_up: How many CONTAINS levels to walk up for each match.
        include_similar: Whether to traverse SIMILAR_TO edges.
        similar_hops: Max hops for SIMILAR_TO traversal.

    Returns:
        ContextSubgraph with typed nodes, edges, matches, and hierarchy.
    """
    all_nodes: dict[str, dict] = {}
    all_edges: list[GraphEdge] = []
    hierarchy: dict[str, list[str]] = {}

    for nid in match_node_ids:
        # Walk hierarchy upward for this match node
        ancestors = walk_hierarchy_up(nid, conn, hierarchy_levels_up)
        prev_id: str | None = None
        for ancestor in ancestors:
            aid = ancestor.get("node_id")
            if not aid:
                continue
            all_nodes[aid] = ancestor
            if prev_id:
                hierarchy.setdefault(prev_id, [])
                if aid not in hierarchy[prev_id]:
                    hierarchy[prev_id].append(aid)
            prev_id = aid
        # The last ancestor in the chain CONTAINS the match node
        if prev_id:
            hierarchy.setdefault(prev_id, [])
            if nid not in hierarchy[prev_id]:
                hierarchy[prev_id].append(nid)

        # Traverse SIMILAR_TO neighbors
        if include_similar:
            similar_nodes = get_neighbors(nid, conn, "SIMILAR_TO", similar_hops)
            for sim_node in similar_nodes:
                sim_id = sim_node.get("node_id")
                if not sim_id:
                    continue
                all_nodes[sim_id] = sim_node
                all_edges.append(
                    GraphEdge(
                        type="SIMILAR_TO",
                        source=nid,
                        target=sim_id,
                        score=sim_node.get("edge_score"),
                    )
                )
                # Walk up 1 hierarchy level for each similar node
                sim_ancestors = walk_hierarchy_up(sim_id, conn, 1)
                for ancestor in sim_ancestors:
                    aid = ancestor.get("node_id")
                    if not aid:
                        continue
                    all_nodes[aid] = ancestor
                    hierarchy.setdefault(aid, [])
                    if sim_id not in hierarchy[aid]:
                        hierarchy[aid].append(sim_id)

    return ContextSubgraph(
        nodes=[GraphNode(**p) for p in all_nodes.values() if p.get("node_id")],
        edges=all_edges,
        matches=list(match_node_ids),
        hierarchy=hierarchy,
    )
