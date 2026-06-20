from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.memory import MessageMemoryStore


class MessageMemoryStoreTests(unittest.TestCase):
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
        self.assertEqual(pending[0]["sourceMessageStartId"], 2)
        self.assertEqual(pending[0]["sourceMessageEndId"], 4)

    def test_accept_memory_review_candidate_promotes_to_memory_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            candidate = memory.save_memory_review_candidate(
                "session-1",
                "project",
                "Amadeus uses Python-first runtime.",
                confidence=0.85,
                source_message_end_id=9,
            )
            result = memory.accept_memory_review_candidate(int(candidate["candidateId"]))
            accepted = memory.list_memory_review_candidates(status="accepted")
            items = memory.list_memory_items(scope="project")

        self.assertTrue(result["accepted"])
        self.assertFalse(result["duplicateMemoryItem"])
        self.assertEqual(result["candidate"]["status"], "accepted")
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
