from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.memory import MessageMemoryStore
from amadeus.orchestrator import OrchestratorService, PlanningOptions


class OrchestratorServiceTests(unittest.TestCase):
    def test_create_root_goal_uses_orchestrator_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            service = OrchestratorService(memory)

            root = service.create_root_goal(
                session_id="session-1",
                title="Implement long task planning",
                body="Build the control plane.",
                options=PlanningOptions(worker_profile="planner"),
            )

        self.assertEqual(root["title"], "Implement long task planning")
        self.assertEqual(root["body"], "Build the control plane.")
        self.assertEqual(root["workerProfile"], "planner")
        self.assertEqual(root["rootTaskId"], root["id"])

    def test_validate_graph_rejects_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = OrchestratorService(MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite"))

            with self.assertRaisesRegex(ValueError, "cycle"):
                service.validate_graph({
                    "tasks": [
                        {"tempId": "a", "title": "A", "dependsOn": ["b"]},
                        {"tempId": "b", "title": "B", "dependsOn": ["a"]},
                    ],
                })

    def test_apply_task_graph_creates_children_and_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            service = OrchestratorService(memory)
            root = service.create_root_goal(session_id="session-1", title="Root")

            applied = service.apply_task_graph(str(root["id"]), {
                "tasks": [
                    {
                        "tempId": "research",
                        "title": "Research current task system",
                        "body": "Read task storage and worker code.",
                        "workerProfile": "researcher",
                        "acceptanceCriteria": ["Summarize current task state"],
                        "allowedToolsets": ["read"],
                    },
                    {
                        "tempId": "design",
                        "title": "Design orchestrator",
                        "body": "Use research output.",
                        "workerProfile": "planner",
                        "dependsOn": ["research"],
                        "disallowedTools": ["terminal"],
                    },
                ],
            })
            graph = memory.get_task_graph(str(root["id"]))

        self.assertEqual(applied["rootTaskId"], root["id"])
        self.assertEqual(len(applied["tasks"]), 2)
        self.assertEqual(len(applied["edges"]), 1)
        temp_ids = applied["tempTaskIds"]
        self.assertIn("research", temp_ids)
        self.assertIn("design", temp_ids)
        design = next(task for task in graph["tasks"] if task["id"] == temp_ids["design"])
        self.assertEqual(design["rootTaskId"], root["id"])
        self.assertEqual(design["parentTaskId"], root["id"])
        self.assertEqual(design["workerProfile"], "planner")
        self.assertEqual(design["disallowedTools"], ["terminal"])
        self.assertEqual(graph["edges"][0]["fromTaskId"], temp_ids["research"])
        self.assertEqual(graph["edges"][0]["toTaskId"], temp_ids["design"])

    def test_dispatch_ready_respects_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            submitted: list[str] = []
            service = OrchestratorService(memory, submit_task=submitted.append)
            root = service.create_root_goal(session_id="session-1", title="Root")
            applied = service.apply_task_graph(str(root["id"]), {
                "tasks": [
                    {"tempId": "first", "title": "First"},
                    {"tempId": "second", "title": "Second", "dependsOn": ["first"]},
                ],
            })
            first_id = str(applied["tempTaskIds"]["first"])
            second_id = str(applied["tempTaskIds"]["second"])

            first_dispatch = service.dispatch_ready(str(root["id"]))
            memory.start_task(first_id, claim_lock="worker")
            memory.complete_task(first_id, claim_lock="worker", result="done")
            submitted.clear()
            second_dispatch = service.dispatch_ready(str(root["id"]))

        self.assertEqual(first_dispatch, [first_id])
        self.assertEqual(second_dispatch, [second_id])
        self.assertEqual(submitted, [second_id])

    def test_review_completed_child_returns_terminal_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            service = OrchestratorService(memory)
            task = memory.create_task(session_id="session-1", title="Child")
            memory.start_task(str(task["id"]), claim_lock="worker")
            memory.complete_task(str(task["id"]), claim_lock="worker", result="ok")

            decision = service.review_completed_child(str(task["id"]))

        self.assertTrue(decision["accepted"])
        self.assertTrue(decision["terminal"])
        self.assertEqual(decision["result"], "ok")


if __name__ == "__main__":
    unittest.main()
