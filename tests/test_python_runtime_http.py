from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus import server as runtime_server
from amadeus.agent import AgentRuntime, PermissionBroker
from amadeus.memory import MessageMemoryStore


class SummaryRuntime(AgentRuntime):
    def _request_conversation_summary(self, previous_summary: dict | None, messages: list[dict]) -> str:
        return "HTTP compacted summary"


class PythonRuntimeHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.previous_api_key = os.environ.get("OPENAI_API_KEY")
        self.previous_memory_store = runtime_server.memory_store
        self.previous_agent_runtime = runtime_server.agent_runtime
        self.previous_permission_broker = runtime_server.permission_broker

        database_path = Path(self.tmpdir.name) / "amadeus.sqlite"
        memory_store = MessageMemoryStore(database_path)
        runtime_server.memory_store = memory_store
        runtime_server.agent_runtime = AgentRuntime(
            memory_store,
            audio_runtime=None,
            tools_config_path=Path(self.tmpdir.name) / "missing-tools.yaml",
        )
        runtime_server.permission_broker = PermissionBroker()

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
        request = Request(
            self.url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
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

    def test_tools_list_exposes_permission_state_and_enabled_schemas(self) -> None:
        payload = self.get_json("/tools/list")

        self.assertTrue(payload["ok"])
        tool_names = {entry["name"] for entry in payload["tools"]}
        schema_names = {entry["function"]["name"] for entry in payload["schemas"]}
        self.assertIn("get_current_time", tool_names)
        self.assertIn("get_current_time", schema_names)

    def test_tool_permission_unknown_request_returns_unresolved(self) -> None:
        payload = self.post_json("/tools/permission", {"requestId": "missing", "approved": True})

        self.assertEqual(payload, {"ok": True, "resolved": False})

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
