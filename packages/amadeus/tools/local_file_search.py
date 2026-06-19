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


def is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def local_file_search(args: dict[str, Any]) -> dict[str, Any]:
    requested_query = args.get("query")
    query = requested_query.strip() if isinstance(requested_query, str) else ""
    if not query:
        return {"error": "query is required"}

    requested_root = args.get("root")
    root_text = requested_root.strip() if isinstance(requested_root, str) and requested_root.strip() else "."
    search_root = (REPO_ROOT / root_text).resolve()
    if not is_inside(search_root, REPO_ROOT) or not search_root.exists():
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
        relative_path = current.relative_to(REPO_ROOT).as_posix()
        if normalized_query in relative_path.casefold():
            results.append({"path": relative_path, "preview": relative_path, "match": "path"})
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
        "root": search_root.relative_to(REPO_ROOT).as_posix() or ".",
        "maxResults": max_results,
        "results": results,
        "scannedFiles": scanned_files,
    }


LOCAL_FILE_SEARCH_TOOL_SPEC = ToolSpec(
    name="local_file_search",
    display_name="Searching local files",
    permission="ask",
    enabled=True,
    handler=local_file_search,
    schema={
        "type": "function",
        "function": {
            "name": "local_file_search",
            "description": "Search filenames and small text files inside the project workspace. Use this when the user asks to find local project files, docs, code, configuration, or notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search text to match in paths or file contents.",
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
