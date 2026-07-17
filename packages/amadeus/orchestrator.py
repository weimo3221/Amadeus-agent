from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from amadeus.memory import MessageMemoryStore
from amadeus.model import first_choice_message, parse_json_object_from_text
from amadeus.worker_policy import (
    ALLOWED_WORKER_PROFILES,
    DEFAULT_PROFILE_TOOLSETS,
    DEFAULT_WORKER_PROFILE,
    KNOWN_TOOLSETS,
    PROFILE_TOOLSET_POLICY,
)


TaskSubmitter = Callable[[str], None]
TaskCanceller = Callable[..., dict[str, object]]
logger = logging.getLogger(__name__)

TASK_GRAPH_JSON_SHAPE = (
    '{"tasks":[{"tempId":"short-id","title":"task title","body":"worker instructions",'
    '"workerProfile":"researcher|planner|coder|reviewer|synthesizer",'
    '"acceptanceCriteria":["criterion"],"contextHints":{},"allowedToolsets":["read"],'
    '"disallowedTools":[],"dependsOn":["other-temp-id"]}],"edges":[]}'
)
DEFAULT_GRAPH_MAX_CONCURRENCY = 2
DEFAULT_GRAPH_MAX_REPLANS = 3


class PlanningModel(Protocol):
    @property
    def model(self) -> str:
        ...

    def post_chat_completion(self, payload: dict[str, Any], *, timeout_seconds: int | None = None) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class PlanningOptions:
    worker_profile: str | None = None
    max_children: int = 8
    auto_dispatch: bool = False


@dataclass(frozen=True)
class GraphTaskSpec:
    temp_id: str
    title: str
    body: str = ""
    worker_profile: str | None = None
    acceptance_criteria: list[object] = field(default_factory=list)
    context_hints: dict[str, object] = field(default_factory=dict)
    allowed_toolsets: list[object] = field(default_factory=list)
    disallowed_tools: list[object] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GraphEdgeSpec:
    from_temp_id: str
    to_temp_id: str
    edge_type: str = "blocks"
    required_status: str = "succeeded"
    metadata: dict[str, object] = field(default_factory=dict)


