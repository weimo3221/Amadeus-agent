from __future__ import annotations

import time
from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int


MAX_DELEGATE_QUERIES = 3
MAX_DELEGATE_PATHS = 5
MAX_DELEGATE_TASK_CHARS = 1000
MAX_CONCURRENCY = 2
DEFAULT_DELEGATE_WAIT_SECONDS = 240
MAX_DELEGATE_WAIT_SECONDS = 900
DELEGATE_POLL_INTERVAL_SECONDS = 0.05
TERMINAL_TASK_STATUSES = {"succeeded", "failed", "cancelled", "blocked"}


def _normalize_text(value: Any, field_name: str, max_chars: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if "\x00" in normalized:
        raise ValueError(f"{field_name} must be UTF-8 text")
    return normalized[:max_chars]


def _normalize_string_list(value: Any, *, max_items: int, max_chars: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("queries and paths must be arrays when provided")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value[:max_items]:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        normalized.append(text[:max_chars])
        seen.add(text)
    return normalized


def delegate_task(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    try:
        task = _normalize_text(args.get("task"), "task", MAX_DELEGATE_TASK_CHARS)
        queries = _normalize_string_list(args.get("queries"), max_items=MAX_DELEGATE_QUERIES, max_chars=160)
        paths = _normalize_string_list(args.get("paths"), max_items=MAX_DELEGATE_PATHS, max_chars=240)
    except ValueError as error:
        return {"error": str(error)}

    if not queries:
        queries = [task[:160]]

    include_memory = bool(args.get("includeMemory")) if isinstance(args.get("includeMemory"), bool) else True
    max_results = normalize_positive_int(args.get("maxResults"), 5, 1, 10)
    memory_store = getattr(context, "memory_store", None)
    task_worker = getattr(context, "task_worker", None)
    if memory_store is None:
        return {"error": "memory store is not available"}
    if task_worker is None:
        return {"error": "isolated task worker is not available"}
    if str(getattr(context, "worker_profile", "") or "").strip():
        return {"error": "recursive child-agent delegation is not allowed"}

    session_id = str(getattr(context, "session_id", "default") or "default")
    timeout_seconds = getattr(context, "timeout_seconds", DEFAULT_DELEGATE_WAIT_SECONDS)
    available_wait = max(1, int(float(timeout_seconds or DEFAULT_DELEGATE_WAIT_SECONDS)) - 2)
    wait_seconds = min(
        normalize_positive_int(
            args.get("maxWaitSeconds"),
            available_wait,
            1,
            MAX_DELEGATE_WAIT_SECONDS,
        ),
        available_wait,
    )
    body_lines = [
        task,
        "",
        "Use only the restricted read/search tools available in this child runtime.",
        f"Return one concise evidence-backed summary with at most {max_results} primary findings.",
    ]
    if queries:
        body_lines.extend(["", "Suggested search queries:", *[f"- {query}" for query in queries]])
    if paths:
        body_lines.extend(["", "Explicit files to inspect:", *[f"- {path}" for path in paths]])
    if include_memory:
        body_lines.append("You may search the source session memory, but do not reproduce unrelated conversation content.")
    else:
        body_lines.append("Do not search source session memory.")

    context_hints: dict[str, object] = {
        "sandboxMode": "read_only",
        "delegateQueries": queries,
        "delegatePaths": paths,
        "includeMemory": include_memory,
        "maxResults": max_results,
    }
    cwd = getattr(context, "cwd", None)
    if cwd is not None:
        context_hints["workspacePath"] = str(cwd)

    allowed_toolsets = ["read"]
    if include_memory:
        allowed_toolsets.append("search")
    child_task = memory_store.create_task(
        session_id=session_id,
        title=f"Delegated research: {task[:120]}",
        body="\n".join(body_lines),
        kind="delegated",
        source="model",
        worker_profile="researcher",
        acceptance_criteria=[
            "Answer the delegated question directly.",
            "Ground the summary in the allowed source memory or workspace files.",
            "Return only the concise handoff needed by the parent agent.",
        ],
        context_hints=context_hints,
        allowed_toolsets=allowed_toolsets,
        disallowed_tools=[
            "delegate_task",
            "read_session_messages",
            "create_task",
            "cancel_task",
            "update_plan",
        ],
        max_attempts=2,
    )
    child_task_id = str(child_task["id"])
    try:
        task_worker.submit(child_task_id)
    except Exception as error:
        memory_store.cancel_task(child_task_id, reason=f"Child task submission failed: {error}")
        return {
            "error": f"isolated child task submission failed: {error}",
            "childTaskId": child_task_id,
        }

    deadline = time.monotonic() + wait_seconds
    final_task = child_task
    while True:
        current = memory_store.get_task(child_task_id)
        if current is not None:
            final_task = current
        status = str(final_task.get("status") or "")
        if status in TERMINAL_TASK_STATUSES:
            break
        if bool(getattr(context, "is_cancelled", lambda: False)()):
            task_worker.cancel(child_task_id, reason="Parent delegate tool cancelled")
            return {
                "error": "isolated child task cancelled with parent turn",
                "childTaskId": child_task_id,
                "status": "cancelled",
            }
        if time.monotonic() >= deadline:
            task_worker.cancel(child_task_id, reason="Parent delegate wait timed out")
            return {
                "error": f"isolated child task timed out after {wait_seconds} seconds",
                "childTaskId": child_task_id,
                "status": "cancelled",
            }
        time.sleep(DELEGATE_POLL_INTERVAL_SECONDS)

    status = str(final_task.get("status") or "")
    if status != "succeeded":
        detail = str(
            final_task.get("blockedReason")
            or final_task.get("error")
            or final_task.get("handoffSummary")
            or f"child task ended with status {status}"
        )
        return {
            "error": detail,
            "childTaskId": child_task_id,
            "status": status,
        }

    summary = str(final_task.get("handoffSummary") or final_task.get("result") or "").strip()
    artifacts = memory_store.list_task_artifacts(child_task_id, limit=20)

    return {
        "task": task,
        "delegateType": "isolated_child_agent",
        "childTaskId": child_task_id,
        "status": status,
        "runnerKind": final_task.get("runnerKind"),
        "attemptCount": final_task.get("attemptCount"),
        "maxDepth": 1,
        "maxConcurrency": MAX_CONCURRENCY,
        "allowedTools": [
            "search_files",
            "read_file",
            *(["search_memory"] if include_memory else []),
        ],
        "summary": summary,
        "artifactCount": len(artifacts),
    }


DELEGATE_TASK_TOOL_SPEC = ToolSpec(
    name="delegate_task",
    display_name="Delegating restricted research task",
    permission="allow",
    enabled=True,
    handler=delegate_task,
    prompt_hint="Use only for bounded research/search subtasks that should run in a tracked isolated child agent; never for writes, shell execution, UI control, recursive delegation, or broad autonomous work.",
    schema={
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": (
                "Run a restricted research/search subtask in a tracked isolated child agent with max_depth=1. "
                "The child executes through the task worker with its own session, WorkerContext, and read-only WorkerRuntimeScope. "
                "It can search source-session memory, search files, and read explicit file windows, but cannot write files, "
                "run shell commands, delegate recursively, or control Live2D/audio. The parent receives only the child summary and task metadata."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Focused research task for the restricted delegate.",
                    },
                    "queries": {
                        "type": "array",
                        "description": "Optional search queries. Defaults to the task text and is capped at 3.",
                        "items": {"type": "string"},
                    },
                    "paths": {
                        "type": "array",
                        "description": "Optional workspace-relative files to read in bounded windows. Capped at 5.",
                        "items": {"type": "string"},
                    },
                    "includeMemory": {
                        "type": "boolean",
                        "description": "Whether the child may search the parent source-session memory. Defaults to true.",
                    },
                    "maxResults": {
                        "type": "number",
                        "description": "Maximum results per search query. Defaults to 5 and is capped at 10.",
                    },
                    "maxWaitSeconds": {
                        "type": "number",
                        "description": "Maximum synchronous wait for the child task, bounded by runtime policy.",
                    },
                },
                "required": ["task"],
                "additionalProperties": False,
            },
        },
    },
)
