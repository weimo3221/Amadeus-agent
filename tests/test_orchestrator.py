from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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
            runnable_task_ids = {
                task["id"]
                for task in memory.list_runnable_tasks(limit=10)
            }

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
        self.assertEqual(
            next(task for task in graph["tasks"] if task["id"] == root["id"])["status"],
            "blocked",
        )
        self.assertNotIn(
            root["id"],
            runnable_task_ids,
        )

    def test_apply_task_graph_rejects_second_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            service = OrchestratorService(memory)
            root = service.create_root_goal(session_id="session-1", title="Root")
            service.apply_task_graph(
                str(root["id"]),
                {"tasks": [{"tempId": "first", "title": "First"}]},
            )

            with self.assertRaisesRegex(ValueError, "already been applied"):
                service.apply_task_graph(
                    str(root["id"]),
                    {"tasks": [{"tempId": "second", "title": "Second"}]},
                )

    def test_apply_task_graph_claims_root_once_under_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            root = OrchestratorService(memory).create_root_goal(
                session_id="session-1",
                title="Root",
            )

            def apply(index: int) -> str:
                try:
                    OrchestratorService(memory).apply_task_graph(
                        str(root["id"]),
                        {
                            "tasks": [
                                {
                                    "tempId": f"child-{index}",
                                    "title": f"Child {index}",
                                },
                            ],
                        },
                    )
                    return "applied"
                except ValueError:
                    return "rejected"

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(apply, range(2)))
            graph = memory.get_task_graph(str(root["id"]))

        self.assertEqual(results.count("applied"), 1)
        self.assertEqual(results.count("rejected"), 1)
        self.assertEqual(len(graph["tasks"]), 2)

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

    def test_plan_root_repairs_invalid_model_graph_before_applying(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            model = FakePlanningModel([
                '{"goal":"Build planning","approach":"Inspect then design","acceptanceCriteria":["graph created"],"outOfScope":[]}',
                '{"tasks":[{"tempId":"research","title":"Research","workerProfile":"researcher","allowedToolsets":["read","terminal"]}],"edges":[]}',
                '{"tasks":[{"tempId":"research","title":"Research","workerProfile":"researcher","allowedToolsets":["read","search"]}],"edges":[]}',
            ])
            service = OrchestratorService(memory, model_client=model)
            root = service.create_root_goal(session_id="session-1", title="Build planning")

            planned = service.plan_root(str(root["id"]))
            events = memory.list_task_events(str(root["id"]))

        self.assertFalse(planned["fallback"])
        self.assertTrue(planned["repaired"])
        self.assertEqual(planned["decompositionSource"], "model_repaired")
        self.assertIn("cannot allow toolsets", planned["repairReason"])
        self.assertEqual(len(planned["tasks"]), 1)
        self.assertEqual(planned["tasks"][0]["allowedToolsets"], ["read", "search"])
        self.assertEqual(len(model.payloads), 3)
        event_types = [event["type"] for event in events]
        self.assertIn("graph.decomposed", event_types)
        self.assertIn("graph.applied", event_types)
        decomposed_event = next(event for event in events if event["type"] == "graph.decomposed")
        self.assertTrue(decomposed_event["metadata"]["repaired"])
        self.assertEqual(decomposed_event["metadata"]["source"], "model_repaired")

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

    def test_plan_root_falls_back_when_graph_repair_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            model = FakePlanningModel([
                '{"goal":"Fallback root","approach":"Do work","acceptanceCriteria":["done"],"outOfScope":[]}',
                '{"tasks":[{"tempId":"a","title":"A","dependsOn":["b"]},{"tempId":"b","title":"B","dependsOn":["a"]}],"edges":[]}',
                '{"tasks":[{"tempId":"rogue","title":"Rogue","workerProfile":"admin"}],"edges":[]}',
            ])
            service = OrchestratorService(memory, model_client=model)
            root = service.create_root_goal(session_id="session-1", title="Fallback root", body="Do work")

            planned = service.plan_root(str(root["id"]))
            events = memory.list_task_events(str(root["id"]))

        self.assertTrue(planned["fallback"])
        self.assertFalse(planned["repaired"])
        self.assertEqual(planned["decompositionSource"], "fallback")
        self.assertEqual(len(planned["tasks"]), 1)
        self.assertIn("unsupported workerProfile", planned["decompositionError"])
        self.assertEqual(len(model.payloads), 3)
        decomposed_event = next(event for event in events if event["type"] == "graph.decomposed")
        self.assertTrue(decomposed_event["metadata"]["fallback"])

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
            events = memory.list_task_events(str(root["id"]))

        self.assertEqual(first_dispatch, [first_id])
        self.assertEqual(second_dispatch, [second_id])
        self.assertEqual(submitted, [second_id])
        dispatch_events = [event for event in events if event["type"] == "graph.dispatched"]
        self.assertEqual(len(dispatch_events), 2)
        self.assertEqual(dispatch_events[-1]["metadata"]["dispatchedTaskIds"], [second_id])

    def test_graph_concurrency_limits_runnable_children(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            service = OrchestratorService(memory)
            root = service.create_root_goal(session_id="session-1", title="Root")
            applied = service.apply_task_graph(
                str(root["id"]),
                {
                    "tasks": [
                        {"tempId": "first", "title": "First"},
                        {"tempId": "second", "title": "Second"},
                        {"tempId": "third", "title": "Third"},
                    ],
                },
                max_concurrency=2,
            )

            first_batch = memory.list_runnable_tasks(limit=10)
            child_ids = set(applied["tempTaskIds"].values())
            for index, task in enumerate(first_batch):
                memory.start_task(
                    str(task["id"]),
                    claim_lock=f"worker-{index}",
                )
            third_id = next(
                task_id
                for task_id in child_ids
                if task_id not in {task["id"] for task in first_batch}
            )
            refused_claim = memory.start_task(
                third_id,
                claim_lock="overflow-worker",
            )
            while_full = memory.list_runnable_tasks(limit=10)
            memory.complete_task(
                str(first_batch[0]["id"]),
                claim_lock="worker-0",
                result="done",
            )
            after_completion = memory.list_runnable_tasks(limit=10)

        self.assertEqual(len(first_batch), 2)
        self.assertTrue({task["id"] for task in first_batch}.issubset(child_ids))
        self.assertEqual(refused_claim["status"], "queued")
        self.assertEqual(while_full, [])
        self.assertEqual(len(after_completion), 1)
        self.assertIn(after_completion[0]["id"], child_ids)
        self.assertNotIn(
            after_completion[0]["id"],
            {task["id"] for task in first_batch},
        )

    def test_graph_concurrency_claim_gate_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            service = OrchestratorService(memory)
            root = service.create_root_goal(session_id="session-1", title="Root")
            applied = service.apply_task_graph(
                str(root["id"]),
                {
                    "tasks": [
                        {"tempId": "first", "title": "First"},
                        {"tempId": "second", "title": "Second"},
                    ],
                },
                max_concurrency=1,
            )
            child_ids = sorted(applied["tempTaskIds"].values())

            def claim(payload: tuple[int, str]) -> dict[str, object] | None:
                index, task_id = payload
                return memory.start_task(
                    task_id,
                    claim_lock=f"worker-{index}",
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                claimed = list(executor.map(claim, enumerate(child_ids)))
            graph = memory.get_task_graph(str(root["id"]))

        self.assertEqual(
            [task["status"] for task in claimed].count("running"),
            1,
        )
        child_statuses = [
            task["status"]
            for task in graph["tasks"]
            if task["id"] in child_ids
        ]
        self.assertEqual(child_statuses.count("running"), 1)
        self.assertEqual(child_statuses.count("queued"), 1)

    def test_replan_failed_child_rewires_dependencies_and_allows_synthesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            service = OrchestratorService(memory)
            root = service.create_root_goal(session_id="session-1", title="Root")
            applied = service.apply_task_graph(str(root["id"]), {
                "tasks": [
                    {"tempId": "research", "title": "Research"},
                    {
                        "tempId": "implement",
                        "title": "Implement",
                        "workerProfile": "coder",
                        "dependsOn": ["research"],
                    },
                    {
                        "tempId": "review",
                        "title": "Review",
                        "workerProfile": "reviewer",
                        "dependsOn": ["implement"],
                    },
                ],
            })
            research_id = str(applied["tempTaskIds"]["research"])
            implement_id = str(applied["tempTaskIds"]["implement"])
            review_id = str(applied["tempTaskIds"]["review"])
            memory.start_task(research_id, claim_lock="research-worker")
            memory.complete_task(
                research_id,
                claim_lock="research-worker",
                result="research done",
            )
            memory.start_task(implement_id, claim_lock="implement-worker")
            memory.fail_task(
                implement_id,
                claim_lock="implement-worker",
                error="implementation failed",
            )

            replanned = service.replan_failed_child(
                str(root["id"]),
                implement_id,
            )
            replacement_id = str(replanned["replacementTaskId"])
            graph_after_replan = memory.get_task_graph(str(root["id"]))
            runnable = memory.list_runnable_tasks(limit=10)
            memory.start_task(replacement_id, claim_lock="replacement-worker")
            memory.complete_task(
                replacement_id,
                claim_lock="replacement-worker",
                result="replacement done",
            )
            memory.start_task(review_id, claim_lock="review-worker")
            memory.complete_task(
                review_id,
                claim_lock="review-worker",
                result="review done",
            )
            synthesized = service.synthesize_root(str(root["id"]))
            events = memory.list_task_events(str(root["id"]))

        edges = {
            (edge["fromTaskId"], edge["toTaskId"])
            for edge in graph_after_replan["edges"]
        }
        self.assertIn((research_id, replacement_id), edges)
        self.assertIn((replacement_id, review_id), edges)
        self.assertNotIn((implement_id, review_id), edges)
        self.assertEqual([task["id"] for task in runnable], [replacement_id])
        self.assertEqual(
            replanned["rewired"],
            {
                "failedTaskId": implement_id,
                "replacementTaskId": replacement_id,
                "copiedIncomingEdgeCount": 1,
                "rewiredOutgoingEdgeCount": 1,
            },
        )
        self.assertIsNone(replanned["replacementTask"]["readyAt"])
        self.assertTrue(synthesized["completed"])
        self.assertEqual(synthesized["supersededTaskIds"], [implement_id])
        self.assertIn("graph.replanned", [event["type"] for event in events])

    def test_replan_failed_child_is_bounded_per_failed_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            service = OrchestratorService(memory)
            root = service.create_root_goal(session_id="session-1", title="Root")
            applied = service.apply_task_graph(
                str(root["id"]),
                {"tasks": [{"tempId": "child", "title": "Child"}]},
                max_replans=1,
            )
            child_id = str(applied["tempTaskIds"]["child"])
            memory.start_task(child_id, claim_lock="worker")
            memory.fail_task(child_id, claim_lock="worker", error="boom")

            service.replan_failed_child(str(root["id"]), child_id)
            with self.assertRaisesRegex(
                ValueError,
                "already has a replacement",
            ):
                service.replan_failed_child(str(root["id"]), child_id)

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
            events = memory.list_task_events(str(root["id"]))

        self.assertTrue(synthesized["ready"])
        self.assertTrue(synthesized["completed"])
        self.assertTrue(repeated["completed"])
        self.assertEqual(synthesized["result"], "Final synthesized result")
        self.assertEqual(updated_root["status"], "succeeded")
        self.assertEqual(updated_root["result"], "Final synthesized result")
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["title"], "Task graph synthesis")
        self.assertEqual(len(model.payloads), 1)
        synthesis_events = [event for event in events if event["type"] == "graph.synthesized"]
        self.assertEqual(len(synthesis_events), 1)
        self.assertTrue(synthesis_events[0]["metadata"]["completed"])

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
        self.assertEqual(updated_root["status"], "blocked")
        self.assertEqual(updated_root["checkpoint"]["phase"], "orchestrator_waiting")

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

    def test_cancel_graph_cascades_to_active_children_before_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            cancelled: list[str] = []

            def cancel_task(task_id: str, *, reason: str | None = None) -> dict[str, object]:
                cancelled.append(task_id)
                return memory.cancel_task(task_id, reason=reason)

            service = OrchestratorService(memory, cancel_task=cancel_task)
            root = service.create_root_goal(session_id="session-1", title="Root")
            applied = service.apply_task_graph(str(root["id"]), {
                "tasks": [
                    {"tempId": "first", "title": "First"},
                    {"tempId": "second", "title": "Second"},
                ],
            })
            child_ids = {
                str(applied["tempTaskIds"]["first"]),
                str(applied["tempTaskIds"]["second"]),
            }

            result = service.cancel_graph(
                str(root["id"]),
                reason="operator stopped graph",
            )
            graph = memory.get_task_graph(str(root["id"]))
            events = memory.list_task_events(str(root["id"]))

        self.assertEqual(set(cancelled[:-1]), child_ids)
        self.assertEqual(cancelled[-1], root["id"])
        self.assertEqual(set(result["cancelledTaskIds"]), child_ids | {str(root["id"])})
        self.assertTrue(all(task["status"] == "cancelled" for task in graph["tasks"]))
        self.assertIn("graph.cancelled", [event["type"] for event in events])

    def test_cancel_child_does_not_cancel_root_or_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            service = OrchestratorService(memory)
            root = service.create_root_goal(session_id="session-1", title="Root")
            applied = service.apply_task_graph(str(root["id"]), {
                "tasks": [
                    {"tempId": "first", "title": "First"},
                    {"tempId": "second", "title": "Second"},
                ],
            })
            first_id = str(applied["tempTaskIds"]["first"])
            second_id = str(applied["tempTaskIds"]["second"])

            result = service.cancel_graph(first_id, reason="cancel one child")
            graph = memory.get_task_graph(str(root["id"]))

        status_by_id = {
            task["id"]: task["status"]
            for task in graph["tasks"]
        }
        self.assertEqual(result["cancelledTaskIds"], [first_id])
        self.assertEqual(status_by_id[first_id], "cancelled")
        self.assertEqual(status_by_id[second_id], "queued")
        self.assertEqual(status_by_id[str(root["id"])], "blocked")

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
