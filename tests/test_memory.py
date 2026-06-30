from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.memory import MessageMemoryStore


class MessageMemoryStoreTests(unittest.TestCase):
    def test_role_workspace_path_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            workspace_path = str(Path(tmpdir) / "workspace")
            role = memory.create_role("Workspace Role", workspace_path=workspace_path)
            session = memory.create_session(str(role["id"]))
            updated = memory.update_role(str(role["id"]), workspace_path="")

            self.assertEqual(role["workspacePath"], workspace_path)
            self.assertEqual(memory.role_workspace_path_for_session(str(session["id"])), "")
            self.assertEqual(updated["workspacePath"], "")

    def test_default_workspace_path_applies_to_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            default_workspace = str(Path(tmpdir) / "project")
            memory = MessageMemoryStore(
                Path(tmpdir) / "amadeus.sqlite",
                default_workspace_path=default_workspace,
            )
            default_role = next(role for role in memory.list_roles() if role["id"] == "amadeus")
            role = memory.create_role("Default Workspace Role")
            session = memory.create_session(str(role["id"]))
            updated = memory.update_role(str(role["id"]), workspace_path="")

            self.assertEqual(default_role["workspacePath"], default_workspace)
            self.assertEqual(role["workspacePath"], default_workspace)
            self.assertEqual(memory.role_workspace_path_for_session(str(session["id"])), default_workspace)
            self.assertEqual(updated["workspacePath"], default_workspace)

    def test_conversation_summary_persists_and_loads_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "amadeus.sqlite"
            memory = MessageMemoryStore(database_path)
            memory.save("session-1", "user", "first message")
            first = memory.save_conversation_summary("session-1", "Initial summary")
            second = memory.save_conversation_summary(
                "session-1",
                "Updated summary",
                summarized_message_count=7,
                covered_message_count=5,
                source_message_start_id=2,
                source_message_end_id=9,
                covered_through_message_id=9,
                model="test-model",
            )

            reloaded = MessageMemoryStore(database_path).load_conversation_summary("session-1")

        self.assertIsNotNone(reloaded)
        assert reloaded is not None
        self.assertEqual(first["summaryId"] + 1, second["summaryId"])
        self.assertEqual(reloaded["summaryId"], second["summaryId"])
        self.assertEqual(reloaded["sessionId"], "session-1")
        self.assertEqual(reloaded["content"], "Updated summary")
        self.assertEqual(reloaded["charCount"], len("Updated summary"))
        self.assertEqual(reloaded["summarizedMessageCount"], 7)
        self.assertEqual(reloaded["coveredMessageCount"], 5)
        self.assertEqual(reloaded["sourceMessageStartId"], 2)
        self.assertEqual(reloaded["sourceMessageEndId"], 9)
        self.assertEqual(reloaded["coveredThroughMessageId"], 9)
        self.assertEqual(reloaded["model"], "test-model")
        self.assertIsInstance(reloaded["createdAt"], str)
        self.assertIsInstance(reloaded["updatedAt"], str)

    def test_conversation_summary_defaults_to_current_message_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save("session-1", "user", "hello")
            memory.save("session-1", "assistant", "hi")

            summary = memory.save_conversation_summary("session-1", "Two-message summary")

        self.assertEqual(summary["summarizedMessageCount"], 2)
        self.assertEqual(summary["coveredMessageCount"], 2)

    def test_load_can_filter_after_message_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            first_id = memory.save("session-1", "user", "old")
            memory.save("session-1", "assistant", "new")

            messages = memory.load("session-1", after_message_id=first_id)

        self.assertEqual(messages, [{"role": "assistant", "content": "new"}])

    def test_reset_deletes_conversation_summary_for_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save_conversation_summary("session-1", "Summary to delete")
            memory.save_conversation_summary("session-2", "Summary to keep")

            memory.reset("session-1")

            self.assertIsNone(memory.load_conversation_summary("session-1"))
            self.assertEqual(memory.load_conversation_summary("session-2")["content"], "Summary to keep")  # type: ignore[index]

    def test_conversation_summary_rejects_empty_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")

            with self.assertRaises(ValueError):
                memory.save_conversation_summary("session-1", "  ")

    def test_memory_items_can_be_saved_listed_and_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            user_item = memory.save_memory_item(
                "user",
                "The user prefers concise Chinese updates.",
                confidence=0.9,
                source_session_id="session-1",
                source_message_id=12,
            )
            memory.save_memory_item("project", "The project uses Python-first runtime.", confidence=0.8)

            user_items = memory.list_memory_items(scope="user")
            queried_items = memory.list_memory_items(query="Python-first")
            deleted = memory.delete_memory_item(int(user_item["memoryItemId"]))
            active_items = memory.list_memory_items(scope="user")
            deleted_items = memory.list_memory_items(scope="user", include_deleted=True)

        self.assertEqual(len(user_items), 1)
        self.assertEqual(user_items[0]["content"], "The user prefers concise Chinese updates.")
        self.assertEqual(user_items[0]["confidence"], 0.9)
        self.assertEqual(user_items[0]["sourceSessionId"], "session-1")
        self.assertEqual(user_items[0]["sourceMessageId"], 12)
        self.assertEqual(len(queried_items), 1)
        self.assertEqual(queried_items[0]["scope"], "project")
        self.assertTrue(deleted)
        self.assertEqual(active_items, [])
        self.assertEqual(len(deleted_items), 1)
        self.assertTrue(deleted_items[0]["deleted"])

    def test_memory_items_validate_scope_and_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")

            with self.assertRaises(ValueError):
                memory.save_memory_item("invalid", "fact")
            with self.assertRaises(ValueError):
                memory.save_memory_item("user", "fact", confidence=1.5)

    def test_memory_review_candidates_can_be_saved_listed_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            first = memory.save_memory_review_candidate(
                "session-1",
                "user",
                "The user prefers concise updates.",
                confidence=0.8,
                reason="User explicitly requested concise status updates.",
                scope_reason="This is a stable user preference.",
                safety_labels=["explicit", "non_secret", "correct scope", "explicit"],
                retention_type="stable_preference",
                source_message_start_id=2,
                source_message_end_id=4,
            )
            duplicate = memory.save_memory_review_candidate(
                "session-1",
                "user",
                "The user prefers concise updates.",
                confidence=0.6,
            )
            pending = memory.list_memory_review_candidates(session_id="session-1", status="pending")

        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["candidateId"], duplicate["candidateId"])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["scope"], "user")
        self.assertEqual(pending[0]["confidence"], 0.8)
        self.assertEqual(pending[0]["reason"], "User explicitly requested concise status updates.")
        self.assertEqual(pending[0]["scopeReason"], "This is a stable user preference.")
        self.assertEqual(pending[0]["safetyLabels"], ["explicit", "non_secret", "correct_scope"])
        self.assertEqual(pending[0]["retentionType"], "stable_preference")
        self.assertEqual(pending[0]["sourceMessageStartId"], 2)
        self.assertEqual(pending[0]["sourceMessageEndId"], 4)

    def test_memory_review_candidate_migration_preserves_legacy_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "amadeus.sqlite"
            now = "2026-06-21T00:00:00+00:00"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE memory_review_candidates (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      session_id TEXT NOT NULL,
                      scope TEXT NOT NULL CHECK (scope IN ('user', 'agent', 'project')),
                      content TEXT NOT NULL,
                      confidence REAL NOT NULL DEFAULT 0.7,
                      reason TEXT,
                      source_message_start_id INTEGER,
                      source_message_end_id INTEGER,
                      status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected', 'superseded')),
                      memory_item_id INTEGER,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO memory_review_candidates (
                      session_id,
                      scope,
                      content,
                      confidence,
                      reason,
                      source_message_start_id,
                      source_message_end_id,
                      status,
                      created_at,
                      updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        "legacy-session",
                        "user",
                        "The user prefers migration-safe memory.",
                        0.75,
                        "Legacy candidate reason.",
                        3,
                        5,
                        now,
                        now,
                    ),
                )

            memory = MessageMemoryStore(database_path)
            with sqlite3.connect(database_path) as connection:
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(memory_review_candidates)").fetchall()
                }
            candidates = memory.list_memory_review_candidates(session_id="legacy-session", status="pending")
            accepted = memory.accept_memory_review_candidate(int(candidates[0]["candidateId"]))
            items = memory.list_memory_items(scope="user")

        self.assertIn("scope_reason", columns)
        self.assertIn("safety_labels", columns)
        self.assertIn("retention_type", columns)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["content"], "The user prefers migration-safe memory.")
        self.assertEqual(candidates[0]["confidence"], 0.75)
        self.assertEqual(candidates[0]["reason"], "Legacy candidate reason.")
        self.assertEqual(candidates[0]["scopeReason"], "")
        self.assertEqual(candidates[0]["safetyLabels"], [])
        self.assertEqual(candidates[0]["retentionType"], "long_term")
        self.assertEqual(candidates[0]["sourceMessageStartId"], 3)
        self.assertEqual(candidates[0]["sourceMessageEndId"], 5)
        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["candidate"]["status"], "accepted")
        self.assertEqual(accepted["candidate"]["scopeReason"], "")
        self.assertEqual(accepted["candidate"]["safetyLabels"], [])
        self.assertEqual(accepted["candidate"]["retentionType"], "long_term")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], "The user prefers migration-safe memory.")

    def test_accept_memory_review_candidate_promotes_to_memory_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            candidate = memory.save_memory_review_candidate(
                "session-1",
                "project",
                "Amadeus uses Python-first runtime.",
                confidence=0.85,
                scope_reason="This is a durable project fact.",
                safety_labels=["explicit", "correct_scope"],
                retention_type="durable_project_fact",
                source_message_end_id=9,
            )
            result = memory.accept_memory_review_candidate(int(candidate["candidateId"]))
            accepted = memory.list_memory_review_candidates(status="accepted")
            items = memory.list_memory_items(scope="project")

        self.assertTrue(result["accepted"])
        self.assertFalse(result["duplicateMemoryItem"])
        self.assertEqual(result["candidate"]["status"], "accepted")
        self.assertEqual(result["candidate"]["scopeReason"], "This is a durable project fact.")
        self.assertEqual(result["candidate"]["safetyLabels"], ["explicit", "correct_scope"])
        self.assertEqual(result["candidate"]["retentionType"], "durable_project_fact")
        self.assertEqual(result["item"]["content"], "Amadeus uses Python-first runtime.")
        self.assertEqual(result["item"]["sourceSessionId"], "session-1")
        self.assertEqual(result["item"]["sourceMessageId"], 9)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["memoryItemId"], result["item"]["memoryItemId"])
        self.assertEqual(len(items), 1)

    def test_reject_memory_review_candidate_does_not_write_memory_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            candidate = memory.save_memory_review_candidate(
                "session-1",
                "agent",
                "Do not store transient task progress.",
                confidence=0.7,
            )
            result = memory.reject_memory_review_candidate(int(candidate["candidateId"]))
            rejected = memory.list_memory_review_candidates(status="rejected")
            items = memory.list_memory_items(scope="agent")

        self.assertTrue(result["rejected"])
        self.assertEqual(result["candidate"]["status"], "rejected")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(items, [])

    def test_rejected_memory_review_candidate_suppresses_same_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            candidate = memory.save_memory_review_candidate(
                "session-1",
                "agent",
                "Never store secrets in durable memory.",
                confidence=0.7,
            )
            memory.reject_memory_review_candidate(int(candidate["candidateId"]))
            repeated = memory.save_memory_review_candidate(
                "session-1",
                "agent",
                "Never store secrets in durable memory.",
                confidence=0.9,
            )
            pending = memory.list_memory_review_candidates(session_id="session-1", status="pending")
            rejected = memory.list_memory_review_candidates(session_id="session-1", status="rejected")

        self.assertTrue(repeated["duplicate"])
        self.assertTrue(repeated["suppressed"])
        self.assertEqual(repeated["status"], "rejected")
        self.assertEqual(pending, [])
        self.assertEqual(len(rejected), 1)

    def test_memory_review_jobs_can_be_recorded_and_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            job = memory.start_memory_review_job("session-1", "manual")
            finished = memory.finish_memory_review_job(
                int(job["jobId"]),
                "completed",
                source_message_start_id=2,
                source_message_end_id=5,
                source_message_count=4,
                proposed_candidate_count=3,
                saved_candidate_count=2,
                suppressed_candidate_count=1,
                duration_ms=123,
            )
            jobs = memory.list_memory_review_jobs(session_id="session-1")

        self.assertEqual(job["status"], "running")
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["trigger"], "manual")
        self.assertEqual(finished["sourceMessageStartId"], 2)
        self.assertEqual(finished["sourceMessageEndId"], 5)
        self.assertEqual(finished["sourceMessageCount"], 4)
        self.assertEqual(finished["proposedCandidateCount"], 3)
        self.assertEqual(finished["savedCandidateCount"], 2)
        self.assertEqual(finished["suppressedCandidateCount"], 1)
        self.assertEqual(finished["durationMs"], 123)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["jobId"], job["jobId"])

    def test_memory_review_jobs_record_skipped_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            job = memory.start_memory_review_job("session-1", "auto")
            finished = memory.finish_memory_review_job(int(job["jobId"]), "skipped", reason="below_threshold")
            skipped = memory.list_memory_review_jobs(status="skipped")

        self.assertEqual(finished["status"], "skipped")
        self.assertEqual(finished["reason"], "below_threshold")
        self.assertEqual(len(skipped), 1)

    def test_tasks_can_be_created_listed_cancelled_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            task = memory.create_task(
                session_id="session-1",
                title="Research task persistence",
                body="Check SQLite task storage.",
                priority=5,
            )
            listed = memory.list_tasks(session_id="session-1", active_only=True)
            cancelled = memory.cancel_task(str(task["id"]), reason="User stopped the task")
            events = memory.list_task_events(str(task["id"]))

        self.assertEqual(task["status"], "queued")
        self.assertEqual(listed["summary"]["queued"], 1)
        self.assertEqual(listed["tasks"][0]["title"], "Research task persistence")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["error"], "User stopped the task")
        self.assertEqual([event["type"] for event in events], ["created", "cancelled"])
        self.assertEqual(events[1]["metadata"], {"previousStatus": "queued"})

    def test_task_worker_state_transitions_can_succeed_fail_and_ignore_finished_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            succeeded_task = memory.create_task(session_id="session-1", title="Succeed")
            running = memory.start_task(str(succeeded_task["id"]), claim_lock="worker-1")
            succeeded = memory.complete_task(str(succeeded_task["id"]), claim_lock="worker-1", result="Finished")
            unchanged = memory.cancel_task(str(succeeded_task["id"]), reason="Too late")

            failed_task = memory.create_task(session_id="session-1", title="Fail")
            memory.start_task(str(failed_task["id"]), claim_lock="worker-2")
            failed = memory.fail_task(str(failed_task["id"]), claim_lock="worker-2", error="Boom")
            events = memory.list_task_events(str(succeeded_task["id"]))

        self.assertEqual(running["status"], "running")
        self.assertIsNotNone(running["lastHeartbeat"])
        self.assertEqual(succeeded["status"], "succeeded")
        self.assertEqual(succeeded["result"], "Finished")
        self.assertEqual(unchanged["status"], "succeeded")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "Boom")
        self.assertEqual([event["type"] for event in events], ["created", "running", "succeeded"])

    def test_task_status_migration_maps_legacy_done_to_succeeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "amadeus.sqlite"
            now = "2026-06-30T00:00:00+00:00"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE tasks (
                      id TEXT PRIMARY KEY,
                      session_id TEXT NOT NULL,
                      title TEXT NOT NULL,
                      body TEXT NOT NULL DEFAULT '',
                      status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'blocked', 'done', 'failed', 'cancelled')),
                      priority INTEGER NOT NULL DEFAULT 0,
                      due_at TEXT,
                      claim_lock TEXT,
                      last_heartbeat TEXT,
                      result TEXT,
                      error TEXT,
                      created_at TEXT NOT NULL,
                      updated_at TEXT NOT NULL,
                      finished_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO tasks (
                      id, session_id, title, body, status, priority, created_at, updated_at, finished_at
                    )
                    VALUES ('task-legacy', 'session-1', 'Legacy done', '', 'done', 0, ?, ?, ?)
                    """,
                    (now, now, now),
                )

            memory = MessageMemoryStore(database_path)
            task = memory.get_task("task-legacy")
            listed = memory.list_tasks(session_id="session-1", status="succeeded")

        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(listed["summary"]["succeeded"], 1)

    def test_replace_memory_item_updates_active_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            item = memory.save_memory_item("project", "Old fact.", confidence=0.4)
            replaced = memory.replace_memory_item(
                int(item["memoryItemId"]),
                "New fact.",
                scope="user",
                confidence=0.8,
            )
            items = memory.list_memory_items(scope="user")

        self.assertIsNotNone(replaced)
        assert replaced is not None
        self.assertEqual(replaced["memoryItemId"], item["memoryItemId"])
        self.assertEqual(replaced["scope"], "user")
        self.assertEqual(replaced["content"], "New fact.")
        self.assertEqual(replaced["confidence"], 0.8)
        self.assertEqual(len(items), 1)

    def test_accept_memory_review_candidate_reuses_existing_memory_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            existing = memory.save_memory_item("user", "The user prefers Chinese responses.", confidence=1.0)
            candidate = memory.save_memory_review_candidate(
                "session-1",
                "user",
                "The user prefers Chinese responses.",
                confidence=0.6,
            )
            result = memory.accept_memory_review_candidate(int(candidate["candidateId"]))
            items = memory.list_memory_items(scope="user")

        self.assertTrue(result["accepted"])
        self.assertTrue(result["duplicateMemoryItem"])
        self.assertEqual(result["item"]["memoryItemId"], existing["memoryItemId"])
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
