from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.memory import MessageMemoryStore
from amadeus.task_supervisor import DurableTaskSupervisor


class DurableTaskSupervisorTests(unittest.TestCase):
    def test_default_poll_and_lease_windows_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            supervisor = DurableTaskSupervisor(
                database_path=Path(tmpdir) / "amadeus.sqlite",
            )

            poll_interval = supervisor.poll_interval_seconds
            lease_seconds = supervisor.lease_seconds
            supervisor.close(detach_children=True)

        self.assertEqual(poll_interval, 1.0)
        self.assertEqual(lease_seconds, 45.0)

    def test_supervisor_is_single_primary_and_adopts_live_process_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "amadeus.sqlite"
            memory = MessageMemoryStore(database)
            task = memory.create_task(session_id="session-1", title="Adopt live process")
            memory.start_task(
                str(task["id"]),
                claim_lock="worker-claim",
                lease_owner="worker-one",
                lease_seconds=30,
                runner_kind="process_entrypoint",
            )
            memory.register_task_process(
                task_id=str(task["id"]),
                run_id="run-live-1",
                supervisor_id="supervisor-old",
                pid=os.getpid(),
            )
            primary = DurableTaskSupervisor(
                database_path=database,
                owner_id="supervisor-primary",
                poll_interval_seconds=0.1,
                lease_seconds=5,
            )
            standby = DurableTaskSupervisor(
                database_path=database,
                owner_id="supervisor-standby",
                poll_interval_seconds=0.1,
                lease_seconds=5,
            )

            try:
                self.assertTrue(primary.acquire())
                self.assertFalse(standby.acquire())
                status = primary.tick()
                process_record = memory.list_task_processes(
                    task_id=str(task["id"]),
                )[0]
                events = memory.list_task_events(str(task["id"]))
                lease = memory.get_supervisor_lease("task-supervisor")
            finally:
                primary.close(detach_children=True)

            self.assertTrue(standby.acquire())
            standby.close(detach_children=True)

        self.assertTrue(status["leaseAcquired"])
        self.assertEqual(status["reconciliation"]["adopted"], 1)
        self.assertEqual(process_record["status"], "adopted")
        self.assertEqual(process_record["supervisorId"], "supervisor-primary")
        self.assertEqual(lease["ownerId"], "supervisor-primary")
        self.assertIn("subprocess_adopted", [event["type"] for event in events])

    def test_run_once_releases_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "amadeus.sqlite"
            supervisor = DurableTaskSupervisor(
                database_path=database,
                owner_id="supervisor-once",
                poll_interval_seconds=0.1,
                lease_seconds=5,
            )

            return_code = supervisor.run(once=True)
            lease = MessageMemoryStore(database).get_supervisor_lease("task-supervisor")

        self.assertEqual(return_code, 0)
        self.assertIsNone(lease)

    def test_tick_does_not_reconcile_or_dispatch_after_losing_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "amadeus.sqlite"
            memory = MessageMemoryStore(database)
            supervisor = DurableTaskSupervisor(
                database_path=database,
                owner_id="supervisor-old",
                poll_interval_seconds=0.1,
                lease_seconds=5,
            )
            self.assertTrue(supervisor.acquire())
            with memory.connect() as connection:
                connection.execute(
                    "UPDATE supervisor_leases SET owner_id = ? WHERE name = ?",
                    ("supervisor-new", "task-supervisor"),
                )

            with (
                mock.patch.object(
                    supervisor.runner,
                    "reconcile_durable_processes",
                ) as reconcile,
                mock.patch.object(supervisor.worker, "recover") as recover,
            ):
                with self.assertRaisesRegex(RuntimeError, "lost its durable lease"):
                    supervisor.tick()
            supervisor.close(detach_children=True)

        reconcile.assert_not_called()
        recover.assert_not_called()

    def test_run_heartbeats_lease_while_dispatch_tick_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database = Path(tmpdir) / "amadeus.sqlite"
            memory = MessageMemoryStore(database)
            supervisor = DurableTaskSupervisor(
                database_path=database,
                owner_id="supervisor-blocked",
                poll_interval_seconds=0.1,
                lease_seconds=1,
            )
            entered_recover = threading.Event()
            release_recover = threading.Event()
            return_codes: list[int] = []

            def blocked_recover() -> list[dict[str, object]]:
                entered_recover.set()
                release_recover.wait(timeout=3)
                return []

            with (
                mock.patch.object(
                    supervisor.runner,
                    "reconcile_durable_processes",
                    return_value={
                        "observed": 0,
                        "adopted": 0,
                        "lost": 0,
                        "terminated": 0,
                    },
                ),
                mock.patch.object(
                    supervisor.worker,
                    "recover",
                    side_effect=blocked_recover,
                ),
            ):
                thread = threading.Thread(
                    target=lambda: return_codes.append(supervisor.run(once=True)),
                    daemon=True,
                )
                thread.start()
                self.assertTrue(entered_recover.wait(timeout=2))
                time.sleep(1.1)
                standby = memory.acquire_supervisor_lease(
                    "task-supervisor",
                    owner_id="supervisor-standby",
                    pid=os.getpid(),
                    lease_seconds=1,
                )
                release_recover.set()
                thread.join(timeout=3)

        self.assertFalse(standby["acquired"])
        self.assertFalse(thread.is_alive())
        self.assertEqual(return_codes, [0])


if __name__ == "__main__":
    unittest.main()
