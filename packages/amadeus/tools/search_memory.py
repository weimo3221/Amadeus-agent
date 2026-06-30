from __future__ import annotations

from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int


DEFAULT_MEMORY_SEARCH_LIMIT = 8
MAX_MEMORY_SEARCH_LIMIT = 20


def search_memory(args: dict[str, Any], context: Any) -> dict[str, Any]:
    requested_query = args.get("query")
    query = requested_query.strip() if isinstance(requested_query, str) else ""
    if not query:
        return {"error": "query is required"}

    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    limit = normalize_positive_int(args.get("limit"), DEFAULT_MEMORY_SEARCH_LIMIT, 1, MAX_MEMORY_SEARCH_LIMIT)
    include_all_sessions = bool(args.get("includeAllSessions")) if isinstance(args.get("includeAllSessions"), bool) else False
    session_id = None if include_all_sessions else getattr(context, "session_id", "default")

    results = memory_store.search(query, session_id=session_id, limit=limit)
    return {
        "query": query,
        "sessionId": session_id,
        "includeAllSessions": include_all_sessions,
        "limit": limit,
        "resultCount": len(results),
        "results": results,
    }


SEARCH_MEMORY_TOOL_SPEC = ToolSpec(
    name="search_memory",
    display_name="Searching memory",
    permission="allow",
    enabled=True,
    handler=search_memory,
    prompt_hint="Use when the user asks about earlier messages, remembered preferences, past decisions, or conversation history.",
    schema={
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search prior conversation memory when the user asks about earlier messages, remembered preferences, past decisions, or conversation history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for in saved conversation memory.",
                    },
                    "limit": {
                        "type": "number",
                        "description": "Maximum memory matches to return. Defaults to 8 and is capped at 20.",
                    },
                    "includeAllSessions": {
                        "type": "boolean",
                        "description": "Search all sessions instead of only the current session. Defaults to false.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
)
