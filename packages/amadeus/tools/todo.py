from __future__ import annotations

from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int


def todo(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    session_id = str(getattr(context, "session_id", "companion:default") or "companion:default")
    raw_todos = args.get("todos")
    merge = bool(args.get("merge")) if isinstance(args.get("merge"), bool) else False
    active_only = bool(args.get("activeOnly")) if isinstance(args.get("activeOnly"), bool) else False
    limit = normalize_positive_int(args.get("limit"), 100, 1, 256)

    try:
        if raw_todos is None:
            return memory_store.list_todos(session_id=session_id, active_only=active_only, limit=limit)
        if not isinstance(raw_todos, list):
            return {"error": "todos must be an array when provided"}
        return {
            "action": "updated",
            **memory_store.save_todos(session_id=session_id, todos=raw_todos, merge=merge),
        }
    except ValueError as error:
        return {"error": str(error)}


TODO_TOOL_SPEC = ToolSpec(
    name="todo",
    display_name="Managing todo list",
    permission="allow",
    enabled=True,
    handler=todo,
    prompt_hint=(
        "Use for user-facing persistent todo lists, daily tasks, shopping/checklists, or multi-item personal reminders. "
        "Use update_plan for your current response plan; use todo when the user wants items remembered across turns."
    ),
    schema={
        "type": "function",
        "function": {
            "name": "todo",
            "description": (
                "Read or update the current session's persistent todo list. "
                "Call with no todos to read. Provide todos to replace or merge items. "
                "Only one item should normally be in_progress."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "Todo items to write. Omit to read the current todo list.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Stable id for the item. Omit for a generated id.",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Short user-facing todo text.",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed", "cancelled"],
                                },
                            },
                            "required": ["content", "status"],
                            "additionalProperties": False,
                        },
                    },
                    "merge": {
                        "type": "boolean",
                        "description": "When true, update existing items by id and append new items. Defaults to false, replacing the whole list.",
                    },
                    "activeOnly": {
                        "type": "boolean",
                        "description": "When reading, return only pending and in_progress items.",
                    },
                    "limit": {
                        "type": "number",
                        "description": "When reading, maximum items to return.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
)
