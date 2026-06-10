from __future__ import annotations

import json
import mimetypes
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4


RUNTIME_DIR = Path(__file__).resolve().parent
PACKAGES_DIR = RUNTIME_DIR.parent
REPO_ROOT = PACKAGES_DIR.parent
sys.path.insert(0, str(PACKAGES_DIR))

from amadeus.memory import MessageMemoryStore
from amadeus.agent import AgentRuntime, PermissionBroker, PermissionRequest
from amadeus.audio import AudioFallbackResult, AudioOutputCommand, AudioRuntime, LocalAudioLibrary
from amadeus.tools import execute_tool, list_tools


HOST = os.environ.get("AMADEUS_PYTHON_RUNTIME_HOST", os.environ.get("AMADEUS_PYTHON_TOOLS_HOST", "127.0.0.1"))
PORT = int(os.environ.get("AMADEUS_PYTHON_RUNTIME_PORT", os.environ.get("AMADEUS_PYTHON_TOOLS_PORT", "8790")))
DATABASE_PATH = Path(os.environ.get("AMADEUS_MEMORY_DB", str(REPO_ROOT / "data" / "amadeus.sqlite")))
AUDIO_ROOT = Path(os.environ.get("AMADEUS_AUDIO_ROOT", str(RUNTIME_DIR / "assets" / "audio")))
PUBLIC_BASE_URL = os.environ.get("AMADEUS_PYTHON_RUNTIME_URL", f"http://{HOST}:{PORT}")

memory_store = MessageMemoryStore(DATABASE_PATH)
audio_library = LocalAudioLibrary(AUDIO_ROOT, PUBLIC_BASE_URL)
audio_runtime = AudioRuntime(audio_library)
permission_broker = PermissionBroker()
agent_runtime = AgentRuntime(memory_store, audio_runtime)


