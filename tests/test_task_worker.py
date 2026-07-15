from __future__ import annotations

import tempfile
import threading
import time
import unittest
import os
import contextlib
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.agent import AgentEvent, PermissionRequest
from amadeus.memory import MessageMemoryStore
from amadeus.task_worker_entrypoint import main as task_worker_entrypoint_main, run_task_once
from amadeus.workers import InProcessTaskRunner, ProcessTaskRunner, SubprocessTaskRunner, SynchronousTaskRunner, TaskCallable, TaskWorker, build_task_runner, build_worker_context


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


class SlowRuntime:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def run_turn(self, session_id: str, user_text: str, request_permission: Callable[[PermissionRequest], bool]):
        yield AgentEvent("agent.turn.started", {"sessionId": session_id, "turnId": "turn-slow", "startedAt": "now"})
        self.started.set()
        self.release.wait(timeout=2)
        yield AgentEvent("assistant.message", {"text": "slow completed"})


class ImmediateTaskRunner:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.shutdown_called = False

    def submit(self, task_id: str, run_task: TaskCallable) -> None:
        self.submitted.append(task_id)
        run_task(task_id)

    def shutdown(self, *, wait: bool = True) -> None:
        self.shutdown_called = True


class FakeProcess:
    def __init__(self, return_code: int = 0) -> None:
        self.pid = 12345
        self.return_code = return_code
        self.terminated = False

    def wait(self) -> int:
        return self.return_code

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True


