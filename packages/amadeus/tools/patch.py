from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from amadeus.tools.base import ToolSpec
from amadeus.tools.local_file_search import REPO_ROOT, SEARCHABLE_EXTENSIONS, SKIPPED_SEARCH_DIRS, is_inside


MAX_PATCH_FILE_BYTES = 512 * 1024
MAX_PATCH_TEXT_BYTES = 512 * 1024
MAX_DIFF_CHARS = 6000


def _text_arg(args: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = args.get(name)
        if isinstance(value, str):
            return value
    return None


def _bool_arg(args: dict[str, Any], *names: str) -> bool:
    for name in names:
        value = args.get(name)
        if isinstance(value, bool):
            return value
    return False


def _is_restricted_path(path: Path) -> bool:
    relative_parts = path.relative_to(REPO_ROOT).parts
    return any(part in SKIPPED_SEARCH_DIRS for part in relative_parts)


def _normalize_line_endings_for_match(content: str, old_text: str, new_text: str) -> tuple[str, str]:
    if old_text in content:
        return old_text, new_text

    if "\r\n" in content and "\r\n" not in old_text:
        old_crlf = old_text.replace("\n", "\r\n")
        new_crlf = new_text.replace("\n", "\r\n")
        if old_crlf in content:
            return old_crlf, new_crlf

    return old_text, new_text


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
    if len(diff) <= MAX_DIFF_CHARS:
        return diff, False
    return diff[:MAX_DIFF_CHARS], True


def patch(args: dict[str, Any]) -> dict[str, Any]:
    requested_path = args.get("path")
    path_text = requested_path.strip() if isinstance(requested_path, str) else ""
    if not path_text:
        return {"error": "path is required"}

    old_text = _text_arg(args, "oldText", "old_string")
    new_text = _text_arg(args, "newText", "new_string")
    if old_text is None or new_text is None:
        return {"error": "oldText and newText are required"}
    if not old_text:
        return {"error": "oldText cannot be empty"}
    if old_text == new_text:
        return {"error": "oldText and newText are identical"}

    target_path = (REPO_ROOT / path_text).resolve()
    if not is_inside(target_path, REPO_ROOT):
        return {"error": "path must be inside the project workspace"}
    if _is_restricted_path(target_path):
        return {"error": "path is restricted and cannot be patched"}
    if not target_path.exists() or not target_path.is_file():
        return {"error": "path must point to an existing file"}
    if target_path.suffix.casefold() not in SEARCHABLE_EXTENSIONS:
        return {"error": "file type is not patchable by this tool"}

    try:
        size_bytes = target_path.stat().st_size
    except OSError:
        return {"error": "could not inspect file"}

    if size_bytes > MAX_PATCH_FILE_BYTES:
        return {"error": "file is too large to patch safely"}

    try:
        content = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"error": "file is not readable as utf-8 text"}

    matched_old_text, replacement_text = _normalize_line_endings_for_match(content, old_text, new_text)
    match_count = content.count(matched_old_text)
    replace_all = _bool_arg(args, "replaceAll", "replace_all")

    if match_count == 0:
        return {"error": "oldText was not found; use read_file to verify the current file contents"}
    if match_count > 1 and not replace_all:
        return {
            "error": "oldText matched multiple times; include more surrounding context or set replaceAll=true",
            "matchCount": match_count,
        }

    new_content = content.replace(matched_old_text, replacement_text) if replace_all else content.replace(matched_old_text, replacement_text, 1)
    new_size_bytes = len(new_content.encode("utf-8"))
    if new_size_bytes > MAX_PATCH_TEXT_BYTES:
        return {"error": "patched file would be too large"}

    relative_path = target_path.relative_to(REPO_ROOT).as_posix()
    diff, diff_truncated = _diff_preview(relative_path, content, new_content)

    try:
        target_path.write_text(new_content, encoding="utf-8")
    except OSError:
        return {"error": "could not write patched file"}

    return {
        "path": relative_path,
        "changed": True,
        "replacements": match_count if replace_all else 1,
        "replaceAll": replace_all,
        "sizeBytesBefore": size_bytes,
        "sizeBytesAfter": new_size_bytes,
        "diff": diff,
        "diffTruncated": diff_truncated,
    }


PATCH_TOOL_SPEC = ToolSpec(
    name="patch",
    display_name="Patching local file",
    permission="ask",
    enabled=True,
    handler=patch,
    schema={
        "type": "function",
        "function": {
            "name": "patch",
            "description": "Apply a safe single-file text replacement inside the project workspace. Use this for local edits after read_file. oldText must uniquely identify the target text unless replaceAll=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file path to patch.",
                    },
                    "oldText": {
                        "type": "string",
                        "description": "Exact text to replace. Include enough surrounding context to make it unique.",
                    },
                    "newText": {
                        "type": "string",
                        "description": "Replacement text.",
                    },
                    "replaceAll": {
                        "type": "boolean",
                        "description": "Replace every occurrence. Defaults to false; by default oldText must be unique.",
                    },
                },
                "required": ["path", "oldText", "newText"],
                "additionalProperties": False,
            },
        },
    },
)
