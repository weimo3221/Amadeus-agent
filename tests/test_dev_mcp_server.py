from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dev_mcp_server import DevMcpHandler


class DevMcpServerTests(unittest.TestCase):
    def test_hermes_fixture_lists_and_reads_no_token_tools(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), DevMcpHandler)
        server.fixture = "hermes"  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            url = f"http://{host}:{port}/mcp"

            listed = self.post_json(url, {"jsonrpc": "2.0", "id": "1", "method": "tools/list", "params": {}})
            tool_names = {tool["name"] for tool in listed["result"]["tools"]}
            self.assertIn("conversations_list", tool_names)
            self.assertIn("messages_read", tool_names)

            conversations = self.post_json(url, {
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tools/call",
                "params": {"name": "conversations_list", "arguments": {"platform": "telegram"}},
            })
            conversations_payload = json.loads(conversations["result"]["content"][0]["text"])
            self.assertEqual(conversations_payload["count"], 1)
            session_key = conversations_payload["conversations"][0]["session_key"]

            messages = self.post_json(url, {
                "jsonrpc": "2.0",
                "id": "3",
                "method": "tools/call",
                "params": {"name": "messages_read", "arguments": {"session_key": session_key, "limit": 2}},
            })
            messages_payload = json.loads(messages["result"]["content"][0]["text"])
            self.assertEqual(messages_payload["count"], 2)
            self.assertEqual(messages_payload["messages"][-1]["role"], "assistant")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def post_json(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
