from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


logger = logging.getLogger(__name__)


ScheduledJobPublisher = Callable[[dict[str, object], str], None]
TaskSubmitter = Callable[[str], None]


@dataclass(frozen=True)
class ParsedSchedule:
    kind: str
    value: str
    display: str
    next_run_at: str
    interval_seconds: int | None = None
    hour: int | None = None
    minute: int | None = None
    day_of_month: int | None = None
    day_of_week: str | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind,
            "value": self.value,
            "display": self.display,
        }
        if self.interval_seconds is not None:
            payload["intervalSeconds"] = self.interval_seconds
        if self.hour is not None:
            payload["hour"] = self.hour
        if self.minute is not None:
            payload["minute"] = self.minute
        if self.day_of_month is not None:
            payload["dayOfMonth"] = self.day_of_month
        if self.day_of_week is not None:
            payload["dayOfWeek"] = self.day_of_week
        return payload


def parse_schedule(value: str, *, now: datetime | None = None) -> ParsedSchedule:
    text = str(value or "").strip()
    if not text:
        raise ValueError("schedule is required")
    now_dt = _utc(now)
    lower = text.lower()
    recurring = False
    body = lower
    if body.startswith("every "):
        recurring = True
        body = body.removeprefix("every ").strip()

    duration = _parse_duration(body)
    if duration is not None:
        seconds, display_unit = duration
        if seconds < 1:
            raise ValueError("schedule duration must be at least 1 second")
        next_run = now_dt + timedelta(seconds=seconds)
        if recurring:
            return ParsedSchedule(
                kind="interval",
                value=text,
                display=f"Every {display_unit}",
                next_run_at=next_run.isoformat(),
                interval_seconds=seconds,
            )
        return ParsedSchedule(
            kind="once",
            value=text,
            display=f"Once in {display_unit}",
            next_run_at=next_run.isoformat(),
        )

    timestamp = _parse_datetime(text, now_dt)
    if timestamp is not None:
        return ParsedSchedule(
            kind="once",
            value=text,
            display=f"Once at {timestamp.isoformat()}",
            next_run_at=timestamp.isoformat(),
        )

    cron = _parse_five_field_cron(text, now_dt)
    if cron is not None:
        return cron

    raise ValueError("unsupported schedule format")


def compute_next_run_at(schedule: dict[str, Any], *, now: datetime | None = None) -> str | None:
    now_dt = _utc(now)
    kind = str(schedule.get("kind") or "")
    if kind == "interval":
        seconds = int(schedule.get("intervalSeconds") or 0)
        if seconds < 1:
            raise ValueError("interval schedule requires intervalSeconds")
        return (now_dt + timedelta(seconds=seconds)).isoformat()
    if kind == "daily":
        return _next_matching_time(now_dt, minute=int(schedule["minute"]), hour=int(schedule["hour"])).isoformat()
    if kind == "weekly":
        weekdays = _parse_weekdays(str(schedule.get("dayOfWeek") or "*"))
        return _next_matching_time(
            now_dt,
            minute=int(schedule["minute"]),
            hour=int(schedule["hour"]),
            weekdays=weekdays,
        ).isoformat()
    if kind == "monthly":
        return _next_matching_time(
            now_dt,
            minute=int(schedule["minute"]),
            hour=int(schedule["hour"]),
            day_of_month=int(schedule["dayOfMonth"]),
        ).isoformat()
    return None


class ScheduledJobWorker:
    def __init__(
        self,
        memory_store_provider: Callable[[], Any],
        *,
        publish_job_event: ScheduledJobPublisher | None = None,
        submit_task: TaskSubmitter | None = None,
        interval_seconds: float = 1.0,
    ) -> None:
        self._memory_store_provider = memory_store_provider
        self._publish_job_event = publish_job_event
        self._submit_task = submit_task
        self._interval_seconds = max(0.25, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="amadeus-scheduler", daemon=True)
            self._thread.start()

    def stop(self, *, timeout: float | None = None) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def tick(self) -> int:
        memory_store = self._memory_store_provider()
        due_jobs = memory_store.list_due_scheduled_jobs()
        fired = 0
        for job in due_jobs:
            job_id = str(job.get("id") or "")
            if not job_id:
                continue
            claimed = memory_store.claim_scheduled_job(job_id)
            if not claimed:
                continue
            self._publish(claimed, "running")
            try:
                message = str(claimed.get("message") or "").strip()
                if not message:
                    raise ValueError("scheduled message is empty")
                session_id = str(claimed.get("sessionId") or "companion:default")
                if str(claimed.get("mode") or "message") == "agent_task":
                    task = memory_store.create_task(
                        session_id=session_id,
                        title=str(claimed.get("title") or "Scheduled task"),
                        body=message,
                        kind="scheduled_prompt",
                        source="scheduled_job",
                        worker_type="agent",
                        artifacts=[{"type": "scheduled_job", "jobId": job_id}],
                    )
                    task_id = str(task["id"])
                    if self._submit_task is not None:
                        self._submit_task(task_id)
                    completed = memory_store.complete_scheduled_job_run(
                        job_id,
                        message="Scheduled job created a background task",
                        last_task_id=task_id,
                        metadata={"taskId": task_id, "mode": "agent_task"},
                    )
                    self._publish(completed, "fired")
                else:
                    memory_store.save(session_id, "assistant", message)
                    completed = memory_store.complete_scheduled_job_run(job_id)
                    self._publish_message(session_id, message, completed)
                    self._publish(completed, "fired")
                fired += 1
            except Exception as error:
                logger.info("Scheduled job failed jobId=%s error=%s", job_id, error)
                failed = memory_store.fail_scheduled_job_run(job_id, str(error))
                self._publish(failed, "failed")
        return fired

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self.tick()
            except Exception as error:
                logger.info("Scheduled job tick failed error=%s", error)

    def _publish(self, job: dict[str, object], action: str) -> None:
        if self._publish_job_event is None:
            return
        try:
            self._publish_job_event(job, action)
        except Exception as error:
            logger.info("Scheduled job event publish failed jobId=%s action=%s error=%s", job.get("id"), action, error)

    def _publish_message(self, session_id: str, message: str, job: dict[str, object]) -> None:
        if self._publish_job_event is None:
            return
        try:
            self._publish_job_event(
                {
                    "id": job.get("id"),
                    "sessionId": session_id,
                    "message": message,
                    "job": job,
                },
                "message",
            )
        except Exception as error:
            logger.info("Scheduled message publish failed sessionId=%s error=%s", session_id, error)


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _parse_duration(value: str) -> tuple[int, str] | None:
    match = re.fullmatch(r"(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hour|hours|d|day|days)", value)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("s"):
        return amount, f"{amount} second{'s' if amount != 1 else ''}"
    if unit.startswith("m"):
        return amount * 60, f"{amount} minute{'s' if amount != 1 else ''}"
    if unit.startswith("h"):
        return amount * 60 * 60, f"{amount} hour{'s' if amount != 1 else ''}"
    return amount * 24 * 60 * 60, f"{amount} day{'s' if amount != 1 else ''}"


