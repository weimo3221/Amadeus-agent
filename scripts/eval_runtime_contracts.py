#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import contextlib
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages"))

from amadeus.agent import AgentEvent, AgentRuntime, PermissionRequest
from amadeus.audio import (
    AudioFallbackResult,
    AudioOutputCommand,
    AudioOutputResult,
    AudioRuntime,
    LocalAudioLibrary,
    NoopTtsProvider,
)
from amadeus.context import ContextAssembler
from amadeus.memory import MessageMemoryStore
from amadeus.memory_safety import evaluate_memory_candidate
from amadeus.mcp import McpServerConfig, build_mcp_tool_specs
from amadeus.orchestrator import OrchestratorService
from amadeus.task_supervisor import DurableTaskSupervisor
from amadeus.tool_runtime import ToolContext, ToolRegistry
from amadeus.worker_policy import WorkerRuntimeScope, worker_action_permission_decision
from amadeus.workers import (
    InProcessTaskRunner,
    ProcessTaskRunner,
    SubprocessTaskRunner,
    SynchronousTaskRunner,
    TaskCallable,
    TaskWorker,
    build_worker_context,
    worker_session_id_for_task,
)

CheckMetrics = dict[str, object]


def write_eval_wav(path: Path, amplitudes: list[int], frame_rate: int = 1000) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(frame_rate)
        frames = bytearray()
        for amplitude in amplitudes:
            sample = max(-32768, min(32767, int(amplitude)))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav_file.writeframes(bytes(frames))


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

    def cancel(self, task_id: str) -> None:
        del task_id

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


def eval_runner_contract_matrix() -> None:
    runner_factories: list[tuple[str, Callable[[], object]]] = [
        ("sync", SynchronousTaskRunner),
        ("in_process", lambda: InProcessTaskRunner(max_workers=1)),
    ]
    if ProcessTaskRunner.supported():
        runner_factories.append(("process", lambda: ProcessTaskRunner(max_workers=1)))

    results: dict[str, dict[str, object]] = {}
    for runner_kind, runner_factory in runner_factories:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            runner = runner_factory()
            worker = TaskWorker(
                lambda: memory,
                lambda: EvalRuntime(),
                runner=runner,
                runner_kind=runner_kind,
            )
            task = memory.create_task(
                session_id="eval-session",
                title=f"Runner contract {runner_kind}",
                body="Return one durable result.",
            )

            worker.submit(str(task["id"]))
            worker.shutdown()

            finished = memory.get_task(str(task["id"]))
            attempts = memory.list_task_attempts(str(task["id"]))
            artifacts = memory.list_task_artifacts(str(task["id"]))
            event_types = [str(event["type"]) for event in memory.list_task_events(str(task["id"]))]
            require(finished is not None and finished["status"] == "succeeded", f"{runner_kind} runner did not succeed")
            require(len(attempts) == 1 and attempts[0]["status"] == "succeeded", f"{runner_kind} attempt contract regressed")
            require(any(artifact["type"] == "summary" for artifact in artifacts), f"{runner_kind} summary artifact missing")
            require(event_types == ["created", "running", "succeeded"], f"{runner_kind} lifecycle events regressed")
            results[runner_kind] = {
                "status": finished["status"],
                "attempts": len(attempts),
                "artifacts": len(artifacts),
            }

    require("sync" in results and "in_process" in results, "runner contract matrix did not execute required runners")


class EvalChildRuntime:
    def __init__(self, memory_store: MessageMemoryStore) -> None:
        self.memory_store = memory_store
        self.sessions: list[str] = []
        self.scopes: list[WorkerRuntimeScope] = []

    def worker_runtime_scope(self, scope: WorkerRuntimeScope):
        self.scopes.append(scope)
        return contextlib.nullcontext()

    def run_turn(self, session_id: str, user_text: str, request_permission: Callable[[PermissionRequest], bool]):
        del request_permission
        self.sessions.append(session_id)
        self.memory_store.save(session_id, "user", user_text)
        yield AgentEvent("agent.turn.started", {"sessionId": session_id, "turnId": "eval-child-turn", "startedAt": "now"})
        self.memory_store.save(session_id, "assistant", "isolated child summary")
        yield AgentEvent("assistant.message", {"text": "isolated child summary"})


