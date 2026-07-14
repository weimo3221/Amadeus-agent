from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from amadeus.memory import MessageMemoryStore


TaskSubmitter = Callable[[str], None]


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
    ) -> None:
        self.memory_store = memory_store
        self.submit_task = submit_task

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
        edges = _parse_edges(graph.get("edges"), tasks)
        _validate_acyclic(tasks, edges)
        return {
            "ok": True,
            "tasks": [task.__dict__ for task in tasks],
            "edges": [edge.__dict__ for edge in edges],
            "taskCount": len(tasks),
            "edgeCount": len(edges),
        }

    def apply_task_graph(self, root_task_id: str, graph: dict[str, object]) -> dict[str, object]:
        root = self.memory_store.get_task(root_task_id)
        if root is None:
            raise ValueError("root task not found")
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

        return {
            "rootTaskId": root_id,
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
        return dispatched

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
                allowed_toolsets=_list(raw.get("allowedToolsets") or raw.get("allowed_toolsets")),
                disallowed_tools=_list(raw.get("disallowedTools") or raw.get("disallowed_tools")),
                depends_on=[str(item).strip() for item in _list(raw.get("dependsOn") or raw.get("depends_on")) if str(item).strip()],
            )
        )
    return tasks


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


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}
