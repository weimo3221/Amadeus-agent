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


if __name__ == "__main__":
    unittest.main()
