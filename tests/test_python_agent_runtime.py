from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Iterable

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.agent import AgentRuntime, PermissionBroker, PermissionRequest
from amadeus.memory import MessageMemoryStore
from amadeus.tool_runtime import ToolRegistry
from amadeus.tools import ToolSpec


class FakeAgentRuntime(AgentRuntime):
    def __init__(
        self,
        memory_store: MessageMemoryStore,
        tool_decision: dict[str, Any] | None = None,
        deltas: list[str] | None = None,
        tools_config_path: Path | None = None,
    ) -> None:
        os.environ["OPENAI_API_KEY"] = "test-key"
        self.final_messages: list[list[dict[str, Any]]] = []
        self.tool_decision = tool_decision or {"role": "assistant", "content": "", "tool_calls": []}
        self.deltas = deltas or ["ok"]
        super().__init__(
            memory_store,
            audio_runtime=None,
            tools_config_path=tools_config_path or Path(tempfile.mkdtemp()) / "missing-tools.yaml",
        )

    def _request_tool_decision(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return self.tool_decision

    def _stream_final_response(self, messages: list[dict[str, Any]]) -> Iterable[str]:
        self.final_messages.append(messages)
        yield from self.deltas


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tmpdir.name) / "amadeus.sqlite"
        self.memory = MessageMemoryStore(self.database_path)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_missing_api_key_returns_structured_error(self) -> None:
        os.environ["OPENAI_API_KEY"] = ""
        runtime = AgentRuntime(self.memory, audio_runtime=None, tools_config_path=Path(self.tmpdir.name) / "missing.yaml")

        events = list(runtime.run_turn("default", "hello", lambda _request: False))

        self.assertEqual(events[0].type, "error")
        self.assertEqual(events[0].payload["code"], "missing_api_key")
        self.assertEqual(self.memory.count("default"), 0)

    def test_simple_turn_persists_user_and_assistant_messages(self) -> None:
        runtime = FakeAgentRuntime(self.memory, deltas=["Hello", " there"])

        events = list(runtime.run_turn("default", "hi", lambda _request: False))

        self.assertIn(("assistant.message", {"text": "Hello there"}), [(event.type, event.payload) for event in events])
        self.assertEqual(self.memory.count("default"), 2)
        self.assertEqual(self.memory.load("default"), [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello there"},
        ])

    def test_system_prompt_includes_stable_memory_snapshot(self) -> None:
        self.memory.update_stable_memory("user", "add", content="The user prefers concise Chinese updates.")
        self.memory.update_stable_memory("agent", "add", content="The project uses Python-first AgentRuntime.")

        runtime = FakeAgentRuntime(self.memory)

        self.assertIn("<stable_memory target=\"agent\"", runtime.system_prompt)
        self.assertIn("Python-first AgentRuntime", runtime.system_prompt)
        self.assertIn("<stable_memory target=\"user\"", runtime.system_prompt)
        self.assertIn("concise Chinese", runtime.system_prompt)

    def test_allow_tool_executes_without_permission_request(self) -> None:
        runtime = FakeAgentRuntime(
            self.memory,
            tool_decision={
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_time",
                    "type": "function",
                    "function": {"name": "get_current_time", "arguments": "{}"},
                }],
            },
        )
        permission_requests: list[PermissionRequest] = []

        events = list(runtime.run_turn("default", "what time is it", lambda request: permission_requests.append(request) or False))

        self.assertEqual(permission_requests, [])
        tool_finished = [event.payload for event in events if event.type == "tool.finished"]
        self.assertEqual(tool_finished[0]["toolName"], "get_current_time")
        self.assertTrue(tool_finished[0]["ok"])
        self.assertIsInstance(tool_finished[0]["durationMs"], int)
        tool_audit = [event.payload for event in events if event.type == "tool.audit"]
        self.assertEqual([entry["decision"] for entry in tool_audit], ["started", "finished"])
        self.assertEqual(tool_audit[0]["toolName"], "get_current_time")
        self.assertTrue(tool_audit[1]["ok"])
        self.assertIsInstance(tool_audit[1]["durationMs"], int)
        self.assertEqual([record.decision for record in runtime.tool_audit_records()], ["started", "finished"])
        self.assertEqual(
            [record.decision for record in runtime.persisted_tool_audit_records("default")],
            ["started", "finished"],
        )
        final_history = runtime.final_messages[-1]
        tool_messages = [message for message in final_history if message["role"] == "tool"]
        self.assertEqual(tool_messages[0]["tool_call_id"], "call_time")
        self.assertIn("formatted", tool_messages[0]["content"])

    def test_persisted_audit_records_survive_runtime_recreation(self) -> None:
        runtime = FakeAgentRuntime(
            self.memory,
            tool_decision={
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_time",
                    "type": "function",
                    "function": {"name": "get_current_time", "arguments": "{}"},
                }],
            },
        )

        list(runtime.run_turn("default", "what time is it", lambda _request: False))
        recreated = FakeAgentRuntime(self.memory)

        persisted = recreated.persisted_tool_audit_records("default")
        self.assertEqual([record.decision for record in persisted], ["started", "finished"])
        self.assertEqual(persisted[0].tool_name, "get_current_time")
        self.assertTrue(persisted[1].ok)

    def test_large_tool_result_writes_compact_output_to_model_context(self) -> None:
        runtime = FakeAgentRuntime(
            self.memory,
            tool_decision={
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_large",
                    "type": "function",
                    "function": {"name": "large_tool", "arguments": "{}"},
                }],
            },
        )
        runtime.tool_registry = ToolRegistry(
            specs=[
                ToolSpec(
                    name="large_tool",
                    display_name="Large Tool",
                    permission="allow",
                    enabled=True,
                    schema={"type": "function", "function": {"name": "large_tool"}},
                    handler=lambda _args: {"text": "x" * 5000},
                ),
            ],
            config_path=Path(self.tmpdir.name) / "missing-tools.yaml",
        )

        events = list(runtime.run_turn("default", "run large tool", lambda _request: False))

        tool_finished = [event.payload for event in events if event.type == "tool.finished"]
        self.assertTrue(tool_finished[0]["ok"])
        self.assertTrue(tool_finished[0]["outputTruncated"])
        self.assertIn("resultPreview", tool_finished[0])

        final_history = runtime.final_messages[-1]
        tool_messages = [message for message in final_history if message["role"] == "tool"]
        compact_result = json.loads(tool_messages[0]["content"])
        self.assertEqual(compact_result["_amadeus_result_truncated"], True)
        self.assertEqual(compact_result["tool_name"], "large_tool")
        self.assertGreater(compact_result["original_char_count"], 4000)
        self.assertLess(len(tool_messages[0]["content"]), 1400)

    def test_ask_tool_denial_returns_tool_error_to_model(self) -> None:
        runtime = FakeAgentRuntime(
            self.memory,
            tool_decision={
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_dice",
                    "type": "function",
                    "function": {"name": "roll_dice", "arguments": "{\"sides\":6,\"count\":1}"},
                }],
            },
        )
        permission_requests: list[PermissionRequest] = []

        events = list(runtime.run_turn("default", "roll a die", lambda request: permission_requests.append(request) or False))

        self.assertEqual(len(permission_requests), 1)
        self.assertEqual(permission_requests[0].tool_name, "roll_dice")
        tool_finished = [event.payload for event in events if event.type == "tool.finished"]
        self.assertEqual(tool_finished[0], {
            "toolName": "roll_dice",
            "ok": False,
            "failureCode": "permission_denied",
        })
        tool_audit = [event.payload for event in events if event.type == "tool.audit"]
        self.assertEqual([entry["decision"] for entry in tool_audit], ["started", "denied"])
        self.assertEqual(tool_audit[1]["failureCode"], "permission_denied")
        persisted = runtime.persisted_tool_audit_records("default")
        self.assertEqual([record.decision for record in persisted], ["started", "denied"])
        self.assertEqual(persisted[1].failure_code, "permission_denied")
        final_history = runtime.final_messages[-1]
        tool_messages = [message for message in final_history if message["role"] == "tool"]
        self.assertIn("Permission denied", tool_messages[0]["content"])

    def test_tool_config_overrides_enabled_and_permission(self) -> None:
        config_path = Path(self.tmpdir.name) / "tools.yaml"
        config_path.write_text(
            "\n".join([
                "tools:",
                "  roll_dice:",
                "    enabled: false",
                "    permission: deny",
            ]),
            encoding="utf-8",
        )

        runtime = FakeAgentRuntime(self.memory, tools_config_path=config_path)
        tool_state = {entry["name"]: entry for entry in runtime.tool_permission_state()}

        self.assertFalse(tool_state["roll_dice"]["enabled"])
        self.assertEqual(tool_state["roll_dice"]["permission"], "deny")

    def test_repeated_failing_tool_call_is_blocked_by_guardrail(self) -> None:
        repeated_tool_calls = [
            {
                "id": f"call_missing_{index}",
                "type": "function",
                "function": {"name": "missing_tool", "arguments": "{\"query\":\"same\"}"},
            }
            for index in range(3)
        ]
        runtime = FakeAgentRuntime(
            self.memory,
            tool_decision={"role": "assistant", "content": "", "tool_calls": repeated_tool_calls},
        )

        events = list(runtime.run_turn("default", "try the bad tool", lambda _request: False))

        self.assertEqual(
            [event.payload for event in events if event.type == "tool.finished"],
            [
                {"toolName": "missing_tool", "ok": False, "failureCode": "unknown_tool"},
                {"toolName": "missing_tool", "ok": False, "failureCode": "unknown_tool"},
                {"toolName": "missing_tool", "ok": False, "failureCode": "guardrail_blocked"},
            ],
        )
        final_history = runtime.final_messages[-1]
        tool_results = [
            json.loads(message["content"])
            for message in final_history
            if message["role"] == "tool"
        ]
        self.assertIn("Unknown tool", tool_results[0]["error"])
        self.assertIn("Unknown tool", tool_results[1]["error"])
        self.assertIn("Blocked repeated failing tool call", tool_results[2]["error"])
        tool_audit = [event.payload for event in events if event.type == "tool.audit"]
        self.assertEqual(
            [entry["decision"] for entry in tool_audit],
            ["started", "failed", "started", "failed", "started", "blocked"],
        )
        self.assertEqual(tool_audit[-1]["failureCode"], "guardrail_blocked")
        persisted = runtime.persisted_tool_audit_records("default")
        self.assertEqual(
            [record.decision for record in persisted],
            ["started", "failed", "started", "failed", "started", "blocked"],
        )
        self.assertEqual(persisted[-1].failure_code, "guardrail_blocked")

    def test_repeated_successful_tool_call_is_blocked_as_no_progress(self) -> None:
        repeated_tool_calls = [
            {
                "id": f"call_time_{index}",
                "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }
            for index in range(3)
        ]
        runtime = FakeAgentRuntime(
            self.memory,
            tool_decision={"role": "assistant", "content": "", "tool_calls": repeated_tool_calls},
        )

        events = list(runtime.run_turn("default", "repeat the same tool", lambda _request: False))

        self.assertEqual(
            [event.payload["failureCode"] for event in events if event.type == "tool.finished" and not event.payload["ok"]],
            ["no_progress_loop"],
        )
        final_history = runtime.final_messages[-1]
        tool_results = [
            json.loads(message["content"])
            for message in final_history
            if message["role"] == "tool"
        ]
        self.assertEqual(len(tool_results), 3)
        self.assertIn("formatted", tool_results[0])
        self.assertIn("formatted", tool_results[1])
        self.assertIn("Blocked no-progress repeated tool call", tool_results[2]["error"])
        tool_audit = [event.payload for event in events if event.type == "tool.audit"]
        self.assertEqual(
            [entry["decision"] for entry in tool_audit],
            ["started", "finished", "started", "finished", "started", "blocked"],
        )
        self.assertEqual(tool_audit[-1]["failureCode"], "no_progress_loop")


class PermissionBrokerTests(unittest.TestCase):
    def test_resolve_unknown_request_returns_false(self) -> None:
        broker = PermissionBroker()

        self.assertFalse(broker.resolve("missing", True))

    def test_resolve_registered_request_returns_true(self) -> None:
        broker = PermissionBroker()
        broker.register("request-1")

        self.assertTrue(broker.resolve("request-1", True))
        self.assertTrue(broker.wait("request-1", timeout_seconds=0.01))


if __name__ == "__main__":
    unittest.main()
