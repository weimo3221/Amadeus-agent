from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus import server as runtime_server
from amadeus.agent import AgentRuntime, PermissionBroker
from amadeus.live2d import LocalLive2DModelLibrary
from amadeus.memory import MessageMemoryStore
from amadeus.orchestrator import OrchestratorService
from amadeus.runtime_events import RuntimeEventBus
from scripts.dev_mcp_server import DevMcpHandler


class NoopTaskWorker:
    def __init__(self) -> None:
        self.submitted: list[str] = []

    def submit(self, task_id: str) -> None:
        self.submitted.append(task_id)
        return None

    def cancel(self, task_id: str, *, reason: str | None = None) -> dict[str, object]:
        return runtime_server.memory_store.cancel_task(task_id, reason=reason)


class FakePlanningModel:
    model = "fake-planner"

    def __init__(self) -> None:
        self.responses: list[str] = []

    def post_chat_completion(self, payload: dict[str, Any], *, timeout_seconds: int | None = None) -> dict[str, Any]:
        content = self.responses.pop(0)
        return {"choices": [{"message": {"content": content}}]}


class FakeEmbeddingDeploymentManager:
    def __init__(self) -> None:
        self.deploy_calls: list[tuple[Any, bool]] = []
        self.cancel_calls = 0
        self.status_payload = {
            "status": "idle",
            "phase": "idle",
            "message": "waiting",
            "error": "",
            "startedAt": "",
            "finishedAt": "",
            "modelId": "BAAI/bge-m3",
            "localDir": "",
            "active": False,
        }

    def status(self, config: Any = None) -> dict[str, Any]:
        local_dir = str(getattr(config, "local_dir", "") or "")
        model_id = str(getattr(config, "model_id", "BAAI/bge-m3") or "BAAI/bge-m3")
        return {
            "configured": True,
            "provider": str(getattr(config, "provider", "local_bge_m3") or "local_bge_m3"),
            "modelId": model_id,
            "localDir": local_dir,
            "dimensions": int(getattr(config, "dimensions", 1024) or 1024),
            "normalizeEmbeddings": bool(getattr(config, "normalize_embeddings", True)),
            "batchSize": int(getattr(config, "batch_size", 8) or 8),
            "device": str(getattr(config, "device", "auto") or "auto"),
            "dependenciesInstalled": False,
            "dependencyModules": {"huggingface_hub": False, "FlagEmbedding": False},
            "dependencyInstallCommand": "python -m pip install --upgrade huggingface_hub FlagEmbedding",
            "modelInstalled": False,
            "deployed": False,
            "deployment": {**self.status_payload, "modelId": model_id, "localDir": local_dir},
        }

    def deploy(self, config: Any, *, force: bool = False) -> dict[str, Any]:
        self.deploy_calls.append((config, force))
        self.status_payload = {
            "status": "running",
            "phase": "queued",
            "message": "fake deploy queued",
            "error": "",
            "startedAt": "2026-07-09T00:00:00+00:00",
            "finishedAt": "",
            "modelId": str(getattr(config, "model_id", "BAAI/bge-m3")),
            "localDir": str(getattr(config, "local_dir", "")),
            "active": True,
        }
        return self.status(config)

    def cancel(self) -> dict[str, Any]:
        self.cancel_calls += 1
        self.status_payload = {
            **self.status_payload,
            "status": "cancelled",
            "phase": "cancelled",
            "message": "fake deploy cancelled",
            "finishedAt": "2026-07-09T00:00:01+00:00",
            "active": False,
        }
        return {"cancelled": True, "deployment": self.status_payload}


class FakeMemoryEmbeddingProvider:
    provider = "local_bge_m3"
    model_id = "BAAI/bge-m3"
    dimensions = 1024

    def available(self) -> bool:
        return True

    def encode_texts(self, texts: list[str] | tuple[str, ...]) -> list[list[float]]:
        return [[1.0, *([0.0] * (self.dimensions - 1))] for _ in texts]


class FakeMcpHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}

        if method == "tools/list":
            self.write_json({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [{
                        "name": "echo",
                        "description": "Echo a string",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                    }],
                },
            })
            return

        if method == "tools/call":
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            self.write_json({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": str(arguments.get("text") or "")}],
                },
            })
            return

        self.write_json({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "unknown method"},
        })

    def write_json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


class SummaryRuntime(AgentRuntime):
    def _request_conversation_summary(self, previous_summary: dict | None, messages: list[dict]) -> str:
        return "HTTP compacted summary"


class ReviewRuntime(AgentRuntime):
    def _request_memory_review(
        self,
        session_id: str,
        messages: list[dict],
        existing_items: list[dict],
        pending_candidates: list[dict],
    ) -> list[dict]:
        return [
            {
                "scope": "user",
                "content": "The user prefers HTTP-reviewed direct answers.",
                "confidence": 0.82,
                "reason": "The user asked for direct answers.",
                "sourceMessageStartId": int(messages[0]["id"]),
                "sourceMessageEndId": int(messages[-1]["id"]),
            }
        ]


