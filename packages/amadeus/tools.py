from __future__ import annotations

import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
REPO_ROOT = Path(__file__).resolve().parents[2]
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


def normalize_positive_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback

    return max(minimum, min(maximum, number))


def get_current_time(args: dict[str, Any]) -> dict[str, Any]:
    requested_timezone = args.get("timeZone")
    time_zone = requested_timezone if isinstance(requested_timezone, str) and requested_timezone else "Asia/Shanghai"

    try:
        zone = ZoneInfo(time_zone)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
        time_zone = "UTC"

    now = datetime.now(zone)
    return {
        "iso": now.astimezone(timezone.utc).isoformat(),
        "timeZone": time_zone,
        "formatted": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


def roll_dice(args: dict[str, Any]) -> dict[str, Any]:
    sides = normalize_positive_int(args.get("sides"), 6, 2, 1000)
    count = normalize_positive_int(args.get("count"), 1, 1, 20)
    rolls = [random.randint(1, sides) for _ in range(count)]

    return {
        "sides": sides,
        "count": count,
        "rolls": rolls,
        "total": sum(rolls),
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


TOOLS: dict[str, ToolHandler] = {
    "get_current_time": get_current_time,
    "roll_dice": roll_dice,
    "local_file_search": local_file_search,
}


def list_tools() -> list[str]:
    return sorted(TOOLS)


def execute_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = TOOLS.get(tool_name)
    if not handler:
        raise KeyError(f"Unknown tool: {tool_name}")

    return handler(args)
