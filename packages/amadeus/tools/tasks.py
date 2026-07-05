from __future__ import annotations

from typing import Any

from amadeus.tasks import normalize_task_status
from amadeus.tools.base import ToolSpec, normalize_positive_int


def create_task(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        return {"error": "title is required"}

    body = args.get("body") if isinstance(args.get("body"), str) else None
    kind = args.get("kind") if isinstance(args.get("kind"), str) else None
    source = args.get("source") if isinstance(args.get("source"), str) else "model"
    plan_item_id = args.get("planItemId") if isinstance(args.get("planItemId"), str) else None
    parent_task_id = args.get("parentTaskId") if isinstance(args.get("parentTaskId"), str) else None
    priority = args.get("priority") if args.get("priority") is not None else None
    due_at = args.get("dueAt") if isinstance(args.get("dueAt"), str) else None
    max_attempts = args.get("maxAttempts") if args.get("maxAttempts") is not None else None
    auto_start = bool(args.get("autoStart")) if isinstance(args.get("autoStart"), bool) else True
    session_id = str(getattr(context, "session_id", "default") or "default")

    try:
        task = memory_store.create_task(
            session_id=session_id,
            title=title,
            body=body,
            kind=kind,
            source=source,
            plan_item_id=plan_item_id,
            parent_task_id=parent_task_id,
            priority=priority,
            due_at=due_at,
            max_attempts=max_attempts,
        )
    except ValueError as error:
        return {"error": str(error)}

    worker_submitted = False
    task_worker = getattr(context, "task_worker", None)
    if auto_start and task_worker is not None:
        task_worker.submit(str(task["id"]))
        worker_submitted = True

    return {
        "action": "created",
        "task": task,
        "workerSubmitted": worker_submitted,
        "autoStart": auto_start,
    }


def list_tasks(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    session_id = str(getattr(context, "session_id", "default") or "default")
    raw_status = args.get("status")
    try:
        status = normalize_task_status(raw_status) if raw_status is not None else None
    except ValueError as error:
        return {"error": str(error)}
    active_only = bool(args.get("activeOnly")) if isinstance(args.get("activeOnly"), bool) else True
    limit = normalize_positive_int(args.get("limit"), 20, 1, 50)

    try:
        return memory_store.list_tasks(
            session_id=session_id,
            status=status,
            active_only=active_only,
            limit=limit,
        )
    except ValueError as error:
        return {"error": str(error)}


def cancel_task(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    task_id = str(args.get("taskId") or "").strip()
    if not task_id:
        return {"error": "taskId is required"}

    task = memory_store.get_task(task_id)
    if task is None:
        return {"error": "task not found"}
    session_id = str(getattr(context, "session_id", "default") or "default")
    if task.get("sessionId") != session_id:
        return {"error": "task not found"}

    reason = args.get("reason") if isinstance(args.get("reason"), str) else None
    task_worker = getattr(context, "task_worker", None)
    try:
        cancelled = task_worker.cancel(task_id, reason=reason) if task_worker is not None else memory_store.cancel_task(task_id, reason=reason)
    except ValueError as error:
        return {"error": str(error)}

    return {
        "action": "cancelled",
        "task": cancelled,
    }


CREATE_TASK_TOOL_SPEC = ToolSpec(
    name="create_task",
    display_name="Creating background task",
    permission="allow",
    enabled=True,
    handler=create_task,
    prompt_hint="Use only for explicit asynchronous, longer-running, queued, tracked, or user-requested background work; do not use for ordinary immediate answers or internal planning.",
    schema={
        "type": "function",
        "function": {
            "name": "create_task",
            "description": (
                "Create a session-scoped background task for explicit user-requested asynchronous or longer-running work. "
                "Use this when the user asks to track, queue, run in the background, or come back to a task. "
                "Do not use it for ordinary immediate answers, simple one-step requests, or internal planning."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short task title.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Optional details or instructions for the task worker.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["agent_turn", "scheduled_prompt", "script", "review", "delegated"],
                        "description": "Task execution kind. Defaults to agent_turn.",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["manual", "model", "scheduled_job", "plan", "api", "system"],
                        "description": "Task origin. Defaults to model for tool-created tasks.",
                    },
                    "planItemId": {
                        "type": "string",
                        "description": "Optional active plan item id this task executes.",
                    },
                    "parentTaskId": {
                        "type": "string",
                        "description": "Optional parent task id for task decomposition.",
                    },
                    "priority": {
                        "type": "number",
                        "description": "Optional priority from -100 to 100. Higher runs first once queued.",
                    },
                    "maxAttempts": {
                        "type": "number",
                        "description": "Optional worker attempt cap from 1 to 10. Defaults to 3.",
                    },
                    "dueAt": {
                        "type": "string",
                        "description": "Optional due timestamp or human-readable deadline.",
                    },
                    "autoStart": {
                        "type": "boolean",
                        "description": "Whether to submit the task to the worker immediately. Defaults to true.",
                    },
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
    },
)


LIST_TASKS_TOOL_SPEC = ToolSpec(
    name="list_tasks",
    display_name="Listing background tasks",
    permission="allow",
    enabled=True,
    handler=list_tasks,
    prompt_hint="Use when the user asks to inspect session background tasks or when you need current task status before answering.",
    schema={
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List session-scoped background tasks, usually to inspect active queued/running/blocked work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["queued", "running", "blocked", "succeeded", "failed", "cancelled"],
                        "description": "Optional exact status filter.",
                    },
                    "activeOnly": {
                        "type": "boolean",
                        "description": "When true, list only queued/running/blocked tasks. Defaults to true.",
                    },
                    "limit": {
                        "type": "number",
                        "description": "Maximum tasks to return. Defaults to 20 and is capped at 50.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
)


CANCEL_TASK_TOOL_SPEC = ToolSpec(
    name="cancel_task",
    display_name="Cancelling background task",
    permission="allow",
    enabled=True,
    handler=cancel_task,
    prompt_hint="Use when the user asks to stop, cancel, or abandon an active session background task.",
    schema={
        "type": "function",
        "function": {
            "name": "cancel_task",
            "description": "Cancel a queued, running, or blocked session task by task id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "taskId": {
                        "type": "string",
                        "description": "Task id returned by create_task or list_tasks.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional cancellation reason.",
                    },
                },
                "required": ["taskId"],
                "additionalProperties": False,
            },
        },
    },
)
