"""Headless Claude Code agent wrapper using subprocess."""

import asyncio
import json
import os
import signal


async def run_agent(
    prompt: str,
    system_prompt: str | None = None,
    max_turns: int = 25,
    timeout: int | None = None,
    allowed_tools: list[str] | None = None,
    cwd: str | None = None,
) -> str:
    """Run a headless Claude Code agent via ``claude -p``.

    Uses the user's Claude Code subscription (no API key required).

    Args:
        prompt: The task prompt to send to the agent.
        system_prompt: Optional system context appended via --append-system-prompt.
        max_turns: Maximum agentic turns (safety bound).
        timeout: Wall-clock timeout in seconds. None means no timeout.
        allowed_tools: Tool names the agent may use. Defaults to no extra tools.
        cwd: Working directory for the subprocess. Defaults to current directory.

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
        "json",
        "--max-turns",
        str(max_turns),
    ]

    if allowed_tools:
        for tool in allowed_tools:
            cmd.extend(["--allowedTools", tool])

    if system_prompt:
        cmd.extend(["--append-system-prompt", system_prompt])

    # Spawn in its own process group so we can kill the entire tree on timeout.
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise FileNotFoundError(
            "Claude CLI not found. Install it: https://docs.anthropic.com/en/docs/claude-code"
        ) from None

    try:
        if timeout and timeout > 0:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        else:
            stdout, stderr = await proc.communicate()
    except TimeoutError as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            proc.kill()
        await proc.communicate()
        raise TimeoutError(f"Claude agent timed out after {timeout}s") from exc

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Claude agent exited with code {proc.returncode}: {err}")

    raw = stdout.decode("utf-8", errors="replace").strip()
    try:
        wrapper = json.loads(raw)
    except json.JSONDecodeError:
        # If not a JSON wrapper, return raw output directly.
        return raw

    # `claude -p --output-format json` wraps output as:
    # {"type": "result", "result": "<agent text>", ...}
    if isinstance(wrapper, dict):
        result_val = wrapper.get("result")
        # Guard against null/non-string result field.
        return result_val if isinstance(result_val, str) else raw
    return raw


