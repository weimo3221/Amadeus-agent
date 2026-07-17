#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Iterable, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_RUNTIME_URL = "http://127.0.0.1:8790"
DEFAULT_SESSION_ID = "cli:default"


class CliError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeHttpError(CliError):
    method: str
    url: str
    status: int
    payload: dict[str, Any]

    def __str__(self) -> str:
        error = self.payload.get("error") or self.payload.get("message") or self.payload
        return f"{self.method} {self.url} failed with HTTP {self.status}: {error}"


class RuntimeClient:
    def __init__(self, runtime_url: str, *, timeout_seconds: float = 120) -> None:
        self.runtime_url = normalize_runtime_url(runtime_url)
        self.timeout_seconds = timeout_seconds

    def endpoint(self, path: str, query: dict[str, Any] | None = None) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{self.runtime_url}{normalized_path}"
        if query:
            clean_query = {
                key: value
                for key, value in query.items()
                if value is not None and value != ""
            }
            if clean_query:
                url = f"{url}?{urlencode(clean_query, doseq=True)}"
        return url

    def get_json(self, path: str, query: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.endpoint(path, query)
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        return self._json_request(request, "GET", url)

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.endpoint(path)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return self._json_request(request, "POST", url)

    def stream_turn(self, session_id: str, text: str, *, skills: list[str]) -> Iterable[dict[str, Any]]:
        url = self.endpoint("/agent/turn")
        body = json.dumps({
            "sessionId": session_id,
            "text": text,
            **({"skills": skills} if skills else {}),
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "Accept": "application/x-ndjson",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise CliError(f"Invalid NDJSON event from runtime: {line}") from error
                    if isinstance(parsed, dict):
                        yield parsed
        except HTTPError as error:
            raise self._http_error("POST", url, error) from error
        except URLError as error:
            raise CliError(f"Could not reach Amadeus runtime at {self.runtime_url}: {error.reason}") from error

    def _json_request(self, request: Request, method: str, url: str) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as error:
            raise self._http_error(method, url, error) from error
        except URLError as error:
            raise CliError(f"Could not reach Amadeus runtime at {self.runtime_url}: {error.reason}") from error

        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError as error:
            raise CliError(f"{method} {url} returned non-JSON response: {body[:200]}") from error
        if not isinstance(payload, dict):
            raise CliError(f"{method} {url} returned non-object JSON")
        return payload

    @staticmethod
    def _http_error(method: str, url: str, error: HTTPError) -> RuntimeHttpError:
        try:
            payload = json.loads(error.read().decode("utf-8") or "{}")
        except Exception:
            payload = {"error": error.reason}
        if not isinstance(payload, dict):
            payload = {"error": str(payload)}
        return RuntimeHttpError(method, url, error.code, payload)


def normalize_runtime_url(value: str | None) -> str:
    candidate = (value or "").strip() or DEFAULT_RUNTIME_URL
    if not candidate.startswith(("http://", "https://")):
        candidate = f"http://{candidate}"
    return candidate.rstrip("/")


def default_runtime_url() -> str:
    return normalize_runtime_url(
        os.environ.get("AMADEUS_CLI_RUNTIME_URL")
        or os.environ.get("AMADEUS_PYTHON_RUNTIME_URL")
        or DEFAULT_RUNTIME_URL
    )


def default_session_id() -> str:
    return os.environ.get("AMADEUS_CLI_SESSION_ID", DEFAULT_SESSION_ID).strip() or DEFAULT_SESSION_ID


def split_skill_args(values: list[str] | None) -> list[str]:
    skills: list[str] = []
    for value in values or []:
        for part in value.split(","):
            skill = part.strip()
            if skill and skill not in skills:
                skills.append(skill)
    return skills


def read_turn_text(parts: list[str], stdin: TextIO) -> str:
    if parts:
        return " ".join(parts).strip()
    if not stdin.isatty():
        return stdin.read().strip()
    return input("amadeus> ").strip()


def summarize_payload(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def command_ask(args: argparse.Namespace, client: RuntimeClient, stdout: TextIO, stderr: TextIO, stdin: TextIO) -> int:
    text = read_turn_text(args.text, stdin)
    if not text:
        raise CliError("ask requires text via arguments, stdin, or interactive prompt")

    skills = split_skill_args(args.skill)
    assistant_text_parts: list[str] = []
    printed_delta = False
    saw_error = False

    for event in client.stream_turn(args.session_id, text, skills=skills):
        event_type = str(event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        assert isinstance(payload, dict)

        if args.json:
            stdout.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stdout.flush()

        if event_type == "tool.permission.request":
            approved = resolve_permission(args, client, payload, stderr, stdin)
            if not args.json:
                decision = "approved" if approved else "denied"
                stderr.write(f"[permission {decision}] {payload.get('toolName') or 'tool'}\n")
                stderr.flush()
            continue

        if args.json:
            continue

        if event_type == "assistant.delta":
            text_delta = summarize_payload(payload, "text")
            if text_delta:
                assistant_text_parts.append(text_delta)
                stdout.write(text_delta)
                stdout.flush()
                printed_delta = True
            continue

        if event_type == "assistant.message":
            full_text = summarize_payload(payload, "text")
            if full_text and not printed_delta:
                stdout.write(full_text)
                stdout.flush()
            if printed_delta:
                stdout.write("\n")
                stdout.flush()
            continue

        if event_type == "audio.tts-ready" and args.show_audio:
            audio_url = payload.get("audioUrl")
            provider = payload.get("provider")
            stdout.write(f"\n[audio] {provider or 'provider'}: {audio_url}\n")
            stdout.flush()
            continue

        if event_type == "error":
            saw_error = True
            code = summarize_payload(payload, "code") or "runtime_error"
            message = summarize_payload(payload, "message") or json.dumps(payload, ensure_ascii=False)
            stderr.write(f"[error] {code}: {message}\n")
            stderr.flush()

    if not args.json and assistant_text_parts and not printed_delta:
        stdout.write("".join(assistant_text_parts) + "\n")
    return 1 if saw_error else 0


def resolve_permission(args: argparse.Namespace, client: RuntimeClient, payload: dict[str, Any], stderr: TextIO, stdin: TextIO) -> bool:
    request_id = payload.get("requestId")
    if not isinstance(request_id, str) or not request_id:
        return False
    if args.auto_approve:
        approved = True
    elif args.deny_permissions or not stdin.isatty():
        approved = False
    else:
        label = payload.get("displayName") or payload.get("toolName") or "tool"
        reason = payload.get("reason")
        stderr.write(f"\nPermission required for {label}\n")
        if reason:
            stderr.write(f"Reason: {reason}\n")
        stderr.write("Allow? [y/N] ")
        stderr.flush()
        approved = stdin.readline().strip().lower() in {"y", "yes", "allow", "approve"}
    client.post_json("/tools/permission", {"requestId": request_id, "approved": approved})
    return approved


def command_doctor(args: argparse.Namespace, client: RuntimeClient, stdout: TextIO, _stderr: TextIO) -> int:
    del _stderr
    health = client.get_json("/runtime/health")
    skills = client.get_json("/skills/list", {"sessionId": args.session_id})
    tools = client.get_json("/tools/list", {"sessionId": args.session_id})
    memory_count = client.get_json("/memory/count", {"sessionId": args.session_id})
    diagnostics = client.get_json("/memory/context/diagnostics", {"sessionId": args.session_id, "limit": 1})
    audio = client.get_json("/audio/config")
    try:
        tools_config = client.get_json("/tools/config")
    except CliError:
        tools_config = {"ok": False, "mcp": {"enabled": False, "servers": []}}

    summary = build_doctor_summary(
        runtime_url=client.runtime_url,
        session_id=args.session_id,
        health=health,
        skills=skills,
        tools=tools,
        tools_config=tools_config,
        memory_count=memory_count,
        diagnostics=diagnostics,
        audio=audio,
    )
    if args.json:
        stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        print_doctor_summary(summary, stdout)
    return 0 if summary["runtime"]["status"] != "error" else 1


def build_doctor_summary(
    *,
    runtime_url: str,
    session_id: str,
    health: dict[str, Any],
    skills: dict[str, Any],
    tools: dict[str, Any],
    tools_config: dict[str, Any],
    memory_count: dict[str, Any],
    diagnostics: dict[str, Any],
    audio: dict[str, Any],
) -> dict[str, Any]:
    checks = health.get("checks") if isinstance(health.get("checks"), dict) else {}
    model = checks.get("model") if isinstance(checks.get("model"), dict) else {}
    memory = checks.get("memory") if isinstance(checks.get("memory"), dict) else {}
    tool_records = tools.get("tools") if isinstance(tools.get("tools"), list) else []
    schemas = tools.get("schemas") if isinstance(tools.get("schemas"), list) else []
    mcp_tool_count = sum(1 for tool in tool_records if str(tool.get("name") or "").startswith("mcp__"))
    mcp_config = tools_config.get("mcp") if isinstance(tools_config.get("mcp"), dict) else {}
    mcp_servers = mcp_config.get("servers") if isinstance(mcp_config.get("servers"), list) else []
    enabled_mcp_servers = [server for server in mcp_servers if isinstance(server, dict) and server.get("enabled") is not False]
    diagnostics_records = diagnostics.get("diagnostics") if isinstance(diagnostics.get("diagnostics"), list) else []
    latest_diagnostic = diagnostics_records[0] if diagnostics_records and isinstance(diagnostics_records[0], dict) else None

    return {
        "runtime": {
            "url": runtime_url,
            "status": health.get("status", "unknown"),
            "ok": bool(health.get("ok")),
        },
        "session": {
            "id": session_id,
            "memoryMessages": int(memory_count.get("memoryMessages") or 0),
        },
        "model": {
            "provider": model.get("provider", ""),
            "model": model.get("model", ""),
            "apiKeyConfigured": bool(model.get("apiKeyConfigured")),
        },
        "skills": {
            "count": len(skills.get("skills") if isinstance(skills.get("skills"), list) else []),
        },
        "tools": {
            "count": len(tool_records),
            "enabledSchemaCount": len(schemas),
            "mcpToolCount": mcp_tool_count,
            "mcpServerCount": len(enabled_mcp_servers),
            "mcpEnabled": bool(mcp_config.get("enabled")),
        },
        "memory": {
            "status": memory.get("status", "unknown"),
            "messageCount": int(memory.get("messageCount") or 0),
            "memoryItemCount": int(memory.get("memoryItemCount") or 0),
            "pendingReviewCandidateCount": int(memory.get("pendingReviewCandidateCount") or 0),
            "latestContextSourceCount": int((latest_diagnostic or {}).get("sourceCount") or 0),
        },
        "audio": {
            "activeProvider": audio.get("activeProvider", ""),
            "runtimeProvider": audio.get("runtimeProvider", ""),
            "macosAvailable": bool(audio.get("macosAvailable")),
        },
    }


def print_doctor_summary(summary: dict[str, Any], stdout: TextIO) -> None:
    runtime = summary["runtime"]
    session = summary["session"]
    model = summary["model"]
    skills = summary["skills"]
    tools = summary["tools"]
    memory = summary["memory"]
    audio = summary["audio"]
    stdout.write(f"Runtime: {runtime['status']} ({runtime['url']})\n")
    stdout.write(f"Session: {session['id']} ({session['memoryMessages']} messages)\n")
    stdout.write(
        f"Model: {model['provider']} / {model['model']} "
        f"({'api key set' if model['apiKeyConfigured'] else 'api key missing'})\n"
    )
    stdout.write(
        f"Skills: {skills['count']} available\n"
        f"Tools: {tools['enabledSchemaCount']} schemas, {tools['mcpToolCount']} MCP tools "
        f"from {tools['mcpServerCount']} enabled MCP servers\n"
    )
    stdout.write(
        f"Memory: {memory['status']}, {memory['memoryItemCount']} facts, "
        f"{memory['pendingReviewCandidateCount']} pending reviews, "
        f"latest context sources={memory['latestContextSourceCount']}\n"
    )
    stdout.write(
        f"Audio: active={audio['activeProvider']} runtime={audio['runtimeProvider']} "
        f"macOS say={'available' if audio['macosAvailable'] else 'unavailable'}\n"
    )


def command_skills(args: argparse.Namespace, client: RuntimeClient, stdout: TextIO, _stderr: TextIO) -> int:
    del _stderr
    if args.view:
        payload = client.get_json("/skills/view", {"sessionId": args.session_id, "name": args.view})
    else:
        payload = client.get_json("/skills/list", {"sessionId": args.session_id})
    if args.json:
        stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return 0

    if args.view:
        skill = payload.get("skill") if isinstance(payload.get("skill"), dict) else {}
        stdout.write(f"{skill.get('identifier') or args.view}\n")
        stdout.write(f"{skill.get('description') or ''}\n")
        instructions = str(skill.get("instructions") or "")
        if instructions:
            stdout.write("\n" + instructions.rstrip() + "\n")
        return 0

    skills = payload.get("skills") if isinstance(payload.get("skills"), list) else []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        identifier = skill.get("identifier") or skill.get("id") or skill.get("name")
        description = skill.get("description") or skill.get("summary") or ""
        category = skill.get("category") or ""
        stdout.write(f"- {identifier} {f'[{category}]' if category else ''} {description}\n")
    if not skills:
        stdout.write("No skills available.\n")
    return 0


def command_memory(args: argparse.Namespace, client: RuntimeClient, stdout: TextIO, _stderr: TextIO) -> int:
    del _stderr
    payload = {
        "count": client.get_json("/memory/count", {"sessionId": args.session_id}),
        "diagnostics": client.get_json("/memory/context/diagnostics", {"sessionId": args.session_id, "limit": args.limit}),
    }
    if args.query:
        payload["items"] = client.get_json("/memory/items", {
            "query": args.query,
            "scope": args.scope,
            "memoryType": args.memory_type,
            "limit": args.limit,
        })
    if args.json:
        stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return 0

    count = payload["count"].get("memoryMessages", 0)
    diagnostics = payload["diagnostics"].get("diagnostics") if isinstance(payload["diagnostics"].get("diagnostics"), list) else []
    stdout.write(f"Session {args.session_id}: {count} messages, {len(diagnostics)} recent context diagnostics\n")
    if args.query:
        items_payload = payload.get("items") if isinstance(payload.get("items"), dict) else {}
        items = items_payload.get("items") if isinstance(items_payload.get("items"), list) else []
        stdout.write(f"Memory item matches: {len(items)}\n")
        for item in items:
            if isinstance(item, dict):
                stdout.write(f"- [{item.get('scope')}/{item.get('memoryType')}] {item.get('content')}\n")
    return 0


def command_speak(args: argparse.Namespace, client: RuntimeClient, stdout: TextIO, _stderr: TextIO, stdin: TextIO) -> int:
    del _stderr
    text = read_turn_text(args.text, stdin)
    if not text:
        raise CliError("speak requires text via arguments, stdin, or interactive prompt")
    payload = client.post_json("/audio/speak", {
        "text": text,
        **({"voice": args.voice} if args.voice else {}),
        **({"format": args.format} if args.format else {}),
    })
    if args.json:
        stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return 0
    if payload.get("audioUrl"):
        stdout.write(f"Audio: {payload.get('audioUrl')} ({payload.get('provider') or 'provider'}, {payload.get('durationMs')} ms)\n")
    elif payload.get("fallback"):
        stdout.write(f"Audio fallback: {payload.get('reason') or 'tts_provider_unavailable'}\n")
    else:
        stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 0 if payload.get("ok", True) else 1


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-url", default=default_runtime_url(), help="Python runtime URL, default from AMADEUS_CLI_RUNTIME_URL or 127.0.0.1:8790")
    parser.add_argument("--session-id", default=default_session_id(), help="Runtime session id. Defaults to cli:default, not the desktop default session.")
    parser.add_argument("--timeout", type=float, default=120, help="HTTP timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amadeus-cli",
        description="Command-line client for the Amadeus Python runtime.",
    )
    add_common_options(parser)
    subparsers = parser.add_subparsers(dest="command")

    ask = subparsers.add_parser("ask", help="Send one turn to /agent/turn")
    ask.add_argument("text", nargs="*", help="Message text. Reads stdin when omitted.")
    ask.add_argument("--skill", action="append", help="Skill identifier. Can be repeated or comma-separated.")
    ask.add_argument("--auto-approve", action="store_true", help="Automatically approve ask-tool permission requests.")
    ask.add_argument("--deny-permissions", action="store_true", help="Automatically deny ask-tool permission requests.")
    ask.add_argument("--show-audio", action="store_true", help="Print audio.tts-ready URLs in human-readable mode.")
    ask.set_defaults(func=command_ask)

    doctor = subparsers.add_parser("doctor", help="Summarize runtime, Skills, MCP, Memory, and Audio health")
    doctor.set_defaults(func=command_doctor)

    skills = subparsers.add_parser("skills", help="List or view runtime skills")
    skills.add_argument("--view", help="Show one skill's instructions")
    skills.set_defaults(func=command_skills)

    memory = subparsers.add_parser("memory", help="Show memory diagnostics or query typed memory")
    memory.add_argument("--query", help="Search typed memory_items")
    memory.add_argument("--scope", choices=["user", "agent", "project"], help="Typed memory scope filter")
    memory.add_argument("--memory-type", help="Typed memory type filter")
    memory.add_argument("--limit", type=int, default=5, help="Result/diagnostic limit")
    memory.set_defaults(func=command_memory)

    speak = subparsers.add_parser("speak", help="Call /audio/speak for a text snippet")
    speak.add_argument("text", nargs="*", help="Text to synthesize. Reads stdin when omitted.")
    speak.add_argument("--voice", help="Provider voice id/name")
    speak.add_argument("--format", default="wav", help="Requested audio format")
    speak.set_defaults(func=command_speak)

    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    commands = {"ask", "doctor", "skills", "memory", "speak", "-h", "--help"}
    if not argv:
        return ["ask"]
    global_options_with_value = {"--runtime-url", "--session-id", "--timeout"}
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == "--":
            return argv
        if value in global_options_with_value:
            index += 2
            continue
        if any(value.startswith(f"{option}=") for option in global_options_with_value):
            index += 1
            continue
        if value == "--json":
            index += 1
            continue
        if value.startswith("-"):
            return argv
        if value in commands:
            return argv
        return argv[:index] + ["ask"] + argv[index:]
    return argv + ["ask"]


def create_client(args: argparse.Namespace) -> RuntimeClient:
    return RuntimeClient(args.runtime_url, timeout_seconds=args.timeout)


def main(argv: list[str] | None = None, *, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr, stdin: TextIO = sys.stdin) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(list(argv if argv is not None else sys.argv[1:])))
    if not getattr(args, "command", None):
        args.command = "ask"
        args.func = command_ask
    client = create_client(args)
    try:
        if args.command == "ask":
            return args.func(args, client, stdout, stderr, stdin)
        if args.command == "speak":
            return args.func(args, client, stdout, stderr, stdin)
        return args.func(args, client, stdout, stderr)
    except CliError as error:
        stderr.write(f"amadeus-cli: {error}\n")
        return 2
    except KeyboardInterrupt:
        stderr.write("amadeus-cli: interrupted\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
