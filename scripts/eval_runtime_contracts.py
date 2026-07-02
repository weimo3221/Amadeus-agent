#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages"))

from amadeus.agent import AgentEvent, PermissionRequest
from amadeus.context import ContextAssembler
from amadeus.memory import MessageMemoryStore
from amadeus.mcp import McpServerConfig, build_mcp_tool_specs
from amadeus.tool_runtime import ToolContext, ToolRegistry
from amadeus.workers import TaskCallable, TaskWorker


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
        done_task = memory.create_task(session_id=str(session["id"]), title="Eval done task", body="Complete this.")
        memory.start_task(str(done_task["id"]), claim_lock="eval-worker")
        memory.complete_task(str(done_task["id"]), claim_lock="eval-worker", result="Eval task completed.")

        identity = memory.role_identity_for_session(str(session["id"]))
        assembled = ContextAssembler(memory, "Base prompt").assemble(str(session["id"]), "status?")

        require(identity["roleName"] == "Eval Agent", "role identity name was not updated")
        require("You are Eval Agent." in str(identity["content"]), "SOUL.md content was not updated")
        require("<active-tasks>" not in assembled.system_context, "active task context should not be injected into system context")
        require("<active-tasks>" in assembled.user_content, "active task context was not injected")
        require("Eval active task" in assembled.user_content, "active task title missing from context")
        require("<recent-tasks>" in assembled.user_content, "recent task context was not injected")
        require("Eval task completed." in assembled.user_content, "recent task result missing from context")


class EvalRuntime:
    def run_turn(self, session_id: str, user_text: str, request_permission: Callable[[PermissionRequest], bool]):
        yield AgentEvent("agent.turn.started", {"sessionId": session_id, "turnId": "eval-turn", "startedAt": "now"})
        yield AgentEvent("assistant.message", {"text": f"done: {user_text}"})


class EvalImmediateRunner:
    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, task_id: str, run_task: TaskCallable) -> None:
        self.submitted.append(task_id)
        run_task(task_id)

    def shutdown(self, *, wait: bool = True) -> None:
        return None


def eval_task_lifecycle_contract() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
        runner = EvalImmediateRunner()
        worker = TaskWorker(lambda: memory, lambda: EvalRuntime(), runner=runner)
        task = memory.create_task(session_id="eval-session", title="Lifecycle", body="Run once.")

        worker.submit(str(task["id"]))

        finished = memory.get_task(str(task["id"]))
        events = memory.list_task_events(str(task["id"]))
        require(runner.submitted == [str(task["id"])], "injected task runner did not receive task")
        require(finished is not None and finished["status"] == "succeeded", "task did not succeed")
        require(finished["result"] == "done: Lifecycle\n\nRun once.", "task result was not recorded")
        require([event["type"] for event in events] == ["created", "running", "succeeded"], "task lifecycle events regressed")


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
    eval_task_lifecycle_contract()
    eval_mcp_tool_contract()
    print("runtime contract evals passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
