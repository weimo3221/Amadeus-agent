from __future__ import annotations

from pathlib import Path
from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int


REPO_ROOT = Path(__file__).resolve().parents[3]
SKIPPED_SEARCH_DIRS = {".git", "node_modules", "dist", "out", "build", ".vite", "__pycache__"}
SEARCHABLE_EXTENSIONS = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
SEARCH_TARGETS = {"all", "files", "content"}


def is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def workspace_root_from_context(context: Any = None) -> Path:
    cwd = getattr(context, "cwd", None)
    if isinstance(cwd, Path):
        return cwd.resolve()
    return REPO_ROOT


def search_files(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    workspace_root = workspace_root_from_context(context)
    requested_query = args.get("query")
    query = requested_query.strip() if isinstance(requested_query, str) else ""
    if not query:
        return {"error": "query is required"}

    requested_target = args.get("target")
    target = requested_target.strip() if isinstance(requested_target, str) else "all"
    if target not in SEARCH_TARGETS:
        return {"error": "target must be one of: all, files, content"}

    requested_root = args.get("root")
    root_text = requested_root.strip() if isinstance(requested_root, str) and requested_root.strip() else "."
    search_root = (workspace_root / root_text).resolve()
    if not is_inside(search_root, workspace_root) or not search_root.exists():
        return {"error": "root must be inside the project workspace"}

    max_results = normalize_positive_int(args.get("maxResults"), 10, 1, 30)
    normalized_query = query.casefold()
    pending = [search_root]
    results: list[dict[str, Any]] = []
    scanned_files = 0

    while pending and len(results) < max_results and scanned_files < 1000:
        current = pending.pop()

        if current.is_dir():
            if current.name in SKIPPED_SEARCH_DIRS:
                continue

            try:
                pending.extend(current.iterdir())
            except OSError:
                continue
            continue

        if not current.is_file():
            continue

        scanned_files += 1
        relative_path = current.relative_to(workspace_root).as_posix()
        if target in {"all", "files"} and normalized_query in relative_path.casefold():
            results.append({"path": relative_path, "preview": relative_path, "match": "path"})
            continue

        if target == "files":
            continue

        try:
            if current.stat().st_size > 256 * 1024 or current.suffix.casefold() not in SEARCHABLE_EXTENSIONS:
                continue
            lines = current.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        for index, line in enumerate(lines, start=1):
            if normalized_query in line.casefold():
                results.append({
                    "path": relative_path,
                    "line": index,
                    "preview": line.strip()[:240],
                    "match": "content",
                })
                break

    return {
        "query": query,
        "target": target,
        "root": search_root.relative_to(REPO_ROOT).as_posix() or ".",
        "maxResults": max_results,
        "results": results,
        "scannedFiles": scanned_files,
    }


SEARCH_FILES_TOOL_SPEC = ToolSpec(
    name="search_files",
    display_name="Searching local files",
    permission="allow",
    enabled=True,
    handler=search_files,
    prompt_hint="Use to find workspace files, docs, code, configuration, or matching local project text before reading specific files.",
    schema={
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search workspace-relative filenames and small text file contents. Use target='files' for path/name search, target='content' for text search, or target='all' when either can satisfy the request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search text to match in paths or file contents.",
                    },
                    "target": {
                        "type": "string",
                        "enum": ["all", "files", "content"],
                        "description": "Search mode. Use 'files' for filenames/paths, 'content' for text contents, and 'all' for both. Defaults to 'all'.",
                    },
                    "root": {
                        "type": "string",
                        "description": "Optional workspace-relative directory to search. Defaults to the project root.",
                    },
                    "maxResults": {
                        "type": "number",
                        "description": "Maximum results to return. Defaults to 10 and is capped at 30.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
)
