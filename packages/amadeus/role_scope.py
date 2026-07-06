from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_IDENTIFIER_RE = re.compile(r"[^a-zA-Z0-9_./:-]+")
_MCP_IDENTIFIER_RE = re.compile(r"[^a-zA-Z0-9_]+")


@dataclass(frozen=True)
class RoleRuntimeScope:
    tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, list[str]]:
        return {
            "tools": list(self.tools),
            "skills": list(self.skills),
            "mcpServers": list(self.mcp_servers),
        }


def normalize_scope_identifier(value: Any, *, max_chars: int = 160) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.replace("\x00", "").strip()
    if not normalized:
        return None
    normalized = _IDENTIFIER_RE.sub("_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        return None
    return normalized[:max_chars]


def normalize_scope_list(value: Any, *, mcp_server: bool = False) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("runtimeScope lists must be arrays of strings")
    seen: set[str] = set()
    normalized: list[str] = []
    for item in value:
        name = normalize_mcp_server_identifier(item) if mcp_server else normalize_scope_identifier(item)
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return tuple(normalized)


def normalize_mcp_server_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    normalized = _MCP_IDENTIFIER_RE.sub("_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or None


def normalize_role_runtime_scope(value: Any) -> RoleRuntimeScope:
    if value is None:
        return RoleRuntimeScope()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return RoleRuntimeScope()
    if not isinstance(value, dict):
        raise ValueError("runtimeScope must be an object")
    return RoleRuntimeScope(
        tools=normalize_scope_list(value.get("tools")),
        skills=normalize_scope_list(value.get("skills")),
        mcp_servers=normalize_scope_list(value.get("mcpServers"), mcp_server=True),
    )


def role_runtime_scope_json(value: Any) -> str:
    scope = normalize_role_runtime_scope(value)
    return json.dumps(scope.to_payload(), ensure_ascii=False, sort_keys=True)


def role_runtime_scope_payload(value: Any) -> dict[str, list[str]]:
    return normalize_role_runtime_scope(value).to_payload()


def mcp_server_name_from_tool(tool_name: str) -> str | None:
    if not tool_name.startswith("mcp__"):
        return None
    parts = tool_name.split("__", 2)
    if len(parts) < 3 or not parts[1]:
        return None
    return parts[1]


def role_allows_tool(scope: RoleRuntimeScope, tool_name: str) -> bool:
    mcp_server = mcp_server_name_from_tool(tool_name)
    if mcp_server:
        return not scope.mcp_servers or mcp_server in scope.mcp_servers
    return not scope.tools or tool_name in scope.tools


def role_allows_skill(scope: RoleRuntimeScope, *, identifier: str, name: str) -> bool:
    if not scope.skills:
        return True
    aliases = {
        normalize_scope_identifier(identifier),
        normalize_scope_identifier(name),
        normalize_scope_identifier(identifier.rsplit("/", 1)[-1]),
    }
    return any(alias in scope.skills for alias in aliases if alias)
