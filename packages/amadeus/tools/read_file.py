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
READABLE_TEXT_EXTENSIONS = SEARCHABLE_EXTENSIONS | {
    ".csv",
    ".env",
    ".ini",
    ".java",
    ".jsx",
    ".log",
    ".rs",
    ".sh",
    ".toml",
    ".xml",
}
IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
PDF_EXTENSIONS = {".pdf"}
BINARY_EXTENSIONS = {
    ".7z",
    ".avif",
    ".db",
    ".doc",
    ".docx",
    ".dylib",
    ".exe",
    ".gz",
    ".ico",
    ".mov",
    ".mp3",
    ".mp4",
    ".o",
    ".parquet",
    ".ppt",
    ".pptx",
    ".sqlite",
    ".tar",
    ".wav",
    ".xls",
    ".xlsx",
    ".zip",
}


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


def _file_kind(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix in READABLE_TEXT_EXTENSIONS:
        return "text"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in PDF_EXTENSIONS:
        return "pdf"
    if suffix in BINARY_EXTENSIONS:
        return "binary"
    return "unknown"


def _unsupported_file_response(path: Path, size_bytes: int, kind: str) -> dict[str, Any]:
    relative_path = path.relative_to(REPO_ROOT).as_posix()
    if kind == "image":
        hint = "This looks like an image. A future vision/read_image tool should inspect it; read_file only reads UTF-8 text."
    elif kind == "pdf":
        hint = "This looks like a PDF. A future pdf_read tool should parse it; read_file only reads UTF-8 text."
    elif kind == "binary":
        hint = "This looks like a binary file and cannot be displayed safely as text."
    else:
        hint = "This file type is not recognized as readable UTF-8 text."

    return {
        "path": relative_path,
        "sizeBytes": size_bytes,
        "kind": kind,
        "supported": False,
        "error": "file type is not readable by read_file",
        "hint": hint,
    }


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

    try:
        size_bytes = target_path.stat().st_size
    except OSError:
        return {"error": "could not inspect file"}

    kind = _file_kind(target_path)
    if kind != "text":
        return _unsupported_file_response(target_path, size_bytes, kind)

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
        "kind": "text",
        "supported": True,
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
            "description": "Read a bounded, line-numbered UTF-8 text window inside the project workspace. For images, PDFs, binaries, and unknown file types, returns a structured unsupported response with kind and hint instead of trying to decode the file.",
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
