"""Typer CLI for pointy-rag."""

import importlib.resources
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse, urlunparse

import psycopg
import typer
from rich.console import Console

app = typer.Typer(
    name="pointy-rag",
    help="Hybrid RAG with Voyage AI embeddings and pgvector.",
    no_args_is_help=True,
)

console = Console()


@app.callback()
def _app_callback(
    workspace: Annotated[
        Path | None,
        typer.Option("--workspace", "-w", help="Path to a pointy-rag workspace"),
    ] = None,
):
    """Global options."""
    from pointy_rag.workspace import find_workspace, set_active_workspace

    ws = find_workspace(workspace)
    if ws is not None:
        set_active_workspace(ws)


def _mask_url_password(url: str) -> str:
    """Mask the password portion of a database URL."""
    parsed = urlparse(url)
    if parsed.password:
        host = parsed.hostname or ""
        host_part = f"[{host}]" if ":" in host else host
        netloc = f"{parsed.username}:***@{host_part}"
        if parsed.port:
            netloc += f":{parsed.port}"
        masked = parsed._replace(netloc=netloc)
        return urlunparse(masked)
    return url


@app.command()
def init(
    path: Annotated[
        Path | None,
        typer.Argument(help="Workspace directory (created if needed; default: cwd)"),
    ] = None,
    database_url: Annotated[
        str | None,
        typer.Option(
            "--database-url",
            help="PostgreSQL connection string (overrides auto-derivation)",
        ),
    ] = None,
):
    """Initialize a workspace: create directory, database, tables, and marker."""
    from pointy_rag.config import get_settings
    from pointy_rag.db import create_tables, ensure_database
    from pointy_rag.workspace import (
        build_database_url,
        sanitize_db_name,
        write_workspace_marker,
    )

    ws_dir = Path(path).resolve() if path else Path.cwd().resolve()

    if not ws_dir.exists():
        typer.confirm(f"Directory {ws_dir} does not exist. Create it?", abort=True)
        ws_dir.mkdir(parents=True)

    if database_url:
        url = database_url
    else:
        db_name = sanitize_db_name(ws_dir.name)
        base_url = get_settings().database_url
        url = build_database_url(db_name, base_url)

    console.print(f"[bold]Initializing workspace:[/] {ws_dir}")
    console.print(f"[bold]Database:[/] {_mask_url_password(url)}")
    try:
        ensure_database(url)
        create_tables(url)
        write_workspace_marker(ws_dir, url)
        console.print("[bold green]\u2713[/] Workspace initialized successfully.")
    except Exception as exc:
        safe_msg = _mask_url_password(str(exc))
        console.print(f"[bold red]\u2717[/] Failed to initialize workspace: {safe_msg}")
        raise typer.Exit(code=1) from exc


@app.command()
def ingest(
    paths: Annotated[
        list[Path],
        typer.Argument(help="Files to ingest (PDF or EPUB)"),
    ],
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Directory for converted markdown"),
    ] = None,
    no_agent: Annotated[
        bool,
        typer.Option("--no-agent", help="Skip Claude agent (fallback, no disclosure)"),
    ] = False,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", help="Agent timeout in seconds per stage"),
    ] = None,
):
    """Ingest documents into the vector store."""
    import asyncio

    from rich.progress import Progress, SpinnerColumn, TextColumn

    from pointy_rag.db import get_connection
    from pointy_rag.ingest import ingest_paths
    from pointy_rag.workspace import get_active_workspace

    if output_dir is None:
        ws = get_active_workspace()
        output_dir = ws.converted_dir if ws is not None else Path("./converted")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Ingesting {len(paths)} file(s)...", total=None)

        def update_progress(msg: str):
            progress.update(task, description=msg)

        try:
            with get_connection() as conn:
                succeeded, failed = asyncio.run(
                    ingest_paths(
                        paths,
                        conn,
                        output_dir=output_dir,
                        use_agent=not no_agent,
                        timeout=timeout,
                        on_progress=update_progress,
                    )
                )
        except Exception as exc:
            console.print(f"[bold red]Error:[/] {exc}")
            raise typer.Exit(code=1) from exc
        finally:
            progress.remove_task(task)

    for doc in succeeded:
        console.print(f"[bold green]\u2713[/] {doc.title} ({doc.format})")

    for path, exc in failed:
        console.print(f"[bold red]\u2717[/] {path.name}: {exc}")

    console.print(f"\n[bold]{len(succeeded)} succeeded, {len(failed)} failed[/]")
    if failed:
        raise typer.Exit(code=1)