class OrchestratorService:
    def __init__(
        self,
        memory_store: MessageMemoryStore,
        *,
        submit_task: TaskSubmitter | None = None,
        cancel_task: TaskCanceller | None = None,
        model_client: PlanningModel | None = None,
    ) -> None:
        self.memory_store = memory_store
        self.submit_task = submit_task
        self.cancel_task = cancel_task
        self.model_client = model_client

    def create_root_goal(
        self,
        *,
        session_id: str,
        title: str,
        body: str | None = None,
        options: PlanningOptions | None = None,
    ) -> dict[str, object]:
        selected = options or PlanningOptions()
        return self.memory_store.create_task(
            session_id=session_id,
            title=title,
            body=body,
            kind="agent_turn",
            source="manual",
            worker_type="agent",
            worker_profile=selected.worker_profile or "orchestrator",
            acceptance_criteria=[],
        )

    def validate_graph(self, graph: dict[str, object], *, max_children: int = 8) -> dict[str, object]:
        tasks = _parse_tasks(graph.get("tasks"), max_children=max_children)
        tasks = _normalize_and_validate_task_policies(tasks)
        edges = _parse_edges(graph.get("edges"), tasks)
        _validate_acyclic(tasks, edges)
        return {
            "ok": True,
            "tasks": [task.__dict__ for task in tasks],
            "edges": [edge.__dict__ for edge in edges],
            "taskCount": len(tasks),
            "edgeCount": len(edges),
        }

    def specify_task(self, root_task_id: str) -> dict[str, object]:
        root = self.memory_store.get_task(root_task_id)
        if root is None:
            raise ValueError("root task not found")
        parsed = self._request_planning_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You turn a user goal into a precise execution spec for a long-running agent task. "
                        "Return only JSON. Do not include markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Task:\n"
                        f"title: {root.get('title')}\n"
                        f"body: {root.get('body') or ''}\n\n"
                        "Return JSON in this exact shape:\n"
                        '{"goal":"clear goal","approach":"short approach","acceptanceCriteria":["criterion"],"outOfScope":["non-goal"]}'
                    ),
                },
            ]
        )
        acceptance = parsed.get("acceptanceCriteria")
        out_of_scope = parsed.get("outOfScope")
        return {
            "goal": str(parsed.get("goal") or root.get("title") or "").strip(),
            "approach": str(parsed.get("approach") or "").strip(),
            "acceptanceCriteria": acceptance if isinstance(acceptance, list) else [],
            "outOfScope": out_of_scope if isinstance(out_of_scope, list) else [],
        }

    def decompose_task(self, root_task_id: str, *, max_children: int = 6) -> dict[str, object]:
        root = self.memory_store.get_task(root_task_id)
        if root is None:
            raise ValueError("root task not found")
        try:
            spec = self.specify_task(root_task_id)
            parsed = self._request_planning_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "You decompose a long-running agent goal into a small dependency graph. "
                            "Return only JSON. Do not include markdown. "
                            "Prefer 2-6 tasks. Use dependencies only when one task needs another task's output."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Root task:\n"
                            f"id: {root.get('id')}\n"
                            f"title: {root.get('title')}\n"
                            f"body: {root.get('body') or ''}\n\n"
                            "Execution spec:\n"
                            f"{json.dumps(spec, ensure_ascii=False)}\n\n"
                            "Allowed worker profiles: researcher, planner, coder, reviewer, synthesizer.\n"
                            "Use conservative tool bounds: researcher should use read/search, coder may request patch/terminal, reviewer should avoid writes.\n\n"
                            "Return JSON in this exact shape:\n"
                            f"{TASK_GRAPH_JSON_SHAPE}"
                        ),
                    },
                ]
            )
            graph = {"tasks": parsed.get("tasks"), "edges": parsed.get("edges")}
            try:
                self.validate_graph(graph, max_children=max_children)
            except Exception as validation_error:
                repaired = self.repair_task_graph(
                    root_task_id,
                    spec=spec,
                    invalid_graph=graph,
                    validation_error=str(validation_error),
                    max_children=max_children,
                )
                self._record_graph_event(
                    root_task_id,
                    "graph.decomposed",
                    "Task graph decomposed after repair",
                    {
                        "source": "model_repaired",
                        "fallback": False,
                        "repaired": True,
                        "repairReason": str(validation_error),
                        "taskCount": len(repaired["graph"].get("tasks") or []),
                        "edgeCount": len(repaired["graph"].get("edges") or []),
                    },
                )
                return {
                    "source": "model_repaired",
                    "spec": spec,
                    "graph": repaired["graph"],
                    "fallback": False,
                    "repaired": True,
                    "repairReason": str(validation_error),
                }
            self._record_graph_event(
                root_task_id,
                "graph.decomposed",
                "Task graph decomposed",
                {
                    "source": "model",
                    "fallback": False,
                    "repaired": False,
                    "taskCount": len(graph.get("tasks") or []),
                    "edgeCount": len(graph.get("edges") or []),
                },
            )
            return {
                "source": "model",
                "spec": spec,
                "graph": graph,
                "fallback": False,
                "repaired": False,
            }
        except Exception as error:
            logger.info("Model-backed task decomposition failed taskId=%s error=%s; using single-task fallback", root_task_id, error)
            self._record_graph_event(
                root_task_id,
                "graph.decomposed",
                "Task graph decomposition fell back to a single child",
                {
                    "source": "fallback",
                    "fallback": True,
                    "repaired": False,
                    "error": str(error),
                    "taskCount": 1,
                    "edgeCount": 0,
                },
            )
            return {
                "source": "fallback",
                "spec": {
                    "goal": str(root.get("title") or "").strip(),
                    "approach": str(root.get("body") or "").strip(),
                    "acceptanceCriteria": root.get("acceptanceCriteria") if isinstance(root.get("acceptanceCriteria"), list) else [],
                    "outOfScope": [],
                },
                "graph": {
                    "tasks": [
                        {
                            "tempId": "work",
                            "title": str(root.get("title") or "Work task"),
                            "body": str(root.get("body") or ""),
                            "workerProfile": "planner",
                            "acceptanceCriteria": root.get("acceptanceCriteria") if isinstance(root.get("acceptanceCriteria"), list) else [],
                            "contextHints": root.get("contextHints") if isinstance(root.get("contextHints"), dict) else {},
                            "allowedToolsets": root.get("allowedToolsets") if isinstance(root.get("allowedToolsets"), list) else [],
                            "disallowedTools": root.get("disallowedTools") if isinstance(root.get("disallowedTools"), list) else [],
                            "dependsOn": [],
                        }
                    ],
                    "edges": [],
                },
                "fallback": True,
                "repaired": False,
                "error": str(error),
            }

    def repair_task_graph(
        self,
        root_task_id: str,
        *,
        spec: dict[str, object],
        invalid_graph: dict[str, object],
        validation_error: str,
        max_children: int = 6,
    ) -> dict[str, object]:
        root = self.memory_store.get_task(root_task_id)
        if root is None:
            raise ValueError("root task not found")
        parsed = self._request_planning_json(
            [
                {
                    "role": "system",
                    "content": (
                        "You repair invalid long-running agent task graphs. "
                        "Return only JSON. Do not include markdown. "
                        "Preserve the user's goal, remove invalid dependencies, avoid cycles, and use conservative worker profiles/toolsets."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Root task:\n"
                        f"id: {root.get('id')}\n"
                        f"title: {root.get('title')}\n"
                        f"body: {root.get('body') or ''}\n\n"
                        "Execution spec:\n"
                        f"{json.dumps(spec, ensure_ascii=False)}\n\n"
                        "Validation error:\n"
                        f"{validation_error}\n\n"
                        "Invalid graph:\n"
                        f"{json.dumps(invalid_graph, ensure_ascii=False)}\n\n"
                        "Allowed worker profiles: researcher, planner, coder, reviewer, synthesizer.\n"
                        f"Profile toolset policy: {json.dumps({key: sorted(value) for key, value in PROFILE_TOOLSET_POLICY.items()}, ensure_ascii=False)}\n\n"
                        "Return a corrected JSON graph in this exact shape:\n"
                        f"{TASK_GRAPH_JSON_SHAPE}"
                    ),
                },
            ]
        )
        graph = {"tasks": parsed.get("tasks"), "edges": parsed.get("edges")}
        self.validate_graph(graph, max_children=max_children)
        return {"graph": graph}

    def plan_root(
        self,
        root_task_id: str,
        *,
        max_children: int = 6,
        max_concurrency: int = DEFAULT_GRAPH_MAX_CONCURRENCY,
        max_replans: int = DEFAULT_GRAPH_MAX_REPLANS,
    ) -> dict[str, object]:
        decomposition = self.decompose_task(root_task_id, max_children=max_children)
        applied = self.apply_task_graph(
            root_task_id,
            decomposition["graph"],  # type: ignore[arg-type]
            max_concurrency=max_concurrency,
            max_replans=max_replans,
        )
        return {
            **applied,
            "spec": decomposition.get("spec"),
            "decompositionSource": decomposition.get("source"),
            "fallback": bool(decomposition.get("fallback")),
            "repaired": bool(decomposition.get("repaired")),
            "repairReason": decomposition.get("repairReason"),
            "decompositionError": decomposition.get("error"),
        }

    def synthesize_root(self, root_task_id: str) -> dict[str, object]:
        graph = self.memory_store.get_task_graph(root_task_id)
        root_id = str(graph["rootTaskId"])
        tasks = list(graph.get("tasks") or [])
        root = next((task for task in tasks if str(task.get("id")) == root_id), None)
        if root is None:
            raise ValueError("root task not found")
        children = [task for task in tasks if str(task.get("id")) != root_id]
        replacement_by_failed_id = {
            str(task["contextHints"].get("replanOfTaskId"))
            for task in children
            if isinstance(task.get("contextHints"), dict)
            and str(task["contextHints"].get("replanOfTaskId") or "")
        }
        effective_children = [
            task
            for task in children
            if str(task.get("id")) not in replacement_by_failed_id
        ]
        if not children:
            return {
                "rootTaskId": root_id,
                "ready": False,
                "completed": False,
                "reason": "root task has no child tasks to synthesize",
                "children": [],
            }
        unfinished = [
            task
            for task in effective_children
            if str(task.get("status") or "")
            not in {"succeeded", "failed", "cancelled"}
        ]
        if unfinished:
            return {
                "rootTaskId": root_id,
                "ready": False,
                "completed": False,
                "reason": "child tasks are still active",
                "pendingTaskIds": [str(task.get("id")) for task in unfinished],
                "children": children,
                "supersededTaskIds": sorted(replacement_by_failed_id),
            }
        failed = [
            task
            for task in effective_children
            if str(task.get("status") or "") in {"failed", "cancelled"}
        ]
        if failed:
            reason = "Cannot synthesize root task because one or more child tasks failed or were cancelled."
            if str(root.get("status") or "") not in {"succeeded", "failed", "cancelled"}:
                self.memory_store.block_task(
                    root_id,
                    reason=reason,
                    result=_fallback_child_summary(effective_children),
                )
                root = self.memory_store.get_task(root_id) or root
            self._record_graph_event(
                root_id,
                "graph.synthesized",
                "Task graph synthesis blocked",
                {
                    "completed": False,
                    "blocked": True,
                    "failedTaskIds": [str(task.get("id")) for task in failed],
                },
            )
            return {
                "rootTaskId": root_id,
                "ready": True,
                "completed": False,
                "blocked": True,
                "reason": reason,
                "failedTaskIds": [str(task.get("id")) for task in failed],
                "rootTask": root,
                "children": children,
                "supersededTaskIds": sorted(replacement_by_failed_id),
            }

        current_status = str(root.get("status") or "")
        if current_status == "succeeded":
            return {
                "rootTaskId": root_id,
                "ready": True,
                "completed": True,
                "result": root.get("result"),
                "rootTask": root,
                "children": children,
            }
        root_checkpoint = (
            root.get("checkpoint")
            if isinstance(root.get("checkpoint"), dict)
            else {}
        )
        if (
            current_status != "blocked"
            or str(root_checkpoint.get("phase") or "")
            != "orchestrator_waiting"
        ):
            return {
                "rootTaskId": root_id,
                "ready": True,
                "completed": False,
                "reason": "root task is not waiting for orchestrator synthesis",
                "rootTask": root,
                "children": children,
                "supersededTaskIds": sorted(replacement_by_failed_id),
            }

        result = self._synthesize_child_results(root, effective_children)
        artifact = self.memory_store.add_task_artifact(
            root_id,
            {
                "type": "summary",
                "title": "Task graph synthesis",
                "content": result,
            },
            metadata={
                "source": "orchestrator",
                "childTaskCount": len(effective_children),
                "supersededTaskIds": sorted(replacement_by_failed_id),
            },
        )
        completed = self.memory_store.complete_orchestrated_task(
            root_id,
            result=result,
        )
        self._record_graph_event(
            root_id,
            "graph.synthesized",
            "Task graph synthesized into root task",
            {
                "completed": True,
                "childTaskCount": len(effective_children),
                "artifactId": artifact.get("id"),
                "supersededTaskIds": sorted(replacement_by_failed_id),
            },
        )
        return {
            "rootTaskId": root_id,
            "ready": True,
            "completed": True,
            "result": result,
            "artifact": artifact,
            "rootTask": completed,
            "children": children,
            "supersededTaskIds": sorted(replacement_by_failed_id),
        }

    def apply_task_graph(
        self,
        root_task_id: str,
        graph: dict[str, object],
        *,
        max_concurrency: int = DEFAULT_GRAPH_MAX_CONCURRENCY,
        max_replans: int = DEFAULT_GRAPH_MAX_REPLANS,
    ) -> dict[str, object]:
        root = self.memory_store.get_task(root_task_id)
        if root is None:
            raise ValueError("root task not found")
        existing_graph = self.memory_store.get_task_graph(root_task_id)
        if len(existing_graph.get("tasks") or []) > 1:
            raise ValueError("task graph has already been applied")
        if str(root.get("status") or "") != "queued":
            raise ValueError("root task must be queued before applying a graph")
        normalized_max_concurrency = max(1, min(16, int(max_concurrency)))
        normalized_max_replans = max(0, min(8, int(max_replans)))
        validated = self.validate_graph(graph)
        task_specs = [
            GraphTaskSpec(
                temp_id=str(item["temp_id"]),
                title=str(item["title"]),
                body=str(item.get("body") or ""),
                worker_profile=str(item["worker_profile"]) if item.get("worker_profile") else None,
                acceptance_criteria=list(item.get("acceptance_criteria") or []),
                context_hints=dict(item.get("context_hints") or {}),
                allowed_toolsets=list(item.get("allowed_toolsets") or []),
                disallowed_tools=list(item.get("disallowed_tools") or []),
                depends_on=list(item.get("depends_on") or []),
            )
            for item in validated["tasks"]  # type: ignore[index]
        ]
        edge_specs = [
            GraphEdgeSpec(
                from_temp_id=str(item["from_temp_id"]),
                to_temp_id=str(item["to_temp_id"]),
                edge_type=str(item.get("edge_type") or "blocks"),
                required_status=str(item.get("required_status") or "succeeded"),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in validated["edges"]  # type: ignore[index]
        ]

        session_id = str(root["sessionId"])
        root_id = str(root["rootTaskId"] or root["id"])
        root_task = self.memory_store.block_task(
            root_id,
            reason="Waiting for child task graph completion",
            checkpoint={
                "status": "blocked",
                "phase": "orchestrator_waiting",
                "reason": "child_graph_active",
                "maxConcurrency": normalized_max_concurrency,
                "maxReplans": normalized_max_replans,
            },
            handoff_summary="The orchestrator is waiting for child task results before synthesis.",
            expected_status="queued",
        )
        temp_to_task_id: dict[str, str] = {}
        created_tasks: list[dict[str, object]] = []
        for spec in task_specs:
            task = self.memory_store.create_task(
                session_id=session_id,
                title=spec.title,
                body=spec.body,
                kind="agent_turn",
                source="plan",
                root_task_id=root_id,
                parent_task_id=str(root["id"]),
                worker_type="agent",
                worker_profile=spec.worker_profile,
                acceptance_criteria=spec.acceptance_criteria,
                context_hints=spec.context_hints,
                allowed_toolsets=spec.allowed_toolsets,
                disallowed_tools=spec.disallowed_tools,
            )
            temp_to_task_id[spec.temp_id] = str(task["id"])
            created_tasks.append(task)

        created_edges: list[dict[str, object]] = []
        for edge in edge_specs:
            created_edges.append(
                self.memory_store.add_task_edge(
                    from_task_id=temp_to_task_id[edge.from_temp_id],
                    to_task_id=temp_to_task_id[edge.to_temp_id],
                    edge_type=edge.edge_type,
                    required_status=edge.required_status,
                    metadata=edge.metadata,
                )
            )

        self._record_graph_event(
            root_id,
            "graph.applied",
            "Task graph applied",
            {
                "parentTaskId": str(root["id"]),
                "taskCount": len(created_tasks),
                "edgeCount": len(created_edges),
                "childTaskIds": [str(task["id"]) for task in created_tasks],
                "maxConcurrency": normalized_max_concurrency,
                "maxReplans": normalized_max_replans,
            },
        )
        return {
            "rootTaskId": root_id,
            "rootTask": root_task,
            "maxConcurrency": normalized_max_concurrency,
            "maxReplans": normalized_max_replans,
            "tasks": created_tasks,
            "edges": created_edges,
            "tempTaskIds": temp_to_task_id,
        }

    def dispatch_ready(self, root_task_id: str | None = None, *, limit: int = 20) -> list[str]:
        if self.submit_task is None:
            return []
        dispatched: list[str] = []
        query_limit = max(limit, min(100, limit * 4)) if root_task_id else limit
        for task in self.memory_store.list_runnable_tasks(limit=query_limit):
            task_id = str(task.get("id") or "")
            if root_task_id:
                if str(task.get("rootTaskId") or task.get("id")) != root_task_id:
                    continue
                if task_id == root_task_id:
                    continue
            if not task_id:
                continue
            self.submit_task(task_id)
            dispatched.append(task_id)
            if len(dispatched) >= limit:
                break
        if root_task_id and dispatched:
            self._record_graph_event(
                root_task_id,
                "graph.dispatched",
                "Ready task graph children dispatched",
                {
                    "dispatchCount": len(dispatched),
                    "dispatchedTaskIds": dispatched,
                },
            )
        return dispatched

    def cancel_graph(
        self,
        task_id: str,
        *,
        reason: str | None = None,
    ) -> dict[str, object]:
        graph = self.memory_store.get_task_graph(task_id)
        root_task_id = str(graph["rootTaskId"])
        tasks = list(graph.get("tasks") or [])
        if task_id != root_task_id:
            if self.cancel_task is not None:
                cancelled_task = self.cancel_task(task_id, reason=reason)
            else:
                cancelled_task = self.memory_store.cancel_task(
                    task_id,
                    reason=reason,
                )
            root = self.memory_store.get_task(root_task_id)
            if root is None:
                raise ValueError("root task not found")
            return {
                "rootTaskId": root_task_id,
                "rootTask": root,
                "cancelledTasks": [cancelled_task],
                "cancelledTaskIds": [str(cancelled_task.get("id"))],
            }
        active = [
            task
            for task in tasks
            if str(task.get("status") or "")
            not in {"succeeded", "failed", "cancelled"}
        ]
        active.sort(key=lambda task: str(task.get("id")) == root_task_id)
        cancelled: list[dict[str, object]] = []
        for task in active:
            child_id = str(task.get("id") or "")
            if not child_id:
                continue
            if self.cancel_task is not None:
                cancelled_task = self.cancel_task(child_id, reason=reason)
            else:
                cancelled_task = self.memory_store.cancel_task(
                    child_id,
                    reason=reason,
                )
            cancelled.append(cancelled_task)
        if len(tasks) > 1:
            self._record_graph_event(
                root_task_id,
                "graph.cancelled",
                "Task graph cancelled",
                {
                    "cancelledTaskIds": [
                        str(task.get("id"))
                        for task in cancelled
                    ],
                    "reason": reason,
                },
            )
        root = self.memory_store.get_task(root_task_id)
        if root is None:
            raise ValueError("root task not found")
        return {
            "rootTaskId": root_task_id,
            "rootTask": root,
            "cancelledTasks": cancelled,
            "cancelledTaskIds": [
                str(task.get("id"))
                for task in cancelled
            ],
        }

    def replan_failed_child(
        self,
        root_task_id: str,
        failed_task_id: str,
    ) -> dict[str, object]:
        graph = self.memory_store.get_task_graph(root_task_id)
        normalized_root_id = str(graph["rootTaskId"])
        tasks = list(graph.get("tasks") or [])
        root = next(
            (
                task
                for task in tasks
                if str(task.get("id")) == normalized_root_id
            ),
            None,
        )
        failed = next(
            (
                task
                for task in tasks
                if str(task.get("id")) == failed_task_id
            ),
            None,
        )
        if root is None or failed is None:
            raise ValueError("task not found in graph")
        if failed_task_id == normalized_root_id:
            raise ValueError("root task cannot be replaced as a child")
        if str(failed.get("status") or "") not in {"failed", "cancelled"}:
            raise ValueError("child task must be failed or cancelled before replanning")
        checkpoint = (
            root.get("checkpoint")
            if isinstance(root.get("checkpoint"), dict)
            else {}
        )
        if (
            str(root.get("status") or "") != "blocked"
            or str(checkpoint.get("phase") or "") != "orchestrator_waiting"
        ):
            raise ValueError("root task is not waiting for orchestrator synthesis")
        replacements = [
            task
            for task in tasks
            if isinstance(task.get("contextHints"), dict)
            and str(task["contextHints"].get("replanOfTaskId") or "")
        ]
        if any(
            str(task["contextHints"].get("replanOfTaskId") or "") == failed_task_id
            for task in replacements
        ):
            raise ValueError("failed child already has a replacement")
        try:
            max_replans = max(
                0,
                min(
                    8,
                    int(
                        checkpoint.get("maxReplans")
                        if checkpoint.get("maxReplans") is not None
                        else DEFAULT_GRAPH_MAX_REPLANS
                    ),
                ),
            )
        except (TypeError, ValueError):
            max_replans = DEFAULT_GRAPH_MAX_REPLANS
        if len(replacements) >= max_replans:
            raise ValueError("task graph replan limit reached")

        failed_context = (
            dict(failed["contextHints"])
            if isinstance(failed.get("contextHints"), dict)
            else {}
        )
        failure_text = str(
            failed.get("error")
            or failed.get("result")
            or "child task did not complete"
        ).strip()
        replacement = self.memory_store.create_task(
            session_id=str(root["sessionId"]),
            title=f"Recovery: {str(failed.get('title') or 'Child task')}",
            body=(
                f"{str(failed.get('body') or '').strip()}\n\n"
                "This is a bounded replacement for a failed child task. "
                f"Previous failure: {failure_text}"
            ).strip(),
            kind=str(failed.get("kind") or "agent_turn"),
            source="plan",
            root_task_id=normalized_root_id,
            parent_task_id=normalized_root_id,
            worker_type=str(failed.get("workerType") or "agent"),
            worker_profile=str(failed.get("workerProfile") or "") or None,
            acceptance_criteria=(
                list(failed["acceptanceCriteria"])
                if isinstance(failed.get("acceptanceCriteria"), list)
                else []
            ),
            context_hints={
                **failed_context,
                "replanOfTaskId": failed_task_id,
                "replanReason": failure_text,
                "replanIndex": len(replacements) + 1,
            },
            allowed_toolsets=(
                list(failed["allowedToolsets"])
                if isinstance(failed.get("allowedToolsets"), list)
                else []
            ),
            disallowed_tools=(
                list(failed["disallowedTools"])
                if isinstance(failed.get("disallowedTools"), list)
                else []
            ),
            ready_at=(
                datetime.now(timezone.utc) + timedelta(days=3650)
            ).isoformat(),
            max_attempts=int(failed.get("maxAttempts") or 1),
        )
        try:
            rewired = self.memory_store.rewire_task_dependencies(
                failed_task_id,
                str(replacement["id"]),
            )
        except Exception:
            self.memory_store.cancel_task(
                str(replacement["id"]),
                reason="orchestrator replan wiring failed",
            )
            raise
        replacement = (
            self.memory_store.get_task(str(replacement["id"]))
            or replacement
        )
        self._record_graph_event(
            normalized_root_id,
            "graph.replanned",
            "Failed child task replaced",
            {
                "failedTaskId": failed_task_id,
                "replacementTaskId": str(replacement["id"]),
                "replanIndex": len(replacements) + 1,
                **rewired,
            },
        )
        return {
            "rootTaskId": normalized_root_id,
            "failedTaskId": failed_task_id,
            "replacementTask": replacement,
            "replacementTaskId": str(replacement["id"]),
            "replanIndex": len(replacements) + 1,
            "maxReplans": max_replans,
            "rewired": rewired,
        }

    def review_completed_child(self, task_id: str) -> dict[str, object]:
        task = self.memory_store.get_task(task_id)
        if task is None:
            raise ValueError("task not found")
        status = str(task.get("status") or "")
        return {
            "taskId": task_id,
            "status": status,
            "accepted": status == "succeeded",
            "blocked": status == "blocked",
            "terminal": status in {"succeeded", "failed", "cancelled"},
            "result": task.get("result"),
            "error": task.get("error"),
        }

    def _synthesize_child_results(self, root: dict[str, object], children: list[dict[str, object]]) -> str:
        if self.model_client is not None:
            try:
                parsed = self._request_planning_json(
                    [
                        {
                            "role": "system",
                            "content": (
                                "You synthesize completed child-agent task outputs into a concise final result. "
                                "Return only JSON. Do not include markdown."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "Root task:\n"
                                f"title: {root.get('title')}\n"
                                f"body: {root.get('body') or ''}\n\n"
                                "Completed children:\n"
                                f"{json.dumps(_child_synthesis_payload(children), ensure_ascii=False)}\n\n"
                                "Return JSON in this exact shape:\n"
                                '{"summary":"short synthesis","result":"final user-facing task result"}'
                            ),
                        },
                    ]
                )
                result = str(parsed.get("result") or parsed.get("summary") or "").strip()
                if result:
                    return result
            except Exception as error:
                logger.info("Model-backed task synthesis failed taskId=%s error=%s; using fallback", root.get("id"), error)
        return _fallback_child_summary(children)

    def _record_graph_event(
        self,
        root_task_id: str,
        event_type: str,
        message: str,
        metadata: dict[str, object],
    ) -> None:
        try:
            self.memory_store.record_task_event(
                root_task_id,
                event_type=event_type,
                message=message,
                metadata={"source": "orchestrator", **metadata},
            )
        except Exception as error:
            logger.info("Failed to record graph event rootTaskId=%s type=%s error=%s", root_task_id, event_type, error)

    def _request_planning_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        if self.model_client is None:
            raise RuntimeError("planning model is not configured")
        payload = {
            "model": self.model_client.model,
            "messages": messages,
            "stream": False,
            "temperature": 0,
        }
        data = self.model_client.post_chat_completion(payload)
        message = first_choice_message(data)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("planning provider returned empty content")
        return parse_json_object_from_text(content)


def _child_synthesis_payload(children: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "id": child.get("id"),
            "title": child.get("title"),
            "workerProfile": child.get("workerProfile"),
            "status": child.get("status"),
            "result": child.get("result"),
            "error": child.get("error"),
        }
        for child in children
    ]


