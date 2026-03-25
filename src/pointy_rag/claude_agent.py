"""Headless Claude Code agent wrapper using subprocess with streaming."""

import asyncio
import json
import logging
import os
import signal

logger = logging.getLogger(__name__)


async def run_agent(
    prompt: str,
    system_prompt: str | None = None,
    max_turns: int = 25,
    timeout: int | None = None,
    allowed_tools: list[str] | None = None,
    cwd: str | None = None,
    model: str | None = None,
) -> str:
    """Run a headless Claude Code agent via ``claude -p`` with streaming.

    Streams stdout line-by-line, logging tool uses and progress events
    as they happen. Returns the final result text.

    Args:
        prompt: The task prompt to send to the agent.
        system_prompt: Optional system context appended via --append-system-prompt.
        max_turns: Maximum agentic turns (safety bound).
        timeout: Wall-clock timeout in seconds. None means no timeout.
        allowed_tools: Tool names the agent may use. Defaults to no extra tools.
        cwd: Working directory for the subprocess. Defaults to current directory.
        model: Optional model override (e.g. "haiku", "sonnet").

    Returns:
        The agent's result text (``result`` field from the JSON wrapper).

    Raises:
        FileNotFoundError: If the ``claude`` CLI is not installed or not on PATH.
        RuntimeError: If the process exits with non-zero status.
        TimeoutError: If the process exceeds the timeout.
    """
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(max_turns),
    ]

    if allowed_tools:
        for tool in allowed_tools:
            cmd.extend(["--allowedTools", tool])

    if model:
        cmd.extend(["--model", model])

    if system_prompt:
        cmd.extend(["--append-system-prompt", system_prompt])

    # Spawn in its own process group so we can kill the entire tree on timeout.
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,
            limit=1024 * 1024,  # 1MB line buffer (default 64KB too small for stream-json)
        )
    except FileNotFoundError:
        raise FileNotFoundError(
            "Claude CLI not found. Install it: https://docs.anthropic.com/en/docs/claude-code"
        ) from None

    result_text: str | None = None
    is_error = False

    async def _stream_stdout() -> None:
        nonlocal result_text, is_error
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")

            if etype == "assistant":
                # Log tool uses from the assistant message
                msg = event.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "tool_use":
                        tool_name = block.get("name", "?")
                        tool_input = block.get("input", {})
                        if tool_name == "Read":
                            target = tool_input.get("file_path", "?")
                            logger.info("  → Read %s", _short_path(target))
                        elif tool_name == "Write":
                            target = tool_input.get("file_path", "?")
                            logger.info("  → Write %s", _short_path(target))
                        elif tool_name == "Edit":
                            target = tool_input.get("file_path", "?")
                            logger.info("  → Edit %s", _short_path(target))
                        elif tool_name == "Bash":
                            cmd_str = tool_input.get("command", "?")
                            logger.info("  → Bash: %s", cmd_str[:80])
                        elif tool_name == "Glob":
                            pattern = tool_input.get("pattern", "?")
                            logger.info("  → Glob %s", pattern)
                        else:
                            logger.info("  → %s", tool_name)

            elif etype == "result":
                result_text = event.get("result")
                is_error = event.get("is_error", False)
                duration = event.get("duration_ms", 0)
                turns = event.get("num_turns", 0)
                cost = event.get("total_cost_usd", 0)
                logger.info(
                    "  Agent done: %d turns, %.1fs, $%.4f",
                    turns, duration / 1000, cost,
                )

    try:
        if timeout and timeout > 0:
            await asyncio.wait_for(_stream_stdout(), timeout=timeout)
        else:
            await _stream_stdout()

        await proc.wait()
    except TimeoutError as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            proc.kill()
        await proc.communicate()
        raise TimeoutError(f"Claude agent timed out after {timeout}s") from exc

    if proc.returncode != 0:
        stderr_bytes = await proc.stderr.read() if proc.stderr else b""
        err = stderr_bytes.decode("utf-8", errors="replace").strip()
        logger.error("Agent exit code %d, stderr: %s", proc.returncode, err[:500])
        raise RuntimeError(f"Claude agent exited with code {proc.returncode}: {err}")

    if is_error:
        logger.error("Agent returned is_error=True, result: %s", str(result_text)[:500])
        raise RuntimeError(f"Claude agent returned error: {result_text}")

    return result_text if isinstance(result_text, str) else ""


def _short_path(path: str) -> str:
    """Shorten a path to just the last 2 components for logging."""
    parts = path.rsplit("/", 2)
    return "/".join(parts[-2:]) if len(parts) > 2 else path
