from __future__ import annotations

import random
from dataclasses import dataclass
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

ToolPermission = str


@dataclass
class ToolSpec:
    name: str
    display_name: str
    permission: ToolPermission
    enabled: bool
    schema: dict[str, Any]
    handler: ToolHandler

    def describe_request(self, args: dict[str, Any]) -> str:
        if self.name == "roll_dice":
            sides = normalize_positive_int(args.get("sides"), 6, 2, 1000)
            count = normalize_positive_int(args.get("count"), 1, 1, 20)
            return f"Allow Amadeus to roll {count} d{sides}?"

        if self.name == "local_file_search":
            query = args.get("query").strip() if isinstance(args.get("query"), str) else "(empty query)"
            root = args.get("root").strip() if isinstance(args.get("root"), str) and args.get("root").strip() else "."
            return f'Allow Amadeus to search local project files under {root} for "{query}"?'

        return f"Allow Amadeus to run {self.display_name}?"


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


DEFAULT_TOOL_SPECS: dict[str, ToolSpec] = {
    "get_current_time": ToolSpec(
        name="get_current_time",
        display_name="Reading current time",
        permission="allow",
        enabled=True,
        handler=get_current_time,
        schema={
            "type": "function",
            "function": {
                "name": "get_current_time",
                "description": "Get the current local date and time. Use this when the user asks about current time, date, today, now, or scheduling context.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "timeZone": {
                            "type": "string",
                            "description": "IANA timezone. Defaults to Asia/Shanghai.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
    ),
    "roll_dice": ToolSpec(
        name="roll_dice",
        display_name="Rolling dice",
        permission="ask",
        enabled=True,
        handler=roll_dice,
        schema={
            "type": "function",
            "function": {
                "name": "roll_dice",
                "description": "Roll dice and return the random results. Use this when the user asks to roll dice.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sides": {
                            "type": "number",
                            "description": "Number of sides per die. Defaults to 6.",
                        },
                        "count": {
                            "type": "number",
                            "description": "Number of dice to roll. Defaults to 1 and is capped at 20.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        },
    ),
    "local_file_search": ToolSpec(
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
    ),
}

TOOLS: dict[str, ToolHandler] = {name: spec.handler for name, spec in DEFAULT_TOOL_SPECS.items()}


def list_tools() -> list[str]:
    return sorted(TOOLS)


def get_tool_spec(tool_name: str) -> ToolSpec | None:
    return DEFAULT_TOOL_SPECS.get(tool_name)


def list_tool_specs() -> list[ToolSpec]:
    return [DEFAULT_TOOL_SPECS[name] for name in sorted(DEFAULT_TOOL_SPECS)]


def execute_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = TOOLS.get(tool_name)
    if not handler:
        raise KeyError(f"Unknown tool: {tool_name}")

    return handler(args)