@app.command()
def convert(
    paths: Annotated[
        list[Path],
        typer.Argument(help="Files to convert (PDF or EPUB)"),
    ],
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir", "-o", help="Directory for converted markdown"),
    ] = Path("."),
    no_agent: Annotated[
        bool,
        typer.Option("--no-agent", help="Skip Claude agent, use library extraction"),
    ] = False,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", help="Agent timeout in seconds per stage"),
    ] = None,
):
    """Convert PDF or EPUB files to markdown without ingesting."""
    import asyncio

    from rich.progress import Progress, SpinnerColumn, TextColumn

    from pointy_rag.converter import convert_to_markdown

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        for path in paths:
            task = progress.add_task(f"Converting {path.name}...", total=None)

            def update_progress(msg: str):
                progress.update(task, description=msg)

            try:
                markdown, out_path = asyncio.run(
                    convert_to_markdown(
                        path,
                        output_dir=output_dir,
                        use_agent=not no_agent,
                        timeout=timeout,
                        on_progress=update_progress,
                    )
                )
                progress.remove_task(task)
                console.print(f"[bold green]\u2713[/] {path.name} -> {out_path}")
            except Exception as exc:
                progress.remove_task(task)
                console.print(f"[bold red]\u2717[/] {path.name}: {exc}")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query")],
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Number of results", min=1, max=100),
    ] = 10,
    threshold: Annotated[
        float,
        typer.Option("--threshold", "-t", help="Minimum similarity score"),
    ] = 0.7,
    level: Annotated[
        int | None,
        typer.Option("--level", "-l", help="Filter by disclosure level (0-3)"),
    ] = None,
    content: Annotated[
        bool,
        typer.Option("--content", "-c", help="Show chunk content"),
    ] = False,
    graph: Annotated[
        bool,
        typer.Option("--graph", "-g", help="Expand results via knowledge graph"),
    ] = False,
):
    """Search the vector store with pointer-based retrieval."""
    if graph:
        graph_search_cmd(query, limit=limit, threshold=threshold)
        return

    from rich.table import Table

    from pointy_rag.db import get_connection
    from pointy_rag.search import batch_children_counts
    from pointy_rag.search import search as do_search

    try:
        with get_connection() as conn:
            results = do_search(query, conn, limit=limit, threshold=threshold)

            if level is not None:
                results = [
                    r
                    for r in results
                    if r.disclosure_doc and r.disclosure_doc.level == level
                ]

            if not results:
                console.print("[yellow]No results found.[/]")
                return

            # Batch-fetch children counts in one query.
            ddoc_ids = [r.disclosure_doc.id for r in results if r.disclosure_doc]
            children_counts = batch_children_counts(ddoc_ids, conn)

            table = Table(title=f"Search: {query!r}")
            table.add_column("Score", style="cyan", width=6)
            table.add_column("Document", style="green")
            table.add_column("Level", width=6)
            table.add_column("Section", style="bold")
            table.add_column("Children", width=8)
            if content:
                table.add_column("Content", max_width=60)

            for r in results:
                doc_title = r.document.title if r.document else "\u2014"
                ddoc = r.disclosure_doc
                ddoc_title = ddoc.title if ddoc else "\u2014"
                ddoc_level = str(ddoc.level) if ddoc else "\u2014"
                children_count = children_counts.get(ddoc.id, 0) if ddoc else 0
                row = [
                    f"{r.score:.3f}",
                    doc_title,
                    ddoc_level,
                    ddoc_title,
                    str(children_count),
                ]
                if content:
                    text = r.chunk.content
                    snippet = text[:200] + "..." if len(text) > 200 else text
                    row.append(snippet)
                table.add_row(*row)

    except Exception as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(table)


