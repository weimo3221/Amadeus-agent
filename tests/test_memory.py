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


if __name__ == "__main__":
    unittest.main()
