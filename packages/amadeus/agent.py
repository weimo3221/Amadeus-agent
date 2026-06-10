from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from amadeus.audio import AudioFallbackResult, AudioOutputCommand, AudioRuntime
from amadeus.memory import MessageMemoryStore
from amadeus.tools import ToolSpec, execute_tool, list_tool_specs


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_CONFIG_PATH = REPO_ROOT / "configs" / "tools.yaml"


@dataclass(frozen=True)
class AgentEvent:
    type: str
    payload: dict[str, Any]

    def to_runtime_event(self, session_id: str) -> dict[str, Any]:
        return {
            "id": str(uuid4()),
            "type": self.type,
            "sessionId": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": self.payload,
        }


@dataclass(frozen=True)
class PermissionRequest:
    request_id: str
    tool_name: str
    display_name: str
    reason: str


PermissionRequester = Callable[[PermissionRequest], bool]


class PermissionBroker:
    def __init__(self) -> None:
        self._pending: dict[str, tuple[threading.Event, bool | None]] = {}
        self._lock = threading.Lock()

    def register(self, request_id: str) -> None:
        with self._lock:
            self._pending[request_id] = (threading.Event(), None)

    def wait(self, request_id: str, timeout_seconds: float = 30) -> bool:
        with self._lock:
            pending = self._pending.get(request_id)
            if pending:
                event = pending[0]
            else:
                event = threading.Event()
                self._pending[request_id] = (event, None)

        approved = False
        try:
            if event.wait(timeout_seconds):
                with self._lock:
                    approved = bool(self._pending.get(request_id, (event, False))[1])
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

        return approved

    def resolve(self, request_id: str, approved: bool) -> bool:
        with self._lock:
            pending = self._pending.get(request_id)
            if not pending:
                return False

            event, _approval = pending
            self._pending[request_id] = (event, approved)
            event.set()
            return True


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    if not path.exists():
        return

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class AgentRuntime:
    def __init__(
        self,
        memory_store: MessageMemoryStore,
        audio_runtime: AudioRuntime | None = None,
        tools_config_path: Path = TOOLS_CONFIG_PATH,
    ) -> None:
        load_dotenv()
        self.memory_store = memory_store
        self.audio_runtime = audio_runtime
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.model = os.environ.get("OPENAI_MODEL", "deepseek-v4-flash")
        self.tool_specs = self._load_tool_specs(tools_config_path)
        self.system_prompt = self._build_system_prompt()

    def run_turn(
        self,
        session_id: str,
        user_text: str,
        request_permission: PermissionRequester,
    ) -> Iterable[AgentEvent]:
        normalized_text = user_text.strip()
        if not normalized_text:
            yield AgentEvent("error", {"code": "empty_message", "message": "Message text is required."})
            return

        if not self.api_key:
            yield AgentEvent("error", {"code": "missing_api_key", "message": "OPENAI_API_KEY is not configured."})
            return

        history = self._load_history(session_id)
        history.append({"role": "user", "content": normalized_text})
        self.memory_store.save(session_id, "user", normalized_text)
        yield AgentEvent("memory.updated", {"memoryMessages": self.memory_store.count(session_id)})

        yield AgentEvent("assistant.state", {"state": "thinking"})
        yield AgentEvent("character.behavior", {
            "emotion": "focused",
            "expression": "serious",
            "motion": "think",
            "intensity": 0.6,
        })

        try:
            tool_decision = self._request_tool_decision(history)
        except RuntimeError as error:
            yield AgentEvent("assistant.state", {"state": "error"})
            yield AgentEvent("error", {"code": "provider_error", "message": str(error)})
            return

        tool_calls = tool_decision.get("tool_calls") or []
        if tool_calls:
            history.append({
                "role": "assistant",
                "content": tool_decision.get("content") or "",
                "tool_calls": tool_calls,
            })

            for tool_call in tool_calls:
                for event in self._execute_tool_call(session_id, tool_call, request_permission, history):
                    yield event

        try:
            yield AgentEvent("assistant.state", {"state": "speaking"})
            yield AgentEvent("character.behavior", {
                "emotion": "neutral",
                "expression": "smile",
                "motion": "talk",
                "intensity": 0.5,
            })
            assistant_text = ""
            for delta in self._stream_final_response(history):
                assistant_text += delta
                yield AgentEvent("assistant.delta", {"text": delta})
        except RuntimeError as error:
            yield AgentEvent("assistant.state", {"state": "error"})
            yield AgentEvent("error", {"code": "provider_error", "message": str(error)})
            return

        history.append({"role": "assistant", "content": assistant_text})
        self.memory_store.save(session_id, "assistant", assistant_text)
        yield AgentEvent("memory.updated", {"memoryMessages": self.memory_store.count(session_id)})
        yield AgentEvent("assistant.message", {"text": assistant_text})

        if self.audio_runtime:
            audio_result = self.audio_runtime.speak(AudioOutputCommand(text=assistant_text, format="wav"))
            if not isinstance(audio_result, AudioFallbackResult):
                yield AgentEvent("audio.tts-ready", {
                    "audioUrl": audio_result.audio_url,
                    "durationMs": audio_result.duration_ms,
                })

        yield AgentEvent("assistant.state", {"state": "idle"})
        yield AgentEvent("character.behavior", {
            "emotion": "neutral",
            "expression": "neutral",
            "motion": "idle",
            "intensity": 0.4,
        })

    def tool_permission_state(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "displayName": spec.display_name,
                "enabled": spec.enabled,
                "permission": spec.permission,
            }
            for spec in self.tool_specs.values()
        ]

    def enabled_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            spec.schema
            for spec in self.tool_specs.values()
            if spec.enabled and spec.permission != "deny"
        ]

    def _load_history(self, session_id: str, limit: int = 40) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.memory_store.load(session_id, limit))
        return messages

    def _execute_tool_call(
        self,
        session_id: str,
        tool_call: dict[str, Any],
        request_permission: PermissionRequester,
        history: list[dict[str, Any]],
    ) -> Iterable[AgentEvent]:
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        tool_name = function.get("name") if isinstance(function.get("name"), str) else ""
        tool_call_id = tool_call.get("id") if isinstance(tool_call.get("id"), str) else str(uuid4())
        spec = self.tool_specs.get(tool_name)

        yield AgentEvent("assistant.state", {"state": "tool-running"})
        yield AgentEvent("tool.started", {
            "toolName": tool_name,
            "displayName": spec.display_name if spec else f"Running {tool_name}",
        })

        if not spec:
            result = {"error": f"Unknown tool: {tool_name}"}
            history.append({"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(result)})
            yield AgentEvent("tool.finished", {"toolName": tool_name, "ok": False})
            return

        args = self._parse_tool_args(function.get("arguments"))
        if not spec.enabled:
            result = {"error": f"Tool is disabled: {tool_name}"}
            history.append({"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(result)})
            yield AgentEvent("tool.finished", {"toolName": tool_name, "ok": False})
            return

        if spec.permission == "deny":
            result = {"error": f"Permission denied for tool: {tool_name}"}
            history.append({"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(result)})
            yield AgentEvent("tool.finished", {"toolName": tool_name, "ok": False})
            return

        if spec.permission == "ask":
            request = PermissionRequest(
                request_id=str(uuid4()),
                tool_name=spec.name,
                display_name=spec.display_name,
                reason=spec.describe_request(args),
            )
            approved = request_permission(request)
            if not approved:
                result = {"error": f"Permission denied for tool: {tool_name}"}
                history.append({"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(result)})
                yield AgentEvent("tool.finished", {"toolName": tool_name, "ok": False})
                return

        try:
            result = execute_tool(tool_name, args)
            ok = "error" not in result
        except Exception as error:
            result = {"error": str(error)}
            ok = False

        history.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result, ensure_ascii=False),
        })
        yield AgentEvent("tool.finished", {"toolName": tool_name, "ok": ok})

    def _request_tool_decision(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": self.enabled_tool_schemas(),
            "tool_choice": "auto",
            "stream": False,
            "temperature": 0,
        }
        data = self._post_json("/chat/completions", payload)
        choices = data.get("choices") if isinstance(data.get("choices"), list) else []
        first = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        return message

    def _stream_final_response(self, messages: list[dict[str, Any]]) -> Iterable[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue

                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue

                    try:
                        payload_data = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choices = payload_data.get("choices") if isinstance(payload_data.get("choices"), list) else []
                    first = choices[0] if choices and isinstance(choices[0], dict) else {}
                    delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield content
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Provider returned {error.code}: {body or error.reason}") from error
        except OSError as error:
            raise RuntimeError(str(error)) from error

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
                return data if isinstance(data, dict) else {}
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Provider returned {error.code}: {body or error.reason}") from error
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(str(error)) from error

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _load_tool_specs(self, config_path: Path) -> dict[str, ToolSpec]:
        specs = {spec.name: deepcopy(spec) for spec in list_tool_specs()}
        config = parse_tools_config(config_path)
        for configured_name, entry in config.items():
            tool_name = "get_current_time" if configured_name == "time" else configured_name
            spec = specs.get(tool_name)
            if not spec:
                continue

            enabled = entry.get("enabled")
            if isinstance(enabled, bool):
                spec.enabled = enabled

            permission = entry.get("permission")
            if permission in {"allow", "ask", "deny"}:
                spec.permission = str(permission)

        return specs

    def _build_system_prompt(self) -> str:
        return "\n".join([
            "You are Amadeus, a desktop Live2D companion agent.",
            "Reply in the same language as the user unless they ask otherwise.",
            "Be concise, practical, and calm.",
            "You can use safe local tools for current time, dice rolls, and searching project files.",
            "When the user asks for the current time, current date, today, now, or scheduling context, you must call get_current_time before answering.",
            "When the user asks to roll dice or generate a dice result, call roll_dice.",
            "When the user asks to find local project files, docs, code, configuration, or notes, call local_file_search.",
            "Do not answer current time or date questions from memory or estimation.",
        ])

    @staticmethod
    def _parse_tool_args(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, str):
            return {}

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}

        return parsed if isinstance(parsed, dict) else {}


def parse_tools_config(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    entries: dict[str, dict[str, Any]] = {}
    in_tools = False
    current_tool: str | None = None

    for raw_line in lines:
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        trimmed = line.strip()

        if indent == 0:
            in_tools = trimmed == "tools:"
            current_tool = None
            continue

        if not in_tools:
            continue

        if indent == 2 and trimmed.endswith(":"):
            current_tool = trimmed[:-1]
            entries[current_tool] = {}
            continue

        if indent != 4 or not current_tool or ":" not in trimmed:
            continue

        key, value = trimmed.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key == "enabled":
            entries[current_tool][key] = parse_bool(value)
        elif key == "permission":
            entries[current_tool][key] = value

    return entries


def parse_bool(value: str) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None
