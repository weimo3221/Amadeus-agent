from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int
from amadeus.tools.search_files import is_inside, workspace_root_from_context
from amadeus.tools.terminal import _trim_text


DEFAULT_EXECUTE_CODE_TIMEOUT_SECONDS = 30
MAX_EXECUTE_CODE_TIMEOUT_SECONDS = 120
DEFAULT_EXECUTE_CODE_OUTPUT_CHARS = 12000
MAX_EXECUTE_CODE_OUTPUT_CHARS = 20000


def _resolve_workdir(args: dict[str, Any], context: Any = None) -> tuple[Path | None, str | None]:
    workspace_root = workspace_root_from_context(context)
    raw_workdir = args.get("cwd") or args.get("workdir")
    workdir_text = raw_workdir.strip() if isinstance(raw_workdir, str) and raw_workdir.strip() else "."
    workdir = (workspace_root / workdir_text).resolve()
    if not is_inside(workdir, workspace_root):
        return None, "cwd must be inside the project workspace"
    if not workdir.exists() or not workdir.is_dir():
        return None, "cwd must point to an existing directory"
    return workdir, None


def _timeout(args: dict[str, Any], context: Any = None) -> int:
    requested = args.get("timeoutSeconds") or args.get("timeout")
    fallback = DEFAULT_EXECUTE_CODE_TIMEOUT_SECONDS
    context_timeout = getattr(context, "timeout_seconds", None)
    if isinstance(context_timeout, int | float) and context_timeout > 0:
        fallback = max(1, min(MAX_EXECUTE_CODE_TIMEOUT_SECONDS, int(context_timeout)))
    return normalize_positive_int(requested, fallback, 1, MAX_EXECUTE_CODE_TIMEOUT_SECONDS)


def execute_code(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    language = args.get("language").strip().lower() if isinstance(args.get("language"), str) else "python"
    if language not in {"python", "py"}:
        return {"error": "only python code execution is supported"}
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"error": "code is required"}

    workdir, error = _resolve_workdir(args, context)
    if error:
        return {"error": error}
    assert workdir is not None

    timeout_seconds = _timeout(args, context)
    max_output_chars = normalize_positive_int(args.get("maxOutputChars"), DEFAULT_EXECUTE_CODE_OUTPUT_CHARS, 100, MAX_EXECUTE_CODE_OUTPUT_CHARS)
    per_stream_chars = max(50, max_output_chars // 2)
    stdin_text = args.get("stdin") if isinstance(args.get("stdin"), str) else None

    with tempfile.TemporaryDirectory(prefix="amadeus_execute_code_") as tmpdir:
        script_path = Path(tmpdir) / "script.py"
        script_path.write_text(code, encoding="utf-8")
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(workdir) if not existing_pythonpath else f"{workdir}{os.pathsep}{existing_pythonpath}"
        try:
            completed = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=str(workdir),
                input=stdin_text,
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
                "error": "code execution timed out",
                "language": "python",
                "cwd": workdir.as_posix(),
                "timeoutSeconds": timeout_seconds,
                "stdout": stdout,
                "stderr": stderr,
                "stdoutTruncated": stdout_truncated,
                "stderrTruncated": stderr_truncated,
            }
        except OSError as error:
            return {"error": f"failed to execute code: {error}"}

    stdout, stdout_truncated = _trim_text(completed.stdout or "", per_stream_chars)
    stderr, stderr_truncated = _trim_text(completed.stderr or "", per_stream_chars)
    return {
        "language": "python",
        "cwd": workdir.as_posix(),
        "exitCode": completed.returncode,
        "ok": completed.returncode == 0,
        "timeoutSeconds": timeout_seconds,
        "stdout": stdout,
        "stderr": stderr,
        "stdoutTruncated": stdout_truncated,
        "stderrTruncated": stderr_truncated,
    }


EXECUTE_CODE_TOOL_SPEC = ToolSpec(
    name="execute_code",
    display_name="Executing Python code",
    permission="ask",
    enabled=True,
    handler=execute_code,
    prompt_hint="Use for bounded Python scripts that batch local analysis or transformations more safely than many separate tool calls. Requires permission.",
    schema={
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "Run a bounded Python script in a temporary file with cwd inside the project workspace. Captures stdout/stderr and returns the exit code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute."},
                    "language": {"type": "string", "enum": ["python"], "description": "Execution language. Only python is supported."},
                    "cwd": {"type": "string", "description": "Workspace-relative working directory. Defaults to the workspace root."},
                    "stdin": {"type": "string", "description": "Optional standard input for the script."},
                    "timeoutSeconds": {"type": "number", "description": "Execution timeout in seconds. Defaults to the tool context timeout and is capped at 120."},
                    "maxOutputChars": {"type": "number", "description": "Maximum combined output characters. Defaults to 12000 and is capped at 20000."},
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    },
)
