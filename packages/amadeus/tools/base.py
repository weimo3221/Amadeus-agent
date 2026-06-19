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
