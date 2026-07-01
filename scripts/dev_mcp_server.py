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
                self.write_json(200, {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a tiny HTTP JSON-RPC MCP server for Amadeus development.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), DevMcpHandler)
    print(f"Amadeus dev MCP server listening at http://{args.host}:{args.port}/mcp", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
