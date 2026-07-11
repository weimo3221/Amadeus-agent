from __future__ import annotations

import logging
from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int


logger = logging.getLogger(__name__)

DEFAULT_MEMORY_ITEMS_LIMIT = 8
MAX_MEMORY_ITEMS_LIMIT = 20
DEFAULT_VECTOR_CANDIDATE_LIMIT = 80


def memory_add(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    scope = args.get("scope").strip() if isinstance(args.get("scope"), str) else ""
    content = args.get("content").strip() if isinstance(args.get("content"), str) else ""
    confidence = args.get("confidence", 1.0)
    memory_type = args.get("memoryType") if isinstance(args.get("memoryType"), str) else None
    metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else None
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
            memory_type=memory_type,
            metadata=metadata,
            actor="tool",
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
    memory_type = args.get("memoryType") if isinstance(args.get("memoryType"), str) and args.get("memoryType").strip() else None
    query = args.get("query") if isinstance(args.get("query"), str) and args.get("query").strip() else None
    metadata_filter = args.get("metadataFilter") if isinstance(args.get("metadataFilter"), dict) else None
    limit = normalize_positive_int(args.get("limit"), DEFAULT_MEMORY_ITEMS_LIMIT, 1, MAX_MEMORY_ITEMS_LIMIT)

    try:
        items, retrieval_provider = search_memory_items_with_optional_vector(
            memory_store,
            context,
            scope=scope,
            memory_type=memory_type,
            query=query,
            metadata_filter=metadata_filter,
            limit=limit,
        )
        memory_item_ids = [
            int(item["memoryItemId"])
            for item in items
            if isinstance(item, dict) and isinstance(item.get("memoryItemId"), int)
        ]
        if memory_item_ids:
            memory_store.record_memory_item_access(memory_item_ids)
        return {
            "scope": scope,
            "memoryType": memory_type,
            "query": query,
            "metadataFilter": metadata_filter or {},
            "limit": limit,
            "retrievalProvider": retrieval_provider,
            "resultCount": len(items),
            "items": items,
        }
    except ValueError as error:
        return {"error": str(error)}


def search_memory_items_with_optional_vector(
    memory_store: Any,
    context: Any,
    *,
    scope: str | None,
    memory_type: str | None,
    query: str | None,
    metadata_filter: dict[str, object] | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str]:
    embedding_provider = getattr(context, "memory_embedding_provider", None)
    if query and embedding_provider is not None and callable(getattr(embedding_provider, "encode_texts", None)):
        try:
            available = getattr(embedding_provider, "available", None)
            if not callable(available) or available():
                query_vector = embedding_provider.encode_texts([query])[0]
                items = memory_store.search_memory_items_hybrid(
                    query=query,
                    query_embedding=query_vector,
                    provider=str(getattr(embedding_provider, "provider", "")),
                    model=str(getattr(embedding_provider, "model_id", "")),
                    dimensions=int(getattr(embedding_provider, "dimensions", 0)),
                    scope=scope,
                    memory_type=memory_type,
                    metadata_filter=metadata_filter,
                    limit=limit,
                    candidate_limit=normalize_positive_int(
                        getattr(context, "memory_vector_candidate_limit", DEFAULT_VECTOR_CANDIDATE_LIMIT),
                        DEFAULT_VECTOR_CANDIDATE_LIMIT,
                        limit,
                        500,
                    ),
                )
                if items:
                    return items, "memory_items_hybrid"
        except Exception as error:
            logger.info("Structured memory vector search failed; falling back to BM25/SQL error=%s", error)

    items = memory_store.list_memory_items(
        scope=scope,
        memory_type=memory_type,
        query=query,
        metadata_filter=metadata_filter,
        limit=limit,
    )
    retrieval_provider = "memory_items_sql"
    for item in items:
        if isinstance(item, dict) and item.get("retrievalProvider"):
            retrieval_provider = str(item["retrievalProvider"])
            break
    return items, retrieval_provider


def memory_replace(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    memory_item_id = args.get("memoryItemId")
    content = args.get("content").strip() if isinstance(args.get("content"), str) else ""
    scope = args.get("scope").strip() if isinstance(args.get("scope"), str) and args.get("scope").strip() else None
    confidence = args.get("confidence")
    memory_type = args.get("memoryType") if isinstance(args.get("memoryType"), str) else None
    metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else None
    if not isinstance(memory_item_id, int):
        return {"error": "memoryItemId must be an integer"}

    try:
        item = memory_store.replace_memory_item(
            memory_item_id,
            content,
            scope=scope,
            confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            memory_type=memory_type,
            metadata=metadata,
            actor="tool",
        )
        if item is None:
            return {"replaced": False, "memoryItemId": memory_item_id, "error": "active memory item not found"}
        return {"replaced": True, "item": item}
    except ValueError as error:
        return {"error": str(error)}


def memory_forget(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    memory_item_id = args.get("memoryItemId")
    if not isinstance(memory_item_id, int):
        return {"error": "memoryItemId must be an integer"}

    try:
        forgotten = memory_store.delete_memory_item(memory_item_id, actor="tool")
        return {"forgotten": forgotten, "memoryItemId": memory_item_id}
    except ValueError as error:
        return {"error": str(error)}


MEMORY_ADD_TOOL_SPEC = ToolSpec(
    name="memory_add",
    display_name="Adding structured memory",
    permission="ask",
    enabled=True,
    handler=memory_add,
    prompt_hint="Use only for durable structured facts after user approval; never store transient progress, secrets, guesses, or raw transcripts.",
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
                    "memoryType": {
                        "type": "string",
                        "enum": ["semantic", "episodic", "procedural", "preference", "project_fact", "agent_instruction"],
                        "description": "Optional Mem0-like memory type. Defaults to semantic.",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional compact JSON metadata for source, tags, or lifecycle hints.",
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


MEMORY_REPLACE_TOOL_SPEC = ToolSpec(
    name="memory_replace",
    display_name="Replacing structured memory",
    permission="ask",
    enabled=True,
    handler=memory_replace,
    prompt_hint="Use only when a durable structured memory item needs correction after user approval.",
    schema={
        "type": "function",
        "function": {
            "name": "memory_replace",
            "description": "Replace one active durable structured memory item after user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memoryItemId": {
                        "type": "integer",
                        "description": "The active structured memory item id to replace.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The corrected durable fact. Keep it concise and non-sensitive.",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["user", "agent", "project"],
                        "description": "Optional corrected scope.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Optional corrected confidence from 0 to 1.",
                    },
                    "memoryType": {
                        "type": "string",
                        "enum": ["semantic", "episodic", "procedural", "preference", "project_fact", "agent_instruction"],
                        "description": "Optional corrected Mem0-like memory type.",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional replacement metadata object.",
                    },
                },
                "required": ["memoryItemId", "content"],
                "additionalProperties": False,
            },
        },
    },
)


MEMORY_FORGET_TOOL_SPEC = ToolSpec(
    name="memory_forget",
    display_name="Forgetting structured memory",
    permission="ask",
    enabled=True,
    handler=memory_forget,
    prompt_hint="Use only when a durable structured memory item should be removed after user approval.",
    schema={
        "type": "function",
        "function": {
            "name": "memory_forget",
            "description": "Delete one active durable structured memory item after user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memoryItemId": {
                        "type": "integer",
                        "description": "The active structured memory item id to delete.",
                    },
                },
                "required": ["memoryItemId"],
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
    prompt_hint="Use when durable structured long-term memory may contain relevant user, agent, or project facts; these facts are not injected automatically.",
    schema={
        "type": "function",
        "function": {
            "name": "search_memory_items",
            "description": (
                "Search durable structured long-term memory facts by optional scope, query, type, and metadata. "
                "Use this when remembered user preferences, agent facts, project facts, or prior durable decisions may matter."
            ),
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
                    "memoryType": {
                        "type": "string",
                        "enum": ["semantic", "episodic", "procedural", "preference", "project_fact", "agent_instruction"],
                        "description": "Optional Mem0-like memory type filter.",
                    },
                    "metadataFilter": {
                        "type": "object",
                        "description": "Optional exact-match metadata filter. Scalar values match equal scalar metadata; scalar values also match list membership.",
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
