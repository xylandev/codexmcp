"""FastMCP server implementation for the Codex MCP project."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Dict, Generator, List, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field
import shutil

mcp = FastMCP("Codex MCP Server-from guda.studio")

# Transient reconnect notices (ASCII "..." or Unicode "…") should not flip success alone.
_RECONNECTING = re.compile(r"^Reconnecting\.(?:\.\.\.|…)\s+\d+/\d+")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _max_concurrent_sessions() -> int:
    return max(1, _env_int("CODEXMCP_MAX_CONCURRENT", 100))


def _output_queue_maxsize() -> int:
    return max(128, _env_int("CODEXMCP_OUTPUT_QUEUE_MAX", 100_000))


def _exec_timeout_sec() -> float:
    raw = os.environ.get("CODEXMCP_EXEC_TIMEOUT_SEC", "0")
    try:
        v = float(raw)
    except ValueError:
        return 0.0
    return max(0.0, v)


_codex_session_sem: Optional[asyncio.Semaphore] = None


def _codex_semaphore() -> asyncio.Semaphore:
    global _codex_session_sem
    if _codex_session_sem is None:
        _codex_session_sem = asyncio.Semaphore(_max_concurrent_sessions())
    return _codex_session_sem


def _terminate_process_tree(process: Optional[subprocess.Popen[Any]]) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name != "nt" and process.pid:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                process.terminate()
            except ProcessLookupError:
                return
    else:
        try:
            process.terminate()
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt" and process.pid:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
        else:
            process.kill()
        process.wait()


async def _async_terminate_process_tree(process: Optional[subprocess.Popen[Any]]) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _terminate_process_tree, process)


def run_shell_command(
    cmd: list[str],
    proc_holder: Optional[Dict[str, Any]] = None,
) -> Generator[str, None, None]:
    """Execute a command and stream its output line-by-line.

    Args:
        cmd: Command and arguments as a list (e.g., ["codex", "exec", "prompt"])

    Yields:
        Output lines from the command
    """
    # On Windows, codex is exposed via a *.cmd shim. Use cmd.exe with /s so
    # user prompts containing quotes/newlines aren't reinterpreted as shell syntax.
    popen_cmd = cmd.copy()
    codex_path = shutil.which('codex') or cmd[0]
    popen_cmd[0] = codex_path

    popen_kw: Dict[str, Any] = dict(
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        encoding="utf-8",
    )
    if os.name != "nt":
        popen_kw["start_new_session"] = True

    process = subprocess.Popen(popen_cmd, **popen_kw)
    if proc_holder is not None:
        proc_holder["process"] = process

    output_queue: queue.Queue[str | None] = queue.Queue(maxsize=_output_queue_maxsize())
    GRACEFUL_SHUTDOWN_DELAY = 0.3

    def is_turn_completed(line: str) -> bool:
        """Check if the line indicates turn completion via JSON parsing."""
        try:
            data = json.loads(line)
            return data.get("type") == "turn.completed"
        except (json.JSONDecodeError, AttributeError, TypeError):
            return False

    def read_output() -> None:
        """Read process output in a separate thread."""
        if process.stdout:
            for line in iter(process.stdout.readline, ""):
                stripped = line.strip()
                output_queue.put(stripped)
                if is_turn_completed(stripped):
                    time.sleep(GRACEFUL_SHUTDOWN_DELAY)
                    process.terminate()
                    break
            process.stdout.close()
        output_queue.put(None)

    thread = threading.Thread(target=read_output)
    thread.start()

    # Yield lines while process is running
    while True:
        try:
            line = output_queue.get(timeout=0.5)
            if line is None:
                break
            yield line
        except queue.Empty:
            if process.poll() is not None and not thread.is_alive():
                break

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    thread.join(timeout=5)

    while not output_queue.empty():
        try:
            line = output_queue.get_nowait()
            if line is not None:
                yield line
        except queue.Empty:
            break


def _execute_codex_sync(
    cmd: list[str],
    return_all_messages: bool,
    proc_holder: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run `codex exec` and aggregate JSON lines; must run in a worker thread (blocks for a long time)."""
    all_messages: list[Dict[str, Any]] = []
    agent_parts: list[str] = []
    success = True
    err_message = ""
    thread_id: Optional[str] = None

    try:
        for line in run_shell_command(cmd, proc_holder):
            try:
                line_dict = json.loads(line.strip())
                if return_all_messages:
                    all_messages.append(line_dict)
                item = line_dict.get("item", {})
                item_type = item.get("type", "")
                if item_type == "agent_message":
                    agent_parts.append(item.get("text", ""))
                if line_dict.get("thread_id") is not None:
                    thread_id = line_dict.get("thread_id")
                if "fail" in line_dict.get("type", ""):
                    success = False
                    err_message += "\n\n[codex error] " + line_dict.get("error", {}).get("message", "")
                if "error" in line_dict.get("type", ""):
                    error_msg = line_dict.get("message", "")
                    if not _RECONNECTING.match(error_msg):
                        success = False
                        err_message += "\n\n[codex error] " + error_msg

            except json.JSONDecodeError:
                err_message += "\n\n[json decode error] " + line
                continue

            except Exception as error:
                err_message += "\n\n[unexpected error] " + f"Unexpected error: {error}. Line: {line!r}"
                success = False
                break
    finally:
        if proc_holder is not None:
            proc_holder.pop("process", None)

    agent_messages = "".join(agent_parts)

    if thread_id is None:
        success = False
        err_message = "Failed to get `SESSION_ID` from the codex session. \n\n" + err_message

    if len(agent_messages) == 0:
        success = False
        err_message = (
            "Failed to get `agent_messages` from the codex session. \n\n You can try to set "
            "`return_all_messages` to `True` to get the full reasoning information. "
        ) + err_message

    if success:
        result: Dict[str, Any] = {
            "success": True,
            "SESSION_ID": thread_id,
            "agent_messages": agent_messages,
        }
    else:
        result = {"success": False, "error": err_message}

    if return_all_messages:
        result["all_messages"] = all_messages

    return result