@app.command("graph-search")
def graph_search_cmd(
    query: Annotated[str, typer.Argument(help="Search query")],
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Number of results", min=1, max=100),
    ] = 10,
    threshold: Annotated[
        float,
        typer.Option("--threshold", "-t", help="Minimum similarity score"),
    ] = 0.7,
    levels_up: Annotated[
        int,
        typer.Option("--levels-up", help="Hierarchy levels to walk up per match"),
    ] = 1,
    no_similar: Annotated[
        bool,
        typer.Option("--no-similar", help="Skip SIMILAR_TO edge traversal"),
    ] = False,
):
    """Search and expand results via the knowledge graph, rendering a reference doc."""
    from pointy_rag.db import get_connection
    from pointy_rag.search import graph_search

    try:
        with get_connection() as conn:
            result = graph_search(
                query,
                conn,
                limit=limit,
                threshold=threshold,
                hierarchy_levels_up=levels_up,
                include_similar=not no_similar,
            )
    except Exception as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[dim]{len(result.vector_results)} vector matches, "
        f"{result.node_count} nodes in context graph, "
        f"{result.edge_count} similarity edges[/]"
    )

    if result.reference_document is not None:
        console.print(result.reference_document)
    else:
        console.print(
            "[yellow]No graph context available (KG disabled or no results).[/]"
        )


@app.command()
def explore(
    query: Annotated[str, typer.Argument(help="Search query")],
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Number of results", min=1, max=100),
    ] = 10,
    threshold: Annotated[
        float,
        typer.Option("--threshold", "-t", help="Minimum similarity score"),
    ] = 0.6,
    levels_up: Annotated[
        int,
        typer.Option("--levels-up", help="Hierarchy levels to walk up per match"),
    ] = 3,
    no_similar: Annotated[
        bool,
        typer.Option("--no-similar", help="Skip SIMILAR_TO edge traversal"),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output directory"),
    ] = None,
):
    """Explore query results as a three-layer progressive disclosure package."""
    from pointy_rag.db import get_connection
    from pointy_rag.search import explore as explore_search

    if output is None:
        output = Path(f"./explore-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}")

    try:
        with get_connection() as conn:
            result = explore_search(
                query,
                conn,
                limit=limit,
                threshold=threshold,
                hierarchy_levels_up=levels_up,
                include_similar=not no_similar,
            )
    except Exception as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[dim]{len(result.vector_results)} vector matches, "
        f"{result.node_count} nodes in context graph, "
        f"{result.edge_count} similarity edges[/]"
    )

    if result.overview is None:
        console.print(
            "[yellow]No graph context available (KG disabled or no results).[/]"
        )
        raise typer.Exit()

    # Write the three-layer package
    contents_dir = output / "contents"
    contents_dir.mkdir(parents=True, exist_ok=True)

    (output / "overview.md").write_text(result.overview)
    if result.llms_txt is not None:
        (output / "llms.txt").write_text(result.llms_txt)

    for node_id, content in result.contents.items():
        (contents_dir / f"{node_id}.md").write_text(content)

    n_files = 2 + len(result.contents)
    console.print(f"Wrote {n_files} files to {output}/")


@app.command("graph-status")
def graph_status():
    """Show knowledge graph statistics."""
    from rich.table import Table

    from pointy_rag.db import get_connection
    from pointy_rag.graph import get_graph_stats

    try:
        with get_connection() as conn:
            stats = get_graph_stats(conn)
    except Exception as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc

    table = Table(title="Knowledge Graph Status")
    table.add_column("Metric", style="bold")
    table.add_column("Count", style="cyan", justify="right")

    total_nodes = stats.get("node_count", 0)
    total_edges = stats.get("edge_count", 0)
    similar_to = stats.get("similar_to_count", 0)
    contains = stats.get("contains_count", 0)

    table.add_row("Total nodes", str(total_nodes))
    table.add_row("Total edges", str(total_edges))
    table.add_row("  CONTAINS edges", str(contains))
    table.add_row("  SIMILAR_TO edges", str(similar_to))

    console.print(table)


