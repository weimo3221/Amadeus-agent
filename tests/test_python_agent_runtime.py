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
        self.assertIn(("tool.finished", {"toolName": "get_current_time", "ok": True}), [(event.type, event.payload) for event in events])
        final_history = runtime.final_messages[-1]
        tool_messages = [message for message in final_history if message["role"] == "tool"]
        self.assertEqual(tool_messages[0]["tool_call_id"], "call_time")
        self.assertIn("formatted", tool_messages[0]["content"])

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
        self.assertIn(("tool.finished", {"toolName": "roll_dice", "ok": False}), [(event.type, event.payload) for event in events])
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
                {"toolName": "missing_tool", "ok": False},
                {"toolName": "missing_tool", "ok": False},
                {"toolName": "missing_tool", "ok": False},
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