def eval_isolated_child_agent_contract() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
        memory.save("eval-parent", "user", "parent history")
        runtime = EvalChildRuntime(memory)
        runner = EvalImmediateRunner()
        worker = TaskWorker(lambda: memory, lambda: runtime, runner=runner)
        registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")

        result = registry.execute(
            "delegate_task",
            {
                "task": "Inspect the task lifecycle contract.",
                "queries": ["task lifecycle"],
                "includeMemory": True,
            },
            ToolContext(
                session_id="eval-parent",
                cwd=Path(tmpdir),
                memory_store=memory,
                task_worker=worker,
                cancel_event=threading.Event(),
            ),
        )
        worker.shutdown()

        child_task_id = str(result.output.get("childTaskId") or "")
        child_task = memory.get_task(child_task_id)
        child_session_id = worker_session_id_for_task(child_task_id)
        child_session = memory.get_session(child_session_id)
        parent_messages = memory.load("eval-parent")
        child_messages = memory.load(child_session_id)
        artifacts = memory.list_task_artifacts(child_task_id)

        require(result.ok, "isolated delegate tool failed")
        require(result.output["delegateType"] == "isolated_child_agent", "delegate did not use isolated child agent")
        require(child_task is not None and child_task["kind"] == "delegated", "tracked delegated task missing")
        require(child_task["status"] == "succeeded", "isolated child task did not succeed")
        require(runtime.sessions == [child_session_id], "child model turn did not use task-specific session")
        require(child_session is not None and child_session["archived"] is True, "child session was not archived")
        require(len(parent_messages) == 1 and parent_messages[0]["content"] == "parent history", "child polluted parent history")
        require(len(child_messages) == 2, "child session did not contain isolated turn history")
        require(runtime.scopes[0].source_session_id == "eval-parent", "child source session scope missing")
        require("delegate_task" not in runtime.scopes[0].allowed_tool_names, "child scope allowed recursive delegation")
        require(any(artifact["type"] == "summary" for artifact in artifacts), "child summary artifact missing")
        require(result.output["summary"] == "isolated child summary", "parent did not receive summary-only handoff")


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
        memory.add_task_artifact(
            first_id,
            {
                "type": "summary",
                "title": "Research handoff",
                "content": "Dependency artifact reached the downstream worker.",
            },
        )
        memory.complete_task(first_id, claim_lock="eval-worker-1", result="research done")
        second_dispatch = service.dispatch_ready(str(root["id"]))
        second_context = build_worker_context(memory, second_id)
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
        require(
            "Dependency artifact reached the downstream worker." in second_context.to_prompt(),
            "dependency artifact handoff was not present in WorkerContext",
        )
        require(synthesized["completed"] is True, "root synthesis did not complete")
        require(updated_root is not None and updated_root["status"] == "succeeded", "root task did not succeed after synthesis")
        require(updated_root["result"] == "Evaluated root result", "root synthesis result was not persisted")
        event_types = [str(event["type"]) for event in events]
        require("graph.decomposed" in event_types, "graph decomposition event missing")
        require("graph.applied" in event_types, "graph applied event missing")
        require("graph.dispatched" in event_types, "graph dispatched event missing")
        require("graph.synthesized" in event_types, "graph synthesized event missing")