@app.command()
def drill(
    doc_id: Annotated[str, typer.Argument(help="Disclosure doc ID to drill into")],
    content: Annotated[
        bool,
        typer.Option("--content", "-c", help="Show full content of children"),
    ] = False,
):
    """Drill into a disclosure document and view its children."""
    from rich.panel import Panel
    from rich.table import Table

    from pointy_rag.db import get_connection
    from pointy_rag.search import get_children, get_disclosure_content, get_parent_chain

    try:
        with get_connection() as conn:
            doc_content = get_disclosure_content(doc_id, conn)
            if doc_content is None:
                console.print(f"[bold red]Error:[/] Doc {doc_id!r} not found.")
                raise typer.Exit(code=1)

            breadcrumbs = get_parent_chain(doc_id, conn)
            children = get_children(doc_id, conn)

            # Breadcrumb trail.
            if breadcrumbs:
                trail = " > ".join(d.title for d in breadcrumbs)
                console.print(f"[dim]{trail}[/]")

            console.print(Panel(doc_content, title="Content", border_style="green"))

            if children:
                table = Table(title="Children")
                table.add_column("ID", style="cyan", max_width=12)
                table.add_column("Level", width=6)
                table.add_column("Title", style="bold")
                if content:
                    table.add_column("Content", max_width=60)

                for child in children:
                    row = [
                        child["id"][:12],
                        str(child["level"]),
                        child["title"],
                    ]
                    if content:
                        cc = get_disclosure_content(child["id"], conn) or ""
                        snippet = cc[:200] + "..." if len(cc) > 200 else cc
                        row.append(snippet)
                    table.add_row(*row)

                console.print(table)
            else:
                console.print("[dim]No children (leaf node).[/]")

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def ls():
    """List ingested documents."""
    from rich.table import Table

    from pointy_rag.db import get_connection, list_documents

    try:
        with get_connection() as conn:
            docs = list_documents(conn)

            if not docs:
                console.print("[yellow]No documents ingested yet.[/]")
                return

            table = Table(title="Ingested Documents")
            table.add_column("ID", style="cyan", max_width=12)
            table.add_column("Title", style="bold")
            table.add_column("Format", width=6)
            table.add_column("Chunks", width=8)
            table.add_column("Disclosures", width=12)
            table.add_column("Date", style="dim")

            for d in docs:
                table.add_row(
                    d["id"][:12],
                    d["title"],
                    d["format"],
                    str(d["chunk_count"]),
                    str(d["disclosure_count"]),
                    str(d["created_at"].date()) if d["created_at"] else "\u2014",
                )

            console.print(table)

    except Exception as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc


