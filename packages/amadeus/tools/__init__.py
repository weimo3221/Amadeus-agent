from __future__ import annotations

from typing import Any

from amadeus.tools.base import ToolHandler, ToolPermission, ToolSpec, normalize_positive_int
from amadeus.tools.dice import DICE_TOOL_SPEC, roll_dice
from amadeus.tools.local_file_search import LOCAL_FILE_SEARCH_TOOL_SPEC, SEARCH_FILES_TOOL_SPEC, local_file_search, search_files
from amadeus.tools.patch import PATCH_TOOL_SPEC, patch
from amadeus.tools.read_file import READ_FILE_TOOL_SPEC, read_file
from amadeus.tools.time import TIME_TOOL_SPEC, get_current_time


DEFAULT_TOOL_SPECS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        TIME_TOOL_SPEC,
        DICE_TOOL_SPEC,
        SEARCH_FILES_TOOL_SPEC,
        LOCAL_FILE_SEARCH_TOOL_SPEC,
        READ_FILE_TOOL_SPEC,
        PATCH_TOOL_SPEC,
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
    "execute_tool",
    "get_current_time",
    "get_tool_spec",
    "list_tool_specs",
    "list_tools",
    "local_file_search",
    "normalize_positive_int",
    "patch",
    "read_file",
    "roll_dice",
    "search_files",
]
