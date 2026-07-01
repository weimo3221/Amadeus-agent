from __future__ import annotations

from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int


def schedule_message(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    if memory_store is None:
        return {"error": "memory store is not available"}

    action = str(args.get("action") or "create").strip().lower()
    session_id = str(getattr(context, "session_id", "companion:default") or "companion:default")

    if action == "create":
        message = args.get("message")
        schedule = args.get("schedule")
        if not isinstance(message, str) or not message.strip():
            return {"error": "message is required"}
        if not isinstance(schedule, str) or not schedule.strip():
            return {"error": "schedule is required"}
        title = args.get("title") if isinstance(args.get("title"), str) else None
        repeat_count = args.get("repeatCount") if args.get("repeatCount") is not None else None
        try:
            job = memory_store.create_scheduled_job(
                session_id=session_id,
                title=title,
                message=message,
                schedule=schedule,
                repeat_count=repeat_count if isinstance(repeat_count, int) else None,
            )
        except ValueError as error:
            return {"error": str(error)}
        return {"action": "created", "job": job}

    if action == "list":
        status = args.get("status") if isinstance(args.get("status"), str) else None
        active_only = bool(args.get("activeOnly")) if isinstance(args.get("activeOnly"), bool) else True
        limit = normalize_positive_int(args.get("limit"), 20, 1, 50)
        try:
            return memory_store.list_scheduled_jobs(
                session_id=session_id,
                status=status,
                active_only=active_only,
                limit=limit,
            )
        except ValueError as error:
            return {"error": str(error)}

    job_id = str(args.get("jobId") or "").strip()
    if not job_id:
        return {"error": "jobId is required for this action"}

    try:
        if action == "pause":
            return {"action": "paused", "job": memory_store.pause_scheduled_job(job_id)}
        if action == "resume":
            return {"action": "resumed", "job": memory_store.resume_scheduled_job(job_id)}
        if action in {"cancel", "remove"}:
            reason = args.get("reason") if isinstance(args.get("reason"), str) else None
            return {"action": "cancelled", "job": memory_store.cancel_scheduled_job(job_id, reason=reason)}
    except ValueError as error:
        return {"error": str(error)}

    return {"error": f"unsupported schedule action: {action}"}


SCHEDULE_MESSAGE_TOOL_SPEC = ToolSpec(
    name="schedule_message",
    display_name="Scheduling companion message",
    permission="allow",
    enabled=True,
    handler=schedule_message,
    prompt_hint=(
        "Use when the user asks for reminders, alarms, countdowns, recurring check-ins, "
        "or asks you to proactively say something later or repeatedly. For repeated messages, "
        "use schedule like 'every 10s' and repeatCount for the requested count."
    ),
    schema={
        "type": "function",
        "function": {
            "name": "schedule_message",
            "description": (
                "Create and manage session-scoped scheduled companion messages. "
                "Use this for reminders, timers, recurring check-ins, and explicit proactive messages. "
                "The scheduled message will be delivered back into the current conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "pause", "resume", "cancel"],
                        "description": "Management action. Defaults to create.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label for a new scheduled message.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Assistant message to deliver when the schedule fires.",
                    },
                    "schedule": {
                        "type": "string",
                        "description": "For create: '10s', 'every 10s', 'every 30m', '0 9 * * *', or ISO timestamp.",
                    },
                    "repeatCount": {
                        "type": "number",
                        "description": "For recurring schedules: number of deliveries. Omit for indefinitely recurring.",
                    },
                    "jobId": {
                        "type": "string",
                        "description": "Scheduled job id for pause/resume/cancel.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["scheduled", "running", "paused", "completed", "cancelled", "failed"],
                    },
                    "activeOnly": {
                        "type": "boolean",
                        "description": "For list: only scheduled/running/paused jobs. Defaults to true.",
                    },
                    "limit": {
                        "type": "number",
                        "description": "For list: maximum jobs to return.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional cancellation reason.",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },
)
