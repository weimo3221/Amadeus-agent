from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int
from amadeus.tools.search_files import is_inside, workspace_root_from_context
from amadeus.tools.workspace_sandbox import (
    validate_command_workspace_references,
    workspace_sandbox_enabled,
    workspace_sandbox_environment,
)


DEFAULT_COMMAND_TIMEOUT_SECONDS = 30
MAX_COMMAND_TIMEOUT_SECONDS = 120
DEFAULT_OUTPUT_CHARS = 12000
MAX_OUTPUT_CHARS = 20000


def _resolve_cwd(args: dict[str, Any], context: Any = None) -> tuple[Path | None, str | None]:
    workspace_root = workspace_root_from_context(context)
    raw_cwd = args.get("cwd") or args.get("workdir")
    cwd_text = raw_cwd.strip() if isinstance(raw_cwd, str) and raw_cwd.strip() else "."
    cwd = (workspace_root / cwd_text).resolve()
    if not is_inside(cwd, workspace_root):
        return None, "cwd must be inside the project workspace"
    if not cwd.exists() or not cwd.is_dir():
        return None, "cwd must point to an existing directory"
    return cwd, None


def _trim_text(value: str, max_chars: int) -> tuple[str, bool]:
    if len(value) <= max_chars:
        return value, False
    if max_chars <= 32:
        return value[:max_chars], True
    head_chars = max_chars // 2
    tail_chars = max_chars - head_chars - 32
    return value[:head_chars] + "\n...[truncated]...\n" + value[-tail_chars:], True


def _effective_timeout(args: dict[str, Any], context: Any = None) -> int:
    requested = args.get("timeoutSeconds")
    if requested is None:
        requested = args.get("timeout")
    fallback = DEFAULT_COMMAND_TIMEOUT_SECONDS
    context_timeout = getattr(context, "timeout_seconds", None)
    if isinstance(context_timeout, int | float) and context_timeout > 0:
        fallback = max(1, min(MAX_COMMAND_TIMEOUT_SECONDS, int(context_timeout)))
    return normalize_positive_int(requested, fallback, 1, MAX_COMMAND_TIMEOUT_SECONDS)


