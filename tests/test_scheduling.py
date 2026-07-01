from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.memory import MessageMemoryStore
from amadeus.scheduling import ScheduledJobWorker, compute_next_run_at, parse_schedule
from amadeus.tool_runtime import ToolContext
from amadeus.tools.scheduled_jobs import schedule_message


class SchedulingTests(unittest.TestCase):
    def test_parse_schedule_supports_once_interval_and_cron_shapes(self) -> None:
        now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)

        once = parse_schedule("10s", now=now)
        interval = parse_schedule("every 10s", now=now)
        daily = parse_schedule("5 10 * * *", now=now)
        weekly = parse_schedule("0 8 * * 1,3", now=now)
        monthly = parse_schedule("30 7 15 * *", now=now)

        self.assertEqual(once.kind, "once")
        self.assertEqual(interval.kind, "interval")
        self.assertEqual(interval.interval_seconds, 10)
        self.assertEqual(daily.kind, "daily")
        self.assertEqual(weekly.kind, "weekly")
        self.assertEqual(monthly.kind, "monthly")
        self.assertEqual(compute_next_run_at(interval.to_payload(), now=now), "2026-07-02T09:00:10+00:00")

    def test_scheduled_jobs_can_repeat_pause_resume_cancel_and_log_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            job = memory.create_scheduled_job(
                session_id="session-1",
                title="Say hi",
                message="hi",
                schedule="every 10s",
                repeat_count=2,
            )
            paused = memory.pause_scheduled_job(str(job["id"]))
            resumed = memory.resume_scheduled_job(str(job["id"]))
            claimed = memory.claim_scheduled_job(str(job["id"]))
            self.assertEqual(claimed["status"], "scheduled")

            past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            with memory.connect() as connection:
                connection.execute("UPDATE scheduled_jobs SET next_run_at = ? WHERE id = ?", (past, job["id"]))

            claimed = memory.claim_scheduled_job(str(job["id"]))
            first = memory.complete_scheduled_job_run(str(job["id"]))
            with memory.connect() as connection:
                connection.execute("UPDATE scheduled_jobs SET status = 'running' WHERE id = ?", (job["id"],))
            second = memory.complete_scheduled_job_run(str(job["id"]))
            cancelled = memory.cancel_scheduled_job(str(job["id"]), reason="done")
            events = memory.list_scheduled_job_events(str(job["id"]))

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(resumed["status"], "scheduled")
        self.assertEqual(claimed["status"], "running")
        self.assertEqual(first["status"], "scheduled")
        self.assertEqual(first["completedRuns"], 1)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["completedRuns"], 2)
        self.assertEqual(cancelled["status"], "completed")
        self.assertEqual([event["type"] for event in events], ["created", "paused", "resumed", "running", "scheduled", "completed"])

    def test_worker_delivers_due_message_to_memory_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            job = memory.create_scheduled_job(
                session_id="session-1",
                title="Check in",
                message="我在",
                schedule="every 10s",
                repeat_count=1,
            )
            past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            with memory.connect() as connection:
                connection.execute("UPDATE scheduled_jobs SET next_run_at = ? WHERE id = ?", (past, job["id"]))

            published: list[tuple[str, dict[str, object]]] = []
            worker = ScheduledJobWorker(lambda: memory, publish_job_event=lambda payload, action: published.append((action, payload)))

            fired = worker.tick()
            messages = memory.load("session-1")
            updated = memory.get_scheduled_job(str(job["id"]))

        self.assertEqual(fired, 1)
        self.assertEqual(messages[-1], {"role": "assistant", "content": "我在"})
        self.assertEqual(updated["status"], "completed")
        self.assertEqual([item[0] for item in published], ["running", "message", "fired"])

    def test_schedule_message_tool_creates_and_lists_session_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            context = ToolContext(session_id="session-1", memory_store=memory)

            created = schedule_message(
                {
                    "action": "create",
                    "title": "Four pings",
                    "message": "我在",
                    "schedule": "every 10s",
                    "repeatCount": 4,
                },
                context,
            )
            listed = schedule_message({"action": "list"}, context)

        self.assertEqual(created["action"], "created")
        self.assertEqual(created["job"]["repeatCount"], 4)
        self.assertEqual(listed["summary"]["scheduled"], 1)


if __name__ == "__main__":
    unittest.main()
