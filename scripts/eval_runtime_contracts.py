#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages"))

from amadeus.agent import AgentEvent, AgentRuntime, PermissionRequest
from amadeus.context import ContextAssembler
from amadeus.memory import MessageMemoryStore
from amadeus.mcp import McpServerConfig, build_mcp_tool_specs
from amadeus.orchestrator import OrchestratorService
from amadeus.tool_runtime import ToolContext, ToolRegistry
from amadeus.worker_policy import WorkerRuntimeScope, worker_action_permission_decision
from amadeus.workers import SubprocessTaskRunner, TaskCallable, TaskWorker


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
        require("Lifecycle" in str(finished["result"]) and "Run once." in str(finished["result"]), "task result was not recorded")
        require([event["type"] for event in events] == ["created", "running", "succeeded"], "task lifecycle events regressed")


class EvalPlanningModel:
    model = "eval-planner"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.payloads: list[dict[str, Any]] = []

    def post_chat_completion(self, payload: dict[str, Any], *, timeout_seconds: int | None = None) -> dict[str, Any]:
        self.payloads.append(payload)
        if not self.responses:
            raise RuntimeError("eval planning model has no response")
        return {"choices": [{"message": {"content": self.responses.pop(0)}}]}


def eval_orchestrator_contract() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
        submitted: list[str] = []
        model = EvalPlanningModel([
            '{"goal":"Plan evaluated work","approach":"Repair then execute","acceptanceCriteria":["root succeeds"],"outOfScope":[]}',
            '{"tasks":[{"tempId":"research","title":"Research","workerProfile":"researcher","allowedToolsets":["read","terminal"]},{"tempId":"review","title":"Review","workerProfile":"reviewer","dependsOn":["research"]}],"edges":[]}',
            '{"tasks":[{"tempId":"research","title":"Research","workerProfile":"researcher","allowedToolsets":["read","search"]},{"tempId":"review","title":"Review","workerProfile":"reviewer","dependsOn":["research"]}],"edges":[]}',
            '{"summary":"complete","result":"Evaluated root result"}',
        ])
        service = OrchestratorService(memory, submit_task=submitted.append, model_client=model)
        root = service.create_root_goal(session_id="eval-session", title="Plan evaluated work")

        planned = service.plan_root(str(root["id"]))
        first_id = str(planned["tempTaskIds"]["research"])
        second_id = str(planned["tempTaskIds"]["review"])
        first_dispatch = service.dispatch_ready(str(root["id"]))
        memory.start_task(first_id, claim_lock="eval-worker-1")
        memory.complete_task(first_id, claim_lock="eval-worker-1", result="research done")
        second_dispatch = service.dispatch_ready(str(root["id"]))
        memory.start_task(second_id, claim_lock="eval-worker-2")
        memory.complete_task(second_id, claim_lock="eval-worker-2", result="review done")
        synthesized = service.synthesize_root(str(root["id"]))
        updated_root = memory.get_task(str(root["id"]))
        events = memory.list_task_events(str(root["id"]))

        require(planned["decompositionSource"] == "model_repaired", "orchestrator did not repair invalid model graph")
        require(planned["repaired"] is True, "orchestrator repair flag missing")
        require(planned["tasks"][0]["allowedToolsets"] == ["read", "search"], "repaired toolset policy was not applied")
        require(first_dispatch == [first_id], "first dependency-ready child was not dispatched")
        require(second_dispatch == [second_id], "dependent child was not dispatched after dependency success")
        require(submitted == [first_id, second_id], "submitter did not receive children in dependency order")
        require(synthesized["completed"] is True, "root synthesis did not complete")
        require(updated_root is not None and updated_root["status"] == "succeeded", "root task did not succeed after synthesis")
        require(updated_root["result"] == "Evaluated root result", "root synthesis result was not persisted")
        event_types = [str(event["type"]) for event in events]
        require("graph.decomposed" in event_types, "graph decomposition event missing")
        require("graph.applied" in event_types, "graph applied event missing")
        require("graph.dispatched" in event_types, "graph dispatched event missing")
        require("graph.synthesized" in event_types, "graph synthesized event missing")


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


class EvalProcess:
    pid = 12345

    def wait(self) -> int:
        return 0

    def poll(self) -> int:
        return 0

    def terminate(self) -> None:
        return None


