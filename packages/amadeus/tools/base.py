from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
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

        if self.name in {"search_files", "local_file_search"}:
            query = args.get("query").strip() if isinstance(args.get("query"), str) else "(empty query)"
            root = args.get("root").strip() if isinstance(args.get("root"), str) and args.get("root").strip() else "."
            return f'Allow Amadeus to search local project files under {root} for "{query}"?'

        if self.name == "read_file":
            path = args.get("path").strip() if isinstance(args.get("path"), str) and args.get("path").strip() else "(empty path)"
            return f"Allow Amadeus to read local project file {path}?"

        if self.name == "patch":
            path = args.get("path").strip() if isinstance(args.get("path"), str) and args.get("path").strip() else "(empty path)"
            return f"Allow Amadeus to patch local project file {path}?"

        if self.name == "write_file":
            path = args.get("path").strip() if isinstance(args.get("path"), str) and args.get("path").strip() else "(empty path)"
            return f"Allow Amadeus to write local project file {path}?"

        if self.name == "update_memory":
            target = args.get("target").strip() if isinstance(args.get("target"), str) and args.get("target").strip() else "agent"
            action = args.get("action").strip() if isinstance(args.get("action"), str) and args.get("action").strip() else "update"
            return f"Allow Amadeus to {action} stable {target} memory?"

        return f"Allow Amadeus to run {self.display_name}?"


def normalize_positive_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback

    return max(minimum, min(maximum, number))
