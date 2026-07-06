from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


TOOLS = [
    {
        "name": "echo",
        "description": "Echo text back from the development MCP server.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "project_info",
        "description": "Return basic local project information for Amadeus Agent.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

HERMES_SESSION_KEY = "agent:main:telegram:dm:123456"
HERMES_SESSION_ID = "20260329_120000_abc123"
HERMES_CONVERSATIONS = {
    HERMES_SESSION_KEY: {
        "session_key": HERMES_SESSION_KEY,
        "session_id": HERMES_SESSION_ID,
        "platform": "telegram",
        "chat_type": "dm",
        "display_name": "Alice",
        "created_at": "2026-03-29T12:00:00",
        "updated_at": "2026-03-29T14:30:00",
        "input_tokens": 50000,
        "output_tokens": 2000,
        "total_tokens": 52000,
        "origin": {
            "platform": "telegram",
            "chat_id": "123456",
            "chat_name": "Alice",
            "chat_type": "dm",
            "user_id": "123456",
            "user_name": "Alice",
            "thread_id": None,
        },
    },
    "agent:main:slack:group:C1234:U5678": {
        "session_key": "agent:main:slack:group:C1234:U5678",
        "session_id": "20260328_090000_ghi789",
        "platform": "slack",
        "chat_type": "group",
        "display_name": "Carol",
        "created_at": "2026-03-28T09:00:00",
        "updated_at": "2026-03-28T11:00:00",
        "input_tokens": 10000,
        "output_tokens": 500,
        "total_tokens": 10500,
        "origin": {
            "platform": "slack",
            "chat_id": "C1234",
            "chat_name": "#engineering",
            "chat_type": "group",
            "user_id": "U5678",
            "user_name": "Carol",
            "thread_id": None,
        },
    },
}

HERMES_MESSAGES = {
    HERMES_SESSION_ID: [
        {"id": "1", "role": "user", "content": "Hello Alice!", "timestamp": "2026-03-29T12:00:01"},
        {"id": "2", "role": "assistant", "content": "Hi! How can I help?", "timestamp": "2026-03-29T12:00:05"},
        {
            "id": "3",
            "role": "user",
            "content": "Check the image MEDIA: /tmp/screenshot.png please",
            "timestamp": "2026-03-29T12:01:00",
        },
        {
            "id": "4",
            "role": "assistant",
            "content": "I see the screenshot. It shows a terminal.",
            "timestamp": "2026-03-29T12:01:10",
        },
    ],
    "20260328_090000_ghi789": [
        {"id": "1", "role": "user", "content": "Please summarize today's deploy notes.", "timestamp": "2026-03-28T09:00:01"},
        {"id": "2", "role": "assistant", "content": "The deploy notes focus on MCP reliability.", "timestamp": "2026-03-28T09:00:10"},
    ],
}

HERMES_TOOLS = [
    {
        "name": "conversations_list",
        "description": "List local Hermes-style conversations without requiring platform tokens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "platform": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "search": {"type": "string"},
            },
        },
    },
    {
        "name": "conversation_get",
        "description": "Get one Hermes-style conversation by session key.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_key": {"type": "string"}},
            "required": ["session_key"],
        },
    },
    {
        "name": "messages_read",
        "description": "Read recent local Hermes-style messages for a conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_key": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["session_key"],
        },
    },
    {
        "name": "channels_list",
        "description": "List local Hermes-style message targets derived from conversations.",
        "inputSchema": {
            "type": "object",
            "properties": {"platform": {"type": "string"}},
        },
    },
]


def _json_text(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}


def _coerce_limit(value: Any, *, default: int = 50) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 200))