class RuntimeRequestHandler(BaseHTTPRequestHandler):
    server_version = "AmadeusPythonRuntime/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self.write_json(200, {
                "ok": True,
                "runtime": "python",
                "modules": ["agent", "memory", "model", "tools", "skills", "live2d", "audio"],
                "tools": list_tools(),
                "model": agent_runtime.model,
            })
            return

        if parsed.path == "/tools/list":
            self.write_json(200, {
                "ok": True,
                "tools": agent_runtime.tool_permission_state(),
                "schemas": agent_runtime.enabled_tool_schemas(),
            })
            return

        if parsed.path == "/memory/count":
            query = parse_qs(parsed.query)
            session_id = query.get("sessionId", ["default"])[0]
            self.write_json(200, {"ok": True, "memoryMessages": memory_store.count(session_id)})
            return

        if parsed.path == "/memory/messages":
            query = parse_qs(parsed.query)
            session_id = query.get("sessionId", ["default"])[0]
            limit = parse_int(query.get("limit", ["40"])[0], 40, 1, 200)
            self.write_json(200, {"ok": True, "messages": memory_store.load(session_id, limit)})
            return

        if parsed.path.startswith("/audio/files/"):
            self.handle_audio_file(parsed.path.removeprefix("/audio/files/"))
            return

        self.write_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path == "/agent/turn":
            self.handle_agent_turn()
            return

        if self.path == "/tools/execute":
            self.handle_tool_execute()
            return

        if self.path == "/tools/permission":
            self.handle_tool_permission()
            return

        if self.path == "/memory/messages":
            self.handle_memory_save()
            return

        if self.path == "/memory/reset":
            self.handle_memory_reset()
            return

        if self.path == "/audio/speak":
            self.handle_audio_speak()
            return

        self.write_json(404, {"ok": False, "error": "not_found"})

    def handle_agent_turn(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId", "default")
            text = body.get("text")

            if not isinstance(session_id, str) or not isinstance(text, str):
                self.write_json(400, {"ok": False, "error": "sessionId and text must be strings"})
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            def request_permission(request: PermissionRequest) -> bool:
                permission_broker.register(request.request_id)
                self.write_event(session_id, "tool.permission.request", {
                    "requestId": request.request_id,
                    "toolName": request.tool_name,
                    "displayName": request.display_name,
                    "reason": request.reason,
                })
                return permission_broker.wait(request.request_id)

            for event in agent_runtime.run_turn(session_id, text, request_permission):
                self.write_json_line(event.to_runtime_event(session_id))
        except BrokenPipeError:
            return
        except Exception as error:
            try:
                self.write_json_line({
                    "id": "",
                    "type": "error",
                    "sessionId": "default",
                    "timestamp": "",
                    "payload": {"code": "runtime_error", "message": str(error)},
                })
            except Exception:
                return

    def handle_tool_execute(self) -> None:
        try:
            body = self.read_json_body()
            tool_name = body.get("toolName")
            args = body.get("args") if isinstance(body.get("args"), dict) else {}

            if not isinstance(tool_name, str):
                self.write_json(400, {"ok": False, "error": "toolName must be a string"})
                return

            result = execute_tool(tool_name, args)
            self.write_json(200, {"ok": True, "result": result})
        except KeyError as error:
            self.write_json(404, {"ok": False, "error": str(error)})
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_tool_permission(self) -> None:
        try:
            body = self.read_json_body()
            request_id = body.get("requestId")
            approved = body.get("approved")

            if not isinstance(request_id, str) or not isinstance(approved, bool):
                self.write_json(400, {"ok": False, "error": "requestId must be a string and approved must be a boolean"})
                return

            resolved = permission_broker.resolve(request_id, approved)
            self.write_json(200, {"ok": True, "resolved": resolved})
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_memory_save(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId", "default")
            role = body.get("role")
            content = body.get("content")

            if not isinstance(session_id, str) or not isinstance(role, str) or not isinstance(content, str):
                self.write_json(400, {"ok": False, "error": "sessionId, role, and content must be strings"})
                return

            memory_store.save(session_id, role, content)
            self.write_json(200, {"ok": True, "memoryMessages": memory_store.count(session_id)})
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_memory_reset(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId", "default")
            if not isinstance(session_id, str):
                self.write_json(400, {"ok": False, "error": "sessionId must be a string"})
                return

            memory_store.reset(session_id)
            self.write_json(200, {"ok": True, "memoryMessages": 0})
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_audio_speak(self) -> None:
        try:
            body = self.read_json_body()
            text = body.get("text")
            voice = body.get("voice")
            audio_format = body.get("format", "wav")

            if not isinstance(text, str):
                self.write_json(400, {"ok": False, "error": "text must be a string"})
                return

            if voice is not None and not isinstance(voice, str):
                self.write_json(400, {"ok": False, "error": "voice must be a string when provided"})
                return

            if not isinstance(audio_format, str):
                self.write_json(400, {"ok": False, "error": "format must be a string"})
                return

            result = audio_runtime.speak(AudioOutputCommand(text=text, voice=voice, format=audio_format))
            if isinstance(result, AudioFallbackResult):
                self.write_json(200, {
                    "ok": True,
                    "audioUrl": None,
                    "durationMs": None,
                    "fallback": result.fallback,
                    "reason": result.reason,
                })
                return

            self.write_json(200, {
                "ok": True,
                "audioUrl": result.audio_url,
                "durationMs": result.duration_ms,
                "provider": result.provider,
            })
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_audio_file(self, relative_path: str) -> None:
        file_path = audio_library.resolve_public_path(unquote(relative_path))
        if not file_path:
            self.write_json(404, {"ok": False, "error": "audio_not_found"})
            return

        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8")
        data = json.loads(raw_body or "{}")
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object")

        return data

    def write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def write_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.write_json_line({
            "id": str(uuid4()),
            "type": event_type,
            "sessionId": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        })

    def write_json_line(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        self.wfile.write(line)
        self.wfile.flush()

    def log_message(self, format: str, *args: Any) -> None:
        return


def parse_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback

    return max(minimum, min(maximum, number))


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), RuntimeRequestHandler)
    print(f"Amadeus runtime listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
