from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts.runtime_soak import run_soak


class SoakHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/runtime/health":
            self.write_json({"ok": True, "status": "ok"})
            return
        if self.path.startswith("/runtime/observability"):
            self.write_json({"ok": True, "summary": {"healthStatus": "ok"}})
            return
        if self.path == "/health":
            self.write_json({"ok": True, "runtime": "bridge"})
            return
        self.send_response(404)
        self.end_headers()

    def write_json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


class RuntimeSoakTests(unittest.TestCase):
    def test_soak_polls_runtime_observability_and_bridge_health(self) -> None:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), SoakHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        host, port = httpd.server_address
        base_url = f"http://{host}:{port}"

        try:
            result = run_soak(
                runtime_url=base_url,
                bridge_url=base_url,
                session_id="default",
                duration_seconds=0,
                interval_seconds=0.1,
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

        self.assertTrue(result["ok"])
        self.assertEqual(result["samples"], 1)
        self.assertEqual(result["lastRuntimeStatus"], "ok")
        self.assertEqual(result["lastObservabilityStatus"], "ok")
        self.assertEqual(result["lastBridgeStatus"], "ok")


if __name__ == "__main__":
    unittest.main()
