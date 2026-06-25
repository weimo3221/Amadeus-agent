from __future__ import annotations

import json
import logging
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
from amadeus.audio import AudioFallbackResult, AudioOutputCommand, AudioRuntime, LocalAudioLibrary, create_tts_provider_from_config
from amadeus.live2d import LocalLive2DModelLibrary
from amadeus.tool_runtime import ToolContext
from amadeus.tools import list_tools


HOST = os.environ.get("AMADEUS_PYTHON_RUNTIME_HOST", os.environ.get("AMADEUS_PYTHON_TOOLS_HOST", "127.0.0.1"))
PORT = int(os.environ.get("AMADEUS_PYTHON_RUNTIME_PORT", os.environ.get("AMADEUS_PYTHON_TOOLS_PORT", "8790")))
DATABASE_PATH = Path(os.environ.get("AMADEUS_MEMORY_DB", str(REPO_ROOT / "data" / "amadeus.sqlite")))
AUDIO_ROOT = Path(os.environ.get("AMADEUS_AUDIO_ROOT", str(RUNTIME_DIR / "assets" / "audio")))
LIVE2D_ROOT = Path(os.environ.get("AMADEUS_LIVE2D_ROOT", str(REPO_ROOT / "models" / "live2d")))
HARNESSES_CONFIG_PATH = Path(os.environ.get("AMADEUS_HARNESSES_CONFIG", str(REPO_ROOT / "configs" / "harnesses.yaml")))
PUBLIC_BASE_URL = os.environ.get("AMADEUS_PYTHON_RUNTIME_URL", f"http://{HOST}:{PORT}")
LOG_LEVEL = os.environ.get("AMADEUS_LOG_LEVEL", "INFO").upper()
logger = logging.getLogger(__name__)

memory_store = MessageMemoryStore(DATABASE_PATH)
audio_library = LocalAudioLibrary(AUDIO_ROOT, PUBLIC_BASE_URL)
live2d_library = LocalLive2DModelLibrary(LIVE2D_ROOT, PUBLIC_BASE_URL, HARNESSES_CONFIG_PATH)
audio_runtime = AudioRuntime(audio_library, create_tts_provider_from_config(audio_library))
permission_broker = PermissionBroker()
agent_runtime = AgentRuntime(memory_store, audio_runtime)


