from __future__ import annotations

import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import amadeus_cli


class NonInteractiveInput(io.StringIO):
    def isatty(self) -> bool:
        return False


class FakeRuntimeState:
    def __init__(self) -> None:
        self.turn_payloads: list[dict[str, Any]] = []
        self.permission_payloads: list[dict[str, Any]] = []
        self.audio_payloads: list[dict[str, Any]] = []
        self.permission_event = threading.Event()


def make_event(event_type: str, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"event-{event_type}",
        "type": event_type,
        "sessionId": session_id,
        "timestamp": "2026-07-17T00:00:00+00:00",
        "payload": payload,
    }


def create_handler(state: FakeRuntimeState):
    class FakeRuntimeHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            session_id = query.get("sessionId", ["cli:default"])[0]
            if parsed.path == "/runtime/health":
                self.write_json({
                    "ok": True,
                    "status": "degraded",
                    "checks": {
                        "model": {
                            "status": "degraded",
                            "provider": "openai-compatible",
                            "model": "fake-model",
                            "apiKeyConfigured": False,
                        },
                        "memory": {
                            "status": "ok",
                            "messageCount": 12,
                            "memoryItemCount": 3,
                            "pendingReviewCandidateCount": 1,
                        },
                    },
                })
                return
            if parsed.path == "/skills/list":
                self.write_json({
                    "ok": True,
                    "skills": [{
                        "name": "runtime-debug",
                        "identifier": "development/runtime-debug",
                        "description": "Debug runtime behavior.",
                        "category": "development",
                    }],
                })
                return
            if parsed.path == "/skills/view":
                self.write_json({
                    "ok": True,
                    "skill": {
                        "name": "runtime-debug",
                        "identifier": query.get("name", ["development/runtime-debug"])[0],
                        "description": "Debug runtime behavior.",
                        "instructions": "Use evidence.",
                    },
                })
                return
            if parsed.path == "/tools/list":
                self.write_json({
                    "ok": True,
                    "tools": [
                        {"name": "get_current_time", "enabled": True, "permission": "allow"},
                        {"name": "mcp__local__echo", "enabled": True, "permission": "ask"},
                    ],
                    "schemas": [
                        {"function": {"name": "get_current_time"}},
                        {"function": {"name": "mcp__local__echo"}},
                    ],
                })
                return
            if parsed.path == "/tools/config":
                self.write_json({
                    "ok": True,
                    "mcp": {
                        "enabled": True,
                        "servers": [{
                            "name": "local",
                            "url": "http://127.0.0.1/mcp",
                            "enabled": True,
                        }],
                    },
                })
                return
            if parsed.path == "/memory/count":
                self.write_json({"ok": True, "memoryMessages": 2 if session_id == "cli:default" else 0})
                return
            if parsed.path == "/memory/context/diagnostics":
                self.write_json({
                    "ok": True,
                    "sessionId": session_id,
                    "diagnostics": [{
                        "sessionId": session_id,
                        "turnId": "turn-1",
                        "sourceCount": 2,
                        "sourceCounts": {"retrieval": 2},
                    }],
                })
                return
            if parsed.path == "/memory/items":
                self.write_json({
                    "ok": True,
                    "items": [{
                        "memoryItemId": 1,
                        "scope": "project",
                        "memoryType": "semantic",
                        "content": "CLI memory search works.",
                    }],
                })
                return
            if parsed.path == "/audio/config":
                self.write_json({
                    "ok": True,
                    "activeProvider": "auto",
                    "runtimeProvider": "none",
                    "macosAvailable": False,
                })
                return
            self.write_json({"ok": False, "error": f"unexpected GET {parsed.path}"}, status=404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            body = self.read_json()
            if parsed.path == "/agent/turn":
                state.turn_payloads.append(body)
                session_id = str(body.get("sessionId") or "default")
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.end_headers()
                self.write_event(make_event("memory.updated", session_id, {"memoryMessages": 1}))
                self.write_event(make_event("tool.permission.request", session_id, {
                    "requestId": "permission-1",
                    "toolName": "terminal",
                    "displayName": "terminal command `pwd`",
                    "reason": "test permission flow",
                }))
                state.permission_event.wait(timeout=2)
                self.write_event(make_event("assistant.delta", session_id, {"text": "CLI ok"}))
                self.write_event(make_event("assistant.message", session_id, {"text": "CLI ok"}))
                return
            if parsed.path == "/tools/permission":
                state.permission_payloads.append(body)
                state.permission_event.set()
                self.write_json({"ok": True, "resolved": True})
                return
            if parsed.path == "/audio/speak":
                state.audio_payloads.append(body)
                self.write_json({
                    "ok": True,
                    "audioUrl": None,
                    "durationMs": None,
                    "fallback": "browser_speech_synthesis",
                    "reason": "tts_provider_unavailable:none",
                })
                return
            self.write_json({"ok": False, "error": f"unexpected POST {parsed.path}"}, status=404)

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            parsed = json.loads(raw or "{}")
            return parsed if isinstance(parsed, dict) else {}

        def write_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def write_event(self, payload: dict[str, Any]) -> None:
            self.wfile.write(json.dumps(payload).encode("utf-8") + b"\n")
            self.wfile.flush()

        def log_message(self, format: str, *args: Any) -> None:
            del format, args
            return

    return FakeRuntimeHandler


class AmadeusCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = FakeRuntimeState()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(self.state))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        self.runtime_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def run_cli(self, argv: list[str], *, stdin: io.StringIO | None = None) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = amadeus_cli.main(
            ["--runtime-url", self.runtime_url, *argv],
            stdout=stdout,
            stderr=stderr,
            stdin=stdin or NonInteractiveInput(),
        )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_ask_defaults_to_cli_session_and_forwards_skills(self) -> None:
        code, stdout, stderr = self.run_cli([
            "ask",
            "--skill",
            "development/runtime-debug,web-access",
            "hello",
        ])

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "CLI ok\n")
        self.assertIn("[permission denied] terminal", stderr)
        self.assertEqual(self.state.turn_payloads[0]["sessionId"], "cli:default")
        self.assertEqual(
            self.state.turn_payloads[0]["skills"],
            ["development/runtime-debug", "web-access"],
        )
        self.assertEqual(self.state.permission_payloads[0], {
            "requestId": "permission-1",
            "approved": False,
        })

    def test_ask_accepts_free_text_without_explicit_subcommand(self) -> None:
        code, stdout, _stderr = self.run_cli(["hello", "without", "subcommand"])

        self.assertEqual(code, 0)
        self.assertEqual(stdout, "CLI ok\n")
        self.assertEqual(self.state.turn_payloads[0]["text"], "hello without subcommand")

    def test_doctor_summarizes_skills_mcp_memory_and_audio(self) -> None:
        code, stdout, _stderr = self.run_cli(["doctor"])

        self.assertEqual(code, 0)
        self.assertIn("Runtime: degraded", stdout)
        self.assertIn("Skills: 1 available", stdout)
        self.assertIn("1 MCP tools from 1 enabled MCP servers", stdout)
        self.assertIn("Memory: ok, 3 facts, 1 pending reviews", stdout)
        self.assertIn("Audio: active=auto runtime=none", stdout)

    def test_memory_query_prints_typed_memory_matches(self) -> None:
        code, stdout, _stderr = self.run_cli(["memory", "--query", "CLI", "--scope", "project"])

        self.assertEqual(code, 0)
        self.assertIn("Session cli:default: 2 messages", stdout)
        self.assertIn("[project/semantic] CLI memory search works.", stdout)

    def test_speak_reports_audio_fallback(self) -> None:
        code, stdout, _stderr = self.run_cli(["speak", "hello", "voice"])

        self.assertEqual(code, 0)
        self.assertIn("Audio fallback: tts_provider_unavailable:none", stdout)
        self.assertEqual(self.state.audio_payloads[0]["text"], "hello voice")


if __name__ == "__main__":
    unittest.main()