def windows_escape(prompt):
    """
    Windows 风格的字符串转义函数。
    把常见特殊字符转义成 \\ 形式，适合命令行、JSON 或路径使用。
    比如：\n 变成 \\n，" 变成 \\"。
    """
    # 先处理反斜杠，避免它干扰其他替换
    result = prompt.replace('\\', '\\\\')
    # 双引号，转义成 \"，防止字符串边界乱套
    result = result.replace('"', '\\"')
    # 换行符，Windows 常用 \r\n，但我们分开转义
    result = result.replace('\n', '\\n')
    result = result.replace('\r', '\\r')
    # 制表符，空格的“超级版”
    result = result.replace('\t', '\\t')
    # 其他常见：退格符（像按了后退键）、换页符（打印机跳页用）
    result = result.replace('\b', '\\b')
    result = result.replace('\f', '\\f')
    # 如果有单引号，也转义下（不过 Windows 命令行不那么严格，但保险起见）
    result = result.replace("'", "\\'")
    
    return result

@mcp.tool(
    name="codex",
    description="""
    Executes a non-interactive Codex session via CLI for AI-assisted coding tasks in a workspace.
    This tool wraps `codex exec` with a **fixed read-only sandbox** (not configurable): Codex cannot write to the workspace through this integration; use your host agent to apply patches or edits.

    **Key Features:**
        - **Prompt-Driven Execution:** Natural-language instructions for analysis, plans, unified diff proposals, and code review.
        - **Workspace Isolation:** Runs with `--cd` set to your chosen directory; optional Git-repo check skipping.
        - **Session Persistence:** Resume via `SESSION_ID` for multi-turn tasks.

    **Edge Cases & Best Practices:**
        - Ensure `cd` exists and is accessible; invalid paths may yield failures or empty session output.
        - Set `return_all_messages` to `True` when you need full JSON traces (reasoning, tool calls, etc.).
    """,
    meta={"version": "0.0.0", "author": "guda.studio"},
)
async def codex(
    PROMPT: Annotated[str, "Instruction for the task to send to codex."],
    cd: Annotated[Path, "Set the workspace root for codex before executing the task."],
    SESSION_ID: Annotated[
        str,
        "Resume the specified session of the codex. Defaults to `None`, start a new session.",
    ] = "",
    skip_git_repo_check: Annotated[
        bool,
        "Allow codex running outside a Git repository (useful for one-off directories).",
    ] = True,
    return_all_messages: Annotated[
        bool,
        "Return all messages (e.g. reasoning, tool calls, etc.) from the codex session. Set to `False` by default, only the agent's final reply message is returned.",
    ] = False,
    image: Annotated[
        List[Path],
        Field(
            description="Attach one or more image files to the initial prompt. Separate multiple paths with commas or repeat the flag.",
        ),
    ] = [],
    model: Annotated[
        str,
        Field(
            description="The model to use for the codex session. This parameter is strictly prohibited unless explicitly specified by the user.",
        ),
    ] = "",
    profile: Annotated[
        str,
        "Configuration profile name to load from `~/.codex/config.toml`. This parameter is strictly prohibited unless explicitly specified by the user.",
    ] = "",
) -> Dict[str, Any]:
    """Execute a Codex CLI session and return the results."""
    # Build command as list to avoid injection. Sandbox is hardcoded read-only at the MCP layer.
    cmd = ["codex", "exec", "--sandbox", "read-only", "--cd", str(cd), "--json"]

    if len(image):
        cmd.extend(["--image", ",".join(map(str, image))])

    if model:
        cmd.extend(["--model", model])

    if profile:
        cmd.extend(["--profile", profile])

    if skip_git_repo_check:
        cmd.append("--skip-git-repo-check")

    if SESSION_ID:
        cmd.extend(["resume", str(SESSION_ID)])
        
    if os.name == "nt":
        prompt_arg = windows_escape(PROMPT)
    else:
        prompt_arg = PROMPT
    cmd += ["--", prompt_arg]

    proc_holder: Dict[str, Any] = {}
    timeout_sec = _exec_timeout_sec()
    sem = _codex_semaphore()

    async with sem:
        try:
            if timeout_sec > 0:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        _execute_codex_sync, cmd, return_all_messages, proc_holder
                    ),
                    timeout=timeout_sec,
                )
            return await asyncio.to_thread(
                _execute_codex_sync, cmd, return_all_messages, proc_holder
            )
        except asyncio.TimeoutError:
            proc = proc_holder.get("process")
            await _async_terminate_process_tree(proc)
            return {
                "success": False,
                "error": (
                    f"codex exec exceeded CODEXMCP_EXEC_TIMEOUT_SEC ({timeout_sec}s). "
                    "Subprocess was terminated."
                ),
            }
        except asyncio.CancelledError:
            proc = proc_holder.get("process")
            await _async_terminate_process_tree(proc)
            raise


def run() -> None:
    """Start the MCP server over stdio transport."""
    mcp.run(transport="stdio")
