from __future__ import annotations

import os
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.mcp import mcp_tool_spec_name, normalize_mcp_identifier
from amadeus.role_scope import normalize_role_runtime_scope, role_allows_tool
from amadeus.tool_runtime import ToolAuditLog, ToolAuditStore, ToolContext, ToolLoopGuardrail, ToolRegistry
from amadeus.tool_runtime.registry import parse_tools_config
from amadeus.tools import ToolSpec, execute_tool, list_tools


class ToolRegistryTests(unittest.TestCase):
    def test_mcp_tool_names_use_model_safe_identifiers(self) -> None:
        self.assertEqual(normalize_mcp_identifier("Hermes-Fixture"), "hermes_fixture")
        self.assertEqual(normalize_mcp_identifier("read-file.v1"), "read_file_v1")
        self.assertEqual(mcp_tool_spec_name("Hermes-Fixture", "messages-read"), "mcp__hermes_fixture__messages_read")

    def test_role_runtime_scope_filters_builtin_tools_and_mcp_servers(self) -> None:
        scope = normalize_role_runtime_scope({
            "tools": ["get_current_time"],
            "mcpServers": ["Hermes-Fixture"],
        })

        self.assertTrue(role_allows_tool(scope, "get_current_time"))
        self.assertFalse(role_allows_tool(scope, "read_file"))
        self.assertTrue(role_allows_tool(scope, "mcp__hermes_fixture__messages_read"))
        self.assertFalse(role_allows_tool(scope, "mcp__other__lookup"))

    def test_default_registry_includes_file_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")

        tool_state = {entry["name"]: entry for entry in registry.permission_state()}
        schema_names = {entry["function"]["name"] for entry in registry.enabled_schemas()}

        self.assertIn("search_files", list_tools())
        self.assertIn("search_files", tool_state)
        self.assertEqual(tool_state["search_files"]["permission"], "allow")
        self.assertIn("search_files", schema_names)

        self.assertIn("search_memory", list_tools())
        self.assertIn("search_memory", tool_state)
        self.assertEqual(tool_state["search_memory"]["permission"], "allow")
        self.assertIn("search_memory", schema_names)

        self.assertIn("read_session_messages", list_tools())
        self.assertIn("read_session_messages", tool_state)
        self.assertEqual(tool_state["read_session_messages"]["permission"], "allow")
        self.assertIn("read_session_messages", schema_names)

        self.assertIn("search_memory_items", list_tools())
        self.assertIn("search_memory_items", tool_state)
        self.assertEqual(tool_state["search_memory_items"]["permission"], "allow")
        self.assertIn("search_memory_items", schema_names)

        self.assertIn("read_memory", list_tools())
        self.assertIn("read_memory", tool_state)
        self.assertEqual(tool_state["read_memory"]["permission"], "allow")
        self.assertIn("read_memory", schema_names)

        self.assertIn("update_memory", list_tools())
        self.assertIn("update_memory", tool_state)
        self.assertEqual(tool_state["update_memory"]["permission"], "ask")
        self.assertIn("update_memory", schema_names)

        self.assertIn("memory_add", list_tools())
        self.assertIn("memory_add", tool_state)
        self.assertEqual(tool_state["memory_add"]["permission"], "ask")
        self.assertIn("memory_add", schema_names)

        self.assertIn("memory_replace", list_tools())
        self.assertIn("memory_replace", tool_state)
        self.assertEqual(tool_state["memory_replace"]["permission"], "ask")
        self.assertIn("memory_replace", schema_names)

        self.assertIn("memory_forget", list_tools())
        self.assertIn("memory_forget", tool_state)
        self.assertEqual(tool_state["memory_forget"]["permission"], "ask")
        self.assertIn("memory_forget", schema_names)

        self.assertNotIn("local_file_search", list_tools())
        self.assertNotIn("local_file_search", tool_state)
        self.assertNotIn("local_file_search", schema_names)

        self.assertIn("read_file", list_tools())
        self.assertIn("read_file", tool_state)
        self.assertEqual(tool_state["read_file"]["permission"], "allow")
        self.assertIn("read_file", schema_names)

        self.assertIn("skills_list", list_tools())
        self.assertIn("skills_list", tool_state)
        self.assertEqual(tool_state["skills_list"]["permission"], "allow")
        self.assertIn("skills_list", schema_names)

        self.assertIn("skill_view", list_tools())
        self.assertIn("skill_view", tool_state)
        self.assertEqual(tool_state["skill_view"]["permission"], "allow")
        self.assertIn("skill_view", schema_names)

        self.assertIn("patch", list_tools())
        self.assertIn("patch", tool_state)
        self.assertEqual(tool_state["patch"]["permission"], "ask")
        self.assertIn("patch", schema_names)

        self.assertIn("write_file", list_tools())
        self.assertIn("write_file", tool_state)
        self.assertEqual(tool_state["write_file"]["permission"], "ask")
        self.assertIn("write_file", schema_names)

        self.assertIn("update_plan", list_tools())
        self.assertIn("update_plan", tool_state)
        self.assertEqual(tool_state["update_plan"]["permission"], "allow")
        self.assertIn("update_plan", schema_names)

        self.assertIn("delegate_task", list_tools())
        self.assertIn("delegate_task", tool_state)
        self.assertEqual(tool_state["delegate_task"]["permission"], "allow")
        self.assertIn("delegate_task", schema_names)

        for tool_name, permission in {
            "terminal": "ask",
            "process": "ask",
            "web_search": "allow",
            "web_extract": "ask",
            "vision_analyze": "ask",
            "clarify": "allow",
            "execute_code": "ask",
        }.items():
            self.assertIn(tool_name, list_tools())
            self.assertIn(tool_name, tool_state)
            self.assertEqual(tool_state[tool_name]["permission"], permission)
            self.assertIn(tool_name, schema_names)

        self.assertIn("browser_navigate", list_tools())
        self.assertIn("browser_navigate", tool_state)
        self.assertFalse(tool_state["browser_navigate"]["enabled"])
        self.assertNotIn("browser_navigate", schema_names)

    def test_config_alias_updates_effective_tool_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "tools.yaml"
            config_path.write_text(
                "\n".join([
                    "tools:",
                    "  time:",
                    "    enabled: false",
                    "    permission: deny",
                ]),
                encoding="utf-8",
            )

            registry = ToolRegistry(config_path=config_path)

        tool_state = {entry["name"]: entry for entry in registry.permission_state()}
        schema_names = {entry["function"]["name"] for entry in registry.enabled_schemas()}

        self.assertFalse(tool_state["get_current_time"]["enabled"])
        self.assertEqual(tool_state["get_current_time"]["permission"], "deny")
        self.assertNotIn("get_current_time", schema_names)

    def test_terminal_tool_runs_command_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            result = registry.execute(
                "terminal",
                {"command": "printf hello", "cwd": ".", "timeoutSeconds": 5},
                ToolContext(session_id="session-1", cwd=Path(tmpdir)),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output["exitCode"], 0)
        self.assertEqual(result.output["stdout"], "hello")

    def test_terminal_tool_rejects_cwd_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            result = registry.execute(
                "terminal",
                {"command": "pwd", "cwd": ".."},
                ToolContext(session_id="session-1", cwd=Path(tmpdir)),
            )

        self.assertFalse(result.ok)
        self.assertIn("cwd must be inside", result.output["error"])

    def test_terminal_worker_workspace_sandbox_rejects_absolute_path_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            outside_path = Path(tmpdir).parent / "outside-worker-sandbox.txt"
            result = registry.execute(
                "terminal",
                {"command": f"printf bad > {outside_path}", "cwd": "."},
                ToolContext(
                    session_id="session-1",
                    cwd=Path(tmpdir),
                    worker_workspace_path=str(Path(tmpdir)),
                    worker_sandbox_mode="workspace_execute",
                ),
            )

        self.assertFalse(result.ok)
        self.assertIn("outside the workspace sandbox", result.output["error"])

    def test_process_tool_checks_current_process_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            result = registry.execute(
                "process",
                {"action": "status", "pid": os.getpid()},
                ToolContext(session_id="session-1", cwd=Path(tmpdir)),
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.output["exists"])
        self.assertTrue(result.output["accessible"])

    def test_execute_code_runs_python_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            result = registry.execute(
                "execute_code",
                {"code": "from pathlib import Path\nprint(Path.cwd().name)\n", "cwd": "."},
                ToolContext(session_id="session-1", cwd=Path(tmpdir)),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output["exitCode"], 0)
        self.assertIn(Path(tmpdir).name, result.output["stdout"])

    def test_execute_code_worker_workspace_sandbox_blocks_python_writes_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outside_path = Path(tmpdir).parent / "outside-execute-code-sandbox.txt"
            if outside_path.exists():
                outside_path.unlink()
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            result = registry.execute(
                "execute_code",
                {
                    "code": (
                        "from pathlib import Path\n"
                        f"Path({str(outside_path)!r}).write_text('bad', encoding='utf-8')\n"
                    ),
                    "cwd": ".",
                },
                ToolContext(
                    session_id="session-1",
                    cwd=Path(tmpdir),
                    worker_workspace_path=str(Path(tmpdir)),
                    worker_sandbox_mode="workspace_execute",
                ),
            )

        self.assertTrue(result.ok)
        self.assertNotEqual(result.output["exitCode"], 0)
        self.assertIn("outside workspace sandbox", result.output["stderr"])
        self.assertFalse(outside_path.exists())

    def test_clarify_tool_returns_structured_question(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            result = registry.execute(
                "clarify",
                {
                    "question": "Which backend should browser tools use?",
                    "options": [{"label": "MCP", "description": "Use a configured MCP server."}],
                },
                ToolContext(session_id="session-1", cwd=Path(tmpdir)),
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.output["clarificationRequired"])
        self.assertEqual(result.output["options"][0]["label"], "MCP")

    def test_vision_analyze_extracts_local_png_metadata_without_endpoint(self) -> None:
        png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x02"
            b"\x00\x00\x00\x03"
            b"\x08\x02\x00\x00\x00"
            b"\x00\x00\x00\x00"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "tiny.png"
            image_path.write_bytes(png)
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            result = registry.execute(
                "vision_analyze",
                {"path": "tiny.png"},
                ToolContext(session_id="session-1", cwd=Path(tmpdir)),
            )

        self.assertTrue(result.ok)
        self.assertFalse(result.output["analysisAvailable"])
        self.assertEqual(result.output["metadata"]["format"], "png")
        self.assertEqual(result.output["metadata"]["width"], 2)
        self.assertEqual(result.output["metadata"]["height"], 3)

    def test_web_search_parses_mocked_duckduckgo_results(self) -> None:
        import amadeus.tools.web as web_module

        html = '<a rel="nofollow" class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com">Example Result</a>'
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            with mock.patch.object(web_module, "_fetch_url", return_value={"text": html}):
                result = registry.execute(
                    "web_search",
                    {"query": "example", "maxResults": 3},
                    ToolContext(session_id="session-1", cwd=Path(tmpdir)),
                )

        self.assertTrue(result.ok)
        self.assertEqual(result.output["resultCount"], 1)
        self.assertEqual(result.output["results"][0]["url"], "https://example.com")

    def test_web_extract_parses_mocked_html(self) -> None:
        import amadeus.tools.web as web_module

        html = "<html><head><title>Example</title></head><body><script>bad()</script><h1>Hello</h1><p>World</p></body></html>"
        fetched = {
            "url": "https://example.com",
            "finalUrl": "https://example.com",
            "status": 200,
            "contentType": "text/html",
            "text": html,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            with mock.patch.object(web_module, "_fetch_url", return_value=fetched):
                result = registry.execute(
                    "web_extract",
                    {"url": "https://example.com", "maxChars": 1000},
                    ToolContext(session_id="session-1", cwd=Path(tmpdir)),
                )

        self.assertTrue(result.ok)
        self.assertEqual(result.output["fetchedCount"], 1)
        self.assertEqual(result.output["pages"][0]["title"], "Example")
        self.assertIn("Hello", result.output["pages"][0]["text"])
        self.assertNotIn("bad()", result.output["pages"][0]["text"])

    def test_browser_tool_reports_missing_backend_when_directly_called(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            result = registry.execute(
                "browser_snapshot",
                {},
                ToolContext(session_id="session-1", cwd=Path(tmpdir)),
            )

        self.assertFalse(result.ok)
        self.assertIn("browser backend is not configured", result.output["error"])

    def test_parse_tools_config_reads_mcp_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "tools.yaml"
            config_path.write_text(
                "\n".join([
                    "tools:",
                    "  mcp:",
                    "    enabled: true",
                    "    permission: ask",
                    "    servers:",
                    "      - name: local",
                    "        url: http://127.0.0.1:9999/mcp",
                    "        permission: allow",
                    "        timeoutSeconds: 2",
                ]),
                encoding="utf-8",
            )

            config = parse_tools_config(config_path)

        self.assertTrue(config["mcp"]["enabled"])
        self.assertEqual(config["mcp"]["permission"], "ask")
        self.assertEqual(config["mcp"]["servers"][0]["name"], "local")
        self.assertEqual(config["mcp"]["servers"][0]["permission"], "allow")
        self.assertEqual(config["mcp"]["servers"][0]["timeoutSeconds"], 2)

    def test_mcp_specs_are_discovered_from_configured_server(self) -> None:
        import amadeus.mcp as mcp_module
        from amadeus.mcp import McpServerConfig
        from amadeus.tool_runtime import registry as registry_module

        discovered: list[McpServerConfig] = []
        calls: list[tuple[str, str, dict[str, object]]] = []

        def fake_list_tools(server: McpServerConfig) -> list[dict[str, object]]:
            discovered.append(server)
            return [{
                "name": "lookup",
                "description": "Lookup a value",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }]

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "tools.yaml"
            config_path.write_text(
                "\n".join([
                    "tools:",
                    "  mcp:",
                    "    enabled: true",
                    "    permission: ask",
                    "    servers:",
                    "      - name: local",
                    "        url: http://127.0.0.1:9999/mcp",
                    "        permission: allow",
                ]),
                encoding="utf-8",
            )
            original_builder = registry_module.build_mcp_tool_specs

            def fake_builder(servers: list[McpServerConfig], *, default_permission: str = "ask") -> list[ToolSpec]:
                from amadeus.mcp import build_mcp_tool_specs

                return build_mcp_tool_specs(servers, default_permission=default_permission, list_tools=fake_list_tools)

            registry_module.build_mcp_tool_specs = fake_builder
            original_call_mcp_tool = mcp_module.call_mcp_tool

            def fake_call_mcp_tool(
                server: McpServerConfig,
                tool_name: str,
                arguments: dict[str, object],
                *,
                timeout_seconds: float,
            ) -> dict[str, object]:
                calls.append((server.name, tool_name, arguments))
                return {"server": server.name, "tool": tool_name, "result": {"content": [{"type": "text", "text": arguments["query"]}]}}

            mcp_module.call_mcp_tool = fake_call_mcp_tool
            try:
                registry = ToolRegistry(config_path=config_path)
                result = registry.execute("mcp__local__lookup", {"query": "hello"}, ToolContext(session_id="session-1"))
            finally:
                registry_module.build_mcp_tool_specs = original_builder
                mcp_module.call_mcp_tool = original_call_mcp_tool

        tool_state = {entry["name"]: entry for entry in registry.permission_state()}
        schema_names = {entry["function"]["name"] for entry in registry.enabled_schemas()}

        self.assertEqual(discovered[0].name, "local")
        self.assertIn("mcp__local__lookup", tool_state)
        self.assertEqual(tool_state["mcp__local__lookup"]["permission"], "allow")
        self.assertIn("mcp__local__lookup", schema_names)
        self.assertTrue(result.ok)
        self.assertEqual(calls, [("local", "lookup", {"query": "hello"})])
        self.assertEqual(result.output["result"]["content"][0]["text"], "hello")

    def test_execute_returns_structured_tool_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="echo",
                        display_name="Echo",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "echo"}},
                        handler=lambda args: {"echo": args["text"]},
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute("echo", {"text": "hello"}, ToolContext(session_id="session-1"))

        self.assertTrue(result.ok)
        self.assertEqual(result.tool_name, "echo")
        self.assertEqual(result.output, {"echo": "hello"})
        self.assertEqual(result.model_output, {"echo": "hello"})
        self.assertIsNone(result.failure_code)
        self.assertIsNone(result.output_preview)
        self.assertFalse(result.output_truncated)
        self.assertGreaterEqual(result.duration_ms, 0)

    def test_execute_converts_handler_errors_to_structured_failure(self) -> None:
        def fail(_args: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="fail",
                        display_name="Fail",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "fail"}},
                        handler=fail,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute("fail", {}, ToolContext(session_id="session-1"))

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "tool_exception")
        self.assertEqual(result.output, {"error": "boom"})
        self.assertEqual(result.model_output, {"error": "boom"})
        self.assertFalse(result.output_truncated)

    def test_execute_times_out_slow_tool(self) -> None:
        def slow(_args: dict[str, object]) -> dict[str, object]:
            time.sleep(0.05)
            return {"ok": True}

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="slow",
                        display_name="Slow",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "slow"}},
                        handler=slow,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute("slow", {}, ToolContext(session_id="session-1", timeout_seconds=0.001))

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "tool_timeout")
        self.assertEqual(result.output, {"error": "Tool timed out: slow"})
        self.assertEqual(result.model_output, {"error": "Tool timed out: slow"})
        self.assertFalse(result.output_truncated)

    def test_execute_marks_cancelled_context_as_structured_failure(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="cancelled",
                        display_name="Cancelled",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "cancelled"}},
                        handler=lambda _args: {"ok": True},
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "cancelled",
                {},
                ToolContext(session_id="session-1", cancel_event=cancel_event),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "tool_cancelled")
        self.assertEqual(result.output, {"error": "Tool cancelled: cancelled"})
        self.assertEqual(result.model_output, {"error": "Tool cancelled: cancelled"})

    def test_execute_sets_cancel_event_on_timeout(self) -> None:
        cancel_event = threading.Event()

        def slow(_args: dict[str, object], context: ToolContext) -> dict[str, object]:
            time.sleep(0.05)
            return {"cancelled": context.is_cancelled()}

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="slow",
                        display_name="Slow",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "slow"}},
                        handler=slow,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "slow",
                {},
                ToolContext(session_id="session-1", timeout_seconds=0.001, cancel_event=cancel_event),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "tool_timeout")
        self.assertTrue(cancel_event.is_set())

    def test_execute_passes_context_to_context_aware_handler(self) -> None:
        def read_context(_args: dict[str, object], context: ToolContext) -> dict[str, object]:
            return {
                "sessionId": context.session_id,
                "cwd": context.cwd.name,
                "turnId": context.turn_id,
                "toolCallId": context.tool_call_id,
                "toolName": context.tool_name,
                "permissionRequestId": context.permission_request_id,
                "permissionDecision": context.permission_decision,
                "auditSource": context.audit_metadata["source"],
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="read_context",
                        display_name="Read Context",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "read_context"}},
                        handler=read_context,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "read_context",
                {},
                ToolContext(
                    session_id="session-1",
                    turn_id="turn-1",
                    tool_call_id="call-1",
                    tool_name="read_context",
                    permission_request_id="permission-1",
                    permission_decision="approved",
                    audit_metadata={"source": "unit-test"},
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, {
            "sessionId": "session-1",
            "cwd": "Amadeus-agent",
            "turnId": "turn-1",
            "toolCallId": "call-1",
            "toolName": "read_context",
            "permissionRequestId": "permission-1",
            "permissionDecision": "approved",
            "auditSource": "unit-test",
        })

    def test_execute_compresses_large_success_output_for_model_context(self) -> None:
        large_text = "x" * 200

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="large",
                        display_name="Large",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "large"}},
                        handler=lambda _args: {"text": large_text},
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "large",
                {},
                ToolContext(session_id="session-1", max_model_output_chars=80, output_preview_chars=40),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, {"text": large_text})
        self.assertTrue(result.output_truncated)
        self.assertIsNotNone(result.output_preview)
        self.assertLessEqual(len(result.output_preview or ""), 40)
        self.assertEqual(result.model_output["_amadeus_result_truncated"], True)
        self.assertEqual(result.model_output["tool_name"], "large")
        self.assertGreater(result.model_output["original_char_count"], 80)
        self.assertEqual(result.model_output["preview"], result.output_preview)

    def test_execute_applies_search_files_result_policy(self) -> None:
        long_preview = "match " + ("x" * 220)
        search_results = [
            {
                "path": f"packages/example_{index}.py",
                "line": index,
                "preview": long_preview,
                "match": "content",
            }
            for index in range(8)
        ]
        output = {
            "query": "example",
            "target": "all",
            "root": ".",
            "maxResults": 8,
            "results": search_results,
            "scannedFiles": 42,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="search_files",
                        display_name="Search",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "search_files"}},
                        handler=lambda _args: output,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "search_files",
                {"query": "example"},
                ToolContext(session_id="session-1", max_model_output_chars=4000, output_preview_chars=500),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, output)
        self.assertTrue(result.output_truncated)
        self.assertEqual(result.model_output["_amadeus_result_policy"], "search_files_v1")
        self.assertEqual(result.model_output["tool_name"], "search_files")
        self.assertEqual(result.model_output["target"], "all")
        self.assertEqual(result.model_output["resultCount"], 8)
        self.assertEqual(result.model_output["includedResults"], 5)
        self.assertEqual(result.model_output["omittedResults"], 3)
        self.assertEqual(len(result.model_output["results"]), 5)
        self.assertLessEqual(len(result.model_output["results"][0]["preview"]), 160)
        self.assertIsNotNone(result.output_preview)
        self.assertLessEqual(len(result.output_preview or ""), 160)

    def test_execute_keeps_small_search_files_result_unchanged(self) -> None:
        output = {
            "query": "example",
            "target": "content",
            "root": ".",
            "maxResults": 2,
            "results": [
                {
                    "path": "packages/example.py",
                    "line": 1,
                    "preview": "example",
                    "match": "content",
                },
            ],
            "scannedFiles": 3,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="search_files",
                        display_name="Search",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "search_files"}},
                        handler=lambda _args: output,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "search_files",
                {"query": "example"},
                ToolContext(session_id="session-1", max_model_output_chars=4000, output_preview_chars=500),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, output)
        self.assertEqual(result.model_output, output)
        self.assertFalse(result.output_truncated)
        self.assertIsNone(result.output_preview)

    def test_search_files_can_search_only_filenames(self) -> None:
        output = execute_tool("search_files", {"query": "README", "target": "files", "maxResults": 3})

        self.assertEqual(output["target"], "files")
        self.assertTrue(output["results"])
        self.assertTrue(all(result["match"] == "path" for result in output["results"]))

    def test_search_memory_uses_context_memory_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save("session-1", "user", "I prefer concise status updates")
            memory.save("session-2", "user", "Use verbose explanations")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")

            result = registry.execute(
                "search_memory",
                {"query": "concise", "limit": 5},
                ToolContext(session_id="session-1", memory_store=memory),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output["query"], "concise")
        self.assertEqual(result.output["sessionId"], "session-1")
        self.assertEqual(result.output["resultCount"], 1)
        self.assertIn("concise", result.output["results"][0]["content"])

    def test_read_session_messages_uses_context_memory_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            first_id = memory.save("session-1", "user", "First transcript line")
            memory.save("session-1", "assistant", "Second transcript line")
            memory.save("session-2", "user", "Other session line")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")

            result = registry.execute(
                "read_session_messages",
                {"afterMessageId": first_id, "limit": 10},
                ToolContext(session_id="session-1", memory_store=memory),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output["sessionId"], "session-1")
        self.assertEqual(result.output["returnedCount"], 1)
        self.assertEqual(result.output["messages"][0]["role"], "assistant")
        self.assertIn("Second transcript line", result.output["messages"][0]["content"])
        self.assertNotIn("Other session line", str(result.output))

    def test_delegate_task_runs_restricted_research_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save("session-1", "user", "The task system uses SQLite task_events for state history.")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            context = ToolContext(session_id="session-1", memory_store=memory)

            result = registry.execute(
                "delegate_task",
                {
                    "task": "Find evidence about the task event system.",
                    "queries": ["task_events"],
                    "paths": ["packages/amadeus/tasks.py"],
                    "maxResults": 3,
                },
                context,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output["delegateType"], "restricted_research")
        self.assertEqual(result.output["maxDepth"], 1)
        self.assertEqual(result.output["maxConcurrency"], 2)
        self.assertEqual(result.output["allowedTools"], ["search_files", "read_file", "search_memory"])
        self.assertNotIn("write_file", result.output["allowedTools"])
        self.assertGreaterEqual(result.output["findingCount"], 1)
        self.assertIn("Restricted research delegate completed", result.output["summary"])
        self.assertIn("task_events", result.output["summary"])

    def test_update_plan_persists_session_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            context = ToolContext(session_id="session-1", memory_store=memory)

            created = registry.execute(
                "update_plan",
                {
                    "items": [
                        {"id": "inspect", "content": "Inspect existing task planning code", "status": "completed"},
                        {"id": "implement", "content": "Implement Amadeus planning module", "status": "in_progress"},
                    ]
                },
                context,
            )
            loaded = registry.execute("update_plan", {}, context)

        self.assertTrue(created.ok)
        self.assertTrue(created.output["changed"])
        self.assertEqual(created.output["summary"]["total"], 2)
        self.assertEqual(created.output["summary"]["inProgress"], 1)
        self.assertTrue(loaded.ok)
        self.assertFalse(loaded.output["changed"])
        self.assertEqual(loaded.output["items"][1]["id"], "implement")

    def test_update_plan_merge_updates_existing_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            context = ToolContext(session_id="session-1", memory_store=memory)

            registry.execute(
                "update_plan",
                {
                    "items": [
                        {"id": "a", "content": "First step", "status": "in_progress"},
                        {"id": "b", "content": "Second step", "status": "pending"},
                    ]
                },
                context,
            )
            merged = registry.execute(
                "update_plan",
                {
                    "merge": True,
                    "items": [
                        {"id": "a", "content": "First step", "status": "completed"},
                        {"id": "b", "content": "Second step", "status": "in_progress"},
                    ],
                },
                context,
            )

        self.assertTrue(merged.ok)
        self.assertEqual([item["status"] for item in merged.output["items"]], ["completed", "in_progress"])
        self.assertEqual(merged.output["summary"]["completed"], 1)

    def test_update_plan_rejects_multiple_in_progress_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")

            result = registry.execute(
                "update_plan",
                {
                    "items": [
                        {"id": "a", "content": "First step", "status": "in_progress"},
                        {"id": "b", "content": "Second step", "status": "in_progress"},
                    ]
                },
                ToolContext(session_id="session-1", memory_store=memory),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "tool_error")
        self.assertIn("only one", result.output["error"])

    def test_read_memory_uses_stable_markdown_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.update_stable_memory("user", "add", content="The user prefers Chinese responses.")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")

            result = registry.execute(
                "read_memory",
                {"target": "user"},
                ToolContext(session_id="session-1", memory_store=memory),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output["target"], "user")
        self.assertIn("prefers Chinese", result.output["content"])

    def test_update_memory_add_replace_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")

            added = registry.execute(
                "update_memory",
                {"target": "agent", "action": "add", "content": "Project uses Python-first runtime."},
                ToolContext(session_id="session-1", memory_store=memory),
            )
            replaced = registry.execute(
                "update_memory",
                {
                    "target": "agent",
                    "action": "replace",
                    "oldText": "- Project uses Python-first runtime.",
                    "content": "Project uses Python-first AgentRuntime.",
                },
                ToolContext(session_id="session-1", memory_store=memory),
            )
            removed = registry.execute(
                "update_memory",
                {
                    "target": "agent",
                    "action": "remove",
                    "oldText": "- Project uses Python-first AgentRuntime.",
                },
                ToolContext(session_id="session-1", memory_store=memory),
            )

        self.assertTrue(added.ok)
        self.assertTrue(added.output["changed"])
        self.assertIn("- Project uses Python-first runtime.", added.output["content"])
        self.assertTrue(replaced.ok)
        self.assertIn("AgentRuntime", replaced.output["content"])
        self.assertTrue(removed.ok)
        self.assertNotIn("AgentRuntime", removed.output["content"])

    def test_update_current_role_identity_updates_session_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            role = memory.create_role("Amadeus")
            session = memory.create_session(str(role["id"]))
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")

            result = registry.execute(
                "update_current_role_identity",
                {
                    "name": "小艾",
                    "soulText": "You are 小艾. You answer concisely.",
                },
                ToolContext(session_id=str(session["id"]), memory_store=memory),
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.output["updated"])
        self.assertEqual(result.output["identity"]["roleName"], "小艾")
        self.assertIn("You are 小艾", result.output["identity"]["content"])

    def test_update_memory_rejects_ambiguous_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.update_stable_memory("agent", "add", content="Duplicate entry")
            memory.update_stable_memory("agent", "add", content="Duplicate entry")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")

            result = registry.execute(
                "update_memory",
                {
                    "target": "agent",
                    "action": "replace",
                    "oldText": "- Duplicate entry",
                    "content": "New entry",
                },
                ToolContext(session_id="session-1", memory_store=memory),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "tool_error")
        self.assertIn("exactly one", result.output["error"])

    def test_search_memory_items_uses_context_memory_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            memory.save_memory_item(
                "user",
                "The user prefers concise updates.",
                confidence=0.95,
                memory_type="preference",
                metadata={"source": "profile", "tags": ["updates"]},
            )
            memory.save_memory_item("project", "Amadeus uses Python-first runtime.", confidence=0.9)
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")

            result = registry.execute(
                "search_memory_items",
                {"scope": "user", "query": "concise", "metadataFilter": {"tags": "updates"}, "limit": 5},
                ToolContext(session_id="session-1", memory_store=memory),
            )
            accessed = memory.list_memory_items(scope="user", query="concise")[0]

        self.assertTrue(result.ok)
        self.assertEqual(result.output["scope"], "user")
        self.assertEqual(result.output["query"], "concise")
        self.assertEqual(result.output["metadataFilter"], {"tags": "updates"})
        self.assertEqual(result.output["retrievalProvider"], "memory_items_bm25")
        self.assertEqual(result.output["resultCount"], 1)
        self.assertIn("concise updates", result.output["items"][0]["content"])
        self.assertEqual(accessed["accessCount"], 1)

    def test_memory_add_writes_structured_memory_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            context = ToolContext(session_id="session-1", memory_store=memory)

            first = registry.execute(
                "memory_add",
                {
                    "scope": "project",
                    "content": "Amadeus should prefer Python runtime ownership.",
                    "confidence": 0.9,
                    "sourceMessageId": 12,
                },
                context,
            )
            duplicate = registry.execute(
                "memory_add",
                {"scope": "project", "content": "Amadeus should prefer Python runtime ownership."},
                context,
            )
            items = memory.list_memory_items(scope="project")

        self.assertTrue(first.ok)
        self.assertTrue(first.output["added"])
        self.assertFalse(first.output["duplicate"])
        self.assertEqual(first.output["item"]["sourceSessionId"], "session-1")
        self.assertEqual(first.output["item"]["sourceMessageId"], 12)
        self.assertTrue(duplicate.ok)
        self.assertFalse(duplicate.output["added"])
        self.assertTrue(duplicate.output["duplicate"])
        self.assertEqual(len(items), 1)

    def test_memory_add_permission_request_describes_fact_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            spec = registry.get("memory_add")

        self.assertIsNotNone(spec)
        assert spec is not None
        request = spec.describe_request({"scope": "user", "content": "The user prefers direct answers."})

        self.assertIn("remember this user fact", request)
        self.assertIn("direct answers", request)

    def test_memory_replace_and_forget_mutate_structured_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            item = memory.save_memory_item("project", "Old project fact.", confidence=0.6)
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            context = ToolContext(session_id="session-1", memory_store=memory)

            replaced = registry.execute(
                "memory_replace",
                {
                    "memoryItemId": item["memoryItemId"],
                    "scope": "agent",
                    "content": "Corrected durable agent fact.",
                    "confidence": 0.95,
                },
                context,
            )
            forgotten = registry.execute(
                "memory_forget",
                {"memoryItemId": item["memoryItemId"]},
                context,
            )
            active_items = memory.list_memory_items()

        self.assertTrue(replaced.ok)
        self.assertTrue(replaced.output["replaced"])
        self.assertEqual(replaced.output["item"]["scope"], "agent")
        self.assertEqual(replaced.output["item"]["content"], "Corrected durable agent fact.")
        self.assertEqual(replaced.output["item"]["confidence"], 0.95)
        self.assertTrue(forgotten.ok)
        self.assertTrue(forgotten.output["forgotten"])
        self.assertEqual(active_items, [])

    def test_memory_mutation_permission_requests_describe_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            replace_spec = registry.get("memory_replace")
            forget_spec = registry.get("memory_forget")

        self.assertIsNotNone(replace_spec)
        self.assertIsNotNone(forget_spec)
        assert replace_spec is not None
        assert forget_spec is not None

        replace_request = replace_spec.describe_request({
            "memoryItemId": 7,
            "content": "The corrected fact.",
        })
        forget_request = forget_spec.describe_request({"memoryItemId": 7})

        self.assertIn("replace structured memory item 7", replace_request)
        self.assertIn("The corrected fact", replace_request)
        self.assertIn("forget structured memory item 7", forget_request)

    def test_task_tools_create_list_and_cancel_session_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            context = ToolContext(session_id="session-1", memory_store=memory)

            created = registry.execute(
                "create_task",
                {
                    "title": "Run background research",
                    "body": "Check the project docs.",
                    "priority": 4,
                    "autoStart": False,
                },
                context,
            )
            listed = registry.execute("list_tasks", {"activeOnly": True}, context)
            cancelled = registry.execute(
                "cancel_task",
                {"taskId": created.output["task"]["id"], "reason": "User changed direction"},
                context,
            )
            listed_cancelled = registry.execute(
                "list_tasks",
                {"status": "cancelled", "activeOnly": False},
                context,
            )

        self.assertTrue(created.ok)
        self.assertEqual(created.output["action"], "created")
        self.assertFalse(created.output["workerSubmitted"])
        self.assertEqual(created.output["task"]["status"], "queued")
        self.assertTrue(listed.ok)
        self.assertEqual(listed.output["summary"]["queued"], 1)
        self.assertTrue(cancelled.ok)
        self.assertEqual(cancelled.output["action"], "cancelled")
        self.assertEqual(cancelled.output["task"]["status"], "cancelled")
        self.assertEqual(listed_cancelled.output["summary"]["cancelled"], 1)

    def test_cancel_task_tool_rejects_cross_session_task_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from amadeus.memory import MessageMemoryStore

            memory = MessageMemoryStore(Path(tmpdir) / "amadeus.sqlite")
            task = memory.create_task(session_id="session-1", title="Private task")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")
            other_context = ToolContext(session_id="session-2", memory_store=memory)

            result = registry.execute("cancel_task", {"taskId": task["id"]}, other_context)

        self.assertFalse(result.ok)
        self.assertEqual(result.output["error"], "task not found")

    def test_execute_applies_search_memory_result_policy(self) -> None:
        output = {
            "query": "preference",
            "sessionId": "session-1",
            "includeAllSessions": False,
            "resultCount": 8,
            "results": [
                {
                    "id": index,
                    "sessionId": "session-1",
                    "role": "user",
                    "createdAt": "2026-06-20T00:00:00+00:00",
                    "content": "preference " + ("x" * 500),
                    "snippet": "preference " + ("x" * 500),
                }
                for index in range(8)
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="search_memory",
                        display_name="Search Memory",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "search_memory"}},
                        handler=lambda _args: output,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "search_memory",
                {"query": "preference"},
                ToolContext(session_id="session-1", max_model_output_chars=4000, output_preview_chars=300),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, output)
        self.assertTrue(result.output_truncated)
        self.assertEqual(result.model_output["_amadeus_result_policy"], "search_memory_v1")
        self.assertEqual(result.model_output["includedResults"], 5)
        self.assertEqual(result.model_output["omittedResults"], 3)
        self.assertLessEqual(len(result.model_output["results"][0]["snippet"]), 240)

    def test_execute_applies_search_memory_items_result_policy(self) -> None:
        output = {
            "scope": "user",
            "query": "preference",
            "limit": 20,
            "resultCount": 12,
            "items": [
                {
                    "memoryItemId": index,
                    "scope": "user",
                    "content": "preference " + ("x" * 500),
                    "confidence": 0.9,
                    "sourceSessionId": "session-1",
                    "sourceMessageId": 10 + index,
                    "createdAt": "2026-06-20T00:00:00+00:00",
                    "updatedAt": "2026-06-20T00:00:00+00:00",
                }
                for index in range(12)
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="search_memory_items",
                        display_name="Search Memory Items",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "search_memory_items"}},
                        handler=lambda _args: output,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "search_memory_items",
                {"scope": "user", "query": "preference"},
                ToolContext(session_id="session-1", max_model_output_chars=4000, output_preview_chars=300),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, output)
        self.assertTrue(result.output_truncated)
        self.assertEqual(result.model_output["_amadeus_result_policy"], "search_memory_items_v1")
        self.assertEqual(result.model_output["includedItems"], 8)
        self.assertEqual(result.model_output["omittedItems"], 4)
        self.assertLessEqual(len(result.model_output["items"][0]["content"]), 240)

    def test_execute_applies_read_session_messages_result_policy(self) -> None:
        output = {
            "sessionId": "session-1",
            "currentSessionId": "session-1",
            "limit": 20,
            "afterMessageId": None,
            "totalCount": 14,
            "returnedCount": 14,
            "latestMessageId": 14,
            "hasMore": False,
            "messages": [
                {
                    "id": index,
                    "role": "user" if index % 2 else "assistant",
                    "createdAt": "2026-06-20T00:00:00+00:00",
                    "content": "transcript " + ("x" * 800),
                    "contentCharCount": 811,
                    "contentTruncated": False,
                }
                for index in range(14)
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="read_session_messages",
                        display_name="Read Session Messages",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "read_session_messages"}},
                        handler=lambda _args: output,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "read_session_messages",
                {"limit": 20},
                ToolContext(session_id="session-1", max_model_output_chars=4000, output_preview_chars=500),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, output)
        self.assertTrue(result.output_truncated)
        self.assertEqual(result.model_output["_amadeus_result_policy"], "read_session_messages_v1")
        self.assertEqual(result.model_output["includedMessages"], 8)
        self.assertEqual(result.model_output["omittedMessages"], 6)
        self.assertLessEqual(len(result.model_output["messages"][0]["content"]), 360)

    def test_read_file_reads_workspace_text_file_with_line_numbers(self) -> None:
        output = execute_tool("read_file", {"path": "packages/amadeus/README.md", "lineLimit": 2, "maxChars": 200})

        self.assertEqual(output["path"], "packages/amadeus/README.md")
        self.assertEqual(output["kind"], "text")
        self.assertTrue(output["supported"])
        self.assertIn("# Amadeus Runtime", output["content"])
        self.assertIn("     1 |", output["content"])
        self.assertEqual(output["startLine"], 1)
        self.assertEqual(output["lineCount"], 2)
        self.assertGreaterEqual(output["totalLines"], 2)
        self.assertTrue(output["hasMore"])
        self.assertLessEqual(len(output["content"]), 200)
        self.assertGreater(output["sizeBytes"], 0)

    def test_file_tools_use_context_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            (workspace / "notes.md").write_text("# Workspace Note\nbody\n", encoding="utf-8")
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")

            result = registry.execute(
                "read_file",
                {"path": "notes.md", "lineLimit": 1, "maxChars": 200},
                ToolContext(session_id="session-1", cwd=workspace),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output["path"], "notes.md")
        self.assertIn("# Workspace Note", result.output["content"])

    def test_read_file_blocks_paths_outside_workspace(self) -> None:
        output = execute_tool("read_file", {"path": "../outside.txt"})

        self.assertEqual(output, {"error": "path must be inside the project workspace"})

    def test_read_file_supports_explicit_line_window(self) -> None:
        output = execute_tool(
            "read_file",
            {"path": "packages/amadeus/README.md", "startLine": 2, "lineLimit": 1, "maxChars": 200},
        )

        self.assertEqual(output["startLine"], 2)
        self.assertEqual(output["endLine"], 2)
        self.assertEqual(output["lineCount"], 1)
        self.assertTrue(output["content"].startswith("     2 |"))

    def test_read_file_reports_unsupported_image_kind(self) -> None:
        test_path = Path(__file__).resolve().parents[1] / ".amadeus-test-image.png"
        relative_path = test_path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
        test_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        try:
            output = execute_tool("read_file", {"path": relative_path})

            self.assertEqual(output["path"], relative_path)
            self.assertEqual(output["kind"], "image")
            self.assertFalse(output["supported"])
            self.assertIn("vision", output["hint"])
        finally:
            test_path.unlink(missing_ok=True)

    def test_read_file_reports_unsupported_pdf_kind(self) -> None:
        test_path = Path(__file__).resolve().parents[1] / ".amadeus-test-doc.pdf"
        relative_path = test_path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
        test_path.write_bytes(b"%PDF-1.7\n")
        try:
            output = execute_tool("read_file", {"path": relative_path})

            self.assertEqual(output["kind"], "pdf")
            self.assertFalse(output["supported"])
            self.assertIn("pdf_read", output["hint"])
        finally:
            test_path.unlink(missing_ok=True)

    def test_read_file_reports_unsupported_binary_kind(self) -> None:
        test_path = Path(__file__).resolve().parents[1] / ".amadeus-test-archive.zip"
        relative_path = test_path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
        test_path.write_bytes(b"PK\x03\x04")
        try:
            output = execute_tool("read_file", {"path": relative_path})

            self.assertEqual(output["kind"], "binary")
            self.assertFalse(output["supported"])
            self.assertIn("binary", output["hint"])
        finally:
            test_path.unlink(missing_ok=True)

    def test_read_file_reports_unsupported_unknown_kind(self) -> None:
        test_path = Path(__file__).resolve().parents[1] / ".amadeus-test-data.unknownext"
        relative_path = test_path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
        test_path.write_text("plain text but unknown extension", encoding="utf-8")
        try:
            output = execute_tool("read_file", {"path": relative_path})

            self.assertEqual(output["kind"], "unknown")
            self.assertFalse(output["supported"])
            self.assertIn("not recognized", output["hint"])
        finally:
            test_path.unlink(missing_ok=True)

    def test_execute_does_not_apply_read_file_result_policy(self) -> None:
        content = "x" * 5000
        output = {
            "path": "packages/example.py",
            "sizeBytes": len(content),
            "charCount": len(content),
            "totalLines": 1,
            "startLine": 1,
            "endLine": 1,
            "lineCount": 1,
            "lineLimit": 1,
            "maxChars": len(content),
            "hasMore": False,
            "truncated": False,
            "content": content,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="read_file",
                        display_name="Read",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "read_file"}},
                        handler=lambda _args: output,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "read_file",
                {"path": "packages/example.py"},
                ToolContext(session_id="session-1", max_model_output_chars=4000, output_preview_chars=200),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, output)
        self.assertEqual(result.model_output, output)
        self.assertFalse(result.output_truncated)
        self.assertIsNone(result.output_preview)

    def test_patch_replaces_unique_text_and_returns_diff(self) -> None:
        test_path = Path(__file__).resolve().parents[1] / ".amadeus-test-patch.txt"
        relative_path = test_path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
        test_path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        try:
            output = execute_tool("patch", {"path": relative_path, "oldText": "beta", "newText": "delta"})

            self.assertTrue(output["changed"])
            self.assertEqual(output["path"], relative_path)
            self.assertEqual(output["replacements"], 1)
            self.assertIn("-beta", output["diff"])
            self.assertIn("+delta", output["diff"])
            self.assertEqual(test_path.read_text(encoding="utf-8"), "alpha\ndelta\ngamma\n")
        finally:
            test_path.unlink(missing_ok=True)

    def test_patch_requires_unique_match_by_default(self) -> None:
        test_path = Path(__file__).resolve().parents[1] / ".amadeus-test-patch.txt"
        relative_path = test_path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
        test_path.write_text("same\nsame\n", encoding="utf-8")
        try:
            output = execute_tool("patch", {"path": relative_path, "oldText": "same", "newText": "changed"})

            self.assertIn("multiple times", output["error"])
            self.assertEqual(output["matchCount"], 2)
            self.assertEqual(test_path.read_text(encoding="utf-8"), "same\nsame\n")
        finally:
            test_path.unlink(missing_ok=True)

    def test_patch_replace_all_allows_multiple_matches(self) -> None:
        test_path = Path(__file__).resolve().parents[1] / ".amadeus-test-patch.txt"
        relative_path = test_path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
        test_path.write_text("same\nsame\n", encoding="utf-8")
        try:
            output = execute_tool("patch", {"path": relative_path, "oldText": "same", "newText": "changed", "replaceAll": True})

            self.assertEqual(output["replacements"], 2)
            self.assertTrue(output["replaceAll"])
            self.assertEqual(test_path.read_text(encoding="utf-8"), "changed\nchanged\n")
        finally:
            test_path.unlink(missing_ok=True)

    def test_patch_blocks_paths_outside_workspace(self) -> None:
        output = execute_tool("patch", {"path": "../outside.txt", "oldText": "a", "newText": "b"})

        self.assertEqual(output, {"error": "path must be inside the project workspace"})

    def test_execute_does_not_apply_global_compression_to_patch_result(self) -> None:
        output = {
            "path": "packages/example.py",
            "changed": True,
            "replacements": 1,
            "replaceAll": False,
            "sizeBytesBefore": 10,
            "sizeBytesAfter": 5000,
            "diff": "x" * 5000,
            "diffTruncated": False,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="patch",
                        display_name="Patch",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "patch"}},
                        handler=lambda _args: output,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "patch",
                {"path": "packages/example.py", "oldText": "a", "newText": "b"},
                ToolContext(session_id="session-1", max_model_output_chars=4000, output_preview_chars=200),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.model_output, output)
        self.assertFalse(result.output_truncated)
        self.assertIsNone(result.output_preview)

    def test_write_file_creates_new_text_file(self) -> None:
        test_path = Path(__file__).resolve().parents[1] / ".amadeus-test-write.md"
        relative_path = test_path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
        test_path.unlink(missing_ok=True)
        try:
            output = execute_tool("write_file", {"path": relative_path, "content": "# Title\nbody\n"})

            self.assertTrue(output["changed"])
            self.assertTrue(output["created"])
            self.assertFalse(output["overwritten"])
            self.assertEqual(output["path"], relative_path)
            self.assertEqual(output["lineCount"], 2)
            self.assertIn("+# Title", output["diff"])
            self.assertEqual(test_path.read_text(encoding="utf-8"), "# Title\nbody\n")
        finally:
            test_path.unlink(missing_ok=True)

    def test_write_file_refuses_overwrite_without_flag(self) -> None:
        test_path = Path(__file__).resolve().parents[1] / ".amadeus-test-write.md"
        relative_path = test_path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
        test_path.write_text("original\n", encoding="utf-8")
        try:
            output = execute_tool("write_file", {"path": relative_path, "content": "replacement\n"})

            self.assertIn("already exists", output["error"])
            self.assertEqual(test_path.read_text(encoding="utf-8"), "original\n")
        finally:
            test_path.unlink(missing_ok=True)

    def test_write_file_overwrites_with_diff_when_explicit(self) -> None:
        test_path = Path(__file__).resolve().parents[1] / ".amadeus-test-write.md"
        relative_path = test_path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
        test_path.write_text("original\n", encoding="utf-8")
        try:
            output = execute_tool("write_file", {"path": relative_path, "content": "replacement\n", "overwrite": True})

            self.assertTrue(output["changed"])
            self.assertFalse(output["created"])
            self.assertTrue(output["overwritten"])
            self.assertTrue(output["overwrite"])
            self.assertIn("-original", output["diff"])
            self.assertIn("+replacement", output["diff"])
            self.assertEqual(test_path.read_text(encoding="utf-8"), "replacement\n")
        finally:
            test_path.unlink(missing_ok=True)

    def test_write_file_blocks_paths_outside_workspace(self) -> None:
        output = execute_tool("write_file", {"path": "../outside.md", "content": "x"})

        self.assertEqual(output, {"error": "path must be inside the project workspace"})

    def test_write_file_rejects_non_text_extension(self) -> None:
        output = execute_tool("write_file", {"path": ".amadeus-test-write.png", "content": "x"})

        self.assertEqual(output, {"error": "file type is not writable by this tool"})

    def test_execute_does_not_apply_global_compression_to_write_file_result(self) -> None:
        output = {
            "path": "packages/example.py",
            "changed": True,
            "created": True,
            "overwritten": False,
            "overwrite": False,
            "sizeBytesBefore": None,
            "sizeBytesAfter": 5000,
            "lineCount": 1,
            "diff": "x" * 5000,
            "diffTruncated": False,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="write_file",
                        display_name="Write",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "write_file"}},
                        handler=lambda _args: output,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "write_file",
                {"path": "packages/example.py", "content": "x"},
                ToolContext(session_id="session-1", max_model_output_chars=4000, output_preview_chars=200),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.model_output, output)
        self.assertFalse(result.output_truncated)
        self.assertIsNone(result.output_preview)


