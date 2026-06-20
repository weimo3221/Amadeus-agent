from __future__ import annotations

from pathlib import Path
from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int
from amadeus.tools.local_file_search import REPO_ROOT, SEARCHABLE_EXTENSIONS, is_inside


MAX_READ_FILE_BYTES = 512 * 1024
DEFAULT_READ_FILE_CHARS = 12000
MAX_READ_FILE_CHARS = 20000
DEFAULT_READ_FILE_LINE_LIMIT = 200
MAX_READ_FILE_LINE_LIMIT = 1000


def _normalize_start_line(args: dict[str, Any]) -> int:
    if args.get("startLine") is not None:
        return normalize_positive_int(args.get("startLine"), 1, 1, 1_000_000)

    if args.get("offset") is not None:
        offset = normalize_positive_int(args.get("offset"), 0, 0, 1_000_000)
        return offset + 1

    return 1


def _normalize_line_limit(args: dict[str, Any]) -> int:
    if args.get("lineLimit") is not None:
        return normalize_positive_int(args.get("lineLimit"), DEFAULT_READ_FILE_LINE_LIMIT, 1, MAX_READ_FILE_LINE_LIMIT)

    if args.get("limit") is not None:
        return normalize_positive_int(args.get("limit"), DEFAULT_READ_FILE_LINE_LIMIT, 1, MAX_READ_FILE_LINE_LIMIT)

    return DEFAULT_READ_FILE_LINE_LIMIT


def read_file(args: dict[str, Any]) -> dict[str, Any]:
    requested_path = args.get("path")
    path_text = requested_path.strip() if isinstance(requested_path, str) else ""
    if not path_text:
        return {"error": "path is required"}

    target_path = (REPO_ROOT / path_text).resolve()
    if not is_inside(target_path, REPO_ROOT):
        return {"error": "path must be inside the project workspace"}
    if not target_path.exists() or not target_path.is_file():
        return {"error": "path must point to an existing file"}
    if target_path.suffix.casefold() not in SEARCHABLE_EXTENSIONS:
        return {"error": "file type is not readable by this tool"}

    try:
        size_bytes = target_path.stat().st_size
    except OSError:
        return {"error": "could not inspect file"}

    if size_bytes > MAX_READ_FILE_BYTES:
        return {"error": "file is too large to read safely"}

    start_line = _normalize_start_line(args)
    line_limit = _normalize_line_limit(args)
    max_chars = normalize_positive_int(args.get("maxChars"), DEFAULT_READ_FILE_CHARS, 1, MAX_READ_FILE_CHARS)

    try:
        content = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"error": "file is not readable as utf-8 text"}

    lines = content.splitlines()
    total_lines = len(lines)
    start_index = min(start_line - 1, total_lines)
    end_index = min(start_index + line_limit, total_lines)
    selected_lines = lines[start_index:end_index]

    rendered_lines: list[str] = []
    truncated_by_chars = False
    for line_number, line in enumerate(selected_lines, start=start_index + 1):
        rendered_line = f"{line_number:>6} | {line}"
        projected = "\n".join([*rendered_lines, rendered_line]) if rendered_lines else rendered_line
        if len(projected) > max_chars:
            remaining = max_chars - (len("\n".join(rendered_lines)) + (1 if rendered_lines else 0))
            if remaining > 0:
                rendered_lines.append(rendered_line[:remaining])
            truncated_by_chars = True
            break
        rendered_lines.append(rendered_line)

    returned_content = "\n".join(rendered_lines)
    returned_line_count = len(rendered_lines)
    returned_end_line = start_index + returned_line_count
    has_more = end_index < total_lines or truncated_by_chars

    return {
        "path": target_path.relative_to(REPO_ROOT).as_posix(),
        "sizeBytes": size_bytes,
        "charCount": len(content),
        "totalLines": total_lines,
        "startLine": start_index + 1 if total_lines else 1,
        "endLine": returned_end_line,
        "lineCount": returned_line_count,
        "lineLimit": line_limit,
        "maxChars": max_chars,
        "hasMore": has_more,
        "truncated": truncated_by_chars,
        "content": returned_content,
    }


READ_FILE_TOOL_SPEC = ToolSpec(
    name="read_file",
    display_name="Reading local file",
    permission="ask",
    enabled=True,
    handler=read_file,
    schema={
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a small UTF-8 text file inside the project workspace. Use this after search_files when the user needs the contents of a specific project file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file path to read.",
                    },
                    "maxChars": {
                        "type": "number",
                        "description": "Maximum rendered characters to return from this explicit read window. Defaults to 12000 and is capped at 20000.",
                    },
                    "startLine": {
                        "type": "number",
                        "description": "1-based first line to read. Defaults to 1.",
                    },
                    "lineLimit": {
                        "type": "number",
                        "description": "Maximum lines to return. Defaults to 200 and is capped at 1000.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
)
