from __future__ import annotations

from pathlib import Path
from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int
from amadeus.tools.local_file_search import REPO_ROOT, SEARCHABLE_EXTENSIONS, is_inside


MAX_READ_FILE_BYTES = 512 * 1024
DEFAULT_READ_FILE_CHARS = 4000
MAX_READ_FILE_CHARS = 20000


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

    max_chars = normalize_positive_int(args.get("maxChars"), DEFAULT_READ_FILE_CHARS, 1, MAX_READ_FILE_CHARS)

    try:
        content = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"error": "file is not readable as utf-8 text"}

    truncated = len(content) > max_chars
    returned_content = content[:max_chars] if truncated else content

    return {
        "path": target_path.relative_to(REPO_ROOT).as_posix(),
        "sizeBytes": size_bytes,
        "charCount": len(content),
        "maxChars": max_chars,
        "truncated": truncated,
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
                        "description": "Maximum characters to return. Defaults to 4000 and is capped at 20000.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
)