class TurnRuntime(AgentRuntime):
    def __init__(self, *args, **kwargs) -> None:
        self.decision_messages: list[list[dict]] = []
        self.final_messages: list[list[dict]] = []
        super().__init__(*args, **kwargs)

    def _request_tool_decision(self, session_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        self.decision_messages.append(json.loads(json.dumps(messages)))
        return {"role": "assistant", "content": "", "tool_calls": []}

    def _stream_final_response(self, messages: list[dict[str, Any]]):
        self.final_messages.append(json.loads(json.dumps(messages)))
        yield "HTTP ok"


class PythonRuntimeHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.previous_api_key = os.environ.get("OPENAI_API_KEY")
        self.previous_memory_store = runtime_server.memory_store
        self.previous_agent_runtime = runtime_server.agent_runtime
        self.previous_permission_broker = runtime_server.permission_broker
        self.previous_live2d_library = runtime_server.live2d_library
        self.previous_task_worker = runtime_server.task_worker
        self.previous_orchestrator_service = runtime_server.orchestrator_service
        self.previous_runtime_event_bus = runtime_server.runtime_event_bus

        database_path = Path(self.tmpdir.name) / "amadeus.sqlite"
        self.runtime_config_path = Path(self.tmpdir.name) / "runtime.yaml"
        self.harnesses_config_path = Path(self.tmpdir.name) / "harnesses.yaml"
        self.skills_root = Path(self.tmpdir.name) / "skills"
        skill_dir = self.skills_root / "development" / "runtime-debug"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: runtime-debug\ndescription: Debug runtime behavior.\n---\n\nUse tests before fixes.\n",
            encoding="utf-8",
        )
        live2d_root = Path(self.tmpdir.name) / "live2d"
        live2d_model_dir = live2d_root / "hiyori-free"
        live2d_model_dir.mkdir(parents=True)
        (live2d_model_dir / "hiyori_free_t08.model3.json").write_text('{"Version":3}', encoding="utf-8")
        (live2d_model_dir / "hiyori_free_t08.moc3").write_bytes(b"moc")
        (live2d_model_dir / "manifest.yaml").write_text(
            "\n".join([
                "displayName: Hiyori Free",
                "defaults:",
                "  expression: neutral",
                "  motion: idle",
            ]),
            encoding="utf-8",
        )
        live2d_pro_dir = live2d_root / "hiyori-pro"
        live2d_pro_dir.mkdir(parents=True)
        (live2d_pro_dir / "hiyori_pro.model3.json").write_text('{"Version":3}', encoding="utf-8")
        self.harnesses_config_path.write_text(
            "\n".join([
                "harnesses:",
                "  live2d:",
                "    enabled: true",
                "    model:",
                "      id: hiyori-free",
                "      path: hiyori-free/hiyori_free_t08.model3.json",
                "",
            ]),
            encoding="utf-8",
        )
        memory_store = MessageMemoryStore(database_path)
        self.planning_model = FakePlanningModel()
        runtime_server.memory_store = memory_store
        runtime_server.runtime_event_bus = RuntimeEventBus()
        runtime_server.agent_runtime = TurnRuntime(
            memory_store,
            audio_runtime=None,
            tools_config_path=Path(self.tmpdir.name) / "missing-tools.yaml",
            runtime_config_path=self.runtime_config_path,
            skills_root=self.skills_root,
        )
        runtime_server.permission_broker = PermissionBroker()
        runtime_server.live2d_library = LocalLive2DModelLibrary(live2d_root, "http://runtime", self.harnesses_config_path)
        runtime_server.task_worker = NoopTaskWorker()
        runtime_server.orchestrator_service = OrchestratorService(
            memory_store,
            submit_task=runtime_server.task_worker.submit,
            model_client=self.planning_model,
        )
        runtime_server.agent_runtime.set_task_worker(runtime_server.task_worker)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), runtime_server.RuntimeRequestHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

        runtime_server.memory_store = self.previous_memory_store
        runtime_server.agent_runtime = self.previous_agent_runtime
        runtime_server.permission_broker = self.previous_permission_broker
        runtime_server.live2d_library = self.previous_live2d_library
        runtime_server.task_worker = self.previous_task_worker
        runtime_server.orchestrator_service = self.previous_orchestrator_service
        runtime_server.runtime_event_bus = self.previous_runtime_event_bus

        if self.previous_api_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self.previous_api_key
        self.tmpdir.cleanup()

    def url(self, path: str) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}{path}"

    def get_json(self, path: str) -> dict:
        with urlopen(self.url(path), timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path: str, payload: dict) -> dict:
        return self.post_json_status(path, payload, expected_status=200)

    def post_json_status(self, path: str, payload: dict, *, expected_status: int) -> dict:
        request = Request(
            self.url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, expected_status)
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            self.assertEqual(error.code, expected_status)
            return json.loads(error.read().decode("utf-8"))

    def put_json(self, path: str, payload: dict) -> dict:
        request = Request(
            self.url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read().decode("utf-8"))

    def post_ndjson(self, path: str, payload: dict) -> list[dict]:
        request = Request(
            self.url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            body = response.read().decode("utf-8")
        return [json.loads(line) for line in body.splitlines() if line.strip()]

    def get_ndjson(self, path: str) -> list[dict]:
        with urlopen(self.url(path), timeout=5) as response:
            self.assertEqual(response.status, 200)
            body = response.read().decode("utf-8")
        return [json.loads(line) for line in body.splitlines() if line.strip()]

    def test_tools_list_exposes_permission_state_and_enabled_schemas(self) -> None:
        payload = self.get_json("/tools/list")

        self.assertTrue(payload["ok"])
        tool_names = {entry["name"] for entry in payload["tools"]}
        schema_names = {entry["function"]["name"] for entry in payload["schemas"]}
        self.assertIn("get_current_time", tool_names)
        self.assertIn("get_current_time", schema_names)

    def test_tools_config_updates_mcp_and_reloads_registry(self) -> None:
        from amadeus.mcp import McpServerConfig, build_mcp_tool_specs
        from amadeus.tool_runtime import registry as registry_module

        discovered: list[McpServerConfig] = []
        original_builder = registry_module.build_mcp_tool_specs

        def fake_builder(servers: list[McpServerConfig], *, default_permission: str = "ask"):
            def fake_list_tools(server: McpServerConfig) -> list[dict[str, object]]:
                discovered.append(server)
                return [{
                    "name": "lookup",
                    "description": "Lookup from test MCP",
                    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
                }]

            return build_mcp_tool_specs(servers, default_permission=default_permission, list_tools=fake_list_tools)

        registry_module.build_mcp_tool_specs = fake_builder
        try:
            payload = self.post_json("/tools/config", {
                "mcp": {
                    "enabled": True,
                    "permission": "ask",
                    "servers": [{
                        "name": "local",
                        "url": "http://127.0.0.1:9999/mcp",
                        "enabled": True,
                        "permission": "allow",
                        "timeoutSeconds": 7,
                    }],
                },
            })
            listed = self.get_json("/tools/list")
        finally:
            registry_module.build_mcp_tool_specs = original_builder

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["mcp"]["enabled"])
        self.assertEqual(payload["mcp"]["servers"][0]["name"], "local")
        self.assertEqual(discovered[0].timeout_seconds, 7)
        tool_names = {entry["name"]: entry for entry in listed["tools"]}
        schema_names = {entry["function"]["name"] for entry in listed["schemas"]}
        self.assertIn("mcp__local__lookup", tool_names)
        self.assertEqual(tool_names["mcp__local__lookup"]["permission"], "allow")
        self.assertIn("mcp__local__lookup", schema_names)

    def test_providers_config_assembly_preserves_embedding_section(self) -> None:
        previous_path = runtime_server.PROVIDERS_CONFIG_PATH
        try:
            config_path = Path(self.tmpdir.name) / "providers.yaml"
            runtime_server.PROVIDERS_CONFIG_PATH = config_path
            config_path.write_text(
                "\n".join([
                    "llm:",
                    "  default: deepseek",
                    "  providers:",
                    "    deepseek:",
                    "      model: deepseek-v4-pro",
                    "",
                    "embedding:",
                    "  default: local_bge_m3",
                    "  providers:",
                    "    local_bge_m3:",
                    "      model: BAAI/bge-m3",
                    "      localPath: /tmp/bge-m3",
                    "",
                    "customSection:",
                    "  enabled: true",
                ]),
                encoding="utf-8",
            )

            runtime_server.assemble_providers_config(tts_lines=runtime_server.DEFAULT_TTS_SECTION_LINES)

            content = config_path.read_text(encoding="utf-8")
        finally:
            runtime_server.PROVIDERS_CONFIG_PATH = previous_path

        self.assertIn("embedding:", content)
        self.assertIn("local_bge_m3:", content)
        self.assertIn("customSection:", content)

    def test_tools_config_test_reports_mcp_discovery(self) -> None:
        mcpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeMcpHandler)
        mcp_thread = threading.Thread(target=mcpd.serve_forever, daemon=True)
        mcp_thread.start()
        try:
            host, port = mcpd.server_address
            payload = self.post_json("/tools/config/test", {
                "server": {
                    "name": "dev",
                    "url": f"http://{host}:{port}/mcp",
                    "enabled": True,
                    "permission": "ask",
                    "timeoutSeconds": 5,
                },
            })
        finally:
            mcpd.shutdown()
            mcpd.server_close()
            mcp_thread.join(timeout=2)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["toolCount"], 1)
        self.assertEqual(payload["tools"][0]["name"], "echo")

    def test_tools_config_mcp_server_can_be_executed_after_reload(self) -> None:
        mcpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeMcpHandler)
        mcp_thread = threading.Thread(target=mcpd.serve_forever, daemon=True)
        mcp_thread.start()
        try:
            host, port = mcpd.server_address
            self.post_json("/tools/config", {
                "mcp": {
                    "enabled": True,
                    "permission": "ask",
                    "servers": [{
                        "name": "dev",
                        "url": f"http://{host}:{port}/mcp",
                        "enabled": True,
                        "permission": "allow",
                        "timeoutSeconds": 5,
                    }],
                },
            })
            listed = self.get_json("/tools/list")
            executed = self.post_json("/tools/execute", {
                "toolName": "mcp__dev__echo",
                "args": {"text": "hello mcp"},
            })
        finally:
            mcpd.shutdown()
            mcpd.server_close()
            mcp_thread.join(timeout=2)

        schema_names = {entry["function"]["name"] for entry in listed["schemas"]}
        self.assertIn("mcp__dev__echo", schema_names)
        self.assertTrue(executed["ok"])
        self.assertTrue(executed["toolOk"])
        self.assertEqual(executed["result"]["result"]["content"][0]["text"], "hello mcp")

    def test_tools_config_can_execute_hermes_fixture_mcp_server(self) -> None:
        mcpd = ThreadingHTTPServer(("127.0.0.1", 0), DevMcpHandler)
        mcpd.fixture = "hermes"  # type: ignore[attr-defined]
        mcp_thread = threading.Thread(target=mcpd.serve_forever, daemon=True)
        mcp_thread.start()
        try:
            host, port = mcpd.server_address
            self.post_json("/tools/config", {
                "mcp": {
                    "enabled": True,
                    "permission": "ask",
                    "servers": [{
                        "name": "hermes-fixture",
                        "url": f"http://{host}:{port}/mcp",
                        "enabled": True,
                        "permission": "allow",
                        "timeoutSeconds": 5,
                    }],
                },
            })
            listed = self.get_json("/tools/list")
            conversations = self.post_json("/tools/execute", {
                "toolName": "mcp__hermes_fixture__conversations_list",
                "args": {"platform": "telegram"},
            })
            messages = self.post_json("/tools/execute", {
                "toolName": "mcp__hermes_fixture__messages_read",
                "args": {"session_key": "agent:main:telegram:dm:123456", "limit": 2},
            })
        finally:
            mcpd.shutdown()
            mcpd.server_close()
            mcp_thread.join(timeout=2)

        schema_names = {entry["function"]["name"] for entry in listed["schemas"]}
        self.assertIn("mcp__hermes_fixture__conversations_list", schema_names)
        self.assertTrue(conversations["toolOk"])
        conversation_text = conversations["result"]["result"]["content"][0]["text"]
        self.assertIn("agent:main:telegram:dm:123456", conversation_text)
        self.assertTrue(messages["toolOk"])
        messages_text = messages["result"]["result"]["content"][0]["text"]
        self.assertIn("I see the screenshot", messages_text)

    def test_skills_list_and_view_expose_runtime_skills(self) -> None:
        listed = self.get_json("/skills/list")
        viewed = self.get_json("/skills/view?name=runtime-debug")

        self.assertTrue(listed["ok"])
        self.assertEqual(len(listed["skills"]), 1)
        self.assertEqual(listed["skills"][0]["identifier"], "development/runtime-debug")
        self.assertTrue(viewed["ok"])
        self.assertEqual(viewed["skill"]["name"], "runtime-debug")
        self.assertIn("Use tests before fixes.", viewed["skill"]["instructions"])

    def test_roles_http_round_trip_workspace_path(self) -> None:
        workspace_path = str(Path(self.tmpdir.name) / "workspace")
        created = self.post_json("/roles", {
            "name": "Workspace Role",
            "workspacePath": workspace_path,
        })
        role_id = created["role"]["id"]
        updated = self.put_json(f"/roles/{role_id}", {"workspacePath": ""})

        self.assertTrue(created["ok"])
        self.assertEqual(created["role"]["workspacePath"], workspace_path)
        self.assertEqual(updated["role"]["workspacePath"], "")

    def test_role_runtime_scope_filters_tools_skills_and_direct_execute(self) -> None:
        created = self.post_json("/roles", {
            "name": "Scoped Role",
            "runtimeScope": {
                "tools": ["get_current_time"],
                "skills": ["development/runtime-debug"],
                "mcpServers": [],
            },
        })
        role_id = created["role"]["id"]
        session = self.post_json("/sessions", {"roleId": role_id, "title": "Scoped session"})
        session_id = session["session"]["id"]

        tools = self.get_json(f"/tools/list?sessionId={session_id}")
        skills = self.get_json(f"/skills/list?sessionId={session_id}")
        allowed = self.post_json("/tools/execute", {
            "sessionId": session_id,
            "toolName": "get_current_time",
            "args": {},
        })
        denied = self.post_json_status("/tools/execute", {
            "sessionId": session_id,
            "toolName": "read_file",
            "args": {"path": "README.md"},
        }, expected_status=403)

        self.assertEqual(created["role"]["runtimeScope"]["tools"], ["get_current_time"])
        tool_names = {tool["name"] for tool in tools["tools"]}
        schema_names = {schema["function"]["name"] for schema in tools["schemas"]}
        self.assertEqual(tool_names, {"get_current_time"})
        self.assertEqual(schema_names, {"get_current_time"})
        self.assertEqual([skill["identifier"] for skill in skills["skills"]], ["development/runtime-debug"])
        self.assertTrue(allowed["toolOk"])
        self.assertFalse(denied["ok"])
        self.assertIn("not enabled for this role", denied["error"])

    def test_role_identity_http_round_trip(self) -> None:
        created = self.post_json("/roles", {"name": "Identity Role"})
        role_id = created["role"]["id"]
        identity = self.get_json(f"/roles/{role_id}/identity")
        updated = self.put_json(f"/roles/{role_id}/identity", {
            "name": "小艾",
            "soulText": "You are 小艾. Be direct.",
        })

        self.assertTrue(identity["ok"])
        self.assertIn("Identity Role", identity["identity"]["content"])
        self.assertTrue(updated["ok"])
        self.assertEqual(updated["identity"]["roleName"], "小艾")
        self.assertIn("You are 小艾", updated["identity"]["content"])

    def test_session_plan_http_round_trip(self) -> None:
        saved = self.put_json("/sessions/http-test/plan", {
            "items": [
                {"id": "inspect", "content": "Inspect plan endpoints", "status": "completed"},
                {"id": "wire", "content": "Wire HTTP plan persistence", "status": "in_progress"},
            ],
        })
        loaded = self.get_json("/sessions/http-test/plan")

        self.assertTrue(saved["ok"])
        self.assertEqual(saved["plan"]["summary"]["inProgress"], 1)
        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["plan"]["sessionId"], "http-test")
        self.assertEqual(loaded["plan"]["items"][1]["id"], "wire")

    def test_session_plan_runs_http_list(self) -> None:
        user_message_id = runtime_server.memory_store.save("http-test", "user", "plan this")
        runtime_server.memory_store.save_session_plan(
            "http-test",
            [{"id": "wire", "content": "Wire plan runs", "status": "completed"}],
            turn_id="turn-http",
            user_message_id=user_message_id,
        )
        runtime_server.memory_store.finish_plan_run(session_id="http-test", turn_id="turn-http")

        listed = self.get_json("/sessions/http-test/plan-runs")

        self.assertTrue(listed["ok"])
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["planRuns"][0]["turnId"], "turn-http")
        self.assertEqual(listed["planRuns"][0]["userMessageId"], user_message_id)

    def test_tasks_http_create_list_cancel_and_events(self) -> None:
        created = self.post_json_status("/tasks", {
            "sessionId": "http-test",
            "title": "Wire task HTTP",
            "body": "Expose task store to desktop.",
            "priority": 3,
        }, expected_status=201)
        task_id = created["task"]["id"]
        listed = self.get_json("/tasks?sessionId=http-test&activeOnly=true")
        cancelled = self.post_json(f"/tasks/{task_id}/cancel", {"reason": "No longer needed"})
        events = self.get_json(f"/tasks/{task_id}/events")

        self.assertTrue(created["ok"])
        self.assertEqual(created["event"]["type"], "task.updated")
        self.assertEqual(listed["summary"]["queued"], 1)
        self.assertEqual(cancelled["task"]["status"], "cancelled")
        self.assertEqual(events["eventCount"], 2)
        self.assertEqual(events["events"][1]["type"], "cancelled")

    def test_tasks_http_graph_attempts_and_artifacts(self) -> None:
        root = runtime_server.memory_store.create_task(session_id="http-test", title="Root task")
        child = runtime_server.memory_store.create_task(
            session_id="http-test",
            title="Child task",
            parent_task_id=str(root["id"]),
            root_task_id=str(root["id"]),
            worker_profile="researcher",
        )
        runtime_server.memory_store.add_task_edge(from_task_id=str(root["id"]), to_task_id=str(child["id"]))
        attempt = runtime_server.memory_store.create_task_attempt(str(child["id"]), worker_id="worker-http")
        runtime_server.memory_store.add_task_artifact(
            str(child["id"]),
            {"type": "summary", "title": "HTTP artifact", "content": "Graph endpoint works."},
            attempt_id=str(attempt["id"]),
        )

        graph = self.get_json(f"/tasks/{root['id']}/graph")
        attempts = self.get_json(f"/tasks/{child['id']}/attempts")
        artifacts = self.get_json(f"/tasks/{child['id']}/artifacts")

        self.assertTrue(graph["ok"])
        self.assertEqual(graph["rootTaskId"], root["id"])
        self.assertEqual(len(graph["tasks"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(attempts["attemptCount"], 1)
        self.assertEqual(attempts["attempts"][0]["workerId"], "worker-http")
        self.assertEqual(artifacts["artifactCount"], 1)
        self.assertEqual(artifacts["artifacts"][0]["content"], "Graph endpoint works.")

    def test_tasks_http_decompose_and_dispatch_graph(self) -> None:
        root = runtime_server.memory_store.create_task(session_id="http-test", title="Root graph")

        decomposed = self.post_json(f"/tasks/{root['id']}/decompose", {
            "graph": {
                "tasks": [
                    {"tempId": "first", "title": "First child"},
                    {"tempId": "second", "title": "Second child", "dependsOn": ["first"]},
                ],
            },
        })
        first_id = decomposed["tempTaskIds"]["first"]
        second_id = decomposed["tempTaskIds"]["second"]
        runtime_server.memory_store.start_task(first_id, claim_lock="worker")
        runtime_server.memory_store.complete_task(first_id, claim_lock="worker", result="done")

        dispatched = self.post_json(f"/tasks/{root['id']}/dispatch", {"limit": 10})

        self.assertTrue(decomposed["ok"])
        self.assertEqual(decomposed["rootTaskId"], root["id"])
        self.assertEqual(len(decomposed["tasks"]), 2)
        self.assertEqual(len(decomposed["edges"]), 1)
        self.assertEqual(dispatched["rootTaskId"], root["id"])
        self.assertEqual(dispatched["dispatchedTaskIds"], [second_id])

    def test_tasks_http_auto_decompose_uses_planning_model(self) -> None:
        root = runtime_server.memory_store.create_task(session_id="http-test", title="Auto graph", body="Plan this.")
        self.planning_model.responses = [
            '{"goal":"Auto graph","approach":"Research then design","acceptanceCriteria":["children created"],"outOfScope":[]}',
            '{"tasks":[{"tempId":"research","title":"Research","body":"Inspect context","workerProfile":"researcher"},{"tempId":"design","title":"Design","body":"Use research","workerProfile":"planner","dependsOn":["research"]}],"edges":[]}',
        ]

        decomposed = self.post_json(f"/tasks/{root['id']}/decompose", {"auto": True})

        self.assertTrue(decomposed["ok"])
        self.assertFalse(decomposed["fallback"])
        self.assertEqual(decomposed["decompositionSource"], "model")
        self.assertEqual(decomposed["spec"]["goal"], "Auto graph")
        self.assertEqual(len(decomposed["tasks"]), 2)
        self.assertEqual(len(decomposed["edges"]), 1)

    def test_tasks_http_synthesize_completes_root(self) -> None:
        root = runtime_server.memory_store.create_task(session_id="http-test", title="Synthesize graph")
        self.planning_model.responses = ['{"summary":"merged","result":"Merged final result"}']
        decomposed = self.post_json(f"/tasks/{root['id']}/decompose", {
            "graph": {
                "tasks": [
                    {"tempId": "first", "title": "First child"},
                    {"tempId": "second", "title": "Second child"},
                ],
            },
        })
        first_id = decomposed["tempTaskIds"]["first"]
        second_id = decomposed["tempTaskIds"]["second"]
        runtime_server.memory_store.start_task(first_id, claim_lock="worker-1")
        runtime_server.memory_store.complete_task(first_id, claim_lock="worker-1", result="first done")
        runtime_server.memory_store.start_task(second_id, claim_lock="worker-2")
        runtime_server.memory_store.complete_task(second_id, claim_lock="worker-2", result="second done")

        synthesized = self.post_json(f"/tasks/{root['id']}/synthesize", {})
        updated_root = runtime_server.memory_store.get_task(str(root["id"]))

        self.assertTrue(synthesized["ok"])
        self.assertTrue(synthesized["ready"])
        self.assertTrue(synthesized["completed"])
        self.assertEqual(synthesized["result"], "Merged final result")
        self.assertEqual(updated_root["status"], "succeeded")

    def test_tasks_http_decompose_rejects_invalid_graph(self) -> None:
        root = runtime_server.memory_store.create_task(session_id="http-test", title="Invalid graph")

        response = self.post_json_status(f"/tasks/{root['id']}/decompose", {
            "graph": {
                "tasks": [
                    {"tempId": "a", "title": "A", "dependsOn": ["b"]},
                    {"tempId": "b", "title": "B", "dependsOn": ["a"]},
                ],
            },
        }, expected_status=400)

        self.assertFalse(response["ok"])
        self.assertIn("cycle", response["error"])

    def test_tasks_http_resume_and_approve_blocked_review(self) -> None:
        task = runtime_server.memory_store.create_task(
            session_id="http-test",
            title="Review task",
            review_required=True,
        )
        runtime_server.memory_store.start_task(str(task["id"]), claim_lock="worker")
        runtime_server.memory_store.block_task(str(task["id"]), claim_lock="worker", reason="Needs review", result="Draft")

        approved = self.post_json(f"/tasks/{task['id']}/approve", {})

        self.assertEqual(approved["task"]["status"], "succeeded")

        task_2 = runtime_server.memory_store.create_task(session_id="http-test", title="Blocked task")
        runtime_server.memory_store.start_task(str(task_2["id"]), claim_lock="worker")
        runtime_server.memory_store.block_task(str(task_2["id"]), claim_lock="worker", reason="Need input")

        resumed = self.post_json(f"/tasks/{task_2['id']}/resume", {})

        self.assertEqual(resumed["task"]["status"], "queued")

    def test_runtime_events_streams_published_task_updates(self) -> None:
        received: list[dict] = []

        def read_events() -> None:
            received.extend(self.get_ndjson("/runtime/events?maxEvents=1&idleTimeoutSeconds=2"))

        reader = threading.Thread(target=read_events)
        reader.start()
        threading.Event().wait(0.1)
        runtime_server.runtime_event_bus.publish(
            "task.updated",
            "http-test",
            {
                "action": "running",
                "task": {
                    "id": "task-1",
                    "sessionId": "http-test",
                    "status": "running",
                },
            },
        )
        reader.join(timeout=5)

        self.assertFalse(reader.is_alive())
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["type"], "task.updated")
        self.assertEqual(received[0]["sessionId"], "http-test")
        self.assertEqual(received[0]["payload"]["action"], "running")

    def test_agent_turn_accepts_explicit_skills(self) -> None:
        events = self.post_ndjson("/agent/turn", {
            "sessionId": "http-test",
            "text": "hello",
            "skills": ["runtime-debug"],
        })

        self.assertEqual(events[0]["sessionId"], "http-test")
        self.assertIn("assistant.message", [event["type"] for event in events])
        self.assertIn("<suggested-skills>", runtime_server.agent_runtime.decision_messages[-1][0]["content"])
        self.assertIn("<available_skills>", runtime_server.agent_runtime.decision_messages[-1][0]["content"])

    def test_agent_cancel_reports_when_no_turn_is_running(self) -> None:
        payload = self.post_json("/agent/cancel", {"sessionId": "http-test"})

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["cancelled"])
        self.assertEqual(payload["reason"], "no_running_turn")

    def test_live2d_config_returns_current_model_url(self) -> None:
        payload = self.get_json("/live2d/config")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["model"]["id"], "hiyori-free")
        self.assertEqual(payload["model"]["path"], "hiyori-free/hiyori_free_t08.model3.json")
        self.assertTrue(payload["model"]["url"].endswith("/live2d/models/hiyori-free/hiyori_free_t08.model3.json"))
        self.assertEqual(payload["model"]["manifest"]["displayName"], "Hiyori Free")
        self.assertEqual(payload["display"]["scale"], 0.92)
        self.assertEqual(payload["display"]["offsetX"], 0)
        self.assertEqual(payload["display"]["offsetY"], 0)

    def test_live2d_models_lists_local_models_and_active_selection(self) -> None:
        payload = self.get_json("/live2d/models")

        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["models"]), 2)
        self.assertEqual(payload["activeModel"]["id"], "hiyori-free")
        free_model = next(model for model in payload["models"] if model["id"] == "hiyori-free")
        self.assertTrue(free_model["active"])
        self.assertEqual(free_model["manifest"]["displayName"], "Hiyori Free")

    def test_live2d_select_switches_model_and_persists_harness_config(self) -> None:
        payload = self.post_json("/live2d/select", {"modelId": "hiyori-pro"})

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["model"]["id"], "hiyori-pro")
        self.assertEqual(payload["model"]["path"], "hiyori-pro/hiyori_pro.model3.json")
        persisted = self.harnesses_config_path.read_text(encoding="utf-8")
        self.assertIn("id: hiyori-pro", persisted)
        self.assertIn("path: hiyori-pro/hiyori_pro.model3.json", persisted)

    def test_live2d_model_file_serves_local_assets(self) -> None:
        with urlopen(self.url("/live2d/models/hiyori-free/hiyori_free_t08.model3.json"), timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
            self.assertEqual(json.loads(response.read().decode("utf-8")), {"Version": 3})

        with urlopen(self.url("/live2d/models/hiyori-free/hiyori_free_t08.moc3"), timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"moc")

    def test_tool_permission_unknown_request_returns_unresolved(self) -> None:
        payload = self.post_json("/tools/permission", {"requestId": "missing", "approved": True})

        self.assertEqual(payload, {"ok": True, "resolved": False})

    def test_runtime_config_reload_applies_updated_yaml(self) -> None:
        previous = os.environ.get("AMADEUS_CONTEXT_MAX_TOKENS")
        os.environ.pop("AMADEUS_CONTEXT_MAX_TOKENS", None)
        self.runtime_config_path.write_text(
            "context:\n  maxTokens: 3456\nsummary:\n  triggerMessageCount: 7\n",
            encoding="utf-8",
        )
        try:
            payload = self.post_json("/runtime/config/reload", {})
        finally:
            if previous is None:
                os.environ.pop("AMADEUS_CONTEXT_MAX_TOKENS", None)
            else:
                os.environ["AMADEUS_CONTEXT_MAX_TOKENS"] = previous

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["runtimeConfig"], str(self.runtime_config_path))
        self.assertEqual(payload["config"]["context"]["maxTokens"], 3456)
        self.assertEqual(payload["config"]["summary"]["triggerMessageCount"], 7)
        self.assertEqual(runtime_server.agent_runtime.context_max_tokens, 3456)
        self.assertEqual(runtime_server.agent_runtime.summary_trigger_message_count, 7)

    def test_runtime_health_reports_structured_local_checks(self) -> None:
        runtime_server.agent_runtime.api_key = "test-key"

        payload = self.get_json("/runtime/health")

        self.assertTrue(payload["ok"])
        self.assertIn(payload["status"], {"ok", "degraded"})
        self.assertIn("timestamp", payload)
        self.assertEqual(payload["checks"]["runtime"]["runtime"], "python")
        self.assertEqual(payload["checks"]["runtime"]["serverVersion"], "AmadeusPythonRuntime/0.1")
        self.assertEqual(payload["checks"]["model"]["status"], "ok")
        self.assertTrue(payload["checks"]["model"]["apiKeyConfigured"])
        self.assertEqual(payload["checks"]["memory"]["status"], "ok")
        self.assertEqual(payload["checks"]["memory"]["databasePath"], str(runtime_server.memory_store.database_path))
        self.assertEqual(payload["checks"]["memory"]["messageCount"], 0)
        self.assertIn(payload["checks"]["embedding"]["status"], {"ok", "degraded", "disabled"})
        self.assertEqual(payload["checks"]["embedding"]["modelId"], "BAAI/bge-m3")
        self.assertEqual(payload["checks"]["tools"]["status"], "ok")
        self.assertGreater(payload["checks"]["tools"]["enabledSchemaCount"], 0)
        self.assertEqual(payload["checks"]["live2d"]["status"], "ok")
        self.assertEqual(payload["checks"]["live2d"]["model"]["id"], "hiyori-free")
        self.assertTrue(payload["checks"]["live2d"]["model"]["fileExists"])
        self.assertIn(payload["checks"]["audio"]["status"], {"ok", "disabled"})
        self.assertEqual(payload["checks"]["config"]["runtimeConfig"], str(self.runtime_config_path))
        self.assertFalse(payload["checks"]["config"]["runtimeConfigExists"])
        self.assertEqual(payload["checks"]["config"]["harnessesConfig"], str(self.harnesses_config_path))

    def test_memory_embedding_config_reports_local_bge_defaults(self) -> None:
        payload = self.get_json("/memory/embedding/config")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["embedding"]["provider"], "local_bge_m3")
        self.assertEqual(payload["embedding"]["modelId"], "BAAI/bge-m3")
        self.assertEqual(payload["embedding"]["dimensions"], 1024)
        self.assertIn("dependenciesInstalled", payload["embedding"])
        self.assertIn("modelInstalled", payload["embedding"])
        self.assertIn("deployment", payload["embedding"])
        self.assertEqual(payload["index"]["provider"], "local_bge_m3")
        self.assertEqual(payload["index"]["model"], "BAAI/bge-m3")
        self.assertIn("coverageRatio", payload["index"])
        self.assertIn("backfill", payload)
        self.assertTrue(payload["paths"]["defaultModelDir"].endswith("models/embeddings/bge-m3"))

    def test_memory_embedding_deploy_writes_config_and_queues_manager(self) -> None:
        previous_provider_path = runtime_server.PROVIDERS_CONFIG_PATH
        previous_env_path = runtime_server.ENV_CONFIG_PATH
        previous_manager = runtime_server.embedding_deployment_manager
        env_keys = ["AMADEUS_EMBEDDING_PROVIDER", "AMADEUS_BGE_M3_MODEL_ID", "AMADEUS_BGE_M3_MODEL_DIR"]
        previous_env = {key: os.environ.get(key) for key in env_keys}
        fake_manager = FakeEmbeddingDeploymentManager()
        try:
            providers_path = Path(self.tmpdir.name) / "providers-embedding.yaml"
            env_path = Path(self.tmpdir.name) / ".env.embedding"
            local_dir = Path(self.tmpdir.name) / "models" / "bge-m3"
            runtime_server.PROVIDERS_CONFIG_PATH = providers_path
            runtime_server.ENV_CONFIG_PATH = env_path
            runtime_server.embedding_deployment_manager = fake_manager

            payload = self.post_json_status(
                "/memory/embedding/deploy",
                {"localDir": str(local_dir), "force": True},
                expected_status=202,
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(len(fake_manager.deploy_calls), 1)
            config, force = fake_manager.deploy_calls[0]
            self.assertTrue(force)
            self.assertEqual(config.provider, "local_bge_m3")
            self.assertEqual(config.model_id, "BAAI/bge-m3")
            self.assertEqual(config.local_dir, local_dir)
            self.assertEqual(payload["embedding"]["deployment"]["status"], "running")
            self.assertTrue(payload["embedding"]["deployment"]["active"])

            env_content = env_path.read_text(encoding="utf-8")
            providers_content = providers_path.read_text(encoding="utf-8")
            self.assertIn("AMADEUS_EMBEDDING_PROVIDER=local_bge_m3", env_content)
            self.assertIn(f"AMADEUS_BGE_M3_MODEL_DIR={local_dir}", env_content)
            self.assertIn("embedding:", providers_content)
            self.assertIn("local_bge_m3:", providers_content)
            self.assertIn(str(local_dir), providers_content)
        finally:
            runtime_server.PROVIDERS_CONFIG_PATH = previous_provider_path
            runtime_server.ENV_CONFIG_PATH = previous_env_path
            runtime_server.embedding_deployment_manager = previous_manager
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_memory_embedding_cancel_calls_manager(self) -> None:
        previous_provider_path = runtime_server.PROVIDERS_CONFIG_PATH
        previous_manager = runtime_server.embedding_deployment_manager
        fake_manager = FakeEmbeddingDeploymentManager()
        try:
            providers_path = Path(self.tmpdir.name) / "providers-embedding-cancel.yaml"
            providers_path.write_text(
                "\n".join([
                    "embedding:",
                    "  default: local_bge_m3",
                    "  providers:",
                    "    local_bge_m3:",
                    "      model: BAAI/bge-m3",
                    "      localPath: models/embeddings/bge-m3",
                ]),
                encoding="utf-8",
            )
            fake_manager.status_payload = {
                **fake_manager.status_payload,
                "status": "running",
                "phase": "downloading_model",
                "message": "fake deploy running",
                "active": True,
            }
            runtime_server.PROVIDERS_CONFIG_PATH = providers_path
            runtime_server.embedding_deployment_manager = fake_manager

            payload = self.post_json("/memory/embedding/cancel", {})

            self.assertTrue(payload["ok"])
            self.assertEqual(fake_manager.cancel_calls, 1)
            self.assertTrue(payload["cancelResult"]["cancelled"])
            self.assertEqual(payload["cancelResult"]["deployment"]["status"], "cancelled")
            self.assertEqual(payload["embedding"]["deployment"]["status"], "cancelled")
        finally:
            runtime_server.PROVIDERS_CONFIG_PATH = previous_provider_path
            runtime_server.embedding_deployment_manager = previous_manager

    def test_memory_embedding_backfill_indexes_memory_items_over_http(self) -> None:
        previous_provider_factory = runtime_server.create_local_bge_m3_embedding_provider
        try:
            runtime_server.create_local_bge_m3_embedding_provider = lambda **_: FakeMemoryEmbeddingProvider()
            runtime_server.memory_store.save_memory_item("project", "The deployment target is local.", confidence=0.8)

            payload = self.post_json("/memory/embedding/backfill", {"sync": True, "limit": 10, "batchSize": 1})

        finally:
            runtime_server.create_local_bge_m3_embedding_provider = previous_provider_factory

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["backfillResult"]["embedded"], 1)
        self.assertEqual(payload["backfillResult"]["coverage"]["ready"], 1)

    def test_runtime_feedback_records_desktop_capabilities_and_audio_state(self) -> None:
        main_capabilities = self.post_json("/runtime/feedback", {
            "sessionId": "feedback-session",
            "clientId": "main-ui-client",
            "surface": "main-ui",
            "type": "desktop.capabilities",
            "timestamp": "2026-06-22T00:00:00.000Z",
            "payload": {
                "desktop": {"runtime": "electron", "protocolVersion": 1},
                "live2d": {
                    "available": False,
                    "expressions": [],
                    "motions": [],
                },
                "audio": {
                    "runtimeAudio": False,
                    "speechSynthesis": True,
                    "voiceCount": 2,
                },
            },
        })
        self.assertFalse(main_capabilities["feedback"]["desktopCapabilities"]["live2d"]["available"])

        capabilities = self.post_json("/runtime/feedback", {
            "sessionId": "feedback-session",
            "clientId": "companion-client",
            "surface": "companion",
            "type": "desktop.capabilities",
            "timestamp": "2026-06-22T00:00:00.000Z",
            "payload": {
                "desktop": {"runtime": "electron", "protocolVersion": 1},
                "live2d": {
                    "available": True,
                    "modelId": "hiyori-free",
                    "expressions": ["smile"],
                    "motions": ["Idle"],
                },
                "audio": {
                    "runtimeAudio": True,
                    "speechSynthesis": True,
                    "voiceCount": 2,
                },
            },
        })

        self.assertTrue(capabilities["ok"])
        self.assertEqual(capabilities["feedback"]["desktopCapabilities"]["desktop"]["clientCount"], 2)
        self.assertTrue(capabilities["feedback"]["desktopCapabilities"]["live2d"]["available"])
        self.assertEqual(capabilities["feedback"]["desktopCapabilities"]["live2d"]["modelId"], "hiyori-free")
        self.assertIn("main-ui-client", capabilities["feedback"]["desktopCapabilitiesByClient"])
        self.assertIn("companion-client", capabilities["feedback"]["desktopCapabilitiesByClient"])

        playback = self.post_json("/runtime/feedback", {
            "sessionId": "feedback-session",
            "clientId": "companion-client",
            "surface": "companion",
            "type": "audio.playback-started",
            "timestamp": "2026-06-22T00:00:01.000Z",
            "payload": {
                "source": "runtime_audio",
                "audioUrl": "http://runtime/audio.wav",
                "durationMs": 480,
            },
        })

        self.assertEqual(playback["feedback"]["audioPlayback"]["status"], "playing")
        self.assertEqual(playback["feedback"]["audioPlayback"]["audioUrl"], "http://runtime/audio.wav")
        self.assertEqual(playback["feedback"]["audioPlayback"]["clientId"], "companion-client")
        self.assertEqual(playback["feedback"]["audioPlayback"]["surface"], "companion")
        self.assertEqual(playback["events"][0]["type"], "character.behavior")
        self.assertEqual(playback["events"][0]["payload"]["motion"], "talk")
        self.assertEqual(playback["events"][1]["type"], "audio.lipsync-cues")
        self.assertEqual(playback["events"][1]["payload"]["source"], "runtime_audio")
        self.assertGreaterEqual(len(playback["events"][1]["payload"]["cues"]), 2)

        snapshot = self.get_json("/runtime/feedback?sessionId=feedback-session")
        self.assertEqual(snapshot["feedback"]["sessionId"], "feedback-session")
        self.assertEqual(snapshot["feedback"]["recentEventCount"], 3)
        self.assertEqual(snapshot["feedback"]["recentEvents"][-1]["type"], "audio.playback-started")

    def test_agent_turn_streams_missing_api_key_error_as_ndjson(self) -> None:
        os.environ["OPENAI_API_KEY"] = ""
        runtime_server.agent_runtime.api_key = ""

        events = self.post_ndjson("/agent/turn", {"sessionId": "http-test", "text": "hello"})

        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["sessionId"], "http-test")
        self.assertEqual(events[0]["payload"]["code"], "missing_api_key")
        self.assertEqual(runtime_server.memory_store.count("http-test"), 0)

    def test_memory_search_returns_matching_messages(self) -> None:
        runtime_server.memory_store.save("http-test", "user", "Please remember the blue notebook")
        runtime_server.memory_store.save("other-session", "user", "The red notebook is elsewhere")

        payload = self.get_json("/memory/search?sessionId=http-test&query=blue&limit=5")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["query"], "blue")
        self.assertEqual(payload["sessionId"], "http-test")
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["sessionId"], "http-test")
        self.assertIn("blue", payload["results"][0]["content"])

    def test_memory_context_diagnostics_returns_recent_runtime_ring_buffer(self) -> None:
        runtime_server.agent_runtime._memory_context_used_event(
            "http-test",
            "turn-1",
            {
                "sourceCounts": {"memory_item": 1},
                "sourceCount": 1,
                "coveredThroughMessageId": 0,
                "sources": [{
                    "kind": "memory_item",
                    "sourceId": "1",
                    "contentChars": 32,
                    "reason": "accepted durable structured memory",
                    "metadata": {"scope": "project"},
                }],
            },
        )
        runtime_server.agent_runtime._memory_context_used_event(
            "http-test",
            "turn-2",
            {
                "sourceCounts": {"retrieval": 1},
                "sourceCount": 1,
                "coveredThroughMessageId": 0,
                "sources": [{
                    "kind": "retrieval",
                    "sourceId": "2",
                    "contentChars": 24,
                    "reason": "FTS match for current user message",
                    "metadata": {"role": "assistant"},
                }],
            },
        )
        runtime_server.agent_runtime._memory_context_used_event(
            "other-session",
            "turn-other",
            {
                "sourceCounts": {"retrieval": 1},
                "sourceCount": 1,
                "coveredThroughMessageId": 0,
                "sources": [],
            },
        )

        payload = self.get_json("/memory/context/diagnostics?sessionId=http-test&limit=1")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["sessionId"], "http-test")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["filters"], {"sessionId": "http-test", "limit": 1})
        self.assertEqual(payload["diagnostics"][0]["turnId"], "turn-2")
        self.assertEqual(payload["diagnostics"][0]["sessionId"], "http-test")
        self.assertEqual(payload["diagnostics"][0]["phase"], "turn_start")
        self.assertIn("timestamp", payload["diagnostics"][0])
        self.assertEqual(payload["diagnostics"][0]["sourceCounts"], {"retrieval": 1})

    def test_memory_context_diagnostics_defaults_to_default_session(self) -> None:
        runtime_server.agent_runtime._memory_context_used_event(
            "default",
            "default-turn",
            {
                "sourceCounts": {},
                "sourceCount": 0,
                "coveredThroughMessageId": 0,
                "sources": [],
            },
        )

        payload = self.get_json("/memory/context/diagnostics")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["sessionId"], "default")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["diagnostics"][0]["turnId"], "default-turn")

    def test_memory_items_roundtrip_and_delete_over_http(self) -> None:
        saved = self.post_json("/memory/items", {
            "scope": "user",
            "content": "The user prefers short updates.",
            "confidence": 0.75,
            "memoryType": "preference",
            "metadata": {"source": "http", "tags": ["updates"]},
            "sourceSessionId": "http-test",
            "sourceMessageId": 3,
        })
        listed = self.get_json("/memory/items?scope=user&memoryType=preference&query=short&metadata.source=http&limit=10")
        history = self.get_json(f"/memory/items/history?memoryItemId={saved['item']['memoryItemId']}")
        deleted = self.post_json("/memory/items/delete", {
            "memoryItemId": saved["item"]["memoryItemId"],
        })
        history_after_delete = self.get_json(f"/memory/items/history?memoryItemId={saved['item']['memoryItemId']}")
        listed_after_delete = self.get_json("/memory/items?scope=user")

        self.assertTrue(saved["ok"])
        self.assertEqual(saved["item"]["scope"], "user")
        self.assertEqual(saved["item"]["memoryType"], "preference")
        self.assertEqual(saved["item"]["metadata"], {"source": "http", "tags": ["updates"]})
        self.assertEqual(saved["item"]["confidence"], 0.75)
        self.assertEqual(saved["item"]["sourceSessionId"], "http-test")
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["filters"]["memoryType"], "preference")
        self.assertEqual(listed["filters"]["metadataFilter"], {"source": "http"})
        self.assertEqual(len(listed["items"]), 1)
        self.assertEqual(listed["items"][0]["content"], "The user prefers short updates.")
        self.assertTrue(history["ok"])
        self.assertEqual(history["count"], 1)
        self.assertEqual(history["history"][0]["event"], "ADD")
        self.assertEqual(history["history"][0]["actor"], "api")
        self.assertTrue(deleted["ok"])
        self.assertTrue(deleted["deleted"])
        self.assertEqual(history_after_delete["count"], 2)
        self.assertEqual(history_after_delete["history"][0]["event"], "DELETE")
        self.assertEqual(history_after_delete["history"][0]["actor"], "api")
        self.assertEqual(listed_after_delete["items"], [])

    def test_memory_review_candidates_accept_and_reject_over_http(self) -> None:
        saved = self.post_json("/memory/review/candidates", {
            "sessionId": "http-test",
            "scope": "user",
            "content": "The user prefers direct answers.",
            "confidence": 0.8,
            "reason": "The user asked for direct answers.",
              "scopeReason": "This is a stable user preference.",
              "safetyLabels": ["explicit", "non_secret", "correct_scope"],
              "retentionType": "stable_preference",
            "sourceMessageStartId": 2,
            "sourceMessageEndId": 4,
        })
        duplicate = self.post_json("/memory/review/candidates", {
            "sessionId": "http-test",
            "scope": "user",
            "content": "The user prefers direct answers.",
        })
        listed = self.get_json("/memory/review/candidates?sessionId=http-test&status=pending&scope=user")
        accepted = self.post_json("/memory/review/accept", {
            "candidateId": saved["candidate"]["candidateId"],
        })
        items = self.get_json("/memory/items?scope=user&query=direct")

        rejected_candidate = self.post_json("/memory/review/candidates", {
            "sessionId": "http-test",
            "scope": "project",
            "content": "Temporary implementation detail should not be stored.",
        })
        rejected = self.post_json("/memory/review/reject", {
            "candidateId": rejected_candidate["candidate"]["candidateId"],
        })
        rejected_list = self.get_json("/memory/review/candidates?sessionId=http-test&status=rejected")

        self.assertTrue(saved["ok"])
        self.assertFalse(saved["duplicate"])
        self.assertEqual(saved["candidate"]["status"], "pending")
        self.assertEqual(saved["candidate"]["scopeReason"], "This is a stable user preference.")
        self.assertEqual(saved["candidate"]["safetyLabels"], ["explicit", "non_secret", "correct_scope"])
        self.assertEqual(saved["candidate"]["retentionType"], "stable_preference")
        self.assertEqual(saved["candidate"]["reason"], "The user asked for direct answers.")
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["candidate"]["candidateId"], saved["candidate"]["candidateId"])
        self.assertEqual(len(listed["candidates"]), 1)
        self.assertTrue(accepted["ok"])
        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["candidate"]["status"], "accepted")
        self.assertEqual(accepted["item"]["content"], "The user prefers direct answers.")
        self.assertEqual(len(items["items"]), 1)
        self.assertTrue(rejected["ok"])
        self.assertTrue(rejected["rejected"])
        self.assertEqual(rejected["candidate"]["status"], "rejected")
        self.assertEqual(len(rejected_list["candidates"]), 1)

    def test_memory_review_run_auto_promotes_safe_candidates_over_http(self) -> None:
        runtime_server.memory_store.save("http-test", "user", "Please answer directly over HTTP.")
        runtime_server.memory_store.save("http-test", "assistant", "Understood.")
        runtime_server.agent_runtime = ReviewRuntime(
            runtime_server.memory_store,
            audio_runtime=None,
            tools_config_path=Path(self.tmpdir.name) / "missing-tools.yaml",
        )

        reviewed = self.post_json("/memory/review/run", {"sessionId": "http-test", "force": True})
        listed = self.get_json("/memory/review/candidates?sessionId=http-test&status=accepted")
        jobs = self.get_json("/memory/review/jobs?sessionId=http-test&status=completed")
        items = self.get_json("/memory/items?scope=user")

        self.assertTrue(reviewed["ok"])
        self.assertTrue(reviewed["reviewed"])
        self.assertEqual(reviewed["job"]["status"], "completed")
        self.assertEqual(reviewed["job"]["trigger"], "manual")
        self.assertEqual(reviewed["candidateCount"], 1)
        self.assertEqual(reviewed["promotedItemCount"], 1)
        self.assertEqual(len(listed["candidates"]), 1)
        self.assertEqual(listed["candidates"][0]["status"], "accepted")
        self.assertEqual(listed["candidates"][0]["content"], "The user prefers HTTP-reviewed direct answers.")
        self.assertTrue(jobs["ok"])
        self.assertEqual(len(jobs["jobs"]), 1)
        self.assertEqual(jobs["jobs"][0]["jobId"], reviewed["jobId"])
        self.assertEqual(jobs["jobs"][0]["savedCandidateCount"], 1)
        self.assertEqual(len(items["items"]), 1)
        self.assertEqual(items["items"][0]["content"], "The user prefers HTTP-reviewed direct answers.")

    def test_memory_summary_roundtrip_over_http(self) -> None:
        runtime_server.memory_store.save("http-test", "user", "Long setup")

        saved = self.post_json("/memory/summary", {
            "sessionId": "http-test",
            "content": "The session covered the long setup.",
            "summarizedMessageCount": 1,
        })
        loaded = self.get_json("/memory/summary?sessionId=http-test")

        self.assertTrue(saved["ok"])
        self.assertEqual(saved["summary"]["sessionId"], "http-test")
        self.assertEqual(saved["summary"]["content"], "The session covered the long setup.")
        self.assertEqual(saved["summary"]["summarizedMessageCount"], 1)
        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["summary"]["summaryId"], saved["summary"]["summaryId"])
        self.assertEqual(loaded["summary"]["content"], "The session covered the long setup.")

    def test_memory_reset_clears_summary_over_http(self) -> None:
        runtime_server.memory_store.save_conversation_summary("http-test", "Summary to reset")

        self.post_json("/memory/reset", {"sessionId": "http-test"})
        payload = self.get_json("/memory/summary?sessionId=http-test")

        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["summary"])

    def test_memory_compact_triggers_runtime_summary(self) -> None:
        runtime_server.agent_runtime = SummaryRuntime(
            runtime_server.memory_store,
            audio_runtime=None,
            tools_config_path=Path(self.tmpdir.name) / "missing-tools.yaml",
        )
        runtime_server.agent_runtime.summary_keep_recent_messages = 1
        for index in range(3):
            runtime_server.memory_store.save("http-test", "user", f"message {index}")

        payload = self.post_json("/memory/compact", {"sessionId": "http-test", "force": True})

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["compacted"])
        self.assertEqual(payload["summary"]["content"], "HTTP compacted summary")

    def test_tool_execute_search_memory_has_memory_context(self) -> None:
        runtime_server.memory_store.save("default", "user", "Remember the green tea preference")

        payload = self.post_json("/tools/execute", {
            "toolName": "search_memory",
            "args": {"query": "green tea"},
        })

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["toolOk"])
        self.assertEqual(payload["result"]["resultCount"], 1)
        self.assertIn("green tea", payload["result"]["results"][0]["content"])

    def test_tools_audit_returns_filtered_persisted_records(self) -> None:
        record = runtime_server.agent_runtime.tool_audit_log.append(
            session_id="http-test",
            tool_name="search_files",
            decision="finished",
            ok=True,
            duration_ms=7,
        )
        runtime_server.agent_runtime.tool_audit_store.save(record)
        other_record = runtime_server.agent_runtime.tool_audit_log.append(
            session_id="other-session",
            tool_name="patch",
            decision="finished",
            ok=False,
            failure_code="tool_error",
        )
        runtime_server.agent_runtime.tool_audit_store.save(other_record)

        payload = self.get_json("/tools/audit?sessionId=http-test&toolName=search_files&decision=finished&ok=true")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["filters"]["sessionId"], "http-test")
        self.assertEqual(payload["filters"]["toolName"], "search_files")
        self.assertEqual(payload["filters"]["decision"], "finished")
        self.assertTrue(payload["filters"]["ok"])
        self.assertEqual(payload["records"][0]["recordId"], record.record_id)
        self.assertEqual(payload["records"][0]["toolName"], "search_files")
        self.assertTrue(payload["records"][0]["ok"])


if __name__ == "__main__":
    unittest.main()
