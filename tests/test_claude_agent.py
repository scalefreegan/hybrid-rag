"""Tests for the headless Claude Code agent wrapper."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pointy_rag.claude_agent import (
    run_agent,
    run_conversion_agent,
    run_disclosure_agent,
)


def _make_proc(
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
    *,
    communicate_raises: type[Exception] | None = None,
) -> MagicMock:
    """Build a mock asyncio Process."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    if communicate_raises is not None:
        proc.communicate = AsyncMock(side_effect=communicate_raises)
    else:
        proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


def _json_wrapper(result: str) -> bytes:
    return json.dumps({"type": "result", "result": result}).encode()


@pytest.mark.asyncio
async def test_run_agent_success():
    """Successful run returns the result field from the JSON wrapper."""
    expected = "Agent output here"
    proc = _make_proc(stdout=_json_wrapper(expected))

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        result = await run_agent("Do something")

    assert result == expected


@pytest.mark.asyncio
async def test_run_agent_timeout():
    """Timeout kills the process group and raises TimeoutError."""
    # proc.communicate() is called once for cleanup after timeout.
    proc = _make_proc(stdout=b"", stderr=b"", returncode=0)

    with (
        patch("asyncio.create_subprocess_exec", return_value=proc),
        patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
        patch("os.killpg") as mock_killpg,
    ):
        with pytest.raises(TimeoutError, match="timed out after 5s"):
            await run_agent("Do something", timeout=5)

    mock_killpg.assert_called_once()


@pytest.mark.asyncio
async def test_run_agent_nonzero_exit():
    """Non-zero exit code raises RuntimeError with stderr content."""
    proc = _make_proc(
        stdout=b"",
        stderr=b"something went wrong",
        returncode=1,
    )

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(RuntimeError, match="exited with code 1"):
            await run_agent("Do something")


@pytest.mark.asyncio
async def test_run_agent_start_new_session():
    """Subprocess must be spawned with start_new_session=True."""
    proc = _make_proc(stdout=_json_wrapper("ok"))

    with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        await run_agent("Do something")

    _kwargs = mock_exec.call_args.kwargs
    assert _kwargs.get("start_new_session") is True


@pytest.mark.asyncio
async def test_run_conversion_agent_prompt():
    """run_conversion_agent builds a prompt mentioning source and output paths."""
    proc = _make_proc(stdout=_json_wrapper("converted"))

    with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        result = await run_conversion_agent("/src/doc.pdf", "/out/doc.md", timeout=10)

    assert result == "converted"
    # The prompt should be the second positional arg to claude (after '-p').
    cmd = mock_exec.call_args.args
    prompt_idx = list(cmd).index("-p") + 1
    prompt = cmd[prompt_idx]
    assert "/src/doc.pdf" in prompt
    assert "/out/doc.md" in prompt


@pytest.mark.asyncio
async def test_run_disclosure_agent_prompt():
    """run_disclosure_agent builds a prompt with delimiters and title."""
    proc = _make_proc(stdout=_json_wrapper("summary text"))

    with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        result = await run_disclosure_agent(
            text="Long document text here.",
            title="Annual Report",
            level=2,
            timeout=10,
        )

    assert result == "summary text"
    cmd = mock_exec.call_args.args
    prompt_idx = list(cmd).index("-p") + 1
    prompt = cmd[prompt_idx]
    assert "Annual Report" in prompt
    # Prompt injection defense: document text wrapped in delimiters
    assert "<document>" in prompt
    assert "</document>" in prompt


@pytest.mark.asyncio
async def test_run_agent_claude_not_found():
    """Raise FileNotFoundError with helpful message when claude CLI is missing."""
    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError("No such file"),
    ):
        with pytest.raises(FileNotFoundError, match="Claude CLI not found"):
            await run_agent("Do something")


@pytest.mark.asyncio
async def test_run_conversion_agent_no_output_path():
    """run_conversion_agent without output_path asks for text-only output."""
    proc = _make_proc(stdout=_json_wrapper("# Markdown"))

    with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        result = await run_conversion_agent("/doc.pdf")

    assert result == "# Markdown"
    cmd = mock_exec.call_args.args
    prompt_idx = list(cmd).index("-p") + 1
    prompt = cmd[prompt_idx]
    assert "Output ONLY the markdown" in prompt