class TaskWorkerTests(unittest.TestCase):
    def test_build_task_runner_selects_supported_runner_kinds(self) -> None:
        synchronous = build_task_runner("sync", max_workers=1)
        self.assertIsInstance(synchronous, SynchronousTaskRunner)
        synchronous.shutdown()

        in_process = build_task_runner("in_process", max_workers=1)
        self.assertIsInstance(in_process, InProcessTaskRunner)
        in_process.shutdown()

        if hasattr(os, "fork"):
            process = build_task_runner("process", max_workers=1)
            self.assertIsInstance(process, ProcessTaskRunner)
            process.shutdown()

        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess_runner = build_task_runner("subprocess", max_workers=1, database_path=Path(tmpdir) / "amadeus.sqlite")
            self.assertIsInstance(subprocess_runner, SubprocessTaskRunner)
            subprocess_runner.shutdown()

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

    def test_subprocess_task_runner_launches_entrypoint_with_task_env(self) -> None:
        launches: list[dict[str, object]] = []

        def fake_process_factory(command: list[str], **kwargs: object) -> FakeProcess:
            launches.append({"command": command, **kwargs})
            return FakeProcess()

        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "amadeus.sqlite"
            workspace = Path(tmpdir) / "workspace"
            memory = MessageMemoryStore(database)
            task = memory.create_task(session_id="session-1", title="Run subprocess", worker_profile="researcher")
            runner = SubprocessTaskRunner(
                database_path=database,
                workspace_path=workspace,
                python_executable="/usr/bin/python-test",
                process_factory=fake_process_factory,
            )

            runner.submit(str(task["id"]), lambda task_id: None)
            runner.shutdown()

        self.assertEqual(len(launches), 1)
        command = launches[0]["command"]
        self.assertEqual(command[:3], ["/usr/bin/python-test", "-m", "amadeus.task_worker_entrypoint"])
        self.assertIn("--task-id", command)
        self.assertIn(str(task["id"]), command)
        self.assertIn("--run-id", command)
        env = launches[0]["env"]
        self.assertIsInstance(env, dict)
        self.assertEqual(env["AMADEUS_TASK_ID"], str(task["id"]))
        self.assertEqual(env["AMADEUS_TASK_RUN_ID"], command[command.index("--run-id") + 1])
        self.assertEqual(env["AMADEUS_MEMORY_DB"], str(database))
        self.assertEqual(env["AMADEUS_TASK_RUNNER"], "sync")
        self.assertEqual(env["AMADEUS_WORKER_PROFILE"], "researcher")
        self.assertEqual(env["AMADEUS_WORKSPACE"], str(workspace))
        self.assertIn(str(Path(__file__).resolve().parents[1] / "packages"), env["PYTHONPATH"].split(os.pathsep))
        self.assertEqual(launches[0]["cwd"], str(workspace))

    def test_subprocess_task_runner_requeues_running_task_after_nonzero_exit(self) -> None:
        launches: list[dict[str, object]] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "amadeus.sqlite"
            memory = MessageMemoryStore(database)
            task = memory.create_task(session_id="session-1", title="Recover subprocess", max_attempts=2)
            running = memory.start_task(str(task["id"]), claim_lock="child-claim", lease_owner="child-worker", runner_kind="process_entrypoint")
            self.assertEqual(running["status"], "running")

            def fake_process_factory(command: list[str], **kwargs: object) -> FakeProcess:
                launches.append({"command": command, **kwargs})
                env = kwargs["env"]
                self.assertIsInstance(env, dict)
                memory.create_task_attempt(str(task["id"]), run_id=str(env["AMADEUS_TASK_RUN_ID"]), worker_id="child-worker")
                return FakeProcess(return_code=1)

            runner = SubprocessTaskRunner(database_path=database, process_factory=fake_process_factory)
            runner.submit(str(task["id"]), lambda task_id: None)
            runner.shutdown()

            recovered = memory.get_task(str(task["id"]))
            attempts = memory.list_task_attempts(str(task["id"]))
            events = memory.list_task_events(str(task["id"]))

        self.assertEqual(recovered["status"], "queued")
        self.assertIn("Task subprocess exited with code 1", str(recovered["error"]))
        self.assertEqual(attempts[0]["status"], "abandoned")
        self.assertIn("Task subprocess exited with code 1", str(attempts[0]["error"]))
        self.assertEqual(attempts[0]["checkpoint"]["status"], "abandoned")
        self.assertIn("retry_scheduled", [event["type"] for event in events])

    def test_subprocess_task_runner_fails_running_task_after_final_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "amadeus.sqlite"
            memory = MessageMemoryStore(database)
            task = memory.create_task(session_id="session-1", title="Fail subprocess", max_attempts=1)
            memory.start_task(str(task["id"]), claim_lock="child-claim", lease_owner="child-worker", runner_kind="process_entrypoint")

            def fake_process_factory(command: list[str], **kwargs: object) -> FakeProcess:
                env = kwargs["env"]
                self.assertIsInstance(env, dict)
                memory.create_task_attempt(str(task["id"]), run_id=str(env["AMADEUS_TASK_RUN_ID"]), worker_id="child-worker")
                return FakeProcess(return_code=1)

            runner = SubprocessTaskRunner(database_path=database, process_factory=fake_process_factory)
            runner.submit(str(task["id"]), lambda task_id: None)
            runner.shutdown()

            failed = memory.get_task(str(task["id"]))
            attempts = memory.list_task_attempts(str(task["id"]))

        self.assertEqual(failed["status"], "failed")
        self.assertIn("Task subprocess exited with code 1", str(failed["error"]))
        self.assertEqual(attempts[0]["status"], "abandoned")
        self.assertEqual(attempts[0]["checkpoint"]["reason"], "subprocess_exited")

    def test_task_worker_entrypoint_runs_one_task_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            runtime = SuccessfulRuntime()
            task = memory.create_task(session_id="session-1", title="Run entrypoint")

            finished = run_task_once(memory_store=memory, agent_runtime=runtime, task_id=str(task["id"]), run_id="entry-run-1")
            attempts = memory.list_task_attempts(str(task["id"]))

        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(finished["runnerKind"], "process_entrypoint")
        self.assertEqual(attempts[0]["runId"], "entry-run-1")
        self.assertEqual(attempts[0]["workerId"].split("-")[0], "process_entrypoint")

    def test_task_worker_entrypoint_requires_task_id(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                task_worker_entrypoint_main(["--database", "memory.sqlite"])

        self.assertNotEqual(error.exception.code, 0)

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

        self.assertIn("<task>", str(finished["result"]))
        self.assertIn("title: Summarize", str(finished["result"]))
        self.assertIn("Use the body.", str(finished["result"]))
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

    def test_worker_blocks_review_required_task_after_successful_run(self) -> None:
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
            task = memory.create_task(session_id="session-1", title="Review", review_required=True)

            worker.submit(str(task["id"]))
            finished = self.wait_for_status(memory, str(task["id"]), "blocked")
            worker.shutdown()
            events = memory.list_task_events(str(task["id"]))

        self.assertIn("title: Review", str(finished["result"]))
        self.assertEqual(finished["blockedReason"], "Review required before marking this task complete.")
        self.assertEqual([event["type"] for event in events], ["created", "running", "blocked"])
        self.assertIn(("blocked", "blocked"), published)

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
                    "UPDATE tasks SET lease_expires_at = ?, last_heartbeat = ?, updated_at = ? WHERE id = ?",
                    (old_heartbeat, old_heartbeat, old_heartbeat, str(task["id"])),
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

    def test_worker_does_not_recover_running_task_with_active_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            runtime = SlowRuntime()
            worker = TaskWorker(lambda: memory, lambda: runtime, max_workers=1, stale_after_seconds=1, lease_seconds=2)
            task = memory.create_task(session_id="session-1", title="Lease protected")

            worker.submit(str(task["id"]))
            self.assertTrue(runtime.started.wait(timeout=2))
            recovered = worker.recover()
            running = memory.get_task(str(task["id"]))
            runtime.release.set()
            finished = self.wait_for_status(memory, str(task["id"]), "succeeded")
            worker.shutdown()

        self.assertEqual(recovered, [])
        self.assertIsNotNone(running)
        self.assertEqual(running["status"], "running")
        self.assertIsNotNone(running["leaseOwner"])
        self.assertIsNotNone(running["leaseExpiresAt"])
        self.assertEqual(finished["status"], "succeeded")

    def test_recover_uses_expired_lease_before_heartbeat_age(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            runtime = SuccessfulRuntime()
            task = memory.create_task(session_id="session-1", title="Expired lease")
            memory.start_task(
                str(task["id"]),
                claim_lock="lease-worker",
                lease_owner="worker-a",
                lease_seconds=30,
                runner_kind="in_process",
            )
            now = datetime.now(timezone.utc)
            expired = (now - timedelta(seconds=1)).isoformat()
            fresh = now.isoformat()
            with memory.connect() as connection:
                connection.execute(
                    "UPDATE tasks SET lease_expires_at = ?, last_heartbeat = ?, updated_at = ? WHERE id = ?",
                    (expired, fresh, fresh, str(task["id"])),
                )
            worker = TaskWorker(lambda: memory, lambda: runtime, max_workers=1, stale_after_seconds=300)

            recovered = worker.recover()
            finished = self.wait_for_status(memory, str(task["id"]), "succeeded")
            worker.shutdown()

        self.assertEqual([task["id"] for task in recovered], [task["id"]])
        self.assertEqual(finished["status"], "succeeded")
        self.assertIsNone(finished["leaseOwner"])
        self.assertIsNone(finished["leaseExpiresAt"])

    def test_task_artifacts_are_normalized_to_typed_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            task = memory.create_task(
                session_id="session-1",
                title="Artifacts",
                artifacts=[
                    {"type": "file", "title": "Report", "path": "/tmp/report.md", "extra": {"ignored": True}},
                    {"type": "unknown", "content": {"nested": True}},
                ],
            )

        self.assertEqual(task["artifacts"][0]["type"], "file")
        self.assertEqual(task["artifacts"][0]["title"], "Report")
        self.assertEqual(task["artifacts"][0]["path"], "/tmp/report.md")
        self.assertNotIn("extra", task["artifacts"][0])
        self.assertEqual(task["artifacts"][1]["type"], "summary")
        self.assertEqual(task["artifacts"][1]["content"], "{\"nested\": true}")

    def test_worker_context_includes_dependency_artifacts_and_attempt_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            root = memory.create_task(session_id="session-1", title="Root")
            dependency = memory.create_task(session_id="session-1", title="Dependency", root_task_id=str(root["id"]))
            child = memory.create_task(
                session_id="session-1",
                title="Child",
                body="Use dependency findings.",
                root_task_id=str(root["id"]),
                parent_task_id=str(root["id"]),
                acceptance_criteria=["Summarize the dependency"],
                context_hints={"workspace": "/tmp/project"},
                allowed_toolsets=["read"],
                disallowed_tools=["terminal"],
            )
            memory.add_task_edge(from_task_id=str(dependency["id"]), to_task_id=str(child["id"]))
            attempt = memory.create_task_attempt(str(child["id"]), worker_id="worker-old")
            memory.finish_task_attempt(str(attempt["id"]), status="failed", error="missing dependency summary")
            memory.add_task_artifact(
                str(dependency["id"]),
                {"type": "summary", "title": "Dependency summary", "content": "Dependency completed."},
            )

            context = build_worker_context(memory, str(child["id"]))
            prompt = context.to_prompt()

        self.assertIn("title: Child", prompt)
        self.assertIn("Summarize the dependency", prompt)
        self.assertIn("<dependency-tasks>", prompt)
        self.assertIn("title=Dependency", prompt)
        self.assertIn("<dependency-artifacts>", prompt)
        self.assertIn("Dependency completed.", prompt)
        self.assertIn("<previous-attempts>", prompt)
        self.assertIn("missing dependency summary", prompt)
        self.assertIn('"workspace": "/tmp/project"', prompt)
        self.assertIn('"read"', prompt)
        self.assertIn('"terminal"', prompt)

    def test_worker_records_attempt_and_result_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            runtime = SuccessfulRuntime()
            runner = ImmediateTaskRunner()
            worker = TaskWorker(lambda: memory, lambda: runtime, runner=runner)
            task = memory.create_task(session_id="session-1", title="Attempted")

            worker.submit(str(task["id"]))
            worker.shutdown()
            attempts = memory.list_task_attempts(str(task["id"]))
            artifacts = memory.list_task_artifacts(str(task["id"]))

        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "succeeded")
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["type"], "summary")
        self.assertIn("title: Attempted", str(artifacts[0]["content"]))

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
