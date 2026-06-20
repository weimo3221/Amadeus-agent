from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from amadeus.audio import AudioFallbackResult, AudioOutputCommand, AudioRuntime
from amadeus.memory import MessageMemoryStore
from amadeus.tool_runtime import (
    DEFAULT_TOOLS_CONFIG_PATH,
    ToolAuditLog,
    ToolAuditRecord,
    ToolAuditStore,
    ToolContext,
    ToolLoopGuardrail,
    ToolRegistry,
    parse_bool,
    parse_tools_config,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_CONFIG_PATH = DEFAULT_TOOLS_CONFIG_PATH


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
        self.tool_registry = ToolRegistry(config_path=tools_config_path)
        self.tool_audit_log = ToolAuditLog()
        self.tool_audit_store = ToolAuditStore(memory_store.database_path)
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

            guardrail = ToolLoopGuardrail()
            for tool_call in tool_calls:
                for event in self._execute_tool_call(session_id, tool_call, request_permission, history, guardrail):
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
        return self.tool_registry.permission_state()

    def enabled_tool_schemas(self) -> list[dict[str, Any]]:
        return self.tool_registry.enabled_schemas()

    def tool_audit_records(self) -> list[ToolAuditRecord]:
        return self.tool_audit_log.records()

    def persisted_tool_audit_records(self, session_id: str | None = None, limit: int = 100) -> list[ToolAuditRecord]:
        return self.tool_audit_store.load(session_id=session_id, limit=limit)

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
        guardrail: ToolLoopGuardrail,
    ) -> Iterable[AgentEvent]:
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        tool_name = function.get("name") if isinstance(function.get("name"), str) else ""
        tool_call_id = tool_call.get("id") if isinstance(tool_call.get("id"), str) else str(uuid4())
        spec = self.tool_registry.get(tool_name)
        args = self._parse_tool_args(function.get("arguments"))

        yield AgentEvent("assistant.state", {"state": "tool-running"})
        yield AgentEvent("tool.started", {
            "toolName": tool_name,
            "displayName": spec.display_name if spec else f"Running {tool_name}",
        })
        yield self._audit_tool(session_id, tool_name, decision="started")

        guardrail_decision = guardrail.before_call(tool_name, args)
        if not guardrail_decision.allowed:
            result = {"error": guardrail_decision.reason or "Tool call blocked by guardrail"}
            failure_code = guardrail_decision.failure_code or "guardrail_blocked"
            self._record_tool_result(history, tool_call_id, result)
            yield AgentEvent("tool.finished", self._tool_finished_payload(
                tool_name,
                ok=False,
                failure_code=failure_code,
            ))
            yield self._audit_tool(
                session_id,
                tool_name,
                decision="blocked",
                ok=False,
                failure_code=failure_code,
                detail=result["error"],
            )
            return

        if not spec:
            result = {"error": f"Unknown tool: {tool_name}"}
            guardrail.after_call(tool_name, args, result, False)
            self._record_tool_result(history, tool_call_id, result)
            yield AgentEvent("tool.finished", self._tool_finished_payload(
                tool_name,
                ok=False,
                failure_code="unknown_tool",
            ))
            yield self._audit_tool(
                session_id,
                tool_name,
                decision="failed",
                ok=False,
                failure_code="unknown_tool",
                detail=result["error"],
            )
            return

        if not spec.enabled:
            result = {"error": f"Tool is disabled: {tool_name}"}
            guardrail.after_call(tool_name, args, result, False)
            self._record_tool_result(history, tool_call_id, result)
            yield AgentEvent("tool.finished", self._tool_finished_payload(
                tool_name,
                ok=False,
                failure_code="tool_disabled",
            ))
            yield self._audit_tool(
                session_id,
                tool_name,
                decision="denied",
                ok=False,
                failure_code="tool_disabled",
                detail=result["error"],
            )
            return

        if spec.permission == "deny":
            result = {"error": f"Permission denied for tool: {tool_name}"}
            guardrail.after_call(tool_name, args, result, False)
            self._record_tool_result(history, tool_call_id, result)
            yield AgentEvent("tool.finished", self._tool_finished_payload(
                tool_name,
                ok=False,
                failure_code="permission_denied",
            ))
            yield self._audit_tool(
                session_id,
                tool_name,
                decision="denied",
                ok=False,
                failure_code="permission_denied",
                detail=result["error"],
            )
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
                guardrail.after_call(tool_name, args, result, False)
                self._record_tool_result(history, tool_call_id, result)
                yield AgentEvent("tool.finished", self._tool_finished_payload(
                    tool_name,
                    ok=False,
                    failure_code="permission_denied",
                ))
                yield self._audit_tool(
                    session_id,
                    tool_name,
                    decision="denied",
                    ok=False,
                    failure_code="permission_denied",
                    detail=result["error"],
                )
                return

        result = self.tool_registry.execute(
            tool_name,
            args,
            ToolContext(session_id=session_id, cwd=REPO_ROOT),
        )
        guardrail.after_call(tool_name, args, result.output, result.ok)
        self._record_tool_result(history, tool_call_id, result.model_output)
        yield AgentEvent("tool.finished", self._tool_finished_payload(
            tool_name,
            ok=result.ok,
            duration_ms=result.duration_ms,
            failure_code=result.failure_code,
            result_preview=result.output_preview,
            output_truncated=result.output_truncated,
        ))
        yield self._audit_tool(
            session_id,
            tool_name,
            decision="finished",
            ok=result.ok,
            duration_ms=result.duration_ms,
            failure_code=result.failure_code,
            detail=result.output.get("error") if isinstance(result.output.get("error"), str) else None,
        )

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

    def _build_system_prompt(self) -> str:
        return "\n".join([
            "You are Amadeus, a desktop Live2D companion agent.",
            "Reply in the same language as the user unless they ask otherwise.",
            "Be concise, practical, and calm.",
            "You can use safe local tools for current time, dice rolls, searching project files, and reading bounded project text files.",
            "When the user asks for the current time, current date, today, now, or scheduling context, you must call get_current_time before answering.",
            "When the user asks to roll dice or generate a dice result, call roll_dice.",
            "When the user asks to find local project files, docs, code, configuration, or notes, call search_files.",
            "When the user needs the contents of a specific found text file, call read_file.",
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

    @staticmethod
    def _record_tool_result(history: list[dict[str, Any]], tool_call_id: str, result: dict[str, Any]) -> None:
        history.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result, ensure_ascii=False),
        })

    @staticmethod
    def _tool_finished_payload(
        tool_name: str,
        ok: bool,
        duration_ms: int | None = None,
        failure_code: str | None = None,
        result_preview: str | None = None,
        output_truncated: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"toolName": tool_name, "ok": ok}
        if duration_ms is not None:
            payload["durationMs"] = duration_ms
        if failure_code is not None:
            payload["failureCode"] = failure_code
        if result_preview is not None:
            payload["resultPreview"] = result_preview
        if output_truncated:
            payload["outputTruncated"] = output_truncated
        return payload

    def _audit_tool(
        self,
        session_id: str,
        tool_name: str,
        decision: str,
        ok: bool | None = None,
        duration_ms: int | None = None,
        failure_code: str | None = None,
        detail: str | None = None,
    ) -> AgentEvent:
        record = self.tool_audit_log.append(
            session_id=session_id,
            tool_name=tool_name,
            decision=decision,
            ok=ok,
            duration_ms=duration_ms,
            failure_code=failure_code,
            detail=detail,
        )
        self.tool_audit_store.save(record)
        return AgentEvent("tool.audit", record.to_payload())
