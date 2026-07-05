from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.agent import AgentEvent, PermissionRequest
from amadeus.memory import MessageMemoryStore
from amadeus.workers import TaskCallable, TaskWorker


class SuccessfulRuntime:
    def run_turn(self, session_id: str, user_text: str, request_permission: Callable[[PermissionRequest], bool]):
        yield AgentEvent("agent.turn.started", {"sessionId": session_id, "turnId": "turn-success", "startedAt": "now"})
        yield AgentEvent("assistant.message", {"text": f"completed: {user_text}"})


class FailingRuntime:
    def run_turn(self, session_id: str, user_text: str, request_permission: Callable[[PermissionRequest], bool]):
        yield AgentEvent("agent.turn.started", {"sessionId": session_id, "turnId": "turn-fail", "startedAt": "now"})
        yield AgentEvent("error", {"code": "provider_error", "message": "provider failed"})


class FlakyRuntime:
    def __init__(self) -> None:
        self.calls = 0

    def run_turn(self, session_id: str, user_text: str, request_permission: Callable[[PermissionRequest], bool]):
        self.calls += 1
        yield AgentEvent("agent.turn.started", {"sessionId": session_id, "turnId": f"turn-{self.calls}", "startedAt": "now"})
        if self.calls == 1:
            yield AgentEvent("error", {"code": "provider_error", "message": "temporary provider failure"})
            return
        yield AgentEvent("assistant.message", {"text": "retry completed"})


class CancellableRuntime:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()

    def run_turn(self, session_id: str, user_text: str, request_permission: Callable[[PermissionRequest], bool]):
        yield AgentEvent("agent.turn.started", {"sessionId": session_id, "turnId": "turn-cancel", "startedAt": "now"})
        self.started.set()
        self.cancelled.wait(timeout=2)
        yield AgentEvent("agent.turn.cancelled", {"sessionId": session_id, "turnId": "turn-cancel", "phase": "task_worker"})

    def cancel_turn(self, session_id: str, turn_id: str | None = None) -> dict[str, object]:
        self.cancelled.set()
        return {"sessionId": session_id, "turnId": turn_id, "cancelled": True, "reason": "cancel_requested"}


class ImmediateTaskRunner:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.shutdown_called = False

    def submit(self, task_id: str, run_task: TaskCallable) -> None:
        self.submitted.append(task_id)
        run_task(task_id)

    def shutdown(self, *, wait: bool = True) -> None:
        self.shutdown_called = True


