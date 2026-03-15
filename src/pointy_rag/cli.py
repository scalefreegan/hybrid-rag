"""Typer CLI for pointy-rag."""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse, urlunparse

import typer
from rich.console import Console

app = typer.Typer(
    name="pointy-rag",
    help="Hybrid RAG with Voyage AI embeddings and pgvector.",
    no_args_is_help=True,
)

console = Console()


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
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="PostgreSQL connection string"),
    ] = None,
):
    """Initialize the database: create tables and indexes."""
    from pointy_rag.config import get_settings
    from pointy_rag.db import create_tables

    url = database_url or get_settings().database_url
    console.print(f"[bold]Initializing database:[/] {_mask_url_password(url)}")
    try:
        create_tables(url)
        console.print("[bold green]\u2713[/] Tables created successfully.")
    except Exception as exc:
        safe_msg = _mask_url_password(str(exc))
        console.print(f"[bold red]\u2717[/] Failed to initialize database: {safe_msg}")
        raise typer.Exit(code=1) from exc


@app.command()
def ingest(
    path: Annotated[
        Path,
        typer.Argument(help="File or directory to ingest"),
    ],
    collection: Annotated[
        str,
        typer.Option("--collection", "-c", help="Target collection name"),
    ] = "default",
):
    """Ingest documents into the vector store."""
    console.print(
        f"[bold green]ingest[/] {path} \u2192 {collection} (not yet implemented)"
    )


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
):
    """Search the vector store with pointer-based retrieval."""
    from rich.table import Table

    from pointy_rag.db import get_connection
    from pointy_rag.search import get_children
    from pointy_rag.search import search as do_search

    try:
        with get_connection() as conn:
            results = do_search(
                query, conn, limit=limit, threshold=threshold
            )

            if level is not None:
                results = [
                    r for r in results
                    if r.disclosure_doc and r.disclosure_doc.level == level
                ]

            if not results:
                console.print("[yellow]No results found.[/]")
                return

            table = Table(title=f"Search: {query!r}")
            table.add_column("Score", style="cyan", width=6)
            table.add_column("Document", style="green")
            table.add_column("Level", width=6)
            table.add_column("Section", style="bold")
            table.add_column("Children", width=8)
            if content:
                table.add_column("Content", max_width=60)

            for r in results:
                doc_title = (
                    r.document.title if r.document else "\u2014"
                )
                ddoc = r.disclosure_doc
                ddoc_title = ddoc.title if ddoc else "\u2014"
                level = str(ddoc.level) if ddoc else "\u2014"
                children_count = (
                    len(get_children(ddoc.id, conn))
                    if ddoc else 0
                )
                row = [
                    f"{r.score:.3f}",
                    doc_title,
                    level,
                    ddoc_title,
                    str(children_count),
                ]
                if content:
                    text = r.chunk.content
                    snippet = (
                        text[:200] + "..."
                        if len(text) > 200 else text
                    )
                    row.append(snippet)
                table.add_row(*row)

    except Exception as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(code=1) from exc

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
                console.print(
                    f"[bold red]Error:[/] Doc {doc_id!r} not found."
                )
                raise typer.Exit(code=1)

            breadcrumbs = get_parent_chain(doc_id, conn)
            children = get_children(doc_id, conn)

            # Breadcrumb trail.
            if breadcrumbs:
                trail = " > ".join(d.title for d in breadcrumbs)
                console.print(f"[dim]{trail}[/]")

            console.print(
                Panel(doc_content, title="Content", border_style="green")
            )

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
                        cc = get_disclosure_content(
                            child["id"], conn
                        ) or ""
                        snippet = (
                            cc[:200] + "..."
                            if len(cc) > 200
                            else cc
                        )
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
def ls(
    collection: Annotated[
        str | None,
        typer.Argument(help="Collection to list (omit to list all collections)"),
    ] = None,
):
    """List collections or documents in a collection."""
    if collection:
        console.print(f"[bold green]ls[/] {collection} (not yet implemented)")
    else:
        console.print("[bold green]ls[/] (listing all collections \u2014 not yet implemented)")  # noqa: E501


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
