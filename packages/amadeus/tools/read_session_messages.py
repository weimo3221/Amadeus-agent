from __future__ import annotations

from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int


DEFAULT_SESSION_MESSAGE_LIMIT = 40
MAX_SESSION_MESSAGE_LIMIT = 200
DEFAULT_MESSAGE_CHARS = 2000
MAX_MESSAGE_CHARS = 8000


def read_session_messages(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    current_session_id = str(getattr(context, "session_id", "default") or "default")
    requested_session_id = args.get("sessionId")
    session_id = requested_session_id.strip() if isinstance(requested_session_id, str) and requested_session_id.strip() else current_session_id
    limit = normalize_positive_int(args.get("limit"), DEFAULT_SESSION_MESSAGE_LIMIT, 1, MAX_SESSION_MESSAGE_LIMIT)
    after_message_id = args.get("afterMessageId") if isinstance(args.get("afterMessageId"), int) else None
    max_message_chars = normalize_positive_int(args.get("maxMessageChars"), DEFAULT_MESSAGE_CHARS, 1, MAX_MESSAGE_CHARS)

    try:
        raw_messages = memory_store.load_detailed(
            session_id,
            after_message_id=after_message_id,
            limit=limit,
        )
        latest_message_id = memory_store.latest_message_id(session_id)
        total_count = memory_store.count(session_id)
    except ValueError as error:
        return {"error": str(error)}

    messages: list[dict[str, Any]] = []
    for raw_message in raw_messages:
        content = str(raw_message.get("content", ""))
        truncated = len(content) > max_message_chars
        message: dict[str, Any] = {
            "id": raw_message.get("id"),
            "role": raw_message.get("role"),
            "createdAt": raw_message.get("createdAt"),
            "content": content[:max_message_chars] if truncated else content,
            "contentCharCount": len(content),
            "contentTruncated": truncated,
        }
        if raw_message.get("toolCallId"):
            message["toolCallId"] = raw_message.get("toolCallId")
        if raw_message.get("toolName"):
            message["toolName"] = raw_message.get("toolName")
        if raw_message.get("toolCalls"):
            message["toolCalls"] = raw_message.get("toolCalls")
        messages.append(message)

    last_id = int(messages[-1]["id"]) if messages and isinstance(messages[-1].get("id"), int) else int(after_message_id or 0)
    return {
        "sessionId": session_id,
        "currentSessionId": current_session_id,
        "limit": limit,
        "afterMessageId": after_message_id,
        "totalCount": total_count,
        "returnedCount": len(messages),
        "latestMessageId": latest_message_id,
        "hasMore": bool(messages and last_id < latest_message_id),
        "messages": messages,
    }


READ_SESSION_MESSAGES_TOOL_SPEC = ToolSpec(
    name="read_session_messages",
    display_name="Read session messages",
    permission="allow",
    enabled=True,
    handler=read_session_messages,
    prompt_hint="Use when the user asks to inspect or quote the raw conversation transcript for a bounded session window; use search_memory for semantic search instead.",
    schema={
        "type": "function",
        "function": {
            "name": "read_session_messages",
            "description": (
                "Read a bounded, paginated raw session transcript. "
                "This returns conversation log messages, not durable memory facts. "
                "Use only when the user asks for full session content, transcript details, or exact earlier wording."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sessionId": {
                        "type": "string",
                        "description": "Session to read. Defaults to the current session.",
                    },
                    "limit": {
                        "type": "number",
                        "description": "Maximum messages to return. Defaults to 40 and is capped at 200.",
                    },
                    "afterMessageId": {
                        "type": "integer",
                        "description": "Return messages with id greater than this value for forward pagination.",
                    },
                    "maxMessageChars": {
                        "type": "number",
                        "description": "Maximum characters per message. Defaults to 2000 and is capped at 8000.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
)