def eval_orchestrator_quality_and_replan_contract() -> CheckMetrics:
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
        submitted: list[str] = []
        service = OrchestratorService(memory, submit_task=submitted.append)
        root = service.create_root_goal(session_id="eval-session", title="Quality graph")
        applied = service.apply_task_graph(
            str(root["id"]),
            {
                "tasks": [
                    {"tempId": "research", "title": "Research"},
                    {
                        "tempId": "implement",
                        "title": "Implement",
                        "workerProfile": "coder",
                        "dependsOn": ["research"],
                    },
                    {
                        "tempId": "review",
                        "title": "Review",
                        "workerProfile": "reviewer",
                        "dependsOn": ["implement"],
                    },
                ],
            },
            max_concurrency=1,
            max_replans=1,
        )
        root_id = str(root["id"])
        research_id = str(applied["tempTaskIds"]["research"])
        implement_id = str(applied["tempTaskIds"]["implement"])
        review_id = str(applied["tempTaskIds"]["review"])
        waiting_root = memory.get_task(root_id)
        initial_runnable = memory.list_runnable_tasks(limit=10)
        duplicate_rejected = False
        try:
            service.apply_task_graph(
                root_id,
                {"tasks": [{"tempId": "extra", "title": "Extra"}]},
            )
        except ValueError:
            duplicate_rejected = True

        memory.start_task(research_id, claim_lock="quality-research")
        premature_claim = memory.start_task(implement_id, claim_lock="quality-premature")
        memory.complete_task(
            research_id,
            claim_lock="quality-research",
            result="research done",
        )
        second_wave = memory.list_runnable_tasks(limit=10)
        memory.start_task(implement_id, claim_lock="quality-implement")
        memory.fail_task(
            implement_id,
            claim_lock="quality-implement",
            error="implementation failed",
        )
        blocked = service.synthesize_root(root_id)
        replanned = service.replan_failed_child(root_id, implement_id)
        replacement_id = str(replanned["replacementTaskId"])
        replacement_dispatch = service.dispatch_ready(root_id, limit=1)
        memory.start_task(replacement_id, claim_lock="quality-replacement")
        memory.complete_task(
            replacement_id,
            claim_lock="quality-replacement",
            result="replacement done",
        )
        review_dispatch = service.dispatch_ready(root_id, limit=1)
        memory.start_task(review_id, claim_lock="quality-review")
        memory.complete_task(
            review_id,
            claim_lock="quality-review",
            result="review done",
        )
        synthesized = service.synthesize_root(root_id)
        graph = memory.get_task_graph(root_id)
        events = memory.list_task_events(root_id)

        require(waiting_root is not None and waiting_root["status"] == "blocked", "orchestrator root was not blocked")
        require(waiting_root["checkpoint"]["phase"] == "orchestrator_waiting", "root waiting checkpoint missing")
        require([task["id"] for task in initial_runnable] == [research_id], "initial runnable wave was not width-limited")
        require(duplicate_rejected, "second graph apply was not rejected")
        require(premature_claim is not None and premature_claim["status"] == "queued", "dependency/concurrency claim gate was bypassed")
        require([task["id"] for task in second_wave] == [implement_id], "dependent wave did not wake after dependency completion")
        require(
            blocked["completed"] is False
            and blocked["pendingTaskIds"] == [review_id],
            "synthesis did not wait for downstream child before replan",
        )
        require(replanned["replacementTask"]["readyAt"] is None, "replacement child was not activated after dependency rewiring")
        require(replacement_dispatch == [replacement_id], "replacement child was not dispatched")
        require(review_dispatch == [review_id], "downstream child was not dispatched after replacement success")
        require(synthesized["completed"] is True, "replacement-aware synthesis did not complete")
        require(synthesized["supersededTaskIds"] == [implement_id], "superseded failed task was not tracked")
        require(submitted == [replacement_id, review_id], "dispatch submitter order regressed")
        require(any(event["type"] == "graph.replanned" for event in events), "graph.replanned event missing")

        return {
            "childTaskCount": len(graph["tasks"]) - 1,
            "edgeCount": len(graph["edges"]),
            "dispatchWaveCount": 3,
            "replanCount": 1,
            "supersededTaskCount": len(synthesized["supersededTaskIds"]),
        }


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


