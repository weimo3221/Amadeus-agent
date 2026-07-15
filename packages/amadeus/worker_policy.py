from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_WORKER_PROFILE = "planner"
ALLOWED_WORKER_PROFILES = {"researcher", "planner", "coder", "reviewer", "synthesizer"}
KNOWN_TOOLSETS = {"read", "search", "memory", "web", "plan", "task", "skills", "patch", "write", "terminal", "code", "browser", "vision"}
PROFILE_TOOLSET_POLICY: dict[str, set[str]] = {
    "researcher": {"read", "search", "memory", "web"},
    "planner": {"read", "search", "memory", "plan", "task", "skills"},
    "coder": {"read", "search", "memory", "web", "skills", "patch", "write", "terminal", "code", "browser", "vision"},
    "reviewer": {"read", "search", "memory"},
    "synthesizer": {"read", "memory"},
}
DEFAULT_PROFILE_TOOLSETS: dict[str, list[str]] = {
    "researcher": ["read", "search", "memory", "web"],
    "planner": ["read", "search", "memory", "plan"],
    "coder": ["read", "search", "memory", "patch"],
    "reviewer": ["read", "search", "memory"],
    "synthesizer": ["read", "memory"],
}
BASE_WORKER_TOOLS = {"get_current_time", "clarify"}
PROFILE_AUTO_APPROVED_ASK_TOOLS: dict[str, set[str]] = {
    "researcher": {"web_extract"},
    "planner": set(),
    "coder": {"patch"},
    "reviewer": set(),
    "synthesizer": set(),
}
TOOLSET_TOOL_NAMES: dict[str, set[str]] = {
    "read": {"search_files", "read_file", "read_session_messages"},
    "search": {"search_files", "search_memory"},
    "memory": {"search_memory", "search_memory_items", "read_memory"},
    "web": {"web_search", "web_extract"},
    "plan": {"update_plan"},
    "task": {"create_task", "list_tasks", "cancel_task"},
    "skills": {"skills_list", "skill_view"},
    "patch": {"patch"},
    "write": {"write_file"},
    "terminal": {"terminal", "process"},
    "code": {"execute_code"},
    "browser": {
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_scroll",
        "browser_back",
        "browser_press",
        "browser_get_images",
        "browser_vision",
        "browser_console",
        "browser_cdp",
        "browser_dialog",
    },
    "vision": {"vision_analyze"},
}


@dataclass(frozen=True)
class WorkerRuntimeScope:
    worker_profile: str
    allowed_toolsets: tuple[str, ...]
    allowed_tool_names: frozenset[str]
    workspace_path: str | None = None
    approved_ask_tool_names: frozenset[str] = frozenset()


def build_worker_runtime_scope(task: dict[str, object]) -> WorkerRuntimeScope:
    profile = worker_profile_for_task(task)
    toolsets = tuple(worker_toolsets_for_task(task))
    allowed_tool_names = frozenset(worker_tool_names_for_task(task))
    return WorkerRuntimeScope(
        worker_profile=profile,
        allowed_toolsets=toolsets,
        allowed_tool_names=allowed_tool_names,
        workspace_path=worker_workspace_path_for_task(task),
        approved_ask_tool_names=frozenset(
            name for name in worker_approved_ask_tool_names_for_task(task) if name in allowed_tool_names
        ),
    )


def worker_profile_for_task(task: dict[str, object]) -> str:
    profile = str(task.get("workerProfile") or DEFAULT_WORKER_PROFILE).strip().lower()
    return profile if profile in ALLOWED_WORKER_PROFILES else DEFAULT_WORKER_PROFILE


def worker_toolsets_for_task(task: dict[str, object]) -> list[str]:
    profile = worker_profile_for_task(task)
    raw_allowed = task.get("allowedToolsets")
    explicit = _string_list(raw_allowed)
    allowed_by_profile = PROFILE_TOOLSET_POLICY[profile]
    filtered = [toolset for toolset in explicit if toolset in allowed_by_profile]
    return filtered or list(DEFAULT_PROFILE_TOOLSETS[profile])


def worker_tool_names_for_task(task: dict[str, object]) -> set[str]:
    toolsets = worker_toolsets_for_task(task)
    names = set(BASE_WORKER_TOOLS)
    for toolset in toolsets:
        names.update(TOOLSET_TOOL_NAMES.get(toolset, set()))
    names.difference_update(_string_list(task.get("disallowedTools")))
    return names


def worker_workspace_path_for_task(task: dict[str, object]) -> str | None:
    hints = task.get("contextHints")
    if not isinstance(hints, dict):
        return None
    for key in ("workspacePath", "workspace", "cwd"):
        raw_value = hints.get(key)
        value = str(raw_value or "").strip()
        if value:
            return value
    return None


def worker_approved_ask_tool_names_for_task(task: dict[str, object]) -> set[str]:
    checkpoint = task.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return set()
    if str(checkpoint.get("phase") or "") != "approval_resume_requested":
        return set()
    names = set(_string_list(checkpoint.get("approvedTools")))
    single = str(checkpoint.get("approvedToolName") or "").strip()
    if single:
        names.add(single)
    return names


def worker_permission_decision(scope: WorkerRuntimeScope | None, tool_name: str, permission: str) -> str:
    if scope is None or permission != "ask":
        return "prompt"
    if tool_name in scope.approved_ask_tool_names and tool_name in scope.allowed_tool_names:
        return "auto_approve"
    if tool_name in PROFILE_AUTO_APPROVED_ASK_TOOLS.get(scope.worker_profile, set()) and tool_name in scope.allowed_tool_names:
        return "auto_approve"
    return "deny"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    output: list[str] = []
    for item in value:
        normalized = str(item or "").strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output