class RuntimeRequestHandler(BaseHTTPRequestHandler):
    server_version = "AmadeusPythonRuntime/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            logger.info("Handling runtime health request")
            self.write_json(200, {
                "ok": True,
                "runtime": "python",
                "modules": ["agent", "memory", "model", "tools", "skills", "live2d", "audio"],
                "tools": list_tools(),
                "model": agent_runtime.model,
            })
            return

        if parsed.path == "/runtime/health":
            logger.info("Handling structured runtime health request")
            self.write_json(200, build_runtime_health())
            return

        if parsed.path == "/runtime/feedback":
            query = parse_qs(parsed.query)
            session_id = query.get("sessionId", ["default"])[0]
            snapshot = agent_runtime.harness_feedback_snapshot(session_id)
            logger.info(
                "Handling runtime feedback snapshot sessionId=%s recentEventCount=%s",
                session_id,
                snapshot.get("recentEventCount"),
            )
            self.write_json(200, {"ok": True, "feedback": snapshot})
            return

        if parsed.path == "/tools/list":
            logger.info("Handling tools list request")
            self.write_json(200, {
                "ok": True,
                "tools": agent_runtime.tool_permission_state(),
                "schemas": agent_runtime.enabled_tool_schemas(),
            })
            return

        if parsed.path == "/skills/list":
            logger.info("Handling skills list request")
            self.write_json(200, {
                "ok": True,
                "skills": agent_runtime.skill_catalog.skill_summaries(),
            })
            return

        if parsed.path == "/skills/view":
            query = parse_qs(parsed.query)
            name = optional_query_string(query, "name")
            if not name:
                self.write_json(400, {"ok": False, "error": "name is required"})
                return
            skill = agent_runtime.skill_catalog.view_skill(name)
            if skill is None:
                self.write_json(404, {"ok": False, "error": "skill_not_found"})
                return
            self.write_json(200, {"ok": True, "skill": skill})
            return

        if parsed.path == "/tools/audit":
            query = parse_qs(parsed.query)
            session_id = optional_query_string(query, "sessionId")
            tool_name = optional_query_string(query, "toolName")
            decision = optional_query_string(query, "decision")
            failure_code = optional_query_string(query, "failureCode")
            ok = parse_optional_bool(optional_query_string(query, "ok"))
            limit = parse_int(query.get("limit", ["100"])[0], 100, 1, 500)
            records = agent_runtime.query_tool_audit_records(
                session_id=session_id,
                tool_name=tool_name,
                decision=decision,
                ok=ok,
                failure_code=failure_code,
                limit=limit,
            )
            logger.info(
                "Handling tools audit query sessionId=%s toolName=%s decision=%s ok=%s failureCode=%s limit=%s resultCount=%s",
                session_id,
                tool_name,
                decision,
                ok,
                failure_code,
                limit,
                len(records),
            )
            self.write_json(200, {
                "ok": True,
                "records": [record.to_payload() for record in records],
                "count": len(records),
                "filters": {
                    "sessionId": session_id,
                    "toolName": tool_name,
                    "decision": decision,
                    "ok": ok,
                    "failureCode": failure_code,
                    "limit": limit,
                },
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

        if parsed.path == "/memory/context/diagnostics":
            query = parse_qs(parsed.query)
            session_id = query.get("sessionId", ["default"])[0]
            limit = parse_int(query.get("limit", [str(agent_runtime.context_diagnostics_limit)])[0], agent_runtime.context_diagnostics_limit, 1, 200)
            diagnostics = agent_runtime.memory_context_diagnostics(session_id, limit=limit)
            logger.info(
                "Handling memory context diagnostics sessionId=%s limit=%s count=%s",
                session_id,
                limit,
                len(diagnostics),
            )
            self.write_json(200, {
                "ok": True,
                "sessionId": session_id,
                "diagnostics": diagnostics,
                "count": len(diagnostics),
                "filters": {
                    "sessionId": session_id,
                    "limit": limit,
                },
            })
            return

        if parsed.path == "/memory/items":
            query = parse_qs(parsed.query)
            scope = optional_query_string(query, "scope")
            search_query = optional_query_string(query, "query")
            include_deleted = parse_optional_bool(optional_query_string(query, "includeDeleted")) or False
            limit = parse_int(query.get("limit", ["20"])[0], 20, 1, 100)
            items = memory_store.list_memory_items(
                scope=scope,
                query=search_query,
                include_deleted=include_deleted,
                limit=limit,
            )
            logger.info(
                "Handling memory items list scope=%s queryChars=%s includeDeleted=%s count=%s",
                scope,
                len(search_query or ""),
                include_deleted,
                len(items),
            )
            self.write_json(200, {
                "ok": True,
                "items": items,
                "filters": {
                    "scope": scope,
                    "query": search_query,
                    "includeDeleted": include_deleted,
                    "limit": limit,
                },
            })
            return

        if parsed.path == "/memory/review/candidates":
            query = parse_qs(parsed.query)
            session_id = optional_query_string(query, "sessionId")
            status = optional_query_string(query, "status")
            scope = optional_query_string(query, "scope")
            limit = parse_int(query.get("limit", ["50"])[0], 50, 1, 200)
            candidates = memory_store.list_memory_review_candidates(
                session_id=session_id,
                status=status,
                scope=scope,
                limit=limit,
            )
            logger.info(
                "Handling memory review candidates list sessionId=%s status=%s scope=%s count=%s",
                session_id,
                status,
                scope,
                len(candidates),
            )
            self.write_json(200, {
                "ok": True,
                "candidates": candidates,
                "filters": {
                    "sessionId": session_id,
                    "status": status,
                    "scope": scope,
                    "limit": limit,
                },
            })
            return

        if parsed.path == "/memory/review/jobs":
            query = parse_qs(parsed.query)
            session_id = optional_query_string(query, "sessionId")
            status = optional_query_string(query, "status")
            limit = parse_int(query.get("limit", ["20"])[0], 20, 1, 200)
            jobs = memory_store.list_memory_review_jobs(
                session_id=session_id,
                status=status,
                limit=limit,
            )
            logger.info(
                "Handling memory review jobs list sessionId=%s status=%s count=%s",
                session_id,
                status,
                len(jobs),
            )
            self.write_json(200, {
                "ok": True,
                "jobs": jobs,
                "filters": {
                    "sessionId": session_id,
                    "status": status,
                    "limit": limit,
                },
            })
            return

        if parsed.path == "/memory/summary":
            query = parse_qs(parsed.query)
            session_id = query.get("sessionId", ["default"])[0]
            summary = memory_store.load_conversation_summary(session_id)
            logger.info("Handling memory summary load sessionId=%s found=%s", session_id, summary is not None)
            self.write_json(200, {"ok": True, "sessionId": session_id, "summary": summary})
            return

        if parsed.path == "/memory/search":
            query = parse_qs(parsed.query)
            search_query = query.get("query", [""])[0]
            session_id = query.get("sessionId", ["default"])[0]
            include_all_sessions = query.get("includeAllSessions", ["false"])[0] == "true"
            limit = parse_int(query.get("limit", ["10"])[0], 10, 1, 50)
            results = memory_store.search(
                search_query,
                session_id=None if include_all_sessions else session_id,
                limit=limit,
            )
            self.write_json(200, {
                "ok": True,
                "query": search_query,
                "sessionId": None if include_all_sessions else session_id,
                "includeAllSessions": include_all_sessions,
                "results": results,
            })
            return

        if parsed.path.startswith("/audio/files/"):
            self.handle_audio_file(parsed.path.removeprefix("/audio/files/"))
            return

        if parsed.path == "/live2d/config":
            self.handle_live2d_config()
            return

        if parsed.path == "/live2d/models":
            self.handle_live2d_models()
            return

        if parsed.path.startswith("/live2d/models/"):
            self.handle_live2d_model_file(parsed.path.removeprefix("/live2d/models/"))
            return

        self.write_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path == "/runtime/config/reload":
            self.handle_runtime_config_reload()
            return

        if self.path == "/runtime/feedback":
            self.handle_runtime_feedback()
            return

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

        if self.path == "/memory/items":
            self.handle_memory_item_save()
            return

        if self.path == "/memory/items/delete":
            self.handle_memory_item_delete()
            return

        if self.path == "/memory/review/candidates":
            self.handle_memory_review_candidate_save()
            return

        if self.path == "/memory/review/accept":
            self.handle_memory_review_accept()
            return

        if self.path == "/memory/review/reject":
            self.handle_memory_review_reject()
            return

        if self.path == "/live2d/select":
            self.handle_live2d_select()
            return

        if self.path == "/memory/review/run":
            self.handle_memory_review_run()
            return

        if self.path == "/memory/summary":
            self.handle_memory_summary_save()
            return

        if self.path == "/memory/compact":
            self.handle_memory_compact()
            return

        if self.path == "/memory/reset":
            self.handle_memory_reset()
            return

        if self.path == "/audio/speak":
            self.handle_audio_speak()
            return

        self.write_json(404, {"ok": False, "error": "not_found"})

    def handle_runtime_config_reload(self) -> None:
        try:
            result = agent_runtime.reload_runtime_config()
            logger.info(
                "Handled runtime config reload runtimeConfig=%s",
                result.get("runtimeConfig"),
            )
            self.write_json(200, {"ok": True, **result})
        except Exception as error:
            logger.info("Runtime config reload failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_runtime_feedback(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId", "default")
            event_type = body.get("type")
            payload = body.get("payload")
            timestamp = body.get("timestamp")
            client_id = body.get("clientId")
            surface = body.get("surface")

            if not isinstance(session_id, str) or not isinstance(event_type, str) or not isinstance(payload, dict):
                self.write_json(400, {"ok": False, "error": "sessionId, type, and payload must be provided"})
                return
            if timestamp is not None and not isinstance(timestamp, str):
                self.write_json(400, {"ok": False, "error": "timestamp must be a string when provided"})
                return
            if client_id is not None and not isinstance(client_id, str):
                self.write_json(400, {"ok": False, "error": "clientId must be a string when provided"})
                return
            if surface is not None and not isinstance(surface, str):
                self.write_json(400, {"ok": False, "error": "surface must be a string when provided"})
                return

            snapshot = agent_runtime.observe_harness_feedback(
                session_id,
                event_type,
                payload,
                timestamp=timestamp,
                client_id=client_id,
                surface=surface,
            )
            events = [
                event.to_runtime_event(session_id)
                for event in agent_runtime.harness_events_for_feedback(session_id, event_type, payload)
            ]
            logger.info(
                "Handled runtime feedback sessionId=%s clientId=%s surface=%s type=%s audioStatus=%s live2dAvailable=%s clientCount=%s emittedEvents=%s",
                session_id,
                client_id,
                surface,
                event_type,
                snapshot.get("audioPlayback", {}).get("status"),
                (snapshot.get("desktopCapabilities") or {}).get("live2d", {}).get("available"),
                (snapshot.get("desktopCapabilities") or {}).get("desktop", {}).get("clientCount"),
                len(events),
            )
            self.write_json(200, {"ok": True, "feedback": snapshot, "events": events})
        except ValueError as error:
            logger.info("Rejecting unsupported runtime feedback error=%s", error)
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Runtime feedback handling failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_agent_turn(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId", "default")
            text = body.get("text")
            raw_skills = body.get("skills")

            if not isinstance(session_id, str) or not isinstance(text, str):
                logger.info("Rejecting malformed agent turn request sessionIdType=%s textType=%s", type(session_id).__name__, type(text).__name__)
                self.write_json(400, {"ok": False, "error": "sessionId and text must be strings"})
                return
            if raw_skills is not None and (
                not isinstance(raw_skills, list)
                or any(not isinstance(skill_name, str) for skill_name in raw_skills)
            ):
                logger.info("Rejecting malformed agent turn skills payload skillsType=%s", type(raw_skills).__name__)
                self.write_json(400, {"ok": False, "error": "skills must be an array of strings when provided"})
                return

            skills = [skill_name for skill_name in (raw_skills or []) if isinstance(skill_name, str)]

            logger.info("Handling agent turn request sessionId=%s textChars=%s skillCount=%s", session_id, len(text), len(skills))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            def request_permission(request: PermissionRequest) -> bool:
                permission_broker.register(request.request_id)
                logger.info(
                    "Streaming tool permission request sessionId=%s requestId=%s toolName=%s",
                    session_id,
                    request.request_id,
                    request.tool_name,
                )
                self.write_event(session_id, "tool.permission.request", {
                    "requestId": request.request_id,
                    "toolName": request.tool_name,
                    "displayName": request.display_name,
                    "reason": request.reason,
                })
                return permission_broker.wait(request.request_id)

            for event in agent_runtime.run_turn(session_id, text, request_permission, active_skills=skills):
                logger.info("Streaming runtime event sessionId=%s type=%s", session_id, event.type)
                self.write_json_line(event.to_runtime_event(session_id))
        except BrokenPipeError:
            logger.info("Agent turn stream closed by client")
            return
        except Exception as error:
            logger.info("Agent turn runtime error error=%s", error)
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
                logger.info("Rejecting malformed tool execute request toolNameType=%s", type(tool_name).__name__)
                self.write_json(400, {"ok": False, "error": "toolName must be a string"})
                return

            logger.info("Handling direct tool execute request toolName=%s argKeys=%s", tool_name, sorted(args.keys()))
            result = agent_runtime.tool_registry.execute(
                tool_name,
                args,
                ToolContext(
                    session_id="default",
                    memory_store=memory_store,
                    tool_name=tool_name,
                    permission_decision="direct_execute",
                    audit_metadata={"source": "http_tools_execute"},
                ),
            )
            logger.info(
                "Completed direct tool execute request toolName=%s toolOk=%s failureCode=%s",
                tool_name,
                result.ok,
                result.failure_code,
            )
            self.write_json(200, {
                "ok": True,
                "result": result.output,
                "toolOk": result.ok,
                "failureCode": result.failure_code,
            })
        except KeyError as error:
            logger.info("Direct tool execute unknown tool error=%s", error)
            self.write_json(404, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Direct tool execute runtime error error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_tool_permission(self) -> None:
        try:
            body = self.read_json_body()
            request_id = body.get("requestId")
            approved = body.get("approved")

            if not isinstance(request_id, str) or not isinstance(approved, bool):
                logger.info("Rejecting malformed permission response requestIdType=%s approvedType=%s", type(request_id).__name__, type(approved).__name__)
                self.write_json(400, {"ok": False, "error": "requestId must be a string and approved must be a boolean"})
                return

            resolved = permission_broker.resolve(request_id, approved)
            logger.info("Handled permission response requestId=%s approved=%s resolved=%s", request_id, approved, resolved)
            self.write_json(200, {"ok": True, "resolved": resolved})
        except Exception as error:
            logger.info("Permission response runtime error error=%s", error)
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

    def handle_memory_item_save(self) -> None:
        try:
            body = self.read_json_body()
            scope = body.get("scope")
            content = body.get("content")
            confidence = body.get("confidence", 1.0)
            source_session_id = body.get("sourceSessionId")
            source_message_id = body.get("sourceMessageId")

            if not isinstance(scope, str) or not isinstance(content, str):
                self.write_json(400, {"ok": False, "error": "scope and content must be strings"})
                return
            if not isinstance(confidence, (int, float)):
                self.write_json(400, {"ok": False, "error": "confidence must be a number"})
                return
            if source_session_id is not None and not isinstance(source_session_id, str):
                self.write_json(400, {"ok": False, "error": "sourceSessionId must be a string"})
                return
            if source_message_id is not None and not isinstance(source_message_id, int):
                self.write_json(400, {"ok": False, "error": "sourceMessageId must be an integer"})
                return

            item = memory_store.save_memory_item(
                scope,
                content,
                confidence=float(confidence),
                source_session_id=source_session_id,
                source_message_id=source_message_id,
            )
            logger.info(
                "Saved memory item itemId=%s scope=%s confidence=%s contentChars=%s",
                item["memoryItemId"],
                item["scope"],
                item["confidence"],
                item["charCount"],
            )
            self.write_json(200, {"ok": True, "item": item})
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_memory_item_delete(self) -> None:
        try:
            body = self.read_json_body()
            memory_item_id = body.get("memoryItemId")
            if not isinstance(memory_item_id, int):
                self.write_json(400, {"ok": False, "error": "memoryItemId must be an integer"})
                return

            deleted = memory_store.delete_memory_item(memory_item_id)
            logger.info("Deleted memory item itemId=%s deleted=%s", memory_item_id, deleted)
            self.write_json(200, {"ok": True, "deleted": deleted, "memoryItemId": memory_item_id})
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_memory_review_candidate_save(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId", "default")
            scope = body.get("scope")
            content = body.get("content")
            confidence = body.get("confidence", 0.7)
            reason = body.get("reason")
            scope_reason = body.get("scopeReason")
            safety_labels = body.get("safetyLabels")
            retention_type = body.get("retentionType")
            source_message_start_id = body.get("sourceMessageStartId")
            source_message_end_id = body.get("sourceMessageEndId")

            if not isinstance(session_id, str) or not isinstance(scope, str) or not isinstance(content, str):
                self.write_json(400, {"ok": False, "error": "sessionId, scope, and content must be strings"})
                return
            if not isinstance(confidence, (int, float)):
                self.write_json(400, {"ok": False, "error": "confidence must be a number"})
                return
            if reason is not None and not isinstance(reason, str):
                self.write_json(400, {"ok": False, "error": "reason must be a string"})
                return
            if scope_reason is not None and not isinstance(scope_reason, str):
                self.write_json(400, {"ok": False, "error": "scopeReason must be a string"})
                return
            if safety_labels is not None and (
                not isinstance(safety_labels, list)
                or any(not isinstance(label, str) for label in safety_labels)
            ):
                self.write_json(400, {"ok": False, "error": "safetyLabels must be an array of strings"})
                return
            if retention_type is not None and not isinstance(retention_type, str):
                self.write_json(400, {"ok": False, "error": "retentionType must be a string"})
                return
            if source_message_start_id is not None and not isinstance(source_message_start_id, int):
                self.write_json(400, {"ok": False, "error": "sourceMessageStartId must be an integer"})
                return
            if source_message_end_id is not None and not isinstance(source_message_end_id, int):
                self.write_json(400, {"ok": False, "error": "sourceMessageEndId must be an integer"})
                return

            candidate = memory_store.save_memory_review_candidate(
                session_id,
                scope,
                content,
                confidence=float(confidence),
                reason=reason,
                scope_reason=scope_reason,
                safety_labels=safety_labels,
                retention_type=retention_type,
                source_message_start_id=source_message_start_id,
                source_message_end_id=source_message_end_id,
            )
            logger.info(
                "Saved memory review candidate candidateId=%s sessionId=%s scope=%s confidence=%s duplicate=%s contentChars=%s",
                candidate["candidateId"],
                candidate["sessionId"],
                candidate["scope"],
                candidate["confidence"],
                candidate.get("duplicate"),
                candidate["charCount"],
            )
            self.write_json(200, {"ok": True, "candidate": candidate, "duplicate": bool(candidate.get("duplicate"))})
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_memory_review_accept(self) -> None:
        try:
            body = self.read_json_body()
            candidate_id = body.get("candidateId")
            if not isinstance(candidate_id, int):
                self.write_json(400, {"ok": False, "error": "candidateId must be an integer"})
                return

            result = memory_store.accept_memory_review_candidate(candidate_id)
            logger.info(
                "Accepted memory review candidate candidateId=%s accepted=%s duplicateMemoryItem=%s",
                candidate_id,
                result.get("accepted"),
                result.get("duplicateMemoryItem"),
            )
            self.write_json(200, {"ok": True, **result})
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_memory_review_reject(self) -> None:
        try:
            body = self.read_json_body()
            candidate_id = body.get("candidateId")
            if not isinstance(candidate_id, int):
                self.write_json(400, {"ok": False, "error": "candidateId must be an integer"})
                return

            result = memory_store.reject_memory_review_candidate(candidate_id)
            logger.info(
                "Rejected memory review candidate candidateId=%s rejected=%s",
                candidate_id,
                result.get("rejected"),
            )
            self.write_json(200, {"ok": True, **result})
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_memory_review_run(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId", "default")
            force = body.get("force", True)

            if not isinstance(session_id, str):
                self.write_json(400, {"ok": False, "error": "sessionId must be a string"})
                return
            if not isinstance(force, bool):
                self.write_json(400, {"ok": False, "error": "force must be a boolean"})
                return

            result = agent_runtime.review_memory(session_id, force=force)
            logger.info(
                "Handled memory review run sessionId=%s force=%s reviewed=%s candidateCount=%s",
                session_id,
                force,
                result.get("reviewed"),
                result.get("candidateCount"),
            )
            self.write_json(200, {"ok": True, **result})
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_memory_summary_save(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId", "default")
            content = body.get("content")
            summarized_message_count = body.get("summarizedMessageCount")

            if not isinstance(session_id, str) or not isinstance(content, str):
                self.write_json(400, {"ok": False, "error": "sessionId and content must be strings"})
                return
            if summarized_message_count is not None and not isinstance(summarized_message_count, int):
                self.write_json(400, {"ok": False, "error": "summarizedMessageCount must be an integer"})
                return

            summary = memory_store.save_conversation_summary(
                session_id,
                content,
                summarized_message_count=summarized_message_count,
            )
            logger.info(
                "Saved memory summary sessionId=%s summaryId=%s summarizedMessageCount=%s contentChars=%s",
                session_id,
                summary["summaryId"],
                summary["summarizedMessageCount"],
                summary["charCount"],
            )
            self.write_json(200, {"ok": True, "summary": summary})
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_memory_compact(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId", "default")
            force = body.get("force", True)

            if not isinstance(session_id, str):
                self.write_json(400, {"ok": False, "error": "sessionId must be a string"})
                return
            if not isinstance(force, bool):
                self.write_json(400, {"ok": False, "error": "force must be a boolean"})
                return

            result = agent_runtime.compact_conversation(session_id, force=force)
            logger.info("Handled memory compact sessionId=%s force=%s compacted=%s", session_id, force, result["compacted"])
            self.write_json(200, {"ok": True, **result})
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

        self.write_file_response(file_path)

    def handle_live2d_config(self) -> None:
        selection = live2d_library.configured_model()
        if not selection:
            self.write_json(404, {"ok": False, "error": "live2d_model_not_configured"})
            return

        self.write_json(200, {
            "ok": True,
            "model": {
                "id": selection.model_id,
                "path": selection.relative_path,
                "url": live2d_library.model_url(selection),
                "manifest": live2d_library.read_manifest(selection.relative_path),
            },
            "display": {
                "scale": agent_runtime.desktop_companion_live2d_scale,
                "offsetX": agent_runtime.desktop_companion_live2d_offset_x,
                "offsetY": agent_runtime.desktop_companion_live2d_offset_y,
            },
        })

    def handle_live2d_models(self) -> None:
        selection = live2d_library.configured_model()
        active_model = None
        if selection:
            active_model = {
                "id": selection.model_id,
                "path": selection.relative_path,
                "url": live2d_library.model_url(selection),
                "manifest": live2d_library.read_manifest(selection.relative_path),
            }

        self.write_json(200, {
            "ok": True,
            "models": live2d_library.list_models(),
            "activeModel": active_model,
        })

    def handle_live2d_select(self) -> None:
        try:
            payload = self.read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self.write_json(400, {"ok": False, "error": "invalid_json"})
            return

        model_id = payload.get("modelId")
        if not isinstance(model_id, str):
            self.write_json(400, {"ok": False, "error": "live2d_model_not_found"})
            return

        selection = live2d_library.select_model(model_id)
        if not selection:
            self.write_json(400, {"ok": False, "error": "live2d_model_not_found"})
            return

        self.write_json(200, {
            "ok": True,
            "model": {
                "id": selection.model_id,
                "path": selection.relative_path,
                "url": live2d_library.model_url(selection),
                "manifest": live2d_library.read_manifest(selection.relative_path),
            },
        })

    def handle_live2d_model_file(self, relative_path: str) -> None:
        file_path = live2d_library.resolve_public_path(unquote(relative_path))
        if not file_path:
            self.write_json(404, {"ok": False, "error": "live2d_model_file_not_found"})
            return

        self.write_file_response(file_path)

    def write_file_response(self, file_path: Path) -> None:
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


def optional_query_string(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def parse_optional_bool(value: str | None) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def build_runtime_health() -> dict[str, Any]:
    checks = {
        "runtime": runtime_health_check(),
        "model": model_health_check(),
        "memory": memory_health_check(),
        "tools": tools_health_check(),
        "live2d": live2d_health_check(),
        "audio": audio_health_check(),
        "harnessFeedback": harness_feedback_health_check(),
        "config": config_health_check(),
    }
    status = aggregate_health_status(checks)
    return {
        "ok": True,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }


def aggregate_health_status(checks: dict[str, dict[str, Any]]) -> str:
    statuses = {str(check.get("status") or "unknown") for check in checks.values()}
    if "error" in statuses:
        return "error"
    if "degraded" in statuses:
        return "degraded"
    return "ok"


def runtime_health_check() -> dict[str, Any]:
    return {
        "status": "ok",
        "runtime": "python",
        "serverVersion": RuntimeRequestHandler.server_version,
        "host": HOST,
        "port": PORT,
        "publicBaseUrl": PUBLIC_BASE_URL,
        "repositoryRoot": str(REPO_ROOT),
    }


def model_health_check() -> dict[str, Any]:
    api_key_configured = bool(agent_runtime.api_key)
    return {
        "status": "ok" if api_key_configured else "degraded",
        "provider": agent_runtime.model_client.provider,
        "model": agent_runtime.model,
        "baseUrl": agent_runtime.base_url,
        "streaming": agent_runtime.model_client.config.streaming,
        "apiKeyConfigured": api_key_configured,
    }


def memory_health_check() -> dict[str, Any]:
    try:
        with memory_store.connect() as connection:
            message_count = int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
            memory_item_count = int(connection.execute("SELECT COUNT(*) FROM memory_items WHERE deleted_at IS NULL").fetchone()[0])
            summary_count = int(connection.execute("SELECT COUNT(*) FROM conversation_summaries").fetchone()[0])
            review_candidate_count = int(connection.execute("SELECT COUNT(*) FROM memory_review_candidates WHERE status = 'pending'").fetchone()[0])
        return {
            "status": "ok",
            "databasePath": str(memory_store.database_path),
            "databaseExists": memory_store.database_path.exists(),
            "stableMemoryDir": str(memory_store.stable_memory_dir),
            "messageCount": message_count,
            "memoryItemCount": memory_item_count,
            "summaryCount": summary_count,
            "pendingReviewCandidateCount": review_candidate_count,
            "contextDiagnosticsLimit": agent_runtime.context_diagnostics_limit,
        }
    except Exception as error:
        logger.info("Memory health check failed error=%s", error)
        return {
            "status": "error",
            "databasePath": str(memory_store.database_path),
            "databaseExists": memory_store.database_path.exists(),
            "error": str(error),
        }


def tools_health_check() -> dict[str, Any]:
    try:
        tools = agent_runtime.tool_permission_state()
        schemas = agent_runtime.enabled_tool_schemas()
        permission_counts: dict[str, int] = {}
        for tool in tools:
            permission = str(tool.get("permission") or "unknown")
            permission_counts[permission] = permission_counts.get(permission, 0) + 1
        enabled_count = sum(1 for tool in tools if tool.get("enabled"))
        return {
            "status": "ok",
            "toolCount": len(tools),
            "enabledToolCount": enabled_count,
            "enabledSchemaCount": len(schemas),
            "permissionCounts": permission_counts,
        }
    except Exception as error:
        logger.info("Tools health check failed error=%s", error)
        return {
            "status": "error",
            "error": str(error),
        }


def live2d_health_check() -> dict[str, Any]:
    selection = live2d_library.configured_model()
    if not selection:
        return {
            "status": "degraded",
            "rootDir": str(live2d_library.root_dir),
            "configPath": str(live2d_library.config_path),
            "configured": False,
            "error": "live2d_model_not_configured",
        }

    model_path = live2d_library.resolve_public_path(selection.relative_path)
    return {
        "status": "ok" if model_path else "degraded",
        "rootDir": str(live2d_library.root_dir),
        "configPath": str(live2d_library.config_path),
        "configured": True,
        "model": {
            "id": selection.model_id,
            "path": selection.relative_path,
            "url": live2d_library.model_url(selection),
            "fileExists": model_path is not None,
        },
    }


def audio_health_check() -> dict[str, Any]:
    provider_name = getattr(audio_runtime.tts_provider, "name", "unknown")
    return {
        "status": "disabled" if provider_name == "none" else "ok",
        "audioRoot": str(audio_library.root_dir),
        "cacheDir": str(audio_library.cache_dir),
        "ttsProvider": provider_name,
        "ttsEnabled": provider_name != "none",
    }


def harness_feedback_health_check() -> dict[str, Any]:
    snapshot = agent_runtime.harness_feedback_snapshot("default")
    return {
        "status": "ok",
        "defaultSessionAudioStatus": snapshot.get("audioPlayback", {}).get("status"),
        "defaultSessionLive2DAvailable": (snapshot.get("desktopCapabilities") or {}).get("live2d", {}).get("available"),
        "defaultSessionRecentEventCount": snapshot.get("recentEventCount"),
    }


def config_health_check() -> dict[str, Any]:
    return {
        "status": "ok",
        "runtimeConfig": str(agent_runtime.runtime_config_path),
        "runtimeConfigExists": agent_runtime.runtime_config_path.exists(),
        "harnessesConfig": str(live2d_library.config_path),
        "harnessesConfigExists": live2d_library.config_path.exists(),
        "effectiveRuntimeConfig": agent_runtime._runtime_config_snapshot(),
    }


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = ThreadingHTTPServer((HOST, PORT), RuntimeRequestHandler)
    logger.info("Amadeus runtime starting host=%s port=%s database=%s audioRoot=%s", HOST, PORT, DATABASE_PATH, AUDIO_ROOT)
    print(f"Amadeus runtime listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