class EvalExitProcess:
    pid = 12346

    def __init__(self, return_code: int) -> None:
        self.return_code = return_code

    def wait(self) -> int:
        return self.return_code

    def poll(self) -> int:
        return self.return_code

    def terminate(self) -> None:
        return None


class EvalBlockingProcess:
    pid = 987650

    def __init__(self) -> None:
        self.return_code: int | None = None
        self.terminated = False
        self.finished = threading.Event()

    def wait(self) -> int:
        self.finished.wait(timeout=2)
        return self.return_code if self.return_code is not None else 0

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = -15
        self.finished.set()


def eval_subprocess_supervisor_fault_contract() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        database = root / "amadeus.sqlite"
        memory = MessageMemoryStore(database)

        active_task = memory.create_task(session_id="eval-session", title="Deduplicated subprocess")
        active_process = EvalBlockingProcess()
        launches: list[dict[str, object]] = []

        def blocking_factory(command: list[str], **kwargs: object) -> EvalBlockingProcess:
            launches.append({"command": command, **kwargs})
            return active_process

        active_runner = SubprocessTaskRunner(database_path=database, process_factory=blocking_factory)
        active_runner.submit(str(active_task["id"]), lambda _task_id: None)
        active_runner.submit(str(active_task["id"]), lambda _task_id: None)
        active_status = active_runner.status()
        active_runner.cancel(str(active_task["id"]))
        active_runner.shutdown()
        active_events = [str(event["type"]) for event in memory.list_task_events(str(active_task["id"]))]

        require(len(launches) == 1, "subprocess supervisor launched a duplicate active task")
        require(active_status["activeProcessCount"] == 1, "subprocess supervisor active process count regressed")
        require(active_process.terminated, "subprocess supervisor did not terminate the active process")
        require("subprocess_termination_requested" in active_events, "subprocess termination audit event missing")

        failed_task = memory.create_task(
            session_id="eval-session",
            title="Injected subprocess failure",
            max_attempts=2,
        )
        memory.start_task(
            str(failed_task["id"]),
            claim_lock="eval-child-claim",
            lease_owner="eval-child",
            runner_kind="process_entrypoint",
        )

        def failing_factory(command: list[str], **kwargs: object) -> EvalExitProcess:
            del command
            env = kwargs.get("env")
            require(isinstance(env, dict), "failing subprocess launch env missing")
            memory.create_task_attempt(
                str(failed_task["id"]),
                run_id=str(env["AMADEUS_TASK_RUN_ID"]),
                worker_id="eval-child",
                checkpoint={
                    "status": "running",
                    "phase": "assistant_message_received",
                    "resultPreview": "durable partial result",
                },
            )
            return EvalExitProcess(23)

        failing_runner = SubprocessTaskRunner(database_path=database, process_factory=failing_factory)
        failing_runner.submit(str(failed_task["id"]), lambda _task_id: None)
        failing_runner.shutdown()
        recovered = memory.get_task(str(failed_task["id"]))
        attempts = memory.list_task_attempts(str(failed_task["id"]))

        require(recovered is not None and recovered["status"] == "queued", "non-zero subprocess exit was not requeued")
        require(recovered["checkpoint"]["reason"] == "subprocess_exited", "subprocess recovery reason regressed")
        require("durable partial result" in str(recovered["handoffSummary"]), "partial result was not handed off")
        require(attempts[0]["status"] == "abandoned", "failed subprocess attempt was not marked abandoned")

        periodic_runner = EvalImmediateRunner()
        periodic_worker = TaskWorker(
            lambda: memory,
            lambda: EvalRuntime(),
            runner=periodic_runner,
            runner_kind="subprocess",
            recovery_interval_seconds=0.05,
        )
        periodic_worker.start_supervisor()
        periodic_task = memory.create_task(session_id="eval-session", title="Periodic supervisor dispatch")
        deadline = time.monotonic() + 2
        periodic_result = memory.get_task(str(periodic_task["id"]))
        while periodic_result and periodic_result["status"] != "succeeded" and time.monotonic() < deadline:
            time.sleep(0.02)
            periodic_result = memory.get_task(str(periodic_task["id"]))
        supervisor_status = periodic_worker.status()
        periodic_worker.shutdown()

        require(periodic_result is not None and periodic_result["status"] == "succeeded", "periodic supervisor did not dispatch queued task")
        require(supervisor_status["supervisorRunning"] is True, "periodic recovery supervisor was not running")


