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
    prompt_hint: str | None = None

    def describe_request(self, args: dict[str, Any]) -> str:
        if self.name == "roll_dice":
            sides = normalize_positive_int(args.get("sides"), 6, 2, 1000)
            count = normalize_positive_int(args.get("count"), 1, 1, 20)
            return f"Allow Amadeus to roll {count} d{sides}?"

        if self.name == "search_files":
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

        if self.name == "memory_add":
            scope = args.get("scope").strip() if isinstance(args.get("scope"), str) and args.get("scope").strip() else "memory"
            content = args.get("content").strip() if isinstance(args.get("content"), str) and args.get("content").strip() else "(empty memory)"
            if len(content) > 120:
                content = content[:117] + "..."
            return f'Allow Amadeus to remember this {scope} fact: "{content}"?'

        if self.name == "memory_replace":
            memory_item_id = args.get("memoryItemId") if isinstance(args.get("memoryItemId"), int) else "(missing id)"
            content = args.get("content").strip() if isinstance(args.get("content"), str) and args.get("content").strip() else "(empty memory)"
            if len(content) > 120:
                content = content[:117] + "..."
            return f'Allow Amadeus to replace structured memory item {memory_item_id} with: "{content}"?'

        if self.name == "memory_forget":
            memory_item_id = args.get("memoryItemId") if isinstance(args.get("memoryItemId"), int) else "(missing id)"
            return f"Allow Amadeus to forget structured memory item {memory_item_id}?"

        return f"Allow Amadeus to run {self.display_name}?"


def normalize_positive_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback

    return max(minimum, min(maximum, number))
