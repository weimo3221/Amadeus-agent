from __future__ import annotations

from typing import Any

from amadeus.tools.base import ToolSpec


def update_plan(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    session_id = getattr(context, "session_id", "default")
    raw_items = args.get("items")
    if raw_items is None:
        result = memory_store.load_session_plan(session_id)
        result["changed"] = False
        return result

    if not isinstance(raw_items, list):
        return {"error": "items must be an array"}

    merge = bool(args.get("merge")) if isinstance(args.get("merge"), bool) else False
    try:
        result = memory_store.save_session_plan(session_id, raw_items, merge=merge)
    except ValueError as error:
        return {"error": str(error)}

    result["changed"] = True
    result["merge"] = merge
    return result


UPDATE_PLAN_TOOL_SPEC = ToolSpec(
    name="update_plan",
    display_name="Updating task plan",
    permission="allow",
    enabled=True,
    handler=update_plan,
    schema={
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": (
                "Create, replace, merge, or read the current session task plan. "
                "Use this only when the user's request benefits from an explicit multi-step plan, "
                "status tracking, or visible progress. Do not call it for simple one-step questions, "
                "short factual answers, or casual chat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": (
                            "Full replacement plan by default, or partial updates when merge=true. "
                            "Omit items to read the current plan."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Stable short id for the plan item, such as step-1 or inspect-files.",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Concrete task step or progress item.",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed", "cancelled"],
                                    "description": "Current item status. At most one item may be in_progress.",
                                },
                            },
                            "required": ["content", "status"],
                            "additionalProperties": False,
                        },
                    },
                    "merge": {
                        "type": "boolean",
                        "description": "When true, update matching item ids and append new ids instead of replacing the whole plan.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
)
