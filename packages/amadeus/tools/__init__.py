from __future__ import annotations

from typing import Any

from amadeus.tools.browser import BROWSER_TOOL_SPECS
from amadeus.tools.base import ToolHandler, ToolPermission, ToolSpec, normalize_positive_int
from amadeus.tools.clarify import CLARIFY_TOOL_SPEC, clarify
from amadeus.tools.delegate import DELEGATE_TASK_TOOL_SPEC, delegate_task
from amadeus.tools.dice import DICE_TOOL_SPEC, roll_dice
from amadeus.tools.execute_code import EXECUTE_CODE_TOOL_SPEC, execute_code
from amadeus.tools.identity import UPDATE_CURRENT_ROLE_IDENTITY_TOOL_SPEC, update_current_role_identity
from amadeus.tools.search_files import SEARCH_FILES_TOOL_SPEC, search_files
from amadeus.tools.patch import PATCH_TOOL_SPEC, patch
from amadeus.tools.plan import UPDATE_PLAN_TOOL_SPEC, update_plan
from amadeus.tools.read_file import READ_FILE_TOOL_SPEC, read_file
from amadeus.tools.read_session_messages import READ_SESSION_MESSAGES_TOOL_SPEC, read_session_messages
from amadeus.tools.search_memory import SEARCH_MEMORY_TOOL_SPEC, search_memory
from amadeus.tools.scheduled_jobs import SCHEDULE_MESSAGE_TOOL_SPEC, schedule_message
from amadeus.tools.skills import SKILL_MANAGE_TOOL_SPEC, SKILLS_LIST_TOOL_SPEC, SKILL_VIEW_TOOL_SPEC, skill_manage, skill_view, skills_list
from amadeus.tools.stable_memory import READ_MEMORY_TOOL_SPEC, UPDATE_MEMORY_TOOL_SPEC, read_memory, update_memory
from amadeus.tools.structured_memory import (
    MEMORY_ADD_TOOL_SPEC,
    MEMORY_FORGET_TOOL_SPEC,
    MEMORY_REPLACE_TOOL_SPEC,
    SEARCH_MEMORY_ITEMS_TOOL_SPEC,
    memory_add,
    memory_forget,
    memory_replace,
    search_memory_items,
)
from amadeus.tools.tasks import (
    CANCEL_TASK_TOOL_SPEC,
    CREATE_TASK_TOOL_SPEC,
    LIST_TASKS_TOOL_SPEC,
    cancel_task,
    create_task,
    list_tasks,
)
from amadeus.tools.terminal import PROCESS_TOOL_SPEC, TERMINAL_TOOL_SPEC, process, terminal
from amadeus.tools.time import TIME_TOOL_SPEC, get_current_time
from amadeus.tools.todo import TODO_TOOL_SPEC, todo
from amadeus.tools.vision import VISION_ANALYZE_TOOL_SPEC, vision_analyze
from amadeus.tools.web import WEB_EXTRACT_TOOL_SPEC, WEB_SEARCH_TOOL_SPEC, web_extract, web_search
from amadeus.tools.write_file import WRITE_FILE_TOOL_SPEC, write_file


DEFAULT_TOOL_SPECS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        TIME_TOOL_SPEC,
        DICE_TOOL_SPEC,
        TERMINAL_TOOL_SPEC,
        PROCESS_TOOL_SPEC,
        WEB_SEARCH_TOOL_SPEC,
        WEB_EXTRACT_TOOL_SPEC,
        VISION_ANALYZE_TOOL_SPEC,
        CLARIFY_TOOL_SPEC,
        EXECUTE_CODE_TOOL_SPEC,
        DELEGATE_TASK_TOOL_SPEC,
        CREATE_TASK_TOOL_SPEC,
        LIST_TASKS_TOOL_SPEC,
        CANCEL_TASK_TOOL_SPEC,
        SCHEDULE_MESSAGE_TOOL_SPEC,
        TODO_TOOL_SPEC,
        UPDATE_CURRENT_ROLE_IDENTITY_TOOL_SPEC,
        SEARCH_FILES_TOOL_SPEC,
        READ_FILE_TOOL_SPEC,
        SKILLS_LIST_TOOL_SPEC,
        SKILL_VIEW_TOOL_SPEC,
        SKILL_MANAGE_TOOL_SPEC,
        UPDATE_PLAN_TOOL_SPEC,
        READ_SESSION_MESSAGES_TOOL_SPEC,
        SEARCH_MEMORY_TOOL_SPEC,
        READ_MEMORY_TOOL_SPEC,
        UPDATE_MEMORY_TOOL_SPEC,
        SEARCH_MEMORY_ITEMS_TOOL_SPEC,
        MEMORY_ADD_TOOL_SPEC,
        MEMORY_REPLACE_TOOL_SPEC,
        MEMORY_FORGET_TOOL_SPEC,
        *BROWSER_TOOL_SPECS,
        PATCH_TOOL_SPEC,
        WRITE_FILE_TOOL_SPEC,
    )
}

TOOLS: dict[str, ToolHandler] = {name: spec.handler for name, spec in DEFAULT_TOOL_SPECS.items()}


def list_tools() -> list[str]:
    return sorted(TOOLS)


def get_tool_spec(tool_name: str) -> ToolSpec | None:
    return DEFAULT_TOOL_SPECS.get(tool_name)


def list_tool_specs() -> list[ToolSpec]:
    return [DEFAULT_TOOL_SPECS[name] for name in sorted(DEFAULT_TOOL_SPECS)]


def execute_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = TOOLS.get(tool_name)
    if not handler:
        raise KeyError(f"Unknown tool: {tool_name}")

    return handler(args)


__all__ = [
    "DEFAULT_TOOL_SPECS",
    "TOOLS",
    "ToolHandler",
    "ToolPermission",
    "ToolSpec",
    "BROWSER_TOOL_SPECS",
    "cancel_task",
    "clarify",
    "create_task",
    "execute_tool",
    "execute_code",
    "delegate_task",
    "get_current_time",
    "get_tool_spec",
    "list_tool_specs",
    "list_tools",
    "list_tasks",
    "memory_add",
    "memory_forget",
    "memory_replace",
    "normalize_positive_int",
    "patch",
    "process",
    "read_file",
    "read_memory",
    "read_session_messages",
    "roll_dice",
    "search_files",
    "search_memory",
    "search_memory_items",
    "schedule_message",
    "skill_view",
    "skill_manage",
    "skills_list",
    "todo",
    "terminal",
    "update_current_role_identity",
    "update_memory",
    "update_plan",
    "vision_analyze",
    "web_extract",
    "web_search",
    "write_file",
]
