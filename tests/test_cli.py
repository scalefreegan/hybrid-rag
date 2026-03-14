"""Stub tests for CLI scaffolding."""

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
