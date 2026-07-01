from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from amadeus.tools.base import ToolSpec


logger = logging.getLogger(__name__)
MCP_TOOL_PREFIX = "mcp__"
MCP_TOOL_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    url: str
    enabled: bool = True
    permission: str | None = None
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class McpToolBinding:
    server: McpServerConfig
    tool_name: str


def build_mcp_tool_specs(
    servers: list[McpServerConfig],
    *,
    default_permission: str = "ask",
    list_tools: Callable[[McpServerConfig], list[dict[str, Any]]] | None = None,
) -> list[ToolSpec]:
    tool_specs: list[ToolSpec] = []
    list_tools_fn = list_tools or list_mcp_tools
    for server in servers:
        if not server.enabled:
            continue
        try:
            tools = list_tools_fn(server)
        except Exception as error:
            logger.info("MCP tool discovery failed server=%s url=%s error=%s", server.name, server.url, error)
            continue

        for tool in tools:
            if not isinstance(tool, dict):
                continue
            raw_name = tool.get("name")
            if not isinstance(raw_name, str) or not raw_name.strip():
                continue
            tool_name = raw_name.strip()
            spec_name = mcp_tool_spec_name(server.name, tool_name)
            input_schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object"}
            description = str(tool.get("description") or f"MCP tool {tool_name} from {server.name}")
            binding = McpToolBinding(server=server, tool_name=tool_name)
            tool_specs.append(ToolSpec(
                name=spec_name,
                display_name=f"MCP {server.name}.{tool_name}",
                permission=server.permission or default_permission,
                enabled=True,
                handler=make_mcp_tool_handler(binding),
                prompt_hint=(
                    f"Use MCP tool {server.name}.{tool_name} only when its external server capability is needed. "
                    "Respect the same permission and result-size limits as local tools."
                ),
                schema={
                    "type": "function",
                    "function": {
                        "name": spec_name,
                        "description": description,
                        "parameters": input_schema,
                    },
                },
            ))
    return tool_specs


def make_mcp_tool_handler(binding: McpToolBinding) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    def handler(args: dict[str, Any], context: Any) -> dict[str, Any]:
        if getattr(context, "is_cancelled", lambda: False)():
            return {"error": f"MCP tool cancelled before call: {binding.tool_name}"}
        timeout_seconds = getattr(context, "timeout_seconds", None) or binding.server.timeout_seconds
        return call_mcp_tool(binding.server, binding.tool_name, args, timeout_seconds=timeout_seconds)

    return handler


def list_mcp_tools(server: McpServerConfig) -> list[dict[str, Any]]:
    response = mcp_json_rpc(server, "tools/list", {}, timeout_seconds=server.timeout_seconds)
    tools = response.get("tools") if isinstance(response, dict) else None
    return tools if isinstance(tools, list) else []


def call_mcp_tool(
    server: McpServerConfig,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    response = mcp_json_rpc(
        server,
        "tools/call",
        {
            "name": tool_name,
            "arguments": arguments,
        },
        timeout_seconds=timeout_seconds,
    )
    return {
        "server": server.name,
        "tool": tool_name,
        "result": response,
    }


def mcp_json_rpc(
    server: McpServerConfig,
    method: str,
    params: dict[str, Any],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": method,
        "params": params,
    }
    request = urllib.request.Request(
        server.url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"MCP HTTP {error.code}: {body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"MCP connection failed: {error.reason}") from error

    if not isinstance(response_payload, dict):
        raise RuntimeError("MCP response must be a JSON object")
    if response_payload.get("error"):
        raise RuntimeError(f"MCP error: {response_payload['error']}")
    result = response_payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("MCP result must be a JSON object")
    return result


def mcp_tool_spec_name(server_name: str, tool_name: str) -> str:
    return f"{MCP_TOOL_PREFIX}{normalize_mcp_identifier(server_name)}__{normalize_mcp_identifier(tool_name)}"


def normalize_mcp_identifier(value: str) -> str:
    normalized = MCP_TOOL_NAME_RE.sub("_", value.strip()).strip("_").lower()
    return normalized or "tool"
