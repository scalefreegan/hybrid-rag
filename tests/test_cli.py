"""Stub tests for CLI scaffolding."""

from unittest.mock import patch

from typer.testing import CliRunner

from pointy_rag.cli import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output
    assert "ingest" in result.output
    assert "search" in result.output
    assert "drill" in result.output
    assert "ls" in result.output


def test_workspace_flag_accepted(tmp_path):
    """--workspace flag is parsed without error (even with no marker)."""
    result = runner.invoke(app, ["--workspace", str(tmp_path / "nope"), "--help"])
    assert result.exit_code == 0


def test_init_creates_marker(tmp_path):
    """init <path> creates directory + marker file (DB calls mocked)."""
    ws_dir = tmp_path / "my_workspace"
    with (
        patch("pointy_rag.db.ensure_database"),
        patch("pointy_rag.db.create_tables"),
    ):
        result = runner.invoke(app, ["init", str(ws_dir)], input="y\n")
    assert result.exit_code == 0
    assert (ws_dir / ".pointy-rag.toml").exists()


def test_init_cwd_no_path(tmp_path, monkeypatch):
    """init with no path argument uses cwd."""
    monkeypatch.chdir(tmp_path)
    with (
        patch("pointy_rag.db.ensure_database"),
        patch("pointy_rag.db.create_tables"),
    ):
        result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (tmp_path / ".pointy-rag.toml").exists()