def _fallback_child_summary(children: list[dict[str, object]]) -> str:
    lines = ["Task graph child results:"]
    for index, child in enumerate(children, start=1):
        title = str(child.get("title") or child.get("id") or f"Task {index}").strip()
        status = str(child.get("status") or "unknown")
        result = str(child.get("result") or child.get("error") or "").strip()
        if result:
            lines.append(f"{index}. {title} [{status}]: {result}")
        else:
            lines.append(f"{index}. {title} [{status}]")
    return "\n".join(lines)


def _parse_tasks(raw_tasks: object, *, max_children: int) -> list[GraphTaskSpec]:
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("graph.tasks must be a non-empty array")
    if len(raw_tasks) > max(1, max_children):
        raise ValueError(f"graph.tasks must contain at most {max_children} tasks")
    seen: set[str] = set()
    tasks: list[GraphTaskSpec] = []
    for index, raw in enumerate(raw_tasks, start=1):
        if not isinstance(raw, dict):
            raise ValueError("each graph task must be an object")
        temp_id = str(raw.get("tempId") or raw.get("temp_id") or f"task-{index}").strip()
        title = str(raw.get("title") or "").strip()
        if not temp_id:
            raise ValueError("graph task tempId is required")
        if temp_id in seen:
            raise ValueError(f"duplicate graph task tempId: {temp_id}")
        if not title:
            raise ValueError(f"graph task {temp_id} title is required")
        seen.add(temp_id)
        tasks.append(
            GraphTaskSpec(
                temp_id=temp_id,
                title=title,
                body=str(raw.get("body") or "").strip(),
                worker_profile=_optional_string(raw.get("workerProfile") or raw.get("worker_profile")),
                acceptance_criteria=_list(raw.get("acceptanceCriteria") or raw.get("acceptance_criteria")),
                context_hints=_dict(raw.get("contextHints") or raw.get("context_hints")),
                allowed_toolsets=_string_list(raw.get("allowedToolsets") or raw.get("allowed_toolsets")),
                disallowed_tools=_string_list(raw.get("disallowedTools") or raw.get("disallowed_tools")),
                depends_on=[str(item).strip() for item in _list(raw.get("dependsOn") or raw.get("depends_on")) if str(item).strip()],
            )
        )
    return tasks