def _parse_datetime(value: str, now: datetime) -> datetime | None:
    text = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ][0-2]\d:[0-5]\d(?::[0-5]\d)?(?:Z|[+-]\d{2}:\d{2})?", text):
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    parsed = _utc(parsed)
    if parsed <= now:
        raise ValueError("one-shot schedule must be in the future")
    return parsed


def _parse_five_field_cron(value: str, now: datetime) -> ParsedSchedule | None:
    fields = value.split()
    if len(fields) != 5:
        return None
    minute_text, hour_text, day_text, month_text, weekday_text = fields
    if month_text != "*":
        raise ValueError("monthly names or fixed months are not supported yet")
    minute = _parse_cron_number(minute_text, 0, 59, "minute")
    hour = _parse_cron_number(hour_text, 0, 23, "hour")
    if day_text == "*" and weekday_text == "*":
        next_run = _next_matching_time(now, minute=minute, hour=hour)
        return ParsedSchedule("daily", value, f"Daily at {hour:02d}:{minute:02d}", next_run.isoformat(), hour=hour, minute=minute)
    if day_text == "*":
        weekdays = _parse_weekdays(weekday_text)
        next_run = _next_matching_time(now, minute=minute, hour=hour, weekdays=weekdays)
        return ParsedSchedule("weekly", value, f"Weekly at {hour:02d}:{minute:02d}", next_run.isoformat(), hour=hour, minute=minute, day_of_week=weekday_text)
    if weekday_text == "*":
        day_of_month = _parse_cron_number(day_text, 1, 31, "day of month")
        next_run = _next_matching_time(now, minute=minute, hour=hour, day_of_month=day_of_month)
        return ParsedSchedule("monthly", value, f"Monthly on day {day_of_month} at {hour:02d}:{minute:02d}", next_run.isoformat(), hour=hour, minute=minute, day_of_month=day_of_month)
    raise ValueError("combined day-of-month and day-of-week schedules are not supported")


def _parse_cron_number(value: str, minimum: int, maximum: int, label: str) -> int:
    if not re.fullmatch(r"\d{1,2}", value):
        raise ValueError(f"{label} must be a single number")
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{label} out of range")
    return parsed


def _parse_weekdays(value: str) -> set[int]:
    if value == "*":
        return set(range(7))
    days: set[int] = set()
    for item in value.split(","):
        if not re.fullmatch(r"\d", item):
            raise ValueError("weekday must be * or comma-separated 0-6 values")
        day = int(item)
        if day < 0 or day > 6:
            raise ValueError("weekday out of range")
        days.add(day)
    if not days:
        raise ValueError("weekday list cannot be empty")
    return days


def _next_matching_time(
    now: datetime,
    *,
    minute: int,
    hour: int,
    weekdays: set[int] | None = None,
    day_of_month: int | None = None,
) -> datetime:
    base = now.replace(second=0, microsecond=0)
    candidate = base.replace(hour=hour, minute=minute)
    if candidate <= now:
        candidate += timedelta(days=1)
    for _ in range(370):
        cron_weekday = (candidate.weekday() + 1) % 7
        if weekdays is not None and cron_weekday not in weekdays:
            candidate += timedelta(days=1)
            candidate = candidate.replace(hour=hour, minute=minute)
            continue
        if day_of_month is not None and candidate.day != day_of_month:
            candidate += timedelta(days=1)
            candidate = candidate.replace(hour=hour, minute=minute)
            continue
        return candidate
    raise ValueError("could not compute next run")
