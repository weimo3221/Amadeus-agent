from __future__ import annotations

from typing import Any

from amadeus.tools.base import ToolSpec


def read_memory(args: dict[str, Any], context: Any) -> dict[str, Any]:
    target = args.get("target") if isinstance(args.get("target"), str) else "agent"
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    try:
        return memory_store.read_stable_memory(target)
    except ValueError as error:
        return {"error": str(error)}


def update_memory(args: dict[str, Any], context: Any) -> dict[str, Any]:
    target = args.get("target") if isinstance(args.get("target"), str) else "agent"
    action = args.get("action") if isinstance(args.get("action"), str) else ""
    content = args.get("content") if isinstance(args.get("content"), str) else None
    old_text = args.get("oldText") if isinstance(args.get("oldText"), str) else None
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    try:
        return memory_store.update_stable_memory(
            target=target,
            action=action,
            content=content,
            old_text=old_text,
        )
    except ValueError as error:
        return {"error": str(error)}


READ_MEMORY_TOOL_SPEC = ToolSpec(
    name="read_memory",
    display_name="Reading stable memory",
    permission="allow",
    enabled=True,
    handler=read_memory,
    prompt_hint="Use when stable agent or user profile memory must be read explicitly.",
    schema={
        "type": "function",
        "function": {
            "name": "read_memory",
            "description": "Read the stable Markdown memory file for the agent or user profile.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["agent", "user"],
                        "description": "agent reads MEMORY.md; user reads USER.md.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
)


UPDATE_MEMORY_TOOL_SPEC = ToolSpec(
    name="update_memory",
    display_name="Updating stable memory",
    permission="ask",
    enabled=True,
    handler=update_memory,
    prompt_hint="Use only when the user explicitly asks to remember, update, or remove durable facts, preferences, or important project decisions.",
    schema={
        "type": "function",
        "function": {
            "name": "update_memory",
            "description": "Add, replace, or remove stable Markdown memory entries. Use only for durable facts, user preferences, or important decisions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["agent", "user"],
                        "description": "agent updates MEMORY.md; user updates USER.md.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["add", "replace", "remove"],
                        "description": "Controlled update operation.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Entry text for add/replace. Plain text is normalized to a Markdown bullet.",
                    },
                    "oldText": {
                        "type": "string",
                        "description": "Exact existing text to replace or remove. Required for replace/remove.",
                    },
                },
                "required": ["target", "action"],
                "additionalProperties": False,
            },
        },
    },
)