def eval_durable_supervisor_restart_soak_contract() -> CheckMetrics:
    with tempfile.TemporaryDirectory() as tmpdir:
        database = Path(tmpdir) / "amadeus.sqlite"
        memory = MessageMemoryStore(database)
        live_task = memory.create_task(session_id="eval-session", title="Restart-adopt live worker")
        memory.start_task(
            str(live_task["id"]),
            claim_lock="live-worker-claim",
            lease_owner="live-worker",
            lease_seconds=30,
            runner_kind="process_entrypoint",
        )
        memory.register_task_process(
            task_id=str(live_task["id"]),
            run_id="live-run",
            supervisor_id="soak-before",
            pid=os.getpid(),
        )
        adoption_counts: list[int] = []
        for cycle in range(3):
            supervisor = DurableTaskSupervisor(
                database_path=database,
                owner_id=f"soak-supervisor-{cycle}",
                poll_interval_seconds=0.1,
                lease_seconds=2,
            )
            standby = DurableTaskSupervisor(
                database_path=database,
                owner_id=f"soak-standby-{cycle}",
                poll_interval_seconds=0.1,
                lease_seconds=2,
            )
            try:
                require(supervisor.acquire(), "soak supervisor did not acquire lease")
                require(not standby.acquire(), "standby supervisor acquired an active lease")
                status = supervisor.tick()
                adoption_counts.append(int(status["reconciliation"]["adopted"]))
                lease = memory.get_supervisor_lease("task-supervisor")
                require(
                    lease is not None and lease["ownerId"] == f"soak-supervisor-{cycle}",
                    "supervisor lease owner did not match active cycle",
                )
            finally:
                standby.close(detach_children=True)
                supervisor.close(detach_children=True)
            require(memory.get_supervisor_lease("task-supervisor") is None, "supervisor lease was not released")

        live_record = memory.list_task_processes(task_id=str(live_task["id"]))[0]

        missing_task = memory.create_task(
            session_id="eval-session",
            title="Recover missing worker",
            max_attempts=2,
        )
        memory.start_task(
            str(missing_task["id"]),
            claim_lock="missing-worker-claim",
            lease_owner="missing-worker",
            runner_kind="process_entrypoint",
        )
        memory.create_task_attempt(
            str(missing_task["id"]),
            run_id="missing-run",
            worker_id="missing-worker",
            checkpoint={"status": "running", "phase": "model_turn_started"},
        )
        memory.register_task_process(
            task_id=str(missing_task["id"]),
            run_id="missing-run",
            supervisor_id="soak-missing-before",
            pid=999999999,
        )
        recovery_supervisor = DurableTaskSupervisor(
            database_path=database,
            owner_id="soak-recovery",
            poll_interval_seconds=0.1,
            lease_seconds=2,
            max_workers=1,
        )
        try:
            require(recovery_supervisor.acquire(), "recovery supervisor did not acquire lease")
            recovery_status = recovery_supervisor.tick()
        finally:
            recovery_supervisor.close(detach_children=True)
        recovered_missing = memory.get_task(str(missing_task["id"]))
        attempts = memory.list_task_attempts(str(missing_task["id"]))
        missing_record = memory.list_task_processes(task_id=str(missing_task["id"]))[0]

        require(sum(adoption_counts) >= 3, "supervisor restarts did not repeatedly adopt the live process")
        require(live_record["supervisorId"] == "soak-supervisor-2", "latest supervisor did not own adopted process")
        require(missing_record["status"] == "lost", "missing process was not marked lost")
        require(
            recovered_missing is not None and recovered_missing["status"] in {"queued", "running"},
            "missing process was not reclaimed into an active/retryable state",
        )
        require(attempts[0]["status"] == "abandoned", "missing process attempt was not abandoned")
        return {
            "restartCycles": 3,
            "adoptionEvents": sum(adoption_counts),
            "lostProcesses": int(recovery_status["reconciliation"]["lost"]),
            "recoveredTaskStatus": recovered_missing["status"] if recovered_missing else "missing",
        }