def _call_hermes_fixture_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "conversations_list":
        platform = str(arguments.get("platform") or "").strip().lower()
        search = str(arguments.get("search") or "").strip().lower()
        limit = _coerce_limit(arguments.get("limit"))
        conversations = []
        for key, entry in HERMES_CONVERSATIONS.items():
            origin = entry.get("origin", {})
            entry_platform = str(entry.get("platform") or origin.get("platform") or "")
            display_name = str(entry.get("display_name") or "")
            chat_name = str(origin.get("chat_name") or "")
            if platform and entry_platform.lower() != platform:
                continue
            if search and search not in display_name.lower() and search not in chat_name.lower() and search not in key.lower():
                continue
            conversations.append({
                "session_key": key,
                "session_id": entry.get("session_id", ""),
                "platform": entry_platform,
                "chat_type": entry.get("chat_type", origin.get("chat_type", "")),
                "display_name": display_name,
                "chat_name": chat_name,
                "user_name": origin.get("user_name", ""),
                "updated_at": entry.get("updated_at", ""),
            })
        conversations.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return _json_text({"source": "hermes-agent/mcp_serve.py fixture", "count": len(conversations[:limit]), "conversations": conversations[:limit]})

    if name == "conversation_get":
        session_key = str(arguments.get("session_key") or "")
        entry = HERMES_CONVERSATIONS.get(session_key)
        if not entry:
            return _json_text({"error": f"Conversation not found: {session_key}"})
        origin = entry.get("origin", {})
        return _json_text({
            "session_key": session_key,
            "session_id": entry.get("session_id", ""),
            "platform": entry.get("platform") or origin.get("platform", ""),
            "chat_type": entry.get("chat_type", origin.get("chat_type", "")),
            "display_name": entry.get("display_name", ""),
            "user_name": origin.get("user_name", ""),
            "chat_name": origin.get("chat_name", ""),
            "chat_id": origin.get("chat_id", ""),
            "thread_id": origin.get("thread_id"),
            "updated_at": entry.get("updated_at", ""),
            "created_at": entry.get("created_at", ""),
            "input_tokens": entry.get("input_tokens", 0),
            "output_tokens": entry.get("output_tokens", 0),
            "total_tokens": entry.get("total_tokens", 0),
        })

    if name == "messages_read":
        session_key = str(arguments.get("session_key") or "")
        limit = _coerce_limit(arguments.get("limit"))
        entry = HERMES_CONVERSATIONS.get(session_key)
        if not entry:
            return _json_text({"error": f"Conversation not found: {session_key}"})
        messages = HERMES_MESSAGES.get(str(entry.get("session_id") or ""), [])
        return _json_text({
            "session_key": session_key,
            "count": len(messages[-limit:]),
            "total_in_session": len(messages),
            "messages": messages[-limit:],
        })

    if name == "channels_list":
        platform = str(arguments.get("platform") or "").strip().lower()
        channels = []
        seen = set()
        for entry in HERMES_CONVERSATIONS.values():
            origin = entry.get("origin", {})
            entry_platform = str(entry.get("platform") or origin.get("platform") or "")
            chat_id = str(origin.get("chat_id") or "")
            if platform and entry_platform.lower() != platform:
                continue
            target = f"{entry_platform}:{chat_id}" if chat_id else entry_platform
            if target in seen:
                continue
            seen.add(target)
            channels.append({
                "target": target,
                "platform": entry_platform,
                "name": entry.get("display_name") or origin.get("chat_name", ""),
                "chat_type": entry.get("chat_type", origin.get("chat_type", "")),
            })
        return _json_text({"count": len(channels), "channels": channels})

    raise ValueError(f"unknown Hermes fixture tool: {name}")


def tool_list_for_fixture(fixture: str) -> list[dict[str, Any]]:
    if fixture == "hermes":
        return HERMES_TOOLS
    return TOOLS


class DevMcpHandler(BaseHTTPRequestHandler):
    server_version = "AmadeusDevMCP/0.1"

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self.write_json(404, {"jsonrpc": "2.0", "id": None, "error": {"code": -32004, "message": "not_found"}})
            return

        try:
            request = self.read_json()
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") if isinstance(request.get("params"), dict) else {}

            if method == "tools/list":
                self.write_json(200, {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tool_list_for_fixture(self.fixture)}})
                return

            if method == "tools/call":
                result = self.call_tool(params)
                self.write_json(200, {"jsonrpc": "2.0", "id": request_id, "result": result})
                return

            self.write_json(200, {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"unknown method: {method}"},
            })
        except Exception as error:
            self.write_json(200, {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32000, "message": str(error)},
            })

    def call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}

        if self.fixture == "hermes":
            return _call_hermes_fixture_tool(str(name or ""), arguments)

        if name == "echo":
            text = str(arguments.get("text") or "")
            return {"content": [{"type": "text", "text": text}]}

        if name == "project_info":
            package_json = REPO_ROOT / "package.json"
            package_name = "amadeus-agent"
            if package_json.exists():
                try:
                    package_name = str(json.loads(package_json.read_text(encoding="utf-8")).get("name") or package_name)
                except Exception:
                    package_name = "amadeus-agent"
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "name": package_name,
                        "root": str(REPO_ROOT),
                        "mcpServer": "scripts/dev_mcp_server.py",
                    }, ensure_ascii=False),
                }],
            }

        raise ValueError(f"unknown tool: {name}")

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("request must be a JSON object")
        return payload

    def write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def fixture(self) -> str:
        return str(getattr(self.server, "fixture", "amadeus"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a tiny HTTP JSON-RPC MCP server for Amadeus development.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument(
        "--fixture",
        choices=["amadeus", "hermes"],
        default="amadeus",
        help="Tool fixture to expose. 'hermes' mirrors no-token local tools from ../hermes-agent/mcp_serve.py.",
    )
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), DevMcpHandler)
    httpd.fixture = args.fixture  # type: ignore[attr-defined]
    print(f"Amadeus dev MCP server listening at http://{args.host}:{args.port}/mcp fixture={args.fixture}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
