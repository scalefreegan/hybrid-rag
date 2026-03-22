"""llms.txt assembly — render context subgraphs as structured markdown references."""

from __future__ import annotations

import psycopg
import psycopg.rows

from pointy_rag import db


def _blockquote(text: str) -> str:
    """Prefix each line with '> ' for markdown blockquote rendering."""
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def _fetch_node_content(node_id: str, node_type: str, conn: psycopg.Connection) -> str:
    """Fetch content for a node from the appropriate PG table.

    Args:
        node_id: The node's PG record ID.
        node_type: "disclosure" or "chunk".
        conn: Active psycopg connection.

    Returns:
        Node content string, or empty string if not found.
    """
    if node_type == "disclosure":
        ddoc = db.get_disclosure_doc(node_id, conn)
        return ddoc.content if ddoc else ""
    row = (
        conn.cursor(row_factory=psycopg.rows.dict_row)
        .execute("SELECT content FROM chunks WHERE id = %s", (node_id,))
        .fetchone()
    )
    return row["content"] if row else ""


def _resolve_node_info(node_id: str, conn: psycopg.Connection) -> dict:
    """Resolve node metadata from PG for a match node absent from the subgraph index."""
    ddoc = db.get_disclosure_doc(node_id, conn)
    if ddoc is not None:
        return {
            "node_id": node_id,
            "node_type": "disclosure",
            "level": int(ddoc.level),
            "title": ddoc.title,
            "document_id": ddoc.document_id,
        }
    row = (
        conn.cursor(row_factory=psycopg.rows.dict_row)
        .execute(
            "SELECT c.id, dd.document_id, dd.title AS parent_title"
            " FROM chunks c JOIN disclosure_docs dd ON c.disclosure_doc_id = dd.id"
            " WHERE c.id = %s",
            (node_id,),
        )
        .fetchone()
    )
    if row:
        return {
            "node_id": node_id,
            "node_type": "chunk",
            "level": None,
            "title": f"Chunk ({row['parent_title']})",
            "document_id": row["document_id"],
        }
    return {
        "node_id": node_id,
        "node_type": "chunk",
        "level": None,
        "title": node_id,
        "document_id": "",
    }


def assemble_reference(subgraph: dict, conn: psycopg.Connection) -> str:
    """Render a context subgraph as a structured llms.txt markdown reference document.

    Takes the subgraph produced by graph_query.build_context_subgraph and emits
    a multi-level markdown document with:
      - Ancestor nodes rendered at their hierarchy level with blockquoted content
      - Match nodes prefixed with "Match:" at appropriate heading depth
      - Similar nodes prefixed with "Related:" with source document attribution
      - [ref:<node_id>] pointers on every heading for downstream drill-down

    Args:
        subgraph: Dict from graph_query.build_context_subgraph with keys:
            nodes (list), edges (list), matches (list), hierarchy (dict).
        conn: Active psycopg connection for fetching node content.

    Returns:
        Structured markdown string ready for use as an llms.txt reference document.
    """
    nodes_index: dict[str, dict] = {n["node_id"]: n for n in subgraph.get("nodes", [])}
    match_ids: set[str] = set(subgraph.get("matches", []))
    hierarchy: dict[str, list[str]] = subgraph.get("hierarchy", {})
    edges: list[dict] = subgraph.get("edges", [])

    # Similar nodes: targets of SIMILAR_TO edges whose source is a match
    similar_ids: set[str] = {
        e["target"]
        for e in edges
        if e.get("type") == "SIMILAR_TO" and e.get("source") in match_ids
    }

    # Match nodes may be absent from subgraph["nodes"] — resolve from PG
    for nid in match_ids:
        if nid not in nodes_index:
            nodes_index[nid] = _resolve_node_info(nid, conn)

    # Root nodes: appear in hierarchy keys but not as any node's child
    all_children: set[str] = {c for children in hierarchy.values() for c in children}
    root_ids = [nid for nid in hierarchy if nid not in all_children]

    rendered: set[str] = set()

    def render_subtree(node_id: str) -> str:
        if node_id in rendered:
            return ""
        rendered.add(node_id)

        node = nodes_index.get(node_id)
        if not node:
            return ""

        raw_level = node.get("level")
        level = int(raw_level) if raw_level is not None else 0
        hashes = "#" * min(level + 1, 6)
        title = node.get("title") or node_id
        node_type = node.get("node_type") or "disclosure"
        content = _fetch_node_content(node_id, node_type, conn)

        if node_id in match_ids:
            header = f"{hashes} Match: {title} [ref:{node_id}]"
            body = content
        elif node_id in similar_ids:
            doc_id = node.get("document_id") or ""
            doc_title = doc_id
            if doc_id:
                doc = db.get_document(doc_id, conn)
                if doc:
                    doc_title = doc.title
            header = f"{hashes} Related: {title} [ref:{node_id}]"
            body = f"> From: {doc_title}\n{content}"
        else:
            header = f"{hashes} {title} [ref:{node_id}]"
            body = _blockquote(content)

        child_parts = [render_subtree(c) for c in hierarchy.get(node_id, [])]
        child_text = "\n\n".join(p for p in child_parts if p)

        node_text = f"{header}\n{body}"
        return f"{node_text}\n\n{child_text}" if child_text else node_text

    sections = [s for s in (render_subtree(rid) for rid in root_ids) if s]

    # Render any match nodes not reachable via the hierarchy (e.g. no ancestors)
    for match_id in sorted(match_ids):
        if match_id not in rendered:
            section = render_subtree(match_id)
            if section:
                sections.append(section)

    return "\n\n".join(sections)
