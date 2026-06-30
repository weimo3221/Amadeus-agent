from __future__ import annotations

from typing import Any


TASK_STATUSES = {"queued", "running", "blocked", "succeeded", "failed", "cancelled"}
LEGACY_TASK_STATUS_ALIASES = {"done": "succeeded"}
ACTIVE_TASK_STATUSES = {"queued", "running", "blocked"}
MAX_TASK_TITLE_CHARS = 200
MAX_TASK_BODY_CHARS = 8000
MAX_TASK_RESULT_CHARS = 12000
MAX_TASK_ERROR_CHARS = 4000
MAX_TASK_EVENT_MESSAGE_CHARS = 2000
TRUNCATION_MARKER = "... [truncated]"


def normalize_task_status(status: Any, *, default: str = "queued") -> str:
    normalized = str(status or default).strip().lower()
    normalized = LEGACY_TASK_STATUS_ALIASES.get(normalized, normalized)
    if normalized not in TASK_STATUSES:
        raise ValueError(f"invalid task status: {normalized}")
    return normalized


def normalize_task_title(title: Any) -> str:
    normalized = str(title or "").strip()
    if not normalized:
        raise ValueError("title is required")
    if "\x00" in normalized:
        raise ValueError("title must be UTF-8 text")
    return truncate_text(normalized, MAX_TASK_TITLE_CHARS)


def normalize_task_body(body: Any) -> str:
    normalized = str(body or "").strip()
    if "\x00" in normalized:
        raise ValueError("body must be UTF-8 text")
    return truncate_text(normalized, MAX_TASK_BODY_CHARS)


def normalize_task_priority(priority: Any) -> int:
    if priority is None:
        return 0
    try:
        parsed = int(priority)
    except (TypeError, ValueError):
        raise ValueError("priority must be an integer") from None
    return max(-100, min(100, parsed))


def normalize_optional_text(value: Any, *, max_chars: int, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if "\x00" in normalized:
        raise ValueError(f"{field_name} must be UTF-8 text")
    if not normalized:
        return None
    return truncate_text(normalized, max_chars)


def normalize_task_event_type(event_type: Any) -> str:
    normalized = str(event_type or "").strip().lower()
    if not normalized:
        raise ValueError("event type is required")
    if "\x00" in normalized:
        raise ValueError("event type must be UTF-8 text")
    return truncate_text(normalized, 80)


def truncate_text(content: str, max_chars: int) -> str:
    if len(content) <= max_chars:
        return content
    keep = max_chars - len(TRUNCATION_MARKER)
    return content[:keep] + TRUNCATION_MARKER


def task_summary(tasks: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "total": len(tasks),
        "queued": 0,
        "running": 0,
        "blocked": 0,
        "succeeded": 0,
        "failed": 0,
        "cancelled": 0,
    }
    for task in tasks:
        status = normalize_task_status(task.get("status"), default="queued")
        if status in counts:
            counts[status] += 1
    return counts
