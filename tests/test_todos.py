from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.context import ContextAssembler
from amadeus.memory import MessageMemoryStore
from amadeus.tool_runtime import ToolContext
from amadeus.tools.todo import todo


class TodoTests(unittest.TestCase):
    def test_todos_replace_merge_and_summarize(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            replaced = memory.save_todos(
                session_id="session-1",
                todos=[
                    {"id": "a", "content": "Buy tea", "status": "pending"},
                    {"id": "b", "content": "Reply to mail", "status": "in_progress"},
                ],
            )
            merged = memory.save_todos(
                session_id="session-1",
                todos=[
                    {"id": "a", "content": "Buy green tea", "status": "completed"},
                    {"id": "c", "content": "Stretch", "status": "pending"},
                ],
                merge=True,
            )
            active = memory.list_todos(session_id="session-1", active_only=True)

        self.assertEqual(replaced["summary"]["pending"], 1)
        self.assertEqual(merged["summary"]["completed"], 1)
        self.assertEqual([item["id"] for item in active["todos"]], ["b", "c"])
        self.assertEqual(active["summary"]["inProgress"], 1)

    def test_todo_tool_reads_and_writes_current_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            context = ToolContext(session_id="session-1", memory_store=memory)

            written = todo({
                "todos": [
                    {"id": "one", "content": "Prepare notes", "status": "pending"},
                ],
            }, context)
            read = todo({"activeOnly": True}, context)

        self.assertEqual(written["action"], "updated")
        self.assertEqual(read["summary"]["pending"], 1)
        self.assertEqual(read["todos"][0]["content"], "Prepare notes")

    def test_context_injects_active_todos_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save_todos(
                session_id="session-1",
                todos=[
                    {"id": "done", "content": "Already finished", "status": "completed"},
                    {"id": "next", "content": "Water the plant", "status": "pending"},
                ],
            )
            assembler = ContextAssembler(memory, "Base")

            assembled = assembler.assemble("session-1", "what should I do?")

        self.assertIn("<active-todos>", assembled.system_context)
        self.assertIn("Water the plant", assembled.system_context)
        self.assertNotIn("Already finished", assembled.system_context)
        self.assertEqual(assembled.diagnostics()["sourceCounts"]["active_todos"], 1)

    def test_todo_content_and_count_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            result = memory.save_todos(
                session_id="session-1",
                todos=[
                    {"id": str(index), "content": "x" * 5000, "status": "pending"}
                    for index in range(300)
                ],
            )

        self.assertEqual(len(result["todos"]), 256)
        self.assertLessEqual(len(str(result["todos"][0]["content"])), 1000)
        self.assertTrue(str(result["todos"][0]["content"]).endswith("[truncated]"))


if __name__ == "__main__":
    unittest.main()
