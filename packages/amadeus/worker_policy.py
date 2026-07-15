from __future__ import annotations

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


def worker_toolsets_for_task(task: dict[str, object]) -> list[str]:
    profile = str(task.get("workerProfile") or DEFAULT_WORKER_PROFILE).strip().lower()
    if profile not in ALLOWED_WORKER_PROFILES:
        profile = DEFAULT_WORKER_PROFILE
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