class TaskWorkerTests(unittest.TestCase):
    def test_worker_uses_injected_task_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            runtime = SuccessfulRuntime()
            runner = ImmediateTaskRunner()
            worker = TaskWorker(lambda: memory, lambda: runtime, runner=runner)
            task = memory.create_task(session_id="session-1", title="Run inline")

            worker.submit(str(task["id"]))
            worker.shutdown()
            finished = memory.get_task(str(task["id"]))

        self.assertEqual(runner.submitted, [str(task["id"])])
        self.assertTrue(runner.shutdown_called)
        self.assertIsNotNone(finished)
        self.assertEqual(finished["status"], "succeeded")

    def test_worker_marks_task_succeeded_with_assistant_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            runtime = SuccessfulRuntime()
            published: list[tuple[str, str]] = []
            worker = TaskWorker(
                lambda: memory,
                lambda: runtime,
                max_workers=1,
                publish_task_event=lambda task, action: published.append((str(task["status"]), action)),
            )
            task = memory.create_task(session_id="session-1", title="Summarize", body="Use the body.")

            worker.submit(str(task["id"]))
            finished = self.wait_for_status(memory, str(task["id"]), "succeeded")
            worker.shutdown()
            events = memory.list_task_events(str(task["id"]))

        self.assertEqual(finished["result"], "completed: Summarize\n\nUse the body.")
        self.assertEqual([event["type"] for event in events], ["created", "running", "succeeded"])
        self.assertEqual(published, [("running", "running"), ("succeeded", "succeeded")])

    def test_worker_syncs_linked_plan_item_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save_session_plan(
                "session-1",
                [{"id": "draft", "content": "Draft the plan", "status": "pending"}],
            )
            runtime = SuccessfulRuntime()
            worker = TaskWorker(lambda: memory, lambda: runtime, max_workers=1)
            task = memory.create_task(
                session_id="session-1",
                title="Draft the plan",
                plan_item_id="draft",
                source="plan",
            )

            worker.submit(str(task["id"]))
            self.wait_for_status(memory, str(task["id"]), "succeeded")
            worker.shutdown()
            plan = memory.load_session_plan("session-1")

        self.assertEqual(plan["items"][0]["status"], "completed")

    def test_worker_marks_task_failed_on_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            runtime = FailingRuntime()
            published: list[tuple[str, str]] = []
            worker = TaskWorker(
                lambda: memory,
                lambda: runtime,
                max_workers=1,
                publish_task_event=lambda task, action: published.append((str(task["status"]), action)),
            )
            task = memory.create_task(session_id="session-1", title="Fail", max_attempts=1)

            worker.submit(str(task["id"]))
            finished = self.wait_for_status(memory, str(task["id"]), "failed")
            worker.shutdown()
            events = memory.list_task_events(str(task["id"]))

        self.assertEqual(finished["error"], "provider failed")
        self.assertEqual([event["type"] for event in events], ["created", "running", "failed"])
        self.assertEqual(published, [("running", "running"), ("failed", "failed")])

    def test_worker_retries_transient_failure_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            runtime = FlakyRuntime()
            published: list[tuple[str, str]] = []
            worker = TaskWorker(
                lambda: memory,
                lambda: runtime,
                max_workers=1,
                retry_base_delay_seconds=0.01,
                retry_max_delay_seconds=0.01,
                publish_task_event=lambda task, action: published.append((str(task["status"]), action)),
            )
            task = memory.create_task(session_id="session-1", title="Retry")

            worker.submit(str(task["id"]))
            finished = self.wait_for_status(memory, str(task["id"]), "succeeded")
            worker.shutdown()
            events = memory.list_task_events(str(task["id"]))

        self.assertEqual(finished["result"], "retry completed")
        self.assertEqual(finished["attemptCount"], 2)
        self.assertEqual([event["type"] for event in events], ["created", "running", "retry_scheduled", "running", "succeeded"])
        self.assertIn(("queued", "retry_scheduled"), published)
        self.assertIn(("succeeded", "succeeded"), published)

    def test_worker_recovers_stale_running_task_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            runtime = SuccessfulRuntime()
            task = memory.create_task(session_id="session-1", title="Recover")
            memory.start_task(str(task["id"]), claim_lock="stale-worker")
            old_heartbeat = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
            with memory.connect() as connection:
                connection.execute(
                    "UPDATE tasks SET last_heartbeat = ?, updated_at = ? WHERE id = ?",
                    (old_heartbeat, old_heartbeat, str(task["id"])),
                )
            published: list[tuple[str, str]] = []
            worker = TaskWorker(
                lambda: memory,
                lambda: runtime,
                max_workers=1,
                stale_after_seconds=1,
                publish_task_event=lambda task, action: published.append((str(task["status"]), action)),
            )

            recovered = worker.recover()
            finished = self.wait_for_status(memory, str(task["id"]), "succeeded")
            worker.shutdown()
            events = memory.list_task_events(str(task["id"]))

        self.assertEqual([task["id"] for task in recovered], [task["id"]])
        self.assertEqual(finished["attemptCount"], 2)
        self.assertEqual([event["type"] for event in events], ["created", "running", "recovered", "running", "succeeded"])
        self.assertIn(("queued", "recovered"), published)

    def test_worker_cancel_marks_running_task_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            runtime = CancellableRuntime()
            worker = TaskWorker(lambda: memory, lambda: runtime, max_workers=1)
            task = memory.create_task(session_id="session-1", title="Cancel")

            worker.submit(str(task["id"]))
            self.assertTrue(runtime.started.wait(timeout=2))
            cancelled = worker.cancel(str(task["id"]), reason="User cancelled")
            finished = self.wait_for_status(memory, str(task["id"]), "cancelled")
            worker.shutdown()
            events = memory.list_task_events(str(task["id"]))

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(finished["error"], "User cancelled")
        self.assertEqual([event["type"] for event in events], ["created", "running", "cancelled"])

    @staticmethod
    def wait_for_status(memory: MessageMemoryStore, task_id: str, status: str) -> dict[str, object]:
        deadline = time.time() + 2
        while time.time() < deadline:
            task = memory.get_task(task_id)
            if task and task["status"] == status:
                return task
            time.sleep(0.01)
        task = memory.get_task(task_id)
        raise AssertionError(f"task {task_id} did not reach {status}; last={task}")


if __name__ == "__main__":
    unittest.main()