def terminal(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    command = args.get("command").strip() if isinstance(args.get("command"), str) else ""
    if not command:
        return {"error": "command is required"}

    cwd, error = _resolve_cwd(args, context)
    if error:
        return {"error": error}
    assert cwd is not None
    workspace_root = workspace_root_from_context(context)

    env = None
    if workspace_sandbox_enabled(context):
        sandbox_error = validate_command_workspace_references(command, workspace_root)
        if sandbox_error:
            return {"error": sandbox_error, "command": command, "cwd": cwd.as_posix()}
        env = workspace_sandbox_environment(workspace_root)

    timeout_seconds = _effective_timeout(args, context)
    max_output_chars = normalize_positive_int(args.get("maxOutputChars"), DEFAULT_OUTPUT_CHARS, 100, MAX_OUTPUT_CHARS)
    per_stream_chars = max(50, max_output_chars // 2)

    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired as timeout:
        stdout, stdout_truncated = _trim_text(timeout.stdout or "", per_stream_chars)
        stderr, stderr_truncated = _trim_text(timeout.stderr or "", per_stream_chars)
        return {
            "error": "command timed out",
            "command": command,
            "cwd": cwd.as_posix(),
            "timeoutSeconds": timeout_seconds,
            "stdout": stdout,
            "stderr": stderr,
            "stdoutTruncated": stdout_truncated,
            "stderrTruncated": stderr_truncated,
        }
    except OSError as error:
        return {"error": f"failed to execute command: {error}"}

    stdout, stdout_truncated = _trim_text(completed.stdout or "", per_stream_chars)
    stderr, stderr_truncated = _trim_text(completed.stderr or "", per_stream_chars)
    return {
        "command": command,
        "cwd": cwd.as_posix(),
        "exitCode": completed.returncode,
        "ok": completed.returncode == 0,
        "timeoutSeconds": timeout_seconds,
        "stdout": stdout,
        "stderr": stderr,
        "stdoutTruncated": stdout_truncated,
        "stderrTruncated": stderr_truncated,
    }


def _parse_signal(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        normalized = value.strip().upper()
        if normalized.isdigit():
            return int(normalized)
        if not normalized.startswith("SIG"):
            normalized = f"SIG{normalized}"
        signal_value = getattr(signal, normalized, None)
        if isinstance(signal_value, signal.Signals):
            return int(signal_value)
        if isinstance(signal_value, int):
            return signal_value
    return int(signal.SIGTERM)


def _process_list(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    query = args.get("query").strip().casefold() if isinstance(args.get("query"), str) else ""
    limit = normalize_positive_int(args.get("limit"), 20, 1, 100)
    timeout_seconds = min(10, _effective_timeout(args, context))
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,stat=,etime=,comm=,args="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error": f"failed to list processes: {error}"}
    if completed.returncode != 0:
        return {"error": completed.stderr.strip() or "ps failed"}

    processes: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        parts = line.strip().split(None, 5)
        if len(parts) < 5:
            continue
        pid_text, ppid_text, status, elapsed, command = parts[:5]
        args_text = parts[5] if len(parts) > 5 else command
        combined = f"{pid_text} {ppid_text} {status} {elapsed} {command} {args_text}".casefold()
        if query and query not in combined:
            continue
        try:
            pid = int(pid_text)
            ppid = int(ppid_text)
        except ValueError:
            continue
        processes.append({
            "pid": pid,
            "ppid": ppid,
            "status": status,
            "elapsed": elapsed,
            "command": command,
            "args": args_text,
        })
        if len(processes) >= limit:
            break

    return {
        "action": "list",
        "query": query,
        "limit": limit,
        "resultCount": len(processes),
        "processes": processes,
    }


def _process_status(args: dict[str, Any]) -> dict[str, Any]:
    pid = normalize_positive_int(args.get("pid"), 0, 1, 2_147_483_647)
    if pid <= 0:
        return {"error": "pid is required"}
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {"action": "status", "pid": pid, "exists": False}
    except PermissionError:
        return {"action": "status", "pid": pid, "exists": True, "accessible": False}
    return {"action": "status", "pid": pid, "exists": True, "accessible": True}


def _process_kill(args: dict[str, Any]) -> dict[str, Any]:
    pid = normalize_positive_int(args.get("pid"), 0, 1, 2_147_483_647)
    if pid <= 0:
        return {"error": "pid is required"}
    if pid == os.getpid():
        return {"error": "refusing to signal the Amadeus runtime process"}
    signal_number = _parse_signal(args.get("signal"))
    try:
        os.kill(pid, signal_number)
    except ProcessLookupError:
        return {"error": f"process {pid} does not exist"}
    except PermissionError:
        return {"error": f"permission denied signaling process {pid}"}
    except OSError as error:
        return {"error": str(error)}
    return {"action": "kill", "pid": pid, "signal": signal_number, "sent": True}


def process(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    action = args.get("action").strip().lower() if isinstance(args.get("action"), str) else "list"
    if action == "list":
        return _process_list(args, context)
    if action == "status":
        return _process_status(args)
    if action in {"kill", "signal"}:
        return _process_kill(args)
    return {"error": "action must be one of: list, status, kill"}


TERMINAL_TOOL_SPEC = ToolSpec(
    name="terminal",
    display_name="Running terminal command",
    permission="ask",
    enabled=True,
    handler=terminal,
    prompt_hint="Use for shell commands that inspect or operate on the workspace when file tools are insufficient. Commands run inside the workspace and require permission.",
    schema={
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "Run a bounded foreground shell command inside the project workspace. Non-zero exit codes are returned as command results; timeouts and execution failures return errors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run."},
                    "cwd": {"type": "string", "description": "Workspace-relative working directory. Defaults to the workspace root."},
                    "timeoutSeconds": {"type": "number", "description": "Command timeout in seconds. Defaults to the tool context timeout and is capped at 120."},
                    "maxOutputChars": {"type": "number", "description": "Maximum combined output characters to return. Defaults to 12000 and is capped at 20000."},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
)


PROCESS_TOOL_SPEC = ToolSpec(
    name="process",
    display_name="Inspecting or signaling processes",
    permission="ask",
    enabled=True,
    handler=process,
    prompt_hint="Use to inspect local processes or signal a known process id when the user asks for process management.",
    schema={
        "type": "function",
        "function": {
            "name": "process",
            "description": "List local processes, check whether a process exists, or send a signal to a process id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "status", "kill"], "description": "Process action. Defaults to list."},
                    "pid": {"type": "number", "description": "Process id for status or kill."},
                    "signal": {"type": "string", "description": "Signal for kill, such as TERM, KILL, INT, or a signal number. Defaults to TERM."},
                    "query": {"type": "string", "description": "Optional case-insensitive filter for process list output."},
                    "limit": {"type": "number", "description": "Maximum list results. Defaults to 20 and is capped at 100."},
                },
                "additionalProperties": False,
            },
        },
    },
)
