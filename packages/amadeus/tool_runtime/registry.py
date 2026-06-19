from __future__ import annotations

import concurrent.futures
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

from amadeus.tools import ToolSpec, list_tool_specs


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TOOLS_CONFIG_PATH = REPO_ROOT / "configs" / "tools.yaml"
TOOL_NAME_ALIASES = {
    "time": "get_current_time",
}
VALID_PERMISSIONS = {"allow", "ask", "deny"}


@dataclass(frozen=True)
class ToolContext:
    session_id: str
    cwd: Path = REPO_ROOT
    timeout_seconds: float | None = 30.0


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    output: dict[str, Any]
    ok: bool
    duration_ms: int
    failure_code: str | None = None


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

    def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        spec = self.get(tool_name)
        if not spec:
            raise KeyError(f"Unknown tool: {tool_name}")

        effective_context = context or ToolContext(session_id="default")
        start = perf_counter()
        try:
            output = run_with_timeout(spec.handler, args, effective_context.timeout_seconds)
            ok = "error" not in output
            failure_code = None if ok else "tool_error"
        except TimeoutError:
            output = {"error": f"Tool timed out: {tool_name}"}
            ok = False
            failure_code = "tool_timeout"
        except Exception as error:
            output = {"error": str(error)}
            ok = False
            failure_code = "tool_exception"

        duration_ms = max(0, round((perf_counter() - start) * 1000))
        return ToolResult(
            tool_name=tool_name,
            output=output,
            ok=ok,
            duration_ms=duration_ms,
            failure_code=failure_code,
        )

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


def run_with_timeout(handler: Any, args: dict[str, Any], timeout_seconds: float | None) -> dict[str, Any]:
    if timeout_seconds is None or timeout_seconds <= 0:
        return handler(args)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(handler, args)
    try:
        return future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError as error:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise TimeoutError from error

    finally:
        if future.done():
            executor.shutdown(wait=False, cancel_futures=True)
