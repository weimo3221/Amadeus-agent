from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from amadeus.tools.base import ToolSpec
from amadeus.tools.local_file_search import REPO_ROOT, SKIPPED_SEARCH_DIRS, is_inside
from amadeus.tools.read_file import READABLE_TEXT_EXTENSIONS


MAX_WRITE_FILE_BYTES = 512 * 1024
MAX_WRITE_DIFF_CHARS = 6000


def _bool_arg(args: dict[str, Any], *names: str) -> bool:
    for name in names:
        value = args.get(name)
        if isinstance(value, bool):
            return value
    return False


def _is_restricted_path(path: Path) -> bool:
    relative_parts = path.relative_to(REPO_ROOT).parts
    return any(part in SKIPPED_SEARCH_DIRS for part in relative_parts)


def _diff_preview(path: str, before: str, after: str) -> tuple[str, bool]:
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    if len(diff) <= MAX_WRITE_DIFF_CHARS:
        return diff, False
    return diff[:MAX_WRITE_DIFF_CHARS], True


def write_file(args: dict[str, Any]) -> dict[str, Any]:
    requested_path = args.get("path")
    path_text = requested_path.strip() if isinstance(requested_path, str) else ""
    if not path_text:
        return {"error": "path is required"}

    content = args.get("content")
    if not isinstance(content, str):
        return {"error": "content is required"}

    target_path = (REPO_ROOT / path_text).resolve()
    if not is_inside(target_path, REPO_ROOT):
        return {"error": "path must be inside the project workspace"}
    if _is_restricted_path(target_path):
        return {"error": "path is restricted and cannot be written"}
    if target_path.suffix.casefold() not in READABLE_TEXT_EXTENSIONS:
        return {"error": "file type is not writable by this tool"}

    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_WRITE_FILE_BYTES:
        return {"error": "content is too large to write safely"}

    parent_path = target_path.parent
    if not is_inside(parent_path, REPO_ROOT):
        return {"error": "parent directory must be inside the project workspace"}
    if _is_restricted_path(parent_path):
        return {"error": "parent directory is restricted and cannot be written"}
    if parent_path.exists() and not parent_path.is_dir():
        return {"error": "parent path exists and is not a directory"}

    overwrite = _bool_arg(args, "overwrite")
    existed = target_path.exists()
    if existed and not target_path.is_file():
        return {"error": "path exists and is not a file"}
    if existed and not overwrite:
        return {"error": "path already exists; set overwrite=true to replace it"}

    before = ""
    size_before = 0
    if existed:
        try:
            size_before = target_path.stat().st_size
            before = target_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return {"error": "existing file is not readable as utf-8 text"}

        if size_before > MAX_WRITE_FILE_BYTES:
            return {"error": "existing file is too large to overwrite safely"}

    relative_path = target_path.relative_to(REPO_ROOT).as_posix()
    diff, diff_truncated = _diff_preview(relative_path, before, content)

    try:
        parent_path.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")
    except OSError:
        return {"error": "could not write file"}

    return {
        "path": relative_path,
        "changed": before != content,
        "created": not existed,
        "overwritten": existed,
        "overwrite": overwrite,
        "sizeBytesBefore": size_before if existed else None,
        "sizeBytesAfter": len(content_bytes),
        "lineCount": len(content.splitlines()),
        "diff": diff,
        "diffTruncated": diff_truncated,
    }


WRITE_FILE_TOOL_SPEC = ToolSpec(
    name="write_file",
    display_name="Writing local file",
    permission="ask",
    enabled=True,
    handler=write_file,
    schema={
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or fully overwrite a UTF-8 text file inside the project workspace. Use patch for targeted edits to existing files; use write_file for new files or intentional full replacement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative text file path to create or overwrite.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete UTF-8 text content to write.",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Allow replacing an existing file. Defaults to false.",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
)
