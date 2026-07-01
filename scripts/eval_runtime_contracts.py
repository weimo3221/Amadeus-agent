#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages"))

from amadeus.context import ContextAssembler
from amadeus.memory import MessageMemoryStore
from amadeus.mcp import McpServerConfig, build_mcp_tool_specs
from amadeus.tool_runtime import ToolContext, ToolRegistry


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def eval_role_identity_and_task_context() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
        role = memory.create_role("Eval Role")
        session = memory.create_session(str(role["id"]))
        memory.update_role_identity(str(role["id"]), name="Eval Agent", soul_text="You are Eval Agent.")
        memory.create_task(session_id=str(session["id"]), title="Eval active task", body="Keep this task visible.")

        identity = memory.role_identity_for_session(str(session["id"]))
        assembled = ContextAssembler(memory, "Base prompt").assemble(str(session["id"]), "status?")

        require(identity["roleName"] == "Eval Agent", "role identity name was not updated")
        require("You are Eval Agent." in str(identity["content"]), "SOUL.md content was not updated")
        require("<active-tasks>" in assembled.system_context, "active task context was not injected")
        require("Eval active task" in assembled.system_context, "active task title missing from context")


def eval_mcp_tool_contract() -> None:
    server = McpServerConfig(name="eval", url="http://127.0.0.1:1/mcp", permission="allow")

    def list_tools(_server: McpServerConfig) -> list[dict[str, object]]:
        return [{
            "name": "echo",
            "description": "Echo text",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }]

    specs = build_mcp_tool_specs([server], list_tools=list_tools)
    require(len(specs) == 1, "MCP tool spec was not discovered")
    require(specs[0].name == "mcp__eval__echo", "MCP tool name mapping is unstable")
    require(specs[0].permission == "allow", "MCP server permission was not applied")

    registry = ToolRegistry(specs=specs, config_path=REPO_ROOT / "missing-tools.yaml")
    schemas = registry.enabled_schemas()
    require(schemas[0]["function"]["name"] == "mcp__eval__echo", "MCP schema was not exposed")

    # Override the discovered handler for a deterministic no-network execution check.
    registry._specs["mcp__eval__echo"].handler = lambda args, _context: {  # noqa: SLF001
        "server": "eval",
        "tool": "echo",
        "result": {"content": [{"type": "text", "text": args["text"]}]},
    }
    result = registry.execute("mcp__eval__echo", {"text": "hello"}, ToolContext(session_id="eval-session"))
    require(result.ok, "MCP tool execution failed")
    require(result.output["result"]["content"][0]["text"] == "hello", "MCP tool result was not preserved")


def main() -> int:
    eval_role_identity_and_task_context()
    eval_mcp_tool_contract()
    print("runtime contract evals passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
