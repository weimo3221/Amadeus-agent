from __future__ import annotations

import tempfile
import unittest
from typing import Any
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.memory import MessageMemoryStore
from amadeus.orchestrator import OrchestratorService, PlanningOptions


class FakePlanningModel:
    model = "fake-planner"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.payloads: list[dict[str, Any]] = []

    def post_chat_completion(self, payload: dict[str, Any], *, timeout_seconds: int | None = None) -> dict[str, Any]:
        self.payloads.append(payload)
        content = self.responses.pop(0)
        return {"choices": [{"message": {"content": content}}]}


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

    def test_validate_graph_rejects_unknown_worker_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = OrchestratorService(MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite"))

            with self.assertRaisesRegex(ValueError, "unsupported workerProfile"):
                service.validate_graph({
                    "tasks": [
                        {"tempId": "rogue", "title": "Rogue", "workerProfile": "admin"},
                    ],
                })

    def test_validate_graph_rejects_profile_toolset_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = OrchestratorService(MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite"))

            with self.assertRaisesRegex(ValueError, "cannot allow toolsets: terminal"):
                service.validate_graph({
                    "tasks": [
                        {
                            "tempId": "research",
                            "title": "Research",
                            "workerProfile": "researcher",
                            "allowedToolsets": ["read", "terminal"],
                        },
                    ],
                })

    def test_validate_graph_adds_profile_default_toolsets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = OrchestratorService(MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite"))

            validated = service.validate_graph({
                "tasks": [
                    {"tempId": "review", "title": "Review", "workerProfile": "reviewer"},
                ],
            })

        self.assertEqual(validated["tasks"][0]["worker_profile"], "reviewer")
        self.assertEqual(validated["tasks"][0]["allowed_toolsets"], ["read", "search", "memory"])

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

    def test_plan_root_uses_model_json_then_applies_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            model = FakePlanningModel([
                '{"goal":"Build planning","approach":"Inspect then design","acceptanceCriteria":["graph created"],"outOfScope":[]}',
                '{"tasks":[{"tempId":"inspect","title":"Inspect runtime","body":"Read code","workerProfile":"researcher","acceptanceCriteria":["notes"],"allowedToolsets":["read"]},{"tempId":"design","title":"Design changes","body":"Use notes","workerProfile":"planner","dependsOn":["inspect"]}],"edges":[]}',
            ])
            service = OrchestratorService(memory, model_client=model)
            root = service.create_root_goal(session_id="session-1", title="Build planning")

            planned = service.plan_root(str(root["id"]))

        self.assertFalse(planned["fallback"])
        self.assertEqual(planned["decompositionSource"], "model")
        self.assertEqual(planned["spec"]["goal"], "Build planning")
        self.assertEqual(len(planned["tasks"]), 2)
        self.assertEqual(len(planned["edges"]), 1)
        self.assertEqual(len(model.payloads), 2)

    def test_plan_root_falls_back_to_single_child_when_model_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            model = FakePlanningModel(["not json"])
            service = OrchestratorService(memory, model_client=model)
            root = service.create_root_goal(session_id="session-1", title="Fallback root", body="Do work")

            planned = service.plan_root(str(root["id"]))

        self.assertTrue(planned["fallback"])
        self.assertEqual(planned["decompositionSource"], "fallback")
        self.assertEqual(len(planned["tasks"]), 1)
        self.assertEqual(planned["tasks"][0]["title"], "Fallback root")

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

    def test_synthesize_root_completes_after_children_succeed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            model = FakePlanningModel(['{"summary":"done","result":"Final synthesized result"}'])
            service = OrchestratorService(memory, model_client=model)
            root = service.create_root_goal(session_id="session-1", title="Root")
            applied = service.apply_task_graph(str(root["id"]), {
                "tasks": [
                    {"tempId": "first", "title": "First"},
                    {"tempId": "second", "title": "Second"},
                ],
            })
            first_id = str(applied["tempTaskIds"]["first"])
            second_id = str(applied["tempTaskIds"]["second"])
            memory.start_task(first_id, claim_lock="worker-1")
            memory.complete_task(first_id, claim_lock="worker-1", result="first result")
            memory.start_task(second_id, claim_lock="worker-2")
            memory.complete_task(second_id, claim_lock="worker-2", result="second result")

            synthesized = service.synthesize_root(str(root["id"]))
            repeated = service.synthesize_root(str(root["id"]))
            updated_root = memory.get_task(str(root["id"]))
            artifacts = memory.list_task_artifacts(str(root["id"]))

        self.assertTrue(synthesized["ready"])
        self.assertTrue(synthesized["completed"])
        self.assertTrue(repeated["completed"])
        self.assertEqual(synthesized["result"], "Final synthesized result")
        self.assertEqual(updated_root["status"], "succeeded")
        self.assertEqual(updated_root["result"], "Final synthesized result")
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["title"], "Task graph synthesis")
        self.assertEqual(len(model.payloads), 1)

    def test_synthesize_root_waits_for_active_children(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            service = OrchestratorService(memory)
            root = service.create_root_goal(session_id="session-1", title="Root")
            applied = service.apply_task_graph(str(root["id"]), {
                "tasks": [{"tempId": "first", "title": "First"}],
            })
            child_id = str(applied["tempTaskIds"]["first"])

            synthesized = service.synthesize_root(str(root["id"]))
            updated_root = memory.get_task(str(root["id"]))

        self.assertFalse(synthesized["ready"])
        self.assertFalse(synthesized["completed"])
        self.assertEqual(synthesized["pendingTaskIds"], [child_id])
        self.assertEqual(updated_root["status"], "queued")

    def test_synthesize_root_blocks_when_child_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            service = OrchestratorService(memory)
            root = service.create_root_goal(session_id="session-1", title="Root")
            applied = service.apply_task_graph(str(root["id"]), {
                "tasks": [{"tempId": "first", "title": "First"}],
            })
            child_id = str(applied["tempTaskIds"]["first"])
            memory.start_task(child_id, claim_lock="worker")
            memory.fail_task(child_id, claim_lock="worker", error="boom")

            synthesized = service.synthesize_root(str(root["id"]))
            updated_root = memory.get_task(str(root["id"]))

        self.assertTrue(synthesized["ready"])
        self.assertFalse(synthesized["completed"])
        self.assertTrue(synthesized["blocked"])
        self.assertEqual(synthesized["failedTaskIds"], [child_id])
        self.assertEqual(updated_root["status"], "blocked")

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
