from __future__ import annotations

from typing import Any


VALID_PLAN_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
ACTIVE_PLAN_STATUSES = {"pending", "in_progress"}
MAX_PLAN_ITEMS = 64
MAX_PLAN_ITEM_CONTENT_CHARS = 1000
TRUNCATION_MARKER = "... [truncated]"


def normalize_plan_items(raw_items: Any) -> list[dict[str, str]]:
    if not isinstance(raw_items, list):
        raise ValueError("items must be an array")

    deduped: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for index, raw_item in enumerate(raw_items[:MAX_PLAN_ITEMS], start=1):
        if not isinstance(raw_item, dict):
            raise ValueError("each plan item must be an object")

        item_id = str(raw_item.get("id") or f"item-{index}").strip()
        if not item_id:
            item_id = f"item-{index}"
        content = str(raw_item.get("content") or "").strip()
        if not content:
            raise ValueError("plan item content is required")
        status = str(raw_item.get("status") or "pending").strip().lower()
        if status not in VALID_PLAN_STATUSES:
            raise ValueError(f"invalid plan item status: {status}")
        item = {
            "id": item_id[:80],
            "content": truncate_plan_content(content),
            "status": status,
        }
        if item["id"] not in deduped:
            order.append(item["id"])
        deduped[item["id"]] = item

    normalized_items = [deduped[item_id] for item_id in order]
    if sum(1 for item in normalized_items if item["status"] == "in_progress") > 1:
        raise ValueError("only one plan item can be in_progress")

    return normalized_items


def merge_plan_items(
    current_items: list[dict[str, Any]],
    updates: list[dict[str, Any]],
) -> list[dict[str, str]]:
    normalized_current = normalize_plan_items(current_items)
    normalized_updates = normalize_plan_items(updates)
    by_id = {item["id"]: dict(item) for item in normalized_current}
    order = [item["id"] for item in normalized_current]

    for update in normalized_updates:
        if update["id"] not in by_id:
            order.append(update["id"])
        by_id[update["id"]] = update

    merged = [by_id[item_id] for item_id in order if item_id in by_id]
    return normalize_plan_items(merged)


def truncate_plan_content(content: str) -> str:
    if len(content) <= MAX_PLAN_ITEM_CONTENT_CHARS:
        return content
    keep = MAX_PLAN_ITEM_CONTENT_CHARS - len(TRUNCATION_MARKER)
    return content[:keep] + TRUNCATION_MARKER


def plan_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "total": len(items),
        "pending": 0,
        "inProgress": 0,
        "completed": 0,
        "cancelled": 0,
    }
    for item in items:
        status = str(item.get("status") or "")
        if status == "pending":
            counts["pending"] += 1
        elif status == "in_progress":
            counts["inProgress"] += 1
        elif status == "completed":
            counts["completed"] += 1
        elif status == "cancelled":
            counts["cancelled"] += 1
    return counts


def plan_response(
    session_id: str,
    items: list[dict[str, Any]],
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    normalized_items = normalize_plan_items(items)
    return {
        "sessionId": session_id,
        "items": normalized_items,
        "summary": plan_summary(normalized_items),
        "updatedAt": updated_at,
    }


def empty_plan_response(session_id: str) -> dict[str, Any]:
    return {
        "sessionId": session_id,
        "items": [],
        "summary": plan_summary([]),
        "updatedAt": None,
    }


def format_active_plan_for_context(plan: dict[str, Any]) -> str:
    raw_items = plan.get("items") if isinstance(plan, dict) else None
    if not isinstance(raw_items, list):
        return ""

    active_items = [
        item
        for item in normalize_plan_items(raw_items)
        if item["status"] in ACTIVE_PLAN_STATUSES
    ]
    if not active_items:
        return ""

    markers = {
        "pending": "[ ]",
        "in_progress": "[>]",
        "completed": "[x]",
        "cancelled": "[~]",
    }
    lines = [
        "The current session has an active task plan. Use it as progress context; update it only when the task meaningfully changes.",
    ]
    for item in active_items:
        lines.append(f"- {markers[item['status']]} {item['id']}: {item['content']}")
    return "\n".join(lines)