@app.command("graph-backfill")
def graph_backfill():
    """Migrate existing PostgreSQL data into the knowledge graph (one-time backfill)."""
    from rich.progress import (
        BarColumn,
        Progress,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    from pointy_rag.config import get_settings
    from pointy_rag.db import (
        get_chunks_by_document,
        get_connection,
        get_disclosure_docs_by_document,
        list_documents,
    )
    from pointy_rag.graph import (
        create_chunk_node,
        create_contains_edge,
        create_disclosure_node,
        create_similar_to_edges,
        ensure_graph,
        node_exists,
    )

    settings = get_settings()
    if not settings.kg_enabled:
        console.print(
            "[yellow]Knowledge graph is disabled"
            " (POINTY_KG_ENABLED=false). Aborting.[/]"
        )
        raise typer.Exit(code=1)

    try:
        with get_connection() as conn:
            ensure_graph(conn)
            conn.commit()

            docs = list_documents(conn)
            if not docs:
                console.print("[yellow]No documents found. Nothing to backfill.[/]")
                return

            total_nodes = 0
            total_similarity_edges = 0

            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                doc_task = progress.add_task("Backfilling...", total=len(docs))

                failed_docs: list[str] = []
                for doc in docs:
                    doc_id = doc["id"]
                    title = doc["title"][:40]
                    progress.update(doc_task, description=f"Processing: {title}")

                    try:
                        ddocs = get_disclosure_docs_by_document(doc_id, conn)
                        for ddoc in ddocs:
                            is_new = not node_exists(ddoc.id, conn)
                            create_disclosure_node(ddoc, conn)
                            if is_new:
                                total_nodes += 1
                            if ddoc.parent_id:
                                create_contains_edge(
                                    ddoc.parent_id, ddoc.id, ddoc.ordering, conn
                                )

                        chunks = get_chunks_by_document(doc_id, conn)
                        for chunk in chunks:
                            is_new = not node_exists(chunk.id, conn)
                            create_chunk_node(chunk, doc_id, conn)
                            if is_new:
                                total_nodes += 1
                            create_contains_edge(
                                chunk.disclosure_doc_id, chunk.id, 0, conn
                            )
                            if is_new and chunk.embedding is not None:
                                total_similarity_edges += create_similar_to_edges(
                                    chunk, conn
                                )

                        conn.commit()
                    except psycopg.Error as exc:
                        console.print(
                            f"[yellow]Warning:[/] Failed to backfill {title}: {exc}"
                        )
                        conn.rollback()
                        failed_docs.append(doc_id)

                    progress.advance(doc_task)

        console.print("\n[bold green]\u2713[/] Backfill complete.")
        console.print(f"  Documents processed: {len(docs) - len(failed_docs)}")
        console.print(f"  Nodes created:       {total_nodes}")
        console.print(f"  Similarity edges:    {total_similarity_edges}")
        if failed_docs:
            console.print(f"  [yellow]Failed:              {len(failed_docs)}[/]")

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc


def _parse_skill_frontmatter(text: str) -> dict[str, str]:
    """Extract name and description from YAML frontmatter (no PyYAML dep)."""
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta
    end = text.find("---", 3)
    if end == -1:
        return meta
    for line in text[3:end].strip().splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta


@app.command("install-skill")
def install_skill(
    global_: Annotated[
        bool,
        typer.Option(
            "--global",
            "-g",
            help="Install to ~/.<agent>/skills/ instead of local",
        ),
    ] = False,
    agent: Annotated[
        str,
        typer.Option(
            "--agent",
            "-a",
            help="Target agent (claude, cursor, windsurf, etc.)",
        ),
    ] = "claude",
):
    """Install the pointy-rag Claude Code skill."""
    # 1. Locate bundled SKILL.md
    skill_pkg = importlib.resources.files("pointy_rag._skill")
    skill_src = skill_pkg / "SKILL.md"

    # 2. Parse frontmatter for metadata
    skill_text = skill_src.read_text(encoding="utf-8")
    meta = _parse_skill_frontmatter(skill_text)
    skill_name = meta.get("name", "pointy-rag")
    skill_desc = meta.get("description", "")

    # 3. Determine target directory
    if global_:
        base = Path.home() / f".{agent}" / "skills"
    else:
        base = Path(f".{agent}") / "skills"

    target_dir = base / "pointy-rag"
    target_file = target_dir / "SKILL.md"

    # 4. Copy SKILL.md
    target_dir.mkdir(parents=True, exist_ok=True)
    with importlib.resources.as_file(skill_src) as src_path:
        shutil.copy2(src_path, target_file)

    # 5. Upsert manifest
    manifest_path = base / ".skill-manager-manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"version": "1.0", "skills": {}}

    manifest["skills"][skill_name] = {
        "name": skill_name,
        "path": str(target_dir.resolve()),
        "description": skill_desc,
        "composed_from": [],
        "installed_at": datetime.now(UTC).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    console.print(
        f"[bold green]\u2713[/] Installed [bold]{skill_name}[/] skill to {target_dir}"
    )


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
