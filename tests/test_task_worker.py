from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Callable

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.agent import AgentEvent, PermissionRequest
from amadeus.memory import MessageMemoryStore
from amadeus.workers import TaskWorker


class SuccessfulRuntime:
    def run_turn(self, session_id: str, user_text: str, request_permission: Callable[[PermissionRequest], bool]):
        yield AgentEvent("agent.turn.started", {"sessionId": session_id, "turnId": "turn-success", "startedAt": "now"})
        yield AgentEvent("assistant.message", {"text": f"completed: {user_text}"})


class FailingRuntime:
    def run_turn(self, session_id: str, user_text: str, request_permission: Callable[[PermissionRequest], bool]):
        yield AgentEvent("agent.turn.started", {"sessionId": session_id, "turnId": "turn-fail", "startedAt": "now"})
        yield AgentEvent("error", {"code": "provider_error", "message": "provider failed"})


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


class TaskWorkerTests(unittest.TestCase):
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
            task = memory.create_task(session_id="session-1", title="Fail")

            worker.submit(str(task["id"]))
            finished = self.wait_for_status(memory, str(task["id"]), "failed")
            worker.shutdown()
            events = memory.list_task_events(str(task["id"]))

        self.assertEqual(finished["error"], "provider failed")
        self.assertEqual([event["type"] for event in events], ["created", "running", "failed"])
        self.assertEqual(published, [("running", "running"), ("failed", "failed")])

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
