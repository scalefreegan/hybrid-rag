"""Typer CLI for pointy-rag."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

app = typer.Typer(
    name="pointy-rag",
    help="Hybrid RAG with Voyage AI embeddings and pgvector.",
    no_args_is_help=True,
)

console = Console()


@app.command()
def init(
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="PostgreSQL connection string"),
    ] = None,
):
    """Initialize the database: create tables and indexes."""
    from pointy_rag.db import create_tables, get_database_url

    url = database_url or get_database_url()
    console.print(f"[bold]Initializing database:[/] {url}")
    try:
        create_tables(url)
        console.print("[bold green]✓[/] Tables created successfully.")
    except Exception as exc:
        console.print(f"[bold red]✗[/] Failed to initialize database: {exc}")
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
    console.print(f"[bold green]ingest[/] {path} → {collection} (not yet implemented)")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query")],
    collection: Annotated[
        str,
        typer.Option("--collection", "-c", help="Collection to search"),
    ] = "default",
    top_k: Annotated[
        int,
        typer.Option("--top-k", "-k", help="Number of results"),
    ] = 5,
):
    """Search the vector store with hybrid retrieval."""
    console.print(f"[bold green]search[/] {query!r} k={top_k} (not yet implemented)")


@app.command()
def drill(
    query: Annotated[str, typer.Argument(help="Query to drill into")],
    collection: Annotated[
        str,
        typer.Option("--collection", "-c", help="Collection to search"),
    ] = "default",
):
    """Deep-dive retrieval with re-ranking."""
    console.print(f"[bold green]drill[/] {query!r} (not yet implemented)")


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
        console.print("[bold green]ls[/] (listing all collections — not yet implemented)")  # noqa: E501


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