class ToolLoopGuardrailTests(unittest.TestCase):
    def test_blocks_repeated_exact_failures_after_threshold(self) -> None:
        guardrail = ToolLoopGuardrail(max_failed_repeats=2)
        args = {"query": "missing"}

        self.assertTrue(guardrail.before_call("search_files", args).allowed)
        guardrail.after_call("search_files", args, {"error": "not found"}, ok=False)

        self.assertTrue(guardrail.before_call("search_files", args).allowed)
        guardrail.after_call("search_files", args, {"error": "not found"}, ok=False)

        decision = guardrail.before_call("search_files", args)
        self.assertFalse(decision.allowed)
        self.assertIn("Blocked repeated failing tool call", decision.reason or "")
        self.assertEqual(decision.failure_code, "guardrail_blocked")

    def test_blocks_repeated_completed_calls_as_no_progress(self) -> None:
        guardrail = ToolLoopGuardrail(max_completed_repeats=2)
        args = {"query": "same"}

        self.assertTrue(guardrail.before_call("search_files", args).allowed)
        guardrail.after_call("search_files", args, {"results": []}, ok=True)

        self.assertTrue(guardrail.before_call("search_files", args).allowed)
        guardrail.after_call("search_files", args, {"results": []}, ok=True)

        decision = guardrail.before_call("search_files", args)
        self.assertFalse(decision.allowed)
        self.assertIn("empty file search", decision.reason or "")
        self.assertEqual(decision.failure_code, "no_progress_loop")

    def test_blocks_repeated_empty_file_search_with_specific_reason(self) -> None:
        guardrail = ToolLoopGuardrail(max_completed_repeats=2)
        args = {"query": "missing", "target": "content"}

        self.assertTrue(guardrail.before_call("search_files", args).allowed)
        guardrail.after_call("search_files", args, {"results": []}, ok=True)

        self.assertTrue(guardrail.before_call("search_files", args).allowed)
        guardrail.after_call("search_files", args, {"results": []}, ok=True)

        decision = guardrail.before_call("search_files", args)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.failure_code, "no_progress_loop")
        self.assertIn("empty file search", decision.reason or "")

    def test_workspace_epoch_allows_file_search_after_workspace_change(self) -> None:
        guardrail = ToolLoopGuardrail(max_completed_repeats=2)
        args = {"query": "missing", "target": "content"}

        self.assertTrue(guardrail.before_call("search_files", args, workspace_epoch=0).allowed)
        guardrail.after_call("search_files", args, {"results": []}, ok=True, workspace_epoch=0)

        self.assertTrue(guardrail.before_call("search_files", args, workspace_epoch=0).allowed)
        guardrail.after_call("search_files", args, {"results": []}, ok=True, workspace_epoch=0)

        blocked = guardrail.before_call("search_files", args, workspace_epoch=0)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.failure_code, "no_progress_loop")

        after_mutation = guardrail.before_call("search_files", args, workspace_epoch=1)
        self.assertTrue(after_mutation.allowed)

    def test_blocks_repeated_empty_memory_search_with_specific_reason(self) -> None:
        guardrail = ToolLoopGuardrail(max_completed_repeats=2)
        args = {"query": "preference", "includeAllSessions": False}

        self.assertTrue(guardrail.before_call("search_memory", args).allowed)
        guardrail.after_call("search_memory", args, {"results": []}, ok=True)

        self.assertTrue(guardrail.before_call("search_memory", args).allowed)
        guardrail.after_call("search_memory", args, {"results": []}, ok=True)

        decision = guardrail.before_call("search_memory", args)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.failure_code, "no_progress_loop")
        self.assertIn("empty memory search", decision.reason or "")

    def test_blocks_repeated_empty_structured_memory_search_with_specific_reason(self) -> None:
        guardrail = ToolLoopGuardrail(max_completed_repeats=2)
        args = {"scope": "user", "query": "preference"}

        self.assertTrue(guardrail.before_call("search_memory_items", args).allowed)
        guardrail.after_call("search_memory_items", args, {"items": []}, ok=True)

        self.assertTrue(guardrail.before_call("search_memory_items", args).allowed)
        guardrail.after_call("search_memory_items", args, {"items": []}, ok=True)

        decision = guardrail.before_call("search_memory_items", args)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.failure_code, "no_progress_loop")
        self.assertIn("empty structured memory search", decision.reason or "")

    def test_blocks_repeated_duplicate_structured_memory_write(self) -> None:
        guardrail = ToolLoopGuardrail(max_completed_repeats=2)
        args = {"scope": "project", "content": "Amadeus uses Python runtime."}
        result = {"added": False, "duplicate": True}

        self.assertTrue(guardrail.before_call("memory_add", args).allowed)
        guardrail.after_call("memory_add", args, result, ok=True)

        self.assertTrue(guardrail.before_call("memory_add", args).allowed)
        guardrail.after_call("memory_add", args, result, ok=True)

        decision = guardrail.before_call("memory_add", args)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.failure_code, "no_progress_loop")
        self.assertIn("already remembered", decision.reason or "")

    def test_blocks_repeated_read_file_window_with_specific_reason(self) -> None:
        guardrail = ToolLoopGuardrail(max_completed_repeats=2)
        args = {"path": "README.md", "startLine": 10, "lineLimit": 20}
        result = {"path": "README.md", "content": "same", "startLine": 10, "lineLimit": 20}

        self.assertTrue(guardrail.before_call("read_file", args).allowed)
        guardrail.after_call("read_file", args, result, ok=True)

        self.assertTrue(guardrail.before_call("read_file", args).allowed)
        guardrail.after_call("read_file", args, result, ok=True)

        decision = guardrail.before_call("read_file", args)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.failure_code, "no_progress_loop")
        self.assertIn("read_file window", decision.reason or "")

    def test_workspace_epoch_allows_read_file_after_workspace_change(self) -> None:
        guardrail = ToolLoopGuardrail(max_completed_repeats=2)
        args = {"path": "README.md", "startLine": 10, "lineLimit": 20}
        result = {"path": "README.md", "content": "same", "startLine": 10, "lineLimit": 20}

        self.assertTrue(guardrail.before_call("read_file", args, workspace_epoch=0).allowed)
        guardrail.after_call("read_file", args, result, ok=True, workspace_epoch=0)

        self.assertTrue(guardrail.before_call("read_file", args, workspace_epoch=0).allowed)
        guardrail.after_call("read_file", args, result, ok=True, workspace_epoch=0)

        blocked = guardrail.before_call("read_file", args, workspace_epoch=0)
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.failure_code, "no_progress_loop")

        after_mutation = guardrail.before_call("read_file", args, workspace_epoch=1)
        self.assertTrue(after_mutation.allowed)

    def test_blocks_repeated_patch_failure_by_path_and_old_text(self) -> None:
        guardrail = ToolLoopGuardrail(max_failed_repeats=2)
        first_args = {"path": "README.md", "oldText": "missing", "newText": "one"}
        second_args = {"path": "README.md", "oldText": "missing", "newText": "two"}

        self.assertTrue(guardrail.before_call("patch", first_args).allowed)
        guardrail.after_call("patch", first_args, {"error": "oldText was not found"}, ok=False)

        self.assertTrue(guardrail.before_call("patch", second_args).allowed)
        guardrail.after_call("patch", second_args, {"error": "oldText was not found"}, ok=False)

        decision = guardrail.before_call("patch", {"path": "README.md", "oldText": "missing", "newText": "three"})
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.failure_code, "guardrail_blocked")
        self.assertIn("read_file", decision.reason or "")

    def test_blocks_repeated_write_file_failure_by_path_and_overwrite(self) -> None:
        guardrail = ToolLoopGuardrail(max_failed_repeats=2)
        first_args = {"path": "README.md", "content": "one"}
        second_args = {"path": "README.md", "content": "two"}

        self.assertTrue(guardrail.before_call("write_file", first_args).allowed)
        guardrail.after_call("write_file", first_args, {"error": "file already exists"}, ok=False)

        self.assertTrue(guardrail.before_call("write_file", second_args).allowed)
        guardrail.after_call("write_file", second_args, {"error": "file already exists"}, ok=False)

        decision = guardrail.before_call("write_file", {"path": "README.md", "content": "three"})
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.failure_code, "guardrail_blocked")
        self.assertIn("overwrite", decision.reason or "")

    def test_blocks_worker_redundant_mutation_from_resume_policy(self) -> None:
        guardrail = ToolLoopGuardrail()
        policies = ({
            "action": "skip_redundant_mutation",
            "sourceToolName": "patch",
            "paths": ["src/app.py"],
        },)

        decision = guardrail.before_call(
            "patch",
            {"path": "./src/app.py", "oldText": "old", "newText": "new"},
            file_resume_policies=policies,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.failure_code, "file_resume_policy_blocked")
        self.assertIn("already matches", decision.reason or "")

    def test_file_resume_policy_force_rerun_override_allows_repeating_mutation(self) -> None:
        guardrail = ToolLoopGuardrail()
        policies = ({
            "action": "skip_redundant_mutation",
            "sourceToolName": "patch",
            "paths": ["src/app.py"],
            "override": "force_rerun",
        },)

        decision = guardrail.before_call(
            "patch",
            {"path": "src/app.py", "oldText": "old", "newText": "new"},
            file_resume_policies=policies,
        )

        self.assertTrue(decision.allowed)

    def test_file_resume_policy_ignore_artifact_override_skips_one_policy(self) -> None:
        guardrail = ToolLoopGuardrail()
        policies = ({
            "action": "skip_redundant_mutation",
            "sourceToolName": "patch",
            "paths": ["src/app.py"],
            "override": "ignore_artifact",
        },)

        decision = guardrail.before_call(
            "patch",
            {"path": "src/app.py", "oldText": "old", "newText": "new"},
            file_resume_policies=policies,
        )

        self.assertTrue(decision.allowed)

    def test_requires_read_before_mutating_changed_resume_policy_path(self) -> None:
        guardrail = ToolLoopGuardrail()
        policies = ({
            "action": "reinspect_before_mutation",
            "sourceToolName": "patch",
            "paths": ["src/app.py"],
        },)
        args = {"path": "src/app.py", "oldText": "old", "newText": "new"}

        blocked = guardrail.before_call("patch", args, file_resume_policies=policies)
        guardrail.after_call("read_file", {"path": "src/app.py"}, {"path": "src/app.py", "content": "old"}, ok=True)
        allowed = guardrail.before_call("patch", args, file_resume_policies=policies)

        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.failure_code, "file_resume_policy_reinspect_required")
        self.assertTrue(allowed.allowed)

    def test_file_resume_policy_accept_current_state_override_skips_reinspect_requirement(self) -> None:
        guardrail = ToolLoopGuardrail()
        policies = ({
            "action": "reinspect_before_mutation",
            "sourceToolName": "patch",
            "paths": ["src/app.py"],
            "override": "accept_current_state",
        },)

        decision = guardrail.before_call(
            "patch",
            {"path": "src/app.py", "oldText": "old", "newText": "new"},
            file_resume_policies=policies,
        )

        self.assertTrue(decision.allowed)

    def test_reinspect_resume_policy_read_is_bound_to_workspace_epoch(self) -> None:
        guardrail = ToolLoopGuardrail()
        policies = ({
            "action": "reinspect_before_mutation",
            "paths": ["src/app.py"],
        },)
        args = {"path": "src/app.py", "oldText": "old", "newText": "new"}

        guardrail.after_call(
            "read_file",
            {"path": "src/app.py"},
            {"path": "src/app.py", "content": "old"},
            ok=True,
            workspace_epoch=1,
        )
        same_epoch = guardrail.before_call("patch", args, workspace_epoch=1, file_resume_policies=policies)
        changed_epoch = guardrail.before_call("patch", args, workspace_epoch=2, file_resume_policies=policies)

        self.assertTrue(same_epoch.allowed)
        self.assertFalse(changed_epoch.allowed)
        self.assertEqual(changed_epoch.failure_code, "file_resume_policy_reinspect_required")


