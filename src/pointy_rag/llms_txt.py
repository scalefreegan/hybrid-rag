"""llms.txt assembly — render context subgraphs as structured markdown references."""

from __future__ import annotations

import psycopg
import psycopg.rows

from pointy_rag import db
from pointy_rag.models import DisclosureLevel


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


# ---------------------------------------------------------------------------
# Explore helpers
# ---------------------------------------------------------------------------


def _snippet(text: str, max_len: int = 60) -> str:
    """Truncate text to a single-line snippet with ellipsis."""
    if not text:
        return ""
    line = " ".join(text.split())  # collapse whitespace/newlines
    if len(line) <= max_len:
        return line
    return line[:max_len] + "..."


def _level_label(level: int | None) -> str:
    """Map disclosure level int to human-readable label."""
    if level is None:
        return "chunk"
    try:
        return f"L{level} {DisclosureLevel(level).name}"
    except ValueError:
        return f"L{level}"


def _node_role(node_id: str, match_ids: set[str], similar_ids: set[str]) -> str:
    """Classify a node's role in the explore package."""
    if node_id in match_ids:
        return "match"
    if node_id in similar_ids:
        return "related"
    return "context"


def _prepare_subgraph(
    subgraph: dict, conn: psycopg.Connection
) -> tuple[
    dict[str, dict],
    set[str],
    set[str],
    dict[str, list[str]],
    list[str],
]:
    """Shared subgraph preparation for explore assemblers.

    Returns:
        (nodes_index, match_ids, similar_ids, hierarchy, root_ids)
    """
    nodes_index: dict[str, dict] = {n["node_id"]: n for n in subgraph.get("nodes", [])}
    match_ids: set[str] = set(subgraph.get("matches", []))
    hierarchy: dict[str, list[str]] = subgraph.get("hierarchy", {})
    edges: list[dict] = subgraph.get("edges", [])

    similar_ids: set[str] = {
        e["target"]
        for e in edges
        if e.get("type") == "SIMILAR_TO" and e.get("source") in match_ids
    }

    for nid in match_ids:
        if nid not in nodes_index:
            nodes_index[nid] = _resolve_node_info(nid, conn)

    all_children: set[str] = {c for children in hierarchy.values() for c in children}
    root_ids = [nid for nid in hierarchy if nid not in all_children]

    return nodes_index, match_ids, similar_ids, hierarchy, root_ids


def _build_child_to_parent(hierarchy: dict[str, list[str]]) -> dict[str, str]:
    """Invert hierarchy to child -> parent mapping."""
    child_to_parent: dict[str, str] = {}
    for parent, children in hierarchy.items():
        for child in children:
            child_to_parent[child] = parent
    return child_to_parent


def _ancestor_chain(
    node_id: str,
    child_to_parent: dict[str, str],
    nodes_index: dict[str, dict],
) -> list[str]:
    """Walk up from node_id, returning ancestor IDs from root to immediate parent."""
    chain: list[str] = []
    current = node_id
    while current in child_to_parent:
        parent = child_to_parent[current]
        if parent in nodes_index:
            chain.append(parent)
        current = parent
    chain.reverse()  # root first
    return chain


# ---------------------------------------------------------------------------
# Explore assemblers
# ---------------------------------------------------------------------------


def assemble_explore_overview(
    subgraph: dict, conn: psycopg.Connection, query: str
) -> str:
    """Render Layer 1: ultra-compact structured index.

    Produces a minimal-token overview for agents with stats, hierarchy as
    indented list items, and pointers to llms.txt and contents/ files.
    """
    nodes_index, match_ids, similar_ids, hierarchy, root_ids = _prepare_subgraph(
        subgraph, conn
    )

    doc_ids = {
        n.get("document_id") for n in nodes_index.values() if n.get("document_id")
    }
    n_matches = len(match_ids)
    n_nodes = len(nodes_index)
    n_docs = len(doc_ids)

    lines = [
        "# Context Overview",
        "",
        f"{n_matches} matches | {n_nodes} nodes | {n_docs} documents",
        "",
    ]

    rendered: set[str] = set()

    def _render_tree(node_id: str, indent: int = 0) -> None:
        if node_id in rendered:
            return
        rendered.add(node_id)
        node = nodes_index.get(node_id)
        if not node:
            return

        title = node.get("title") or node_id
        content = _fetch_node_content(
            node_id, node.get("node_type") or "disclosure", conn
        )
        snip = _snippet(content)
        role = _node_role(node_id, match_ids, similar_ids)
        badge = f" [{role}]" if role != "context" else ""

        if indent == 0:
            # Root node as ## heading
            doc_id = node.get("document_id") or ""
            doc_title = title
            if doc_id:
                doc = db.get_document(doc_id, conn)
                if doc:
                    doc_title = doc.title
            lines.append(f"## {doc_title}")
        else:
            prefix = "  " * (indent - 1) + "- "
            if snip:
                entry = f"{prefix}{title}: {snip}{badge}"
            else:
                entry = f"{prefix}{title}{badge}"
            lines.append(entry)

        for child_id in hierarchy.get(node_id, []):
            _render_tree(child_id, indent + 1)

    for rid in root_ids:
        _render_tree(rid)

    # Orphan match nodes not reachable via hierarchy
    for mid in sorted(match_ids):
        if mid not in rendered:
            _render_tree(mid, indent=1)

    lines.append("")
    lines.append("Detail: llms.txt | Full content: contents/{node_id}.md")

    return "\n".join(lines)


