from __future__ import annotations

from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int


DEFAULT_MEMORY_ITEMS_LIMIT = 8
MAX_MEMORY_ITEMS_LIMIT = 20


def memory_add(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    scope = args.get("scope").strip() if isinstance(args.get("scope"), str) else ""
    content = args.get("content").strip() if isinstance(args.get("content"), str) else ""
    confidence = args.get("confidence", 1.0)
    source_message_id = args.get("sourceMessageId") if isinstance(args.get("sourceMessageId"), int) else None
    session_id = getattr(context, "session_id", "default")

    try:
        existing_items = memory_store.list_memory_items(scope=scope, query=content, limit=10)
        for item in existing_items:
            if str(item.get("content", "")).strip() == content.strip():
                return {
                    "added": False,
                    "duplicate": True,
                    "existingItem": item,
                    "message": "An active structured memory item with the same scope and content already exists.",
                }

        item = memory_store.save_memory_item(
            scope,
            content,
            confidence=float(confidence),
            source_session_id=session_id,
            source_message_id=source_message_id,
        )
        return {
            "added": True,
            "duplicate": False,
            "item": item,
        }
    except ValueError as error:
        return {"error": str(error)}


def search_memory_items(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    scope = args.get("scope") if isinstance(args.get("scope"), str) and args.get("scope").strip() else None
    query = args.get("query") if isinstance(args.get("query"), str) and args.get("query").strip() else None
    limit = normalize_positive_int(args.get("limit"), DEFAULT_MEMORY_ITEMS_LIMIT, 1, MAX_MEMORY_ITEMS_LIMIT)

    try:
        items = memory_store.list_memory_items(scope=scope, query=query, limit=limit)
        return {
            "scope": scope,
            "query": query,
            "limit": limit,
            "resultCount": len(items),
            "items": items,
        }
    except ValueError as error:
        return {"error": str(error)}


MEMORY_ADD_TOOL_SPEC = ToolSpec(
    name="memory_add",
    display_name="Adding structured memory",
    permission="ask",
    enabled=True,
    handler=memory_add,
    schema={
        "type": "function",
        "function": {
            "name": "memory_add",
            "description": (
                "Add one durable structured memory fact after user approval. "
                "Use only for stable user preferences, agent/project facts, or durable decisions. "
                "Do not store transient task progress, secrets, guesses, or raw transcripts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["user", "agent", "project"],
                        "description": "user for user preferences/profile facts, agent for agent operating facts, project for durable project facts.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The stable fact to remember. Keep it concise and non-sensitive.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence from 0 to 1. Defaults to 1.0 for explicit user-provided facts.",
                    },
                    "sourceMessageId": {
                        "type": "integer",
                        "description": "Optional source message id if known.",
                    },
                },
                "required": ["scope", "content"],
                "additionalProperties": False,
            },
        },
    },
)


SEARCH_MEMORY_ITEMS_TOOL_SPEC = ToolSpec(
    name="search_memory_items",
    display_name="Searching structured memory",
    permission="allow",
    enabled=True,
    handler=search_memory_items,
    schema={
        "type": "function",
        "function": {
            "name": "search_memory_items",
            "description": "Search durable structured memory facts by optional scope and query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["user", "agent", "project"],
                        "description": "Optional scope filter.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional text filter for memory content.",
                    },
                    "limit": {
                        "type": "number",
                        "description": "Maximum structured memory items to return. Defaults to 8 and is capped at 20.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
)