def eval_memory_quality_contract() -> CheckMetrics:
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
        memory.save("memory-eval-a", "user", "The project notebook color is blue.")
        memory.save("memory-eval-b", "user", "Unrelated release note about orange widgets.")
        memory.save_memory_item(
            "project",
            "项目记忆检索应该支持中文分词召回。",
            confidence=0.9,
            memory_type="semantic",
            metadata={"topic": "memory", "language": "zh"},
        )
        memory.save_memory_item(
            "user",
            "The user prefers concise Chinese status updates.",
            confidence=0.8,
            memory_type="preference",
            metadata={"topic": "style"},
        )

        chinese_results = memory.list_memory_items(
            scope="project",
            query="记忆检索召回",
            limit=3,
        )
        typed_context = ContextAssembler(memory, "Base prompt").assemble(
            "memory-eval-a",
            "What do you know about concise Chinese updates?",
        )
        transcript_context = ContextAssembler(memory, "Base prompt").assemble(
            "memory-eval-a",
            "What color is the project notebook?",
        )
        secret_decision = evaluate_memory_candidate(
            "user",
            "api_key = 'sk-abcdefghijklmnopqrstuvwxyz'",
        )
        debug_decision = evaluate_memory_candidate(
            "project",
            "Temporary pytest failure from the current run should be remembered.",
        )

        require(chinese_results, "Chinese memory item query returned no results")
        require("中文分词召回" in str(chinese_results[0]["content"]), "Chinese memory recall ranked the wrong item first")
        require(
            "The user prefers concise Chinese status updates." not in typed_context.user_content,
            "typed memory item was auto-injected instead of tool-searched",
        )
        require("blue" in transcript_context.user_content, "transcript retrieval context missed relevant session memory")
        require(not secret_decision.allowed and secret_decision.reason.startswith("secret:"), "secret memory candidate was allowed")
        require(not debug_decision.allowed and debug_decision.reason.startswith("temporary_debug:"), "temporary debug memory was allowed")
        return {
            "memoryItemHits": len(chinese_results),
            "topRetrievalProvider": chinese_results[0].get("retrievalProvider"),
            "secretDecision": secret_decision.reason,
            "debugDecision": debug_decision.reason,
        }


class EvalTtsProvider:
    name = "eval_tts"

    def __init__(self, library: LocalAudioLibrary, audio_path: Path) -> None:
        self.library = library
        self.audio_path = audio_path

    def synthesize(self, command: AudioOutputCommand) -> AudioOutputResult | None:
        if not command.text.strip():
            return None
        return AudioOutputResult(
            audio_url=self.library.public_url(self.audio_path),
            duration_ms=self.library.duration_ms(self.audio_path),
            provider=self.name,
        )


