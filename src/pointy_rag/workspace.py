"""Directory-driven workspace for pointy-rag.

A workspace is a directory on disk bound to a PostgreSQL database.
The directory holds artifacts (converted markdown, etc.) and a
`.pointy-rag.toml` marker that records the database URL.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

MARKER_FILE = ".pointy-rag.toml"


@dataclass
class WorkspaceConfig:
    """Workspace configuration loaded from a marker file."""

    directory: Path
    database_url: str

    @property
    def converted_dir(self) -> Path:
        return self.directory / "converted"


def sanitize_db_name(name: str) -> str:
    """Convert a directory leaf name to a valid PostgreSQL identifier.

    - Lowercase, replace non-alphanumeric with ``_``, strip edge underscores
    - Prefix ``pr_`` if starts with digit
    - Truncate to 63 chars (PG identifier limit)
    """
    name = name.lower()
    name = re.sub(r"[^a-z0-9]", "_", name)
    name = name.strip("_")
    if not name:
        return "pointy_rag"
    if name[0].isdigit():
        name = f"pr_{name}"
    return name[:63]


def build_database_url(db_name: str, base_url: str | None = None) -> str:
    """Swap the database name into a PostgreSQL URL.

    Uses *base_url* for host/port/credentials and replaces the path component
    with ``/db_name``.  Falls back to ``localhost:5432`` when no base is given.
    """
    if base_url is None:
        return f"postgresql://localhost:5432/{db_name}"
    parsed = urlparse(base_url)
    replaced = parsed._replace(path=f"/{db_name}")
    return urlunparse(replaced)


def find_workspace(directory: Path | None = None) -> WorkspaceConfig | None:
    """Load workspace config from a marker file.

    Looks for ``MARKER_FILE`` in *directory* (or cwd).  No walk-up — exact
    match only.  Returns ``None`` when no marker is found.
    """
    target = Path(directory) if directory is not None else Path.cwd()
    marker = target / MARKER_FILE
    if not marker.is_file():
        return None
    with open(marker, "rb") as f:
        data = tomllib.load(f)
    ws_section = data.get("workspace", {})
    url = ws_section.get("database_url", "")
    if not url:
        return None
    return WorkspaceConfig(directory=target.resolve(), database_url=url)


def write_workspace_marker(directory: Path, database_url: str) -> Path:
    """Write a ``.pointy-rag.toml`` marker into *directory*."""
    marker = Path(directory) / MARKER_FILE
    marker.write_text(
        f'[workspace]\ndatabase_url = "{database_url}"\n',
        encoding="utf-8",
    )
    return marker


# ---------------------------------------------------------------------------
# Module-level active workspace (set by CLI callback)
# ---------------------------------------------------------------------------
_active_workspace: WorkspaceConfig | None = None


def set_active_workspace(ws: WorkspaceConfig | None) -> None:
    global _active_workspace
    _active_workspace = ws


def get_active_workspace() -> WorkspaceConfig | None:
    return _active_workspace


def resolve_database_url(explicit_url: str | None = None) -> str:
    """Resolve the database URL using the override chain.

    1. *explicit_url* (``--database-url`` flag)
    2. Active workspace marker
    3. ``POINTY_DATABASE_URL`` env var / settings default
    """
    if explicit_url:
        return explicit_url
    ws = get_active_workspace()
    if ws is not None:
        return ws.database_url
    from pointy_rag.config import get_settings

    return get_settings().database_url
