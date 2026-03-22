"""Unit tests for pointy_rag.workspace (no live DB needed)."""

from pathlib import Path
from unittest.mock import patch

from pointy_rag.workspace import (
    WorkspaceConfig,
    build_database_url,
    find_workspace,
    resolve_database_url,
    sanitize_db_name,
    set_active_workspace,
    write_workspace_marker,
)

# -- sanitize_db_name --------------------------------------------------------


def test_sanitize_simple():
    assert sanitize_db_name("beer_books") == "beer_books"


def test_sanitize_spaces_and_hyphens():
    assert sanitize_db_name("my-cool project") == "my_cool_project"


def test_sanitize_leading_digit():
    assert sanitize_db_name("42widgets") == "pr_42widgets"


def test_sanitize_empty():
    assert sanitize_db_name("") == "pointy_rag"
    assert sanitize_db_name("---") == "pointy_rag"


def test_sanitize_long_name():
    long = "a" * 100
    assert len(sanitize_db_name(long)) == 63


def test_sanitize_uppercase():
    assert sanitize_db_name("MyProject") == "myproject"


# -- build_database_url -------------------------------------------------------


def test_build_database_url_no_base():
    assert build_database_url("test_db") == "postgresql://localhost:5432/test_db"


def test_build_database_url_with_base():
    base = "postgresql://user:pass@dbhost:5433/old_db"
    result = build_database_url("new_db", base)
    assert "dbhost:5433" in result
    assert result.endswith("/new_db")
    assert "user:pass" in result


# -- find_workspace -----------------------------------------------------------


def test_find_workspace_no_marker(tmp_path):
    assert find_workspace(tmp_path) is None


def test_find_workspace_with_marker(tmp_path):
    marker = tmp_path / ".pointy-rag.toml"
    marker.write_text(
        '[workspace]\ndatabase_url = "postgresql://localhost:5432/test_ws"\n'
    )
    ws = find_workspace(tmp_path)
    assert ws is not None
    assert ws.database_url == "postgresql://localhost:5432/test_ws"
    assert ws.directory == tmp_path.resolve()


# -- write_workspace_marker ---------------------------------------------------


def test_write_marker_roundtrips(tmp_path):
    url = "postgresql://localhost:5432/roundtrip_db"
    write_workspace_marker(tmp_path, url)
    ws = find_workspace(tmp_path)
    assert ws is not None
    assert ws.database_url == url


def test_converted_dir(tmp_path):
    ws = WorkspaceConfig(directory=tmp_path, database_url="postgresql://x/y")
    assert ws.converted_dir == tmp_path / "converted"


# -- resolve_database_url -----------------------------------------------------


def test_resolve_explicit_wins():
    set_active_workspace(
        WorkspaceConfig(directory=Path("/ws"), database_url="postgresql://ws/db")
    )
    try:
        assert (
            resolve_database_url("postgresql://explicit/db")
            == "postgresql://explicit/db"
        )
    finally:
        set_active_workspace(None)


def test_resolve_workspace_over_env():
    ws = WorkspaceConfig(directory=Path("/ws"), database_url="postgresql://ws/db")
    set_active_workspace(ws)
    try:
        result = resolve_database_url()
        assert result == "postgresql://ws/db"
    finally:
        set_active_workspace(None)


def test_resolve_falls_back_to_settings():
    set_active_workspace(None)
    with patch("pointy_rag.config.get_settings") as mock:
        mock.return_value.database_url = "postgresql://settings/fallback"
        result = resolve_database_url()
    assert result == "postgresql://settings/fallback"