def eval_audio_quality_contract() -> CheckMetrics:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        library = LocalAudioLibrary(root / "audio", "http://localhost:8790")
        fallback = AudioRuntime(library, NoopTtsProvider()).speak(
            AudioOutputCommand(text="hello")
        )
        wav_path = library.cache_dir / "eval.wav"
        write_eval_wav(wav_path, [0, 8000, 16000, 4000, 0], frame_rate=1000)
        runtime = AudioRuntime(library, EvalTtsProvider(library, wav_path))
        runtime.lipsync_max_cues = 12
        spoken = runtime.speak(AudioOutputCommand(text="hello 世界"))

        require(isinstance(fallback, AudioFallbackResult), "noop TTS did not return a browser fallback")
        require(fallback.reason.startswith("tts_provider_unavailable"), "fallback reason was not explicit")
        require(isinstance(spoken, AudioOutputResult), "eval TTS did not return audio output")
        require(spoken.duration_ms == 5, "WAV duration was not measured")
        require(spoken.lipsync_cues is not None and len(spoken.lipsync_cues) >= 2, "lipsync cues missing")
        assert spoken.lipsync_cues is not None
        require(spoken.lipsync_cues[-1]["mouthOpen"] == 0.0, "lipsync cues did not close the mouth")
        require(
            all(0.0 <= float(cue["mouthOpen"]) <= 1.0 for cue in spoken.lipsync_cues),
            "lipsync mouthOpen values were out of range",
        )
        return {
            "fallbackReason": fallback.reason,
            "lipsyncCueCount": len(spoken.lipsync_cues),
            "durationMs": spoken.duration_ms,
        }


def eval_action_approval_scope_contract() -> None:
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    approved_key = "process:kill:pid:42:signal:TERM"
    approved_scope = WorkerRuntimeScope(
        worker_profile="coder",
        allowed_toolsets=("terminal",),
        allowed_tool_names=frozenset({"process"}),
        approved_ask_tool_actions=frozenset({approved_key}),
        approved_ask_tool_action_expirations=((approved_key, future),),
    )
    expired_scope = WorkerRuntimeScope(
        worker_profile="coder",
        allowed_toolsets=("terminal",),
        allowed_tool_names=frozenset({"process"}),
        approved_ask_tool_actions=frozenset({approved_key}),
        approved_ask_tool_action_expirations=((approved_key, past),),
    )

    exact = worker_action_permission_decision(approved_scope, "process", {"action": "kill", "pid": 42}, "ask")
    different_target = worker_action_permission_decision(approved_scope, "process", {"action": "kill", "pid": 43}, "ask")
    different = worker_action_permission_decision(approved_scope, "process", {"action": "list"}, "ask")
    expired = worker_action_permission_decision(expired_scope, "process", {"action": "kill", "pid": 42}, "ask")

    require(exact.decision == "auto_approve", "exact unexpired action approval was not honored")
    require(different_target.decision == "deny", "process approval widened to a different pid")
    require(different.decision == "deny", "action-specific approval widened to a different action")
    require(expired.decision == "deny" and "expired" in str(expired.reason), "expired action approval was not rejected")