class ToolAuditStoreTests(unittest.TestCase):
    def test_saves_and_loads_audit_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ToolAuditStore(Path(tmpdir) / "amadeus.sqlite")
            log = ToolAuditLog()
            record = log.append(
                session_id="session-1",
                tool_name="get_current_time",
                decision="finished",
                ok=True,
                duration_ms=12,
                metadata={"workspaceEpoch": 3},
            )

            store.save(record)
            loaded = store.load(session_id="session-1")

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].record_id, record.record_id)
        self.assertEqual(loaded[0].session_id, "session-1")
        self.assertEqual(loaded[0].tool_name, "get_current_time")
        self.assertEqual(loaded[0].decision, "finished")
        self.assertTrue(loaded[0].ok)
        self.assertEqual(loaded[0].duration_ms, 12)
        self.assertEqual(loaded[0].metadata, {"workspaceEpoch": 3})
        self.assertEqual(loaded[0].to_payload()["metadata"], {"workspaceEpoch": 3})

    def test_filters_audit_records_by_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ToolAuditStore(Path(tmpdir) / "amadeus.sqlite")
            log = ToolAuditLog()
            store.save(log.append(session_id="session-1", tool_name="a", decision="started"))
            store.save(log.append(session_id="session-2", tool_name="b", decision="started"))

            loaded = store.load(session_id="session-2")

        self.assertEqual([record.session_id for record in loaded], ["session-2"])
        self.assertEqual([record.tool_name for record in loaded], ["b"])

    def test_queries_audit_records_by_tool_decision_ok_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ToolAuditStore(Path(tmpdir) / "amadeus.sqlite")
            log = ToolAuditLog()
            store.save(log.append(session_id="session-1", tool_name="search_files", decision="started"))
            store.save(log.append(
                session_id="session-1",
                tool_name="search_files",
                decision="finished",
                ok=True,
                duration_ms=3,
            ))
            store.save(log.append(
                session_id="session-1",
                tool_name="patch",
                decision="finished",
                ok=False,
                failure_code="tool_error",
            ))
            store.save(log.append(
                session_id="session-2",
                tool_name="patch",
                decision="finished",
                ok=False,
                failure_code="tool_timeout",
            ))

            loaded = store.query(
                session_id="session-1",
                tool_name="patch",
                decision="finished",
                ok=False,
                failure_code="tool_error",
            )
            count = store.count(session_id="session-1", decision="finished", ok=False)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].tool_name, "patch")
        self.assertEqual(loaded[0].failure_code, "tool_error")
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
