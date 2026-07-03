from __future__ import annotations

import concurrent.futures
import inspect
import json
import logging
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable

from amadeus.mcp import McpServerConfig, build_mcp_tool_specs
from amadeus.tools import ToolSpec, list_tool_specs


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TOOLS_CONFIG_PATH = REPO_ROOT / "configs" / "tools.yaml"
TOOL_NAME_ALIASES = {
    "time": "get_current_time",
}
VALID_PERMISSIONS = {"allow", "ask", "deny"}
DEFAULT_MAX_MODEL_OUTPUT_CHARS = 4000
DEFAULT_OUTPUT_PREVIEW_CHARS = 1000
SEARCH_FILES_MODEL_RESULT_LIMIT = 5
SEARCH_FILES_MODEL_PREVIEW_CHARS = 160
MEMORY_SEARCH_MODEL_RESULT_LIMIT = 5
MEMORY_SEARCH_MODEL_PREVIEW_CHARS = 240
MEMORY_ITEMS_MODEL_RESULT_LIMIT = 8
MEMORY_ITEMS_MODEL_CONTENT_CHARS = 240
SESSION_MESSAGES_MODEL_RESULT_LIMIT = 8
SESSION_MESSAGES_MODEL_CONTENT_CHARS = 360
MEMORY_PROVIDER_TOOL_NAMES = {
    "search_memory",
    "search_memory_items",
    "memory_add",
    "memory_replace",
    "memory_forget",
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    session_id: str
    cwd: Path = REPO_ROOT
    memory_store: Any | None = None
    task_worker: Any | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    permission_request_id: str | None = None
    permission_decision: str | None = None
    workspace_epoch: int | None = None
    audit_metadata: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float | None = 30.0
    cancel_event: threading.Event | None = None
    max_model_output_chars: int = DEFAULT_MAX_MODEL_OUTPUT_CHARS
    output_preview_chars: int = DEFAULT_OUTPUT_PREVIEW_CHARS

    def is_cancelled(self) -> bool:
        return bool(self.cancel_event and self.cancel_event.is_set())

    def request_cancel(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    output: dict[str, Any]
    model_output: dict[str, Any]
    ok: bool
    duration_ms: int
    failure_code: str | None = None
    output_preview: str | None = None
    output_truncated: bool = False


class ToolRegistry:
    def __init__(
        self,
        specs: Iterable[ToolSpec] | None = None,
        config_path: Path = DEFAULT_TOOLS_CONFIG_PATH,
        memory_tool_specs: Iterable[ToolSpec] | None = None,
    ) -> None:
        config = parse_tools_config(config_path)
        source_specs = specs if specs is not None else list_tool_specs()
        self._specs = {spec.name: deepcopy(spec) for spec in source_specs}
        if memory_tool_specs is not None:
            for tool_name in MEMORY_PROVIDER_TOOL_NAMES:
                self._specs.pop(tool_name, None)
            for spec in memory_tool_specs:
                self._specs[spec.name] = deepcopy(spec)
        if specs is None:
            for mcp_spec in build_mcp_tool_specs(
                parse_mcp_servers_config(config.get("mcp", {})),
                default_permission=str(config.get("mcp", {}).get("permission") or "ask"),
            ):
                self._specs[mcp_spec.name] = mcp_spec
        self._apply_config(config)

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
            logger.info("ToolRegistry execute rejected unknown tool toolName=%s", tool_name)
            raise KeyError(f"Unknown tool: {tool_name}")

        effective_context = context or ToolContext(session_id="default")
        logger.info(
            "ToolRegistry execute starting sessionId=%s turnId=%s toolCallId=%s toolName=%s timeoutSeconds=%s argKeys=%s",
            effective_context.session_id,
            effective_context.turn_id,
            effective_context.tool_call_id,
            tool_name,
            effective_context.timeout_seconds,
            sorted(args.keys()),
        )
        start = perf_counter()
        try:
            if effective_context.is_cancelled():
                logger.info(
                    "ToolRegistry execute cancelled before handler sessionId=%s turnId=%s toolCallId=%s toolName=%s",
                    effective_context.session_id,
                    effective_context.turn_id,
                    effective_context.tool_call_id,
                    tool_name,
                )
                raise ToolCancelledError

            output = run_with_timeout(spec.handler, args, effective_context)
            if effective_context.is_cancelled():
                logger.info(
                    "ToolRegistry execute cancelled after handler sessionId=%s turnId=%s toolCallId=%s toolName=%s",
                    effective_context.session_id,
                    effective_context.turn_id,
                    effective_context.tool_call_id,
                    tool_name,
                )
                raise ToolCancelledError

            ok = "error" not in output
            failure_code = None if ok else "tool_error"
        except ToolCancelledError:
            logger.info("ToolRegistry execute cancelled toolName=%s", tool_name)
            output = {"error": f"Tool cancelled: {tool_name}"}
            ok = False
            failure_code = "tool_cancelled"
        except TimeoutError:
            logger.info("ToolRegistry execute timed out toolName=%s timeoutSeconds=%s", tool_name, effective_context.timeout_seconds)
            output = {"error": f"Tool timed out: {tool_name}"}
            ok = False
            failure_code = "tool_timeout"
        except Exception as error:
            logger.info("ToolRegistry execute handler exception toolName=%s error=%s", tool_name, error)
            output = {"error": str(error)}
            ok = False
            failure_code = "tool_exception"

        model_output, output_preview, output_truncated = normalize_tool_output_for_model(
            tool_name,
            output,
            ok=ok,
            max_chars=effective_context.max_model_output_chars,
            preview_chars=effective_context.output_preview_chars,
        )
        duration_ms = max(0, round((perf_counter() - start) * 1000))
        logger.info(
            "ToolRegistry execute finished sessionId=%s turnId=%s toolCallId=%s toolName=%s ok=%s failureCode=%s durationMs=%s outputTruncated=%s",
            effective_context.session_id,
            effective_context.turn_id,
            effective_context.tool_call_id,
            tool_name,
            ok,
            failure_code,
            duration_ms,
            output_truncated,
        )
        return ToolResult(
            tool_name=tool_name,
            output=output,
            model_output=model_output,
            ok=ok,
            duration_ms=duration_ms,
            failure_code=failure_code,
            output_preview=output_preview,
            output_truncated=output_truncated,
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

    def enabled_prompt_hints(self) -> list[dict[str, str]]:
        return [
            {
                "name": spec.name,
                "hint": spec.prompt_hint,
            }
            for spec in self._specs.values()
            if spec.enabled and spec.permission != "deny" and spec.prompt_hint
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

        if current_tool == "mcp" and indent in {4, 6, 8}:
            parse_mcp_config_line(entries[current_tool], indent, trimmed)

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


def parse_mcp_config_line(entry: dict[str, Any], indent: int, trimmed: str) -> None:
    servers = entry.setdefault("servers", [])
    if not isinstance(servers, list):
        return

    if indent in {4, 6} and trimmed.startswith("- "):
        item = trimmed[2:].strip()
        server: dict[str, Any] = {}
        if item and ":" in item:
            key, value = item.split(":", 1)
            server[key.strip()] = parse_config_scalar(value.strip())
        servers.append(server)
        return

    if indent == 6 and trimmed.endswith(":"):
        entry[trimmed[:-1].strip()] = {}
        return

    if indent in {6, 8} and servers and ":" in trimmed:
        key, value = trimmed.split(":", 1)
        servers[-1][key.strip()] = parse_config_scalar(value.strip())


def parse_config_scalar(value: str) -> Any:
    if value in {"true", "false"}:
        return value == "true"
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def parse_mcp_servers_config(entry: dict[str, Any]) -> list[McpServerConfig]:
    if not entry.get("enabled"):
        return []
    raw_servers = entry.get("servers")
    if not isinstance(raw_servers, list):
        return []

    servers: list[McpServerConfig] = []
    for raw_server in raw_servers:
        if not isinstance(raw_server, dict):
            continue
        name = raw_server.get("name")
        url = raw_server.get("url")
        if not isinstance(name, str) or not name.strip() or not isinstance(url, str) or not url.strip():
            continue
        permission = raw_server.get("permission")
        timeout_seconds = raw_server.get("timeoutSeconds")
        servers.append(McpServerConfig(
            name=name.strip(),
            url=url.strip(),
            enabled=raw_server.get("enabled") is not False,
            permission=str(permission) if permission in VALID_PERMISSIONS else None,
            timeout_seconds=float(timeout_seconds) if isinstance(timeout_seconds, int | float) else 10.0,
        ))
    return servers


def parse_bool(value: str) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


class ToolCancelledError(Exception):
    pass


def run_with_timeout(handler: Any, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    timeout_seconds = context.timeout_seconds
    if timeout_seconds is None or timeout_seconds <= 0:
        return call_tool_handler(handler, args, context)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(call_tool_handler, handler, args, context)
    try:
        return future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError as error:
        context.request_cancel()
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise TimeoutError from error

    finally:
        if future.done():
            executor.shutdown(wait=False, cancel_futures=True)


def call_tool_handler(handler: Any, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    if context.is_cancelled():
        raise ToolCancelledError

    if accepts_tool_context(handler):
        output = handler(args, context)
    else:
        output = handler(args)

    if context.is_cancelled():
        raise ToolCancelledError

    return output


def accepts_tool_context(handler: Any) -> bool:
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return False

    positional_capacity = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    has_varargs = any(
        parameter.kind == inspect.Parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    )
    return has_varargs or len(positional_capacity) >= 2


def normalize_tool_output_for_model(
    tool_name: str,
    output: dict[str, Any],
    ok: bool,
    max_chars: int,
    preview_chars: int,
) -> tuple[dict[str, Any], str | None, bool]:
    model_output, policy_preview, policy_truncated = apply_tool_result_policy(tool_name, output, ok, preview_chars)
    serialized = json.dumps(model_output, ensure_ascii=False, sort_keys=True)
    if policy_truncated and (tool_name == "read_session_messages" or len(serialized) <= max_chars):
        return model_output, policy_preview, True

    if not ok or len(serialized) <= max_chars:
        return model_output, policy_preview, policy_truncated

    if tool_name in {"read_file", "patch", "write_file"}:
        return model_output, policy_preview, policy_truncated

    preview_limit = max(1, min(preview_chars, max_chars))
    preview = serialized[:preview_limit]
    return (
        {
            "_amadeus_result_truncated": True,
            "tool_name": tool_name,
            "original_char_count": len(serialized),
            "preview": preview,
        },
        preview,
        True,
    )


def apply_tool_result_policy(
    tool_name: str,
    output: dict[str, Any],
    ok: bool,
    preview_chars: int,
) -> tuple[dict[str, Any], str | None, bool]:
    if not ok:
        return output, None, False

    if tool_name.startswith("mcp__"):
        return normalize_mcp_output(tool_name, output, preview_chars)

    if tool_name == "search_files":
        return normalize_search_files_output(tool_name, output, preview_chars)

    if tool_name == "search_memory":
        return normalize_search_memory_output(tool_name, output, preview_chars)

    if tool_name == "search_memory_items":
        return normalize_search_memory_items_output(tool_name, output, preview_chars)

    if tool_name == "read_session_messages":
        return normalize_read_session_messages_output(tool_name, output, preview_chars)

    return output, None, False


def normalize_search_files_output(
    tool_name: str,
    output: dict[str, Any],
    preview_chars: int,
) -> tuple[dict[str, Any], str | None, bool]:
    raw_results = output.get("results")
    if not isinstance(raw_results, list):
        return output, None, False

    preview_limit = max(1, min(preview_chars, SEARCH_FILES_MODEL_PREVIEW_CHARS))
    model_results: list[dict[str, Any]] = []
    truncated = len(raw_results) > SEARCH_FILES_MODEL_RESULT_LIMIT

    for raw_result in raw_results[:SEARCH_FILES_MODEL_RESULT_LIMIT]:
        if not isinstance(raw_result, dict):
            model_results.append({"preview": str(raw_result)[:preview_limit]})
            truncated = True
            continue

        model_result: dict[str, Any] = {}
        for key in ("path", "line", "match"):
            if key in raw_result:
                model_result[key] = raw_result[key]

        raw_preview = raw_result.get("preview")
        if raw_preview is not None:
            preview = str(raw_preview)
            if len(preview) > preview_limit:
                preview = preview[:preview_limit]
                truncated = True
            model_result["preview"] = preview

        model_results.append(model_result)

    if not truncated:
        return output, None, False

    result_count = len(raw_results)
    model_output = {
        "_amadeus_result_truncated": True,
        "_amadeus_result_policy": "search_files_v1",
        "tool_name": tool_name,
        "query": output.get("query"),
        "target": output.get("target"),
        "root": output.get("root"),
        "maxResults": output.get("maxResults"),
        "scannedFiles": output.get("scannedFiles"),
        "resultCount": result_count,
        "includedResults": len(model_results),
        "omittedResults": max(0, result_count - len(model_results)),
        "results": model_results,
    }
    preview = json.dumps(model_output, ensure_ascii=False, sort_keys=True)
    return model_output, preview[:preview_limit], True


def normalize_mcp_output(
    tool_name: str,
    output: dict[str, Any],
    preview_chars: int,
) -> tuple[dict[str, Any], str | None, bool]:
    result = output.get("result")
    if not isinstance(result, dict):
        return output, None, False

    serialized_result = json.dumps(result, ensure_ascii=False, sort_keys=True)
    preview_limit = max(1, min(preview_chars, DEFAULT_OUTPUT_PREVIEW_CHARS))
    if len(serialized_result) <= preview_limit:
        return output, None, False

    model_output = {
        "_amadeus_result_truncated": True,
        "_amadeus_result_policy": "mcp_v1",
        "tool_name": tool_name,
        "server": output.get("server"),
        "tool": output.get("tool"),
        "resultCharCount": len(serialized_result),
        "resultPreview": serialized_result[:preview_limit],
    }
    return model_output, model_output["resultPreview"], True


def normalize_search_memory_output(
    tool_name: str,
    output: dict[str, Any],
    preview_chars: int,
) -> tuple[dict[str, Any], str | None, bool]:
    raw_results = output.get("results")
    if not isinstance(raw_results, list):
        return output, None, False

    preview_limit = max(1, min(preview_chars, MEMORY_SEARCH_MODEL_PREVIEW_CHARS))
    model_results: list[dict[str, Any]] = []
    truncated = len(raw_results) > MEMORY_SEARCH_MODEL_RESULT_LIMIT

    for raw_result in raw_results[:MEMORY_SEARCH_MODEL_RESULT_LIMIT]:
        if not isinstance(raw_result, dict):
            model_results.append({"snippet": str(raw_result)[:preview_limit]})
            truncated = True
            continue

        model_result: dict[str, Any] = {}
        for key in ("id", "sessionId", "role", "createdAt"):
            if key in raw_result:
                model_result[key] = raw_result[key]

        preview = str(raw_result.get("snippet") or raw_result.get("content") or "")
        if len(preview) > preview_limit:
            preview = preview[:preview_limit]
            truncated = True
        model_result["snippet"] = preview
        model_results.append(model_result)

    if not truncated:
        return output, None, False

    result_count = len(raw_results)
    model_output = {
        "_amadeus_result_truncated": True,
        "_amadeus_result_policy": "search_memory_v1",
        "tool_name": tool_name,
        "query": output.get("query"),
        "sessionId": output.get("sessionId"),
        "includeAllSessions": output.get("includeAllSessions"),
        "resultCount": result_count,
        "includedResults": len(model_results),
        "omittedResults": max(0, result_count - len(model_results)),
        "results": model_results,
    }
    preview = json.dumps(model_output, ensure_ascii=False, sort_keys=True)
    return model_output, preview[:preview_limit], True


def normalize_search_memory_items_output(
    tool_name: str,
    output: dict[str, Any],
    preview_chars: int,
) -> tuple[dict[str, Any], str | None, bool]:
    raw_items = output.get("items")
    if not isinstance(raw_items, list):
        return output, None, False

    preview_limit = max(1, min(preview_chars, MEMORY_ITEMS_MODEL_CONTENT_CHARS))
    model_items: list[dict[str, Any]] = []
    truncated = len(raw_items) > MEMORY_ITEMS_MODEL_RESULT_LIMIT

    for raw_item in raw_items[:MEMORY_ITEMS_MODEL_RESULT_LIMIT]:
        if not isinstance(raw_item, dict):
            model_items.append({"content": str(raw_item)[:preview_limit]})
            truncated = True
            continue

        model_item: dict[str, Any] = {}
        for key in (
            "memoryItemId",
            "scope",
            "confidence",
            "sourceSessionId",
            "sourceMessageId",
            "createdAt",
            "updatedAt",
        ):
            if key in raw_item:
                model_item[key] = raw_item[key]

        content = str(raw_item.get("content") or "")
        if len(content) > preview_limit:
            content = content[:preview_limit]
            truncated = True
        model_item["content"] = content
        model_items.append(model_item)

    if not truncated:
        return output, None, False

    result_count = len(raw_items)
    model_output = {
        "_amadeus_result_truncated": True,
        "_amadeus_result_policy": "search_memory_items_v1",
        "tool_name": tool_name,
        "scope": output.get("scope"),
        "query": output.get("query"),
        "limit": output.get("limit"),
        "resultCount": result_count,
        "includedItems": len(model_items),
        "omittedItems": max(0, result_count - len(model_items)),
        "items": model_items,
    }
    preview = json.dumps(model_output, ensure_ascii=False, sort_keys=True)
    return model_output, preview[:preview_limit], True


def normalize_read_session_messages_output(
    tool_name: str,
    output: dict[str, Any],
    preview_chars: int,
) -> tuple[dict[str, Any], str | None, bool]:
    raw_messages = output.get("messages")
    if not isinstance(raw_messages, list):
        return output, None, False

    preview_limit = max(1, min(preview_chars, SESSION_MESSAGES_MODEL_CONTENT_CHARS))
    model_messages: list[dict[str, Any]] = []
    truncated = len(raw_messages) > SESSION_MESSAGES_MODEL_RESULT_LIMIT

    for raw_message in raw_messages[:SESSION_MESSAGES_MODEL_RESULT_LIMIT]:
        if not isinstance(raw_message, dict):
            model_messages.append({"content": str(raw_message)[:preview_limit]})
            truncated = True
            continue

        model_message: dict[str, Any] = {}
        for key in ("id", "role", "createdAt", "contentCharCount", "contentTruncated"):
            if key in raw_message:
                model_message[key] = raw_message[key]

        content = str(raw_message.get("content") or "")
        if len(content) > preview_limit:
            content = content[:preview_limit]
            truncated = True
        model_message["content"] = content
        model_messages.append(model_message)

    if not truncated:
        return output, None, False

    message_count = len(raw_messages)
    model_output = {
        "_amadeus_result_truncated": True,
        "_amadeus_result_policy": "read_session_messages_v1",
        "tool_name": tool_name,
        "sessionId": output.get("sessionId"),
        "currentSessionId": output.get("currentSessionId"),
        "limit": output.get("limit"),
        "afterMessageId": output.get("afterMessageId"),
        "totalCount": output.get("totalCount"),
        "returnedCount": output.get("returnedCount"),
        "includedMessages": len(model_messages),
        "omittedMessages": max(0, message_count - len(model_messages)),
        "latestMessageId": output.get("latestMessageId"),
        "hasMore": output.get("hasMore"),
        "messages": model_messages,
    }
    preview = json.dumps(model_output, ensure_ascii=False, sort_keys=True)
    return model_output, preview[:preview_limit], True