def eval_approval_override_audit_contract() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        memory = MessageMemoryStore(
            Path(tmpdir) / "amadeus.sqlite",
            worker_approval_action_ttl_seconds=120,
        )
        task = memory.create_task(session_id="eval-session", title="Audit approval and override")
        artifact = memory.add_task_artifact(
            str(task["id"]),
            {"type": "diff", "title": "Patch", "path": "src/app.py"},
            metadata={
                "toolName": "patch",
                "fileResumePolicy": {
                    "action": "skip_redundant_mutation",
                    "paths": ["src/app.py"],
                },
            },
        )
        memory.set_task_artifact_file_resume_override(
            str(task["id"]),
            str(artifact["id"]),
            "force_rerun",
            audit_source="contract_eval",
            audit_actor="eval-user",
        )
        memory.start_task(str(task["id"]), claim_lock="eval-worker")
        action_key = "process:kill:pid:42:signal:TERM"
        memory.block_task(
            str(task["id"]),
            claim_lock="eval-worker",
            reason="Approval required",
            checkpoint={
                "status": "blocked",
                "phase": "approval_required",
                "reason": "worker_tool_permission_required",
                "toolName": "process",
                "approvalActionKey": action_key,
                "approvalActionLabel": "process kill pid 42 signal TERM",
                "approvalRiskLevel": "high",
                "approvalRiskLabels": ["destructive", "process_signal"],
            },
        )
        resumed = memory.resume_blocked_task(
            str(task["id"]),
            audit_source="contract_eval",
            audit_actor="eval-user",
        )
        events = memory.list_task_events(str(task["id"]))

        override_event = next(event for event in events if event["type"] == "file_resume_override_set")
        blocked_event = next(event for event in events if event["type"] == "blocked")
        resumed_event = next(event for event in events if event["type"] == "resumed")
        require(override_event["metadata"]["override"] == "force_rerun", "override audit value missing")
        require(override_event["metadata"]["auditSource"] == "contract_eval", "override audit source missing")
        require(blocked_event["metadata"]["approvalActionKey"] == action_key, "blocked approval action audit missing")
        require(blocked_event["metadata"]["approvalRiskLevel"] == "high", "blocked approval risk audit missing")
        require(resumed_event["metadata"]["approvedToolAction"] == action_key, "resume approval grant audit missing")
        require(resumed_event["metadata"]["approvalTtlSeconds"] == 120, "resume approval TTL audit missing")
        require(
            resumed_event["metadata"]["approvedToolActionExpiresAt"]
            == resumed["checkpoint"]["approvedToolActionExpiresAt"],
            "resume approval expiration audit diverged from checkpoint",
        )


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
        outside_source = root / "outside-source"
        outside_source.mkdir()
        external_link = workspace / "external-link"
        try:
            external_link.symlink_to(outside_source, target_is_directory=True)
        except OSError:
            external_link = None

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
        require(launches[0].get("start_new_session") == (os.name != "nt"), "subprocess process-group isolation flag regressed")
        if external_link is not None:
            require(not (isolated_workspace.parent / "external-link").exists(), "workspace copy followed an external symlink")

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
        traversal_result = registry.execute(
            "terminal",
            {"command": "printf bad > ../../outside-relative.txt", "cwd": "."},
            ToolContext(
                session_id="eval-session",
                cwd=isolated_workspace,
                worker_workspace_path=str(isolated_workspace),
                worker_workspace_isolation="copy",
                worker_sandbox_mode="workspace_execute",
            ),
        )
        require(not traversal_result.ok, "terminal sandbox allowed a relative traversal path")

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
    checks: list[tuple[str, Callable[[], CheckMetrics | None]]] = [
        ("role_identity_and_task_context", eval_role_identity_and_task_context),
        ("task_lifecycle", eval_task_lifecycle_contract),
        ("runner_contract_matrix", eval_runner_contract_matrix),
        ("isolated_child_agent", eval_isolated_child_agent_contract),
        ("orchestrator_and_artifact_handoff", eval_orchestrator_contract),
        ("orchestrator_quality_and_replan", eval_orchestrator_quality_and_replan_contract),
        ("mcp_tool_contract", eval_mcp_tool_contract),
        ("subprocess_supervisor_faults", eval_subprocess_supervisor_fault_contract),
        ("durable_supervisor_restart_soak", eval_durable_supervisor_restart_soak_contract),
        ("worker_isolation_and_sandbox", eval_worker_isolation_policy_contract),
        ("memory_quality", eval_memory_quality_contract),
        ("audio_quality", eval_audio_quality_contract),
        ("action_approval_scope", eval_action_approval_scope_contract),
        ("approval_override_audit", eval_approval_override_audit_contract),
    ]
    results: list[dict[str, object]] = []
    started_at = time.perf_counter()
    for name, check in checks:
        check_started_at = time.perf_counter()
        metrics = check()
        result: dict[str, object] = {
            "name": name,
            "status": "passed",
            "durationMs": round((time.perf_counter() - check_started_at) * 1000, 2),
        }
        if metrics:
            result["metrics"] = metrics
        results.append(result)
    report = {
        "ok": True,
        "suite": "runtime_contracts",
        "checkCount": len(results),
        "durationMs": round((time.perf_counter() - started_at) * 1000, 2),
        "checks": results,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    print("runtime contract evals passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