def _normalize_and_validate_task_policies(tasks: list[GraphTaskSpec]) -> list[GraphTaskSpec]:
    normalized: list[GraphTaskSpec] = []
    for task in tasks:
        profile = (task.worker_profile or DEFAULT_WORKER_PROFILE).strip().lower()
        if profile not in ALLOWED_WORKER_PROFILES:
            raise ValueError(f"graph task {task.temp_id} has unsupported workerProfile: {task.worker_profile}")
        allowed_toolsets = _dedupe_strings([str(item).strip().lower() for item in task.allowed_toolsets if str(item).strip()])
        unknown_toolsets = [item for item in allowed_toolsets if item not in KNOWN_TOOLSETS]
        if unknown_toolsets:
            raise ValueError(f"graph task {task.temp_id} has unknown allowedToolsets: {', '.join(unknown_toolsets)}")
        allowed_by_profile = PROFILE_TOOLSET_POLICY[profile]
        forbidden_toolsets = [item for item in allowed_toolsets if item not in allowed_by_profile]
        if forbidden_toolsets:
            raise ValueError(
                f"graph task {task.temp_id} workerProfile {profile} cannot allow toolsets: {', '.join(forbidden_toolsets)}"
            )
        if not allowed_toolsets:
            allowed_toolsets = list(DEFAULT_PROFILE_TOOLSETS[profile])
        normalized.append(
            GraphTaskSpec(
                temp_id=task.temp_id,
                title=task.title,
                body=task.body,
                worker_profile=profile,
                acceptance_criteria=task.acceptance_criteria,
                context_hints=task.context_hints,
                allowed_toolsets=allowed_toolsets,
                disallowed_tools=_dedupe_strings(task.disallowed_tools),
                depends_on=task.depends_on,
            )
        )
    return normalized


