from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from amadeus.tools import ToolSpec, list_tool_specs


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TOOLS_CONFIG_PATH = REPO_ROOT / "configs" / "tools.yaml"
TOOL_NAME_ALIASES = {
    "time": "get_current_time",
}
VALID_PERMISSIONS = {"allow", "ask", "deny"}


class ToolRegistry:
    def __init__(
        self,
        specs: Iterable[ToolSpec] | None = None,
        config_path: Path = DEFAULT_TOOLS_CONFIG_PATH,
    ) -> None:
        source_specs = specs if specs is not None else list_tool_specs()
        self._specs = {spec.name: deepcopy(spec) for spec in source_specs}
        self._apply_config(parse_tools_config(config_path))

    def get(self, tool_name: str) -> ToolSpec | None:
        return self._specs.get(tool_name)

    def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        spec = self.get(tool_name)
        if not spec:
            raise KeyError(f"Unknown tool: {tool_name}")

        return spec.handler(args)

    def permission_state(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "displayName": spec.display_name,
                "enabled": spec.enabled,
                "permission": spec.permission,
            }
            for spec in self._specs.values()
        ]

    def enabled_schemas(self) -> list[dict[str, Any]]:
        return [
            spec.schema
            for spec in self._specs.values()
            if spec.enabled and spec.permission != "deny"
        ]

    def _apply_config(self, config: dict[str, dict[str, Any]]) -> None:
        for configured_name, entry in config.items():
            tool_name = TOOL_NAME_ALIASES.get(configured_name, configured_name)
            spec = self._specs.get(tool_name)
            if not spec:
                continue

            enabled = entry.get("enabled")
            if isinstance(enabled, bool):
                spec.enabled = enabled

            permission = entry.get("permission")
            if permission in VALID_PERMISSIONS:
                spec.permission = str(permission)


def parse_tools_config(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    entries: dict[str, dict[str, Any]] = {}
    in_tools = False
    current_tool: str | None = None

    for raw_line in lines:
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        trimmed = line.strip()

        if indent == 0:
            in_tools = trimmed == "tools:"
            current_tool = None
            continue

        if not in_tools:
            continue

        if indent == 2 and trimmed.endswith(":"):
            current_tool = trimmed[:-1]
            entries[current_tool] = {}
            continue

        if indent != 4 or not current_tool or ":" not in trimmed:
            continue

        key, value = trimmed.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key == "enabled":
            entries[current_tool][key] = parse_bool(value)
        elif key == "permission":
            entries[current_tool][key] = value

    return entries


def parse_bool(value: str) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None