def eval_worker_isolation_policy_contract() -> None:
    launches: list[dict[str, Any]] = []

    def fake_process_factory(command: list[str], **kwargs: object) -> EvalProcess:
        launches.append({"command": command, **kwargs})
        return EvalProcess()

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        database = root / "amadeus.sqlite"
        workspace = root / "workspace"
        source = workspace / "src"
        source.mkdir(parents=True)
        (source / "app.py").write_text("print('hello')\n", encoding="utf-8")
        (workspace / "node_modules").mkdir()
        (workspace / "node_modules" / "ignored.txt").write_text("ignored\n", encoding="utf-8")

        memory = MessageMemoryStore(database, default_workspace_path=workspace)
        task = memory.create_task(
            session_id="eval-session",
            title="Isolated worker",
            worker_profile="coder",
            allowed_toolsets=["read", "patch", "terminal", "code"],
            context_hints={"workspacePath": "src", "sandboxMode": "workspace_execute"},
        )
        runner = SubprocessTaskRunner(
            database_path=database,
            workspace_path=workspace,
            workspace_isolation="copy",
            sandbox_root=root / "worker-sandboxes",
            process_factory=fake_process_factory,
        )
        runner.submit(str(task["id"]), lambda _task_id: None)
        runner.shutdown()

        require(len(launches) == 1, "subprocess runner did not launch one child")
        env = launches[0].get("env")
        require(isinstance(env, dict), "subprocess launch env missing")
        isolated_workspace = Path(str(env["AMADEUS_WORKSPACE"]))
        require(env["AMADEUS_WORKER_WORKSPACE_ISOLATION"] == "copy", "worker isolation env was not set")
        require(Path(str(env["AMADEUS_WORKER_WORKSPACE_SOURCE"])) == source.resolve(), "worker source workspace was not preserved")
        require(isolated_workspace != source.resolve(), "isolated worker reused the source workspace")
        require((isolated_workspace / "app.py").exists(), "isolated worker copy did not include task workspace files")
        require(not (isolated_workspace.parent / "node_modules").exists(), "isolated worker copy included ignored generated directories")

        runtime = AgentRuntime(memory, audio_runtime=None, workspace_root=workspace)
        scope = WorkerRuntimeScope(
            worker_profile="coder",
            allowed_toolsets=("read", "patch", "terminal", "code"),
            allowed_tool_names=frozenset({"read_file", "patch", "terminal", "execute_code"}),
            sandbox_mode="workspace_execute",
            workspace_path=str(isolated_workspace),
            workspace_isolation="copy",
            workspace_source_path=str(source.resolve()),
        )
        require(runtime.validate_worker_runtime_scope("eval-session", scope) is None, "copy-isolated worker scope was rejected")
        with runtime.worker_runtime_scope(scope):
            require(runtime._workspace_root_for_session("eval-session") == isolated_workspace.resolve(), "runtime did not use isolated worker workspace")

        registry = ToolRegistry(config_path=root / "missing-tools.yaml")
        outside_path = root / "outside.txt"
        terminal_result = registry.execute(
            "terminal",
            {"command": f"printf bad > {outside_path}", "cwd": "."},
            ToolContext(
                session_id="eval-session",
                cwd=isolated_workspace,
                worker_workspace_path=str(isolated_workspace),
                worker_workspace_isolation="copy",
                worker_sandbox_mode="workspace_execute",
            ),
        )
        require(not terminal_result.ok, "terminal sandbox allowed an outside workspace path")
        require("outside the workspace sandbox" in str(terminal_result.output.get("error")), "terminal sandbox error was not explicit")

        code_result = registry.execute(
            "execute_code",
            {
                "code": (
                    "from pathlib import Path\n"
                    f"Path({str(outside_path)!r}).write_text('bad', encoding='utf-8')\n"
                ),
                "cwd": ".",
            },
            ToolContext(
                session_id="eval-session",
                cwd=isolated_workspace,
                worker_workspace_path=str(isolated_workspace),
                worker_workspace_isolation="copy",
                worker_sandbox_mode="workspace_execute",
            ),
        )
        require(code_result.ok, "execute_code sandbox should return a structured command result")
        require(code_result.output.get("exitCode") != 0, "execute_code sandbox did not fail an outside write")
        require(not outside_path.exists(), "execute_code sandbox wrote outside the workspace")

        decision = worker_action_permission_decision(
            scope,
            "patch",
            {"path": "src/app.py", "oldText": "a", "newText": "b", "replaceAll": True},
            "ask",
        )
        require(decision.decision == "deny", "high-risk profile auto-approval was not denied")
        require("bulk_replace" in decision.risk_labels, "high-risk approval decision missed bulk_replace risk label")


def main() -> int:
    eval_role_identity_and_task_context()
    eval_task_lifecycle_contract()
    eval_orchestrator_contract()
    eval_mcp_tool_contract()
    eval_worker_isolation_policy_contract()
    print("runtime contract evals passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