def _parse_edges(raw_edges: object, tasks: list[GraphTaskSpec]) -> list[GraphEdgeSpec]:
    task_ids = {task.temp_id for task in tasks}
    edges: list[GraphEdgeSpec] = []
    for task in tasks:
        for dependency in task.depends_on:
            if dependency not in task_ids:
                raise ValueError(f"unknown dependency tempId: {dependency}")
            edges.append(GraphEdgeSpec(from_temp_id=dependency, to_temp_id=task.temp_id, metadata={"source": "dependsOn"}))
    if raw_edges is None:
        return _dedupe_edges(edges)
    if not isinstance(raw_edges, list):
        raise ValueError("graph.edges must be an array")
    for raw in raw_edges:
        if not isinstance(raw, dict):
            raise ValueError("each graph edge must be an object")
        from_temp_id = str(raw.get("from") or raw.get("fromTempId") or raw.get("from_temp_id") or "").strip()
        to_temp_id = str(raw.get("to") or raw.get("toTempId") or raw.get("to_temp_id") or "").strip()
        if from_temp_id not in task_ids:
            raise ValueError(f"unknown edge from tempId: {from_temp_id}")
        if to_temp_id not in task_ids:
            raise ValueError(f"unknown edge to tempId: {to_temp_id}")
        edges.append(
            GraphEdgeSpec(
                from_temp_id=from_temp_id,
                to_temp_id=to_temp_id,
                edge_type=str(raw.get("type") or raw.get("edgeType") or raw.get("edge_type") or "blocks").strip(),
                required_status=str(raw.get("requiredStatus") or raw.get("required_status") or "succeeded").strip(),
                metadata=_dict(raw.get("metadata")),
            )
        )
    return _dedupe_edges(edges)


def _dedupe_edges(edges: list[GraphEdgeSpec]) -> list[GraphEdgeSpec]:
    deduped: dict[tuple[str, str, str], GraphEdgeSpec] = {}
    order: list[tuple[str, str, str]] = []
    for edge in edges:
        key = (edge.from_temp_id, edge.to_temp_id, edge.edge_type)
        if key not in deduped:
            order.append(key)
        deduped[key] = edge
    return [deduped[key] for key in order]


def _validate_acyclic(tasks: list[GraphTaskSpec], edges: list[GraphEdgeSpec]) -> None:
    outgoing: dict[str, list[str]] = {task.temp_id: [] for task in tasks}
    for edge in edges:
        outgoing.setdefault(edge.from_temp_id, []).append(edge.to_temp_id)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            raise ValueError("task graph contains a dependency cycle")
        visiting.add(node)
        for child in outgoing.get(node, []):
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for task in tasks:
        visit(task.temp_id)


def _optional_string(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return _dedupe_strings([str(item).strip() for item in value if str(item).strip()])


def _dedupe_strings(values: list[object]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}