def assemble_explore_llms_txt(
    subgraph: dict, conn: psycopg.Connection, query: str
) -> str:
    """Render Layer 2: detailed navigational TOC with descriptions and content links.

    Hierarchical markdown with heading depths, [ref:] pointers, level labels,
    truncated content, and links to full content files.
    """
    nodes_index, match_ids, similar_ids, hierarchy, root_ids = _prepare_subgraph(
        subgraph, conn
    )

    n_matches = len(match_ids)
    doc_ids = {
        n.get("document_id") for n in nodes_index.values() if n.get("document_id")
    }
    n_docs = len(doc_ids)
    n_edges = len(subgraph.get("edges", []))

    sections: list[str] = [
        f'# Explore: "{query}"',
        "",
        f"> {n_matches} matches across {n_docs} documents, "
        f"{len(nodes_index)} nodes in context graph, {n_edges} similarity edges",
    ]

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
        label = _level_label(raw_level)
        role = _node_role(node_id, match_ids, similar_ids)

        # Build heading
        if role == "match":
            header = f"{hashes} Match: {title} [ref:{node_id}]"
        elif role == "related":
            doc_id = node.get("document_id") or ""
            doc_title = doc_id
            if doc_id:
                doc = db.get_document(doc_id, conn)
                if doc:
                    doc_title = doc.title
            header = f"{hashes} Related: {title} [ref:{node_id}]"
            label = f"{label} — From: {doc_title}"
        else:
            header = f"{hashes} {title} [ref:{node_id}]"

        # Truncate content for TOC
        snip = _snippet(content, max_len=200)
        file_link = f"→ [contents/{node_id}.md](contents/{node_id}.md)"
        body = f"*{label}* {file_link}\n{snip}"

        child_parts = [render_subtree(c) for c in hierarchy.get(node_id, [])]
        child_text = "\n\n".join(p for p in child_parts if p)

        node_text = f"{header}\n{body}"
        return f"{node_text}\n\n{child_text}" if child_text else node_text

    for rid in root_ids:
        section = render_subtree(rid)
        if section:
            sections.append(section)

    for mid in sorted(match_ids):
        if mid not in rendered:
            section = render_subtree(mid)
            if section:
                sections.append(section)

    return "\n\n".join(sections)


def assemble_explore_contents(
    subgraph: dict, conn: psycopg.Connection
) -> dict[str, str]:
    """Render Layer 3: full content files with YAML frontmatter and ancestor context.

    Each content file includes ancestor hierarchy content above the node's own
    content, so agents get full context without fetching parent files.

    Returns:
        Dict mapping node_id to markdown content with YAML frontmatter.
    """
    nodes_index, match_ids, similar_ids, hierarchy, _root_ids = _prepare_subgraph(
        subgraph, conn
    )
    child_to_parent = _build_child_to_parent(hierarchy)

    contents: dict[str, str] = {}

    for node_id, node in nodes_index.items():
        title = node.get("title") or node_id
        raw_level = node.get("level")
        node_type = node.get("node_type") or "disclosure"
        role = _node_role(node_id, match_ids, similar_ids)

        # Resolve document title
        doc_id = node.get("document_id") or ""
        doc_title = doc_id
        if doc_id:
            doc = db.get_document(doc_id, conn)
            if doc:
                doc_title = doc.title

        # YAML frontmatter
        fm_lines = [
            "---",
            f"node_id: {node_id}",
            f"title: {title}",
            f"level: {_level_label(raw_level)}",
            f"document: {doc_title}",
            f"role: {role}",
            "---",
        ]

        # Build body: ancestor content + own content
        body_parts: list[str] = []

        # Ancestor hierarchy content
        ancestors = _ancestor_chain(node_id, child_to_parent, nodes_index)
        for anc_id in ancestors:
            anc = nodes_index[anc_id]
            anc_title = anc.get("title") or anc_id
            anc_level = anc.get("level")
            anc_type = anc.get("node_type") or "disclosure"
            anc_hashes = "#" * min(
                (int(anc_level) if anc_level is not None else 0) + 1, 6
            )
            anc_content = _fetch_node_content(anc_id, anc_type, conn)
            anc_label = _level_label(anc_level)
            body_parts.append(
                f"{anc_hashes} {anc_title}\n*{anc_label}*\n\n{anc_content}"
            )

        # Own content
        own_content = _fetch_node_content(node_id, node_type, conn)
        own_hashes = "#" * min((int(raw_level) if raw_level is not None else 0) + 1, 6)

        if role == "related":
            body_parts.append(
                f"{own_hashes} {title}\n> From: {doc_title}\n\n{own_content}"
            )
        else:
            body_parts.append(f"{own_hashes} {title}\n\n{own_content}")

        full_md = "\n".join(fm_lines) + "\n\n" + "\n\n".join(body_parts)
        contents[node_id] = full_md

    return contents


def assemble_explore(
    subgraph: dict, conn: psycopg.Connection, query: str
) -> tuple[str, str, dict[str, str]]:
    """Orchestrate the three-layer explore package assembly.

    Returns:
        (overview, llms_txt, contents_dict)
    """
    overview = assemble_explore_overview(subgraph, conn, query)
    llms_txt_doc = assemble_explore_llms_txt(subgraph, conn, query)
    contents = assemble_explore_contents(subgraph, conn)
    return overview, llms_txt_doc, contents
