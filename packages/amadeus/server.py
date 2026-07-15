from __future__ import annotations

import json
import logging
import mimetypes
import os
import queue
import sys
import yaml
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
from amadeus.audio import AudioFallbackResult, AudioOutputCommand, AudioRuntime, AudioTranscriptCommand, AudioTranscriptFailure, AsrRuntime, LocalAudioLibrary, MacOsSayConfig, MacOsSayTtsProvider, create_asr_provider_from_config, create_tts_provider_from_config
from amadeus.embeddings import BGE_M3_DIMENSIONS, BGE_M3_MODEL_ID, BGE_M3_PROVIDER_ID, LocalEmbeddingConfig, LocalEmbeddingDeploymentManager, default_bge_m3_model_dir, normalize_embedding_local_dir
from amadeus.live2d import LocalLive2DModelLibrary
from amadeus.memory_embeddings import (
    MemoryEmbeddingBackfillRunner,
    MemoryEmbeddingBackfillService,
    create_local_bge_m3_embedding_provider,
    current_local_bge_m3_embedding_config as resolve_local_bge_m3_embedding_config,
    local_bge_m3_embedding_is_configured as resolve_local_bge_m3_embedding_is_configured,
)
from amadeus.mcp import McpServerConfig, list_mcp_tools
from amadeus.model import PROVIDER_PRESETS, parse_bool_value, parse_positive_int_value, parse_providers_config, parse_reasoning_effort, provider_profile
from amadeus.orchestrator import OrchestratorService
from amadeus.runtime_events import RuntimeEventBus
from amadeus.scheduling import ScheduledJobWorker
from amadeus.tool_runtime import ToolContext
from amadeus.tools import list_tools
from amadeus.workers import TaskWorker, build_task_runner


HOST = os.environ.get("AMADEUS_PYTHON_RUNTIME_HOST", os.environ.get("AMADEUS_PYTHON_TOOLS_HOST", "127.0.0.1"))
PORT = int(os.environ.get("AMADEUS_PYTHON_RUNTIME_PORT", os.environ.get("AMADEUS_PYTHON_TOOLS_PORT", "8790")))
DATABASE_PATH = Path(os.environ.get("AMADEUS_MEMORY_DB", str(REPO_ROOT / "data" / "amadeus.sqlite")))
AUDIO_ROOT = Path(os.environ.get("AMADEUS_AUDIO_ROOT", str(RUNTIME_DIR / "assets" / "audio")))
LIVE2D_ROOT = Path(os.environ.get("AMADEUS_LIVE2D_ROOT", str(REPO_ROOT / "models" / "live2d")))
EMBEDDING_MODELS_ROOT = Path(os.environ.get("AMADEUS_EMBEDDING_MODELS_ROOT", str(REPO_ROOT / "models" / "embeddings")))
HARNESSES_CONFIG_PATH = Path(os.environ.get("AMADEUS_HARNESSES_CONFIG", str(REPO_ROOT / "configs" / "harnesses.yaml")))
PROVIDERS_CONFIG_PATH = REPO_ROOT / "configs" / "providers.yaml"
TOOLS_CONFIG_PATH = REPO_ROOT / "configs" / "tools.yaml"
ENV_CONFIG_PATH = REPO_ROOT / ".env"
PUBLIC_BASE_URL = os.environ.get("AMADEUS_PYTHON_RUNTIME_URL", f"http://{HOST}:{PORT}")
LOG_LEVEL = os.environ.get("AMADEUS_LOG_LEVEL", "INFO").upper()
logger = logging.getLogger(__name__)

RUNTIME_CONFIG_FIELDS: dict[str, dict[str, tuple[type, float | int | None, float | int | None]]] = {
    "context": {
        "maxTokens": (int, 1000, None),
        "compactionTriggerRatio": (float, 0.1, 1.0),
        "recentMessageTargetRatio": (float, 0.1, 0.9),
        "summaryChars": (int, 100, None),
        "memoryItemLimit": (int, 1, None),
        "memoryItemChars": (int, 50, None),
        "retrievalLimit": (int, 1, None),
        "retrievalSnippetChars": (int, 50, None),
        "taskLimit": (int, 1, None),
        "recentTaskLimit": (int, 1, None),
        "taskResultChars": (int, 50, None),
        "diagnosticsLimit": (int, 1, None),
    },
    "summary": {
        "triggerMessageCount": (int, 1, None),
        "keepRecentTurns": (int, 1, None),
        "minKeepRecentTurns": (int, 0, None),
        "keepRecentMessages": (int, 1, None),
        "minKeepRecentMessages": (int, 0, None),
        "sourceMaxMessages": (int, 1, None),
        "failureCooldownSeconds": (int, 1, None),
    },
    "memoryReview": {
        "triggerMessageCount": (int, 1, None),
        "sourceMaxMessages": (int, 1, None),
        "existingMemoryLimit": (int, 1, None),
        "pendingLimit": (int, 1, None),
        "maxCandidates": (int, 1, None),
        "successCooldownSeconds": (int, 1, None),
        "failureCooldownSeconds": (int, 1, None),
    },
    "desktop": {
        "companionLive2dScale": (float, 0.25, 2.5),
        "companionLive2dOffsetX": (int, None, None),
        "companionLive2dOffsetY": (int, None, None),
    },
}
RUNTIME_CONFIG_FIELD_ALIASES: dict[tuple[str, str], str] = {
    ("summary", "keepRecentMessages"): "keepRecentTurns",
    ("summary", "minKeepRecentMessages"): "minKeepRecentTurns",
}

memory_store = MessageMemoryStore(DATABASE_PATH, default_workspace_path=REPO_ROOT)
audio_library = LocalAudioLibrary(AUDIO_ROOT, PUBLIC_BASE_URL)
live2d_library = LocalLive2DModelLibrary(LIVE2D_ROOT, PUBLIC_BASE_URL, HARNESSES_CONFIG_PATH)
audio_runtime = AudioRuntime(audio_library, create_tts_provider_from_config(audio_library))
asr_runtime = AsrRuntime(audio_library, create_asr_provider_from_config(audio_library))
embedding_deployment_manager = LocalEmbeddingDeploymentManager(
    repo_root=REPO_ROOT,
    default_model_dir=default_bge_m3_model_dir(REPO_ROOT),
)
memory_embedding_backfill_runner = MemoryEmbeddingBackfillRunner()
permission_broker = PermissionBroker()
agent_runtime = AgentRuntime(memory_store, audio_runtime)
runtime_event_bus = RuntimeEventBus()
TASK_RUNNER_KIND = os.environ.get("AMADEUS_TASK_RUNNER", "in_process")
TASK_MAX_WORKERS = int(os.environ.get("AMADEUS_TASK_MAX_WORKERS", "2"))


def publish_task_update(task: dict[str, object], action: str) -> None:
    runtime_event_bus.publish(
        "task.updated",
        str(task["sessionId"]),
        {
            "task": task,
            "action": action,
        },
    )


def publish_task_graph_update(root_task_id: str, action: str) -> None:
    try:
        task = memory_store.get_task(root_task_id)
        if task is not None:
            publish_task_update(task, action)
    except Exception as error:
        logger.info("Failed to publish task graph update rootTaskId=%s action=%s error=%s", root_task_id, action, error)


def publish_scheduled_job_update(job: dict[str, object], action: str) -> None:
    session_id = str(job.get("sessionId") or "companion:default")
    if action == "message":
        runtime_event_bus.publish("assistant.message", session_id, {"text": str(job.get("message") or "")})
        return
    runtime_event_bus.publish(
        "scheduled.updated",
        session_id,
        {
            "job": job,
            "action": action,
        },
    )


task_worker = TaskWorker(
    lambda: memory_store,
    lambda: agent_runtime,
    publish_task_event=publish_task_update,
    max_workers=TASK_MAX_WORKERS,
    runner_kind=TASK_RUNNER_KIND,
    runner=build_task_runner(TASK_RUNNER_KIND, max_workers=TASK_MAX_WORKERS),
)
agent_runtime.set_task_worker(task_worker)
task_worker.recover()
orchestrator_service = OrchestratorService(memory_store, submit_task=task_worker.submit, model_client=agent_runtime.model_client)
scheduled_job_worker = ScheduledJobWorker(
    lambda: memory_store,
    publish_job_event=publish_scheduled_job_update,
    submit_task=task_worker.submit,
)
scheduled_job_worker.start()


class RuntimeRequestHandler(BaseHTTPRequestHandler):
    server_version = "AmadeusPythonRuntime/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            logger.info("Handling runtime health request")
            self.write_json(200, {
                "ok": True,
                "runtime": "python",
                "modules": ["agent", "memory", "model", "tools", "skills", "tasks", "live2d", "audio"],
                "tools": list_tools(),
                "model": agent_runtime.model,
            })
            return

        if parsed.path == "/runtime/health":
            logger.info("Handling structured runtime health request")
            self.write_json(200, build_runtime_health())
            return

        if parsed.path == "/runtime/config":
            logger.info("Handling runtime config request")
            self.write_json(200, build_runtime_config_payload())
            return

        if parsed.path == "/runtime/events":
            self.handle_runtime_events(parsed)
            return

        if parsed.path == "/roles":
            query = parse_qs(parsed.query)
            include_archived = parse_optional_bool(optional_query_string(query, "includeArchived")) or False
            logger.info("Handling roles list includeArchived=%s", include_archived)
            self.write_json(200, {"ok": True, "roles": memory_store.list_roles(include_archived=include_archived)})
            return

        if parsed.path.startswith("/roles/") and parsed.path.endswith("/identity"):
            role_id = unquote(parsed.path.removeprefix("/roles/").removesuffix("/identity")).strip()
            self.handle_role_identity_get(role_id)
            return

        if parsed.path == "/sessions":
            query = parse_qs(parsed.query)
            role_id = optional_query_string(query, "roleId")
            include_archived = parse_optional_bool(optional_query_string(query, "includeArchived")) or False
            logger.info("Handling sessions list roleId=%s includeArchived=%s", role_id, include_archived)
            self.write_json(200, {
                "ok": True,
                "sessions": memory_store.list_sessions(role_id=role_id, include_archived=include_archived),
            })
            return

        if parsed.path == "/tasks":
            self.handle_tasks_list(parsed)
            return

        if parsed.path == "/scheduled-jobs":
            self.handle_scheduled_jobs_list(parsed)
            return

        if parsed.path == "/todos":
            self.handle_todos_get(parsed)
            return

        if parsed.path.startswith("/scheduled-jobs/") and parsed.path.endswith("/events"):
            job_id = unquote(parsed.path.removeprefix("/scheduled-jobs/").removesuffix("/events")).strip()
            self.handle_scheduled_job_events_list(job_id, parsed)
            return

        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/events"):
            task_id = unquote(parsed.path.removeprefix("/tasks/").removesuffix("/events")).strip()
            self.handle_task_events_list(task_id, parsed)
            return

        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/graph"):
            task_id = unquote(parsed.path.removeprefix("/tasks/").removesuffix("/graph")).strip()
            self.handle_task_graph_get(task_id)
            return

        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/attempts"):
            task_id = unquote(parsed.path.removeprefix("/tasks/").removesuffix("/attempts")).strip()
            self.handle_task_attempts_list(task_id, parsed)
            return

        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/artifacts"):
            task_id = unquote(parsed.path.removeprefix("/tasks/").removesuffix("/artifacts")).strip()
            self.handle_task_artifacts_list(task_id, parsed)
            return

        if parsed.path.startswith("/sessions/") and parsed.path.endswith("/plan"):
            session_id = unquote(parsed.path.removeprefix("/sessions/").removesuffix("/plan")).strip()
            self.handle_session_plan_get(session_id)
            return

        if parsed.path.startswith("/sessions/") and parsed.path.endswith("/plan-runs"):
            session_id = unquote(parsed.path.removeprefix("/sessions/").removesuffix("/plan-runs")).strip()
            self.handle_session_plan_runs_get(session_id, parsed)
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
            query = parse_qs(parsed.query)
            session_id = optional_query_string(query, "sessionId")
            logger.info("Handling tools list request sessionId=%s", session_id)
            self.write_json(200, {
                "ok": True,
                "tools": agent_runtime.tool_permission_state(session_id),
                "schemas": agent_runtime.enabled_tool_schemas(session_id),
            })
            return

        if parsed.path == "/tools/config":
            logger.info("Handling tools config request")
            self.write_json(200, build_tools_config_payload())
            return

        if parsed.path == "/skills/list":
            query = parse_qs(parsed.query)
            session_id = optional_query_string(query, "sessionId")
            logger.info("Handling skills list request sessionId=%s", session_id)
            self.write_json(200, {
                "ok": True,
                "skills": agent_runtime.skill_summaries(session_id),
            })
            return

        if parsed.path == "/skills/view":
            query = parse_qs(parsed.query)
            name = optional_query_string(query, "name")
            session_id = optional_query_string(query, "sessionId")
            if not name:
                self.write_json(400, {"ok": False, "error": "name is required"})
                return
            skill = agent_runtime.view_skill(name, session_id)
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
            memory_type = optional_query_string(query, "memoryType") or optional_query_string(query, "memory_type")
            search_query = optional_query_string(query, "query")
            metadata_filter = parse_metadata_filter_query(query)
            include_deleted = parse_optional_bool(optional_query_string(query, "includeDeleted")) or False
            limit = parse_int(query.get("limit", ["20"])[0], 20, 1, 100)
            items = memory_store.list_memory_items(
                scope=scope,
                memory_type=memory_type,
                query=search_query,
                metadata_filter=metadata_filter,
                include_deleted=include_deleted,
                limit=limit,
            )
            logger.info(
                "Handling memory items list scope=%s memoryType=%s queryChars=%s includeDeleted=%s count=%s",
                scope,
                memory_type,
                len(search_query or ""),
                include_deleted,
                len(items),
            )
            self.write_json(200, {
                "ok": True,
                "items": items,
                "filters": {
                    "scope": scope,
                    "memoryType": memory_type,
                    "query": search_query,
                    "metadataFilter": metadata_filter,
                    "includeDeleted": include_deleted,
                    "limit": limit,
                },
            })
            return

        if parsed.path == "/memory/items/history":
            query = parse_qs(parsed.query)
            memory_item_id = parse_int(query.get("memoryItemId", ["0"])[0], 0, 0, 2_147_483_647)
            limit = parse_int(query.get("limit", ["50"])[0], 50, 1, 200)
            if memory_item_id <= 0:
                self.write_json(400, {"ok": False, "error": "memoryItemId must be a positive integer"})
                return
            history = memory_store.list_memory_item_history(memory_item_id, limit=limit)
            logger.info("Handling memory item history itemId=%s count=%s", memory_item_id, len(history))
            self.write_json(200, {
                "ok": True,
                "memoryItemId": memory_item_id,
                "history": history,
                "count": len(history),
                "filters": {
                    "memoryItemId": memory_item_id,
                    "limit": limit,
                },
            })
            return

        if parsed.path == "/memory/embedding/config":
            logger.info("Handling memory embedding config request")
            self.write_json(200, build_embedding_config_payload())
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

        if parsed.path == "/audio/voices":
            self.handle_audio_voices()
            return

        if parsed.path == "/audio/config":
            self.handle_audio_config_get()
            return

        if parsed.path == "/live2d/config":
            self.handle_live2d_config()
            return

        if parsed.path == "/live2d/models":
            self.handle_live2d_models()
            return

        if parsed.path == "/live2d/behaviors":
            self.handle_live2d_behaviors_get()
            return

        if parsed.path.startswith("/live2d/models/"):
            self.handle_live2d_model_file(parsed.path.removeprefix("/live2d/models/"))
            return

        self.write_json(404, {"ok": False, "error": "not_found"})

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/runtime/config/reload":
            self.handle_runtime_config_reload()
            return

        if self.path == "/runtime/config":
            self.handle_runtime_config_update()
            return

        if self.path == "/tools/config/test":
            self.handle_tools_config_test()
            return

        if self.path == "/tools/config":
            self.handle_tools_config_update()
            return

        if self.path == "/roles":
            self.handle_role_create()
            return

        if self.path == "/sessions":
            self.handle_session_create()
            return

        if self.path == "/tasks":
            self.handle_task_create()
            return

        if self.path == "/scheduled-jobs":
            self.handle_scheduled_job_create()
            return

        parsed = urlparse(self.path)
        if parsed.path.startswith("/scheduled-jobs/") and parsed.path.endswith("/pause"):
            job_id = unquote(parsed.path.removeprefix("/scheduled-jobs/").removesuffix("/pause")).strip()
            self.handle_scheduled_job_pause(job_id)
            return

        if parsed.path.startswith("/scheduled-jobs/") and parsed.path.endswith("/resume"):
            job_id = unquote(parsed.path.removeprefix("/scheduled-jobs/").removesuffix("/resume")).strip()
            self.handle_scheduled_job_resume(job_id)
            return

        if parsed.path.startswith("/scheduled-jobs/") and parsed.path.endswith("/cancel"):
            job_id = unquote(parsed.path.removeprefix("/scheduled-jobs/").removesuffix("/cancel")).strip()
            self.handle_scheduled_job_cancel(job_id)
            return

        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/cancel"):
            task_id = unquote(parsed.path.removeprefix("/tasks/").removesuffix("/cancel")).strip()
            self.handle_task_cancel(task_id)
            return

        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/resume"):
            task_id = unquote(parsed.path.removeprefix("/tasks/").removesuffix("/resume")).strip()
            self.handle_task_resume(task_id)
            return

        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/approve"):
            task_id = unquote(parsed.path.removeprefix("/tasks/").removesuffix("/approve")).strip()
            self.handle_task_approve(task_id)
            return

        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/decompose"):
            task_id = unquote(parsed.path.removeprefix("/tasks/").removesuffix("/decompose")).strip()
            self.handle_task_decompose(task_id)
            return

        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/dispatch"):
            task_id = unquote(parsed.path.removeprefix("/tasks/").removesuffix("/dispatch")).strip()
            self.handle_task_dispatch(task_id)
            return

        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/synthesize"):
            task_id = unquote(parsed.path.removeprefix("/tasks/").removesuffix("/synthesize")).strip()
            self.handle_task_synthesize(task_id)
            return

        if self.path == "/runtime/feedback":
            self.handle_runtime_feedback()
            return

        if self.path == "/agent/turn":
            self.handle_agent_turn()
            return

        if self.path == "/agent/cancel":
            self.handle_agent_cancel()
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

        if self.path == "/memory/embedding/deploy":
            self.handle_memory_embedding_deploy()
            return

        if self.path == "/memory/embedding/backfill":
            self.handle_memory_embedding_backfill()
            return

        if self.path == "/memory/embedding/cancel":
            self.handle_memory_embedding_cancel()
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

        if self.path == "/live2d/import":
            self.handle_live2d_import()
            return

        if self.path == "/live2d/behaviors":
            self.handle_live2d_behaviors_update()
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

        if self.path == "/audio/transcribe" or self.path.startswith("/audio/transcribe?"):
            self.handle_audio_transcribe()
            return

        if self.path == "/audio/config":
            self.handle_audio_config_update()
            return

        self.write_json(404, {"ok": False, "error": "not_found"})

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/roles/") and parsed.path.endswith("/identity"):
            role_id = unquote(parsed.path.removeprefix("/roles/").removesuffix("/identity")).strip()
            self.handle_role_identity_put(role_id)
            return
        if parsed.path.startswith("/roles/"):
            role_id = unquote(parsed.path.removeprefix("/roles/")).strip()
            self.handle_role_update(role_id)
            return
        if parsed.path.startswith("/sessions/") and parsed.path.endswith("/plan"):
            session_id = unquote(parsed.path.removeprefix("/sessions/").removesuffix("/plan")).strip()
            self.handle_session_plan_put(session_id)
            return
        if parsed.path.startswith("/sessions/"):
            session_id = unquote(parsed.path.removeprefix("/sessions/")).strip()
            self.handle_session_update(session_id)
            return
        if parsed.path == "/todos":
            self.handle_todos_put()
            return
        self.write_json(404, {"ok": False, "error": "not_found"})

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/sessions/"):
            session_id = unquote(parsed.path.removeprefix("/sessions/")).strip()
            self.handle_session_delete(session_id)
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

    def handle_tools_config_update(self) -> None:
        try:
            body = self.read_json_body()
            mcp_payload = body.get("mcp")
            if mcp_payload is None:
                self.write_json(400, {"ok": False, "error": "mcp config is required"})
                return
            if not isinstance(mcp_payload, dict):
                self.write_json(400, {"ok": False, "error": "mcp must be an object"})
                return

            update_mcp_tools_config(mcp_payload)
            reload_result = agent_runtime.reload_tool_registry()
            payload = build_tools_config_payload()
            payload["reloaded"] = reload_result
            logger.info(
                "Handled tools config update mcpEnabled=%s serverCount=%s toolCount=%s",
                payload.get("mcp", {}).get("enabled"),
                len(payload.get("mcp", {}).get("servers", [])),
                reload_result.get("toolCount"),
            )
            self.write_json(200, payload)
        except ValueError as error:
            logger.info("Tools config update rejected error=%s", error)
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Tools config update failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_tools_config_test(self) -> None:
        try:
            body = self.read_json_body()
            server_payload = body.get("server")
            if not isinstance(server_payload, dict):
                self.write_json(400, {"ok": False, "error": "server must be an object"})
                return

            server = validate_mcp_server_payload(server_payload, 0)
            server_config = McpServerConfig(
                name=str(server["name"]),
                url=str(server["url"]),
                enabled=bool(server["enabled"]),
                permission=server.get("permission") if isinstance(server.get("permission"), str) else None,
                timeout_seconds=float(server["timeoutSeconds"]),
            )
            tools = list_mcp_tools(server_config)
            logger.info(
                "Tested MCP server name=%s url=%s toolCount=%s",
                server_config.name,
                server_config.url,
                len(tools),
            )
            self.write_json(200, {
                "ok": True,
                "status": "ok",
                "server": server,
                "toolCount": len(tools),
                "tools": [
                    {
                        "name": str(tool.get("name") or ""),
                        "description": str(tool.get("description") or ""),
                    }
                    for tool in tools
                    if isinstance(tool, dict)
                ],
            })
        except ValueError as error:
            logger.info("MCP config test rejected error=%s", error)
            self.write_json(400, {"ok": False, "status": "failed", "error": str(error)})
        except Exception as error:
            logger.info("MCP config test failed error=%s", error)
            self.write_json(200, {"ok": True, "status": "failed", "error": str(error), "toolCount": 0, "tools": []})

    def handle_runtime_events(self, parsed: Any) -> None:
        query = parse_qs(parsed.query)
        idle_timeout_seconds = parse_int(query.get("idleTimeoutSeconds", ["25"])[0], 25, 1, 300)
        max_events = parse_int(query.get("maxEvents", ["0"])[0], 0, 0, 1000)
        subscriber_id, subscriber_queue = runtime_event_bus.subscribe()
        delivered = 0
        logger.info("Runtime event stream subscribed subscriberId=%s idleTimeoutSeconds=%s maxEvents=%s", subscriber_id, idle_timeout_seconds, max_events)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            while max_events <= 0 or delivered < max_events:
                try:
                    event = subscriber_queue.get(timeout=idle_timeout_seconds)
                except queue.Empty:
                    break
                self.wfile.write((json.dumps(event) + "\n").encode("utf-8"))
                self.wfile.flush()
                delivered += 1
        except (BrokenPipeError, ConnectionResetError):
            logger.info("Runtime event stream disconnected subscriberId=%s delivered=%s", subscriber_id, delivered)
        finally:
            runtime_event_bus.unsubscribe(subscriber_id)
            logger.info("Runtime event stream unsubscribed subscriberId=%s delivered=%s", subscriber_id, delivered)

    def handle_role_create(self) -> None:
        try:
            body = self.read_json_body()
            name = body.get("name")
            if not isinstance(name, str):
                self.write_json(400, {"ok": False, "error": "name must be a string"})
                return
            role = memory_store.create_role(
                name,
                description=body.get("description") if isinstance(body.get("description"), str) else None,
                persona=body.get("persona") if isinstance(body.get("persona"), str) else None,
                style=body.get("style") if isinstance(body.get("style"), str) else None,
                provider=body.get("provider") if isinstance(body.get("provider"), str) else None,
                model=body.get("model") if isinstance(body.get("model"), str) else None,
                live2d_model=body.get("live2dModel") if isinstance(body.get("live2dModel"), str) else None,
                tts_voice=body.get("ttsVoice") if isinstance(body.get("ttsVoice"), str) else None,
                workspace_path=body.get("workspacePath") if isinstance(body.get("workspacePath"), str) else None,
                runtime_scope=body.get("runtimeScope") if isinstance(body.get("runtimeScope"), dict) else None,
            )
            session = memory_store.create_session(str(role["id"]))
            logger.info("Created role roleId=%s defaultSessionId=%s", role["id"], session["id"])
            self.write_json(200, {"ok": True, "role": role, "session": session})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Role create failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_role_update(self, role_id: str) -> None:
        try:
            body = self.read_json_body()
            role = memory_store.update_role(
                role_id,
                name=body.get("name") if isinstance(body.get("name"), str) else None,
                description=body.get("description") if isinstance(body.get("description"), str) else None,
                persona=body.get("persona") if isinstance(body.get("persona"), str) else None,
                style=body.get("style") if isinstance(body.get("style"), str) else None,
                provider=body.get("provider") if isinstance(body.get("provider"), str) else None,
                model=body.get("model") if isinstance(body.get("model"), str) else None,
                live2d_model=body.get("live2dModel") if isinstance(body.get("live2dModel"), str) else None,
                tts_voice=body.get("ttsVoice") if isinstance(body.get("ttsVoice"), str) else None,
                workspace_path=body.get("workspacePath") if isinstance(body.get("workspacePath"), str) else None,
                runtime_scope=body.get("runtimeScope") if isinstance(body.get("runtimeScope"), dict) else None,
            )
            logger.info("Updated role roleId=%s", role["id"])
            self.write_json(200, {"ok": True, "role": role})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Role update failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_role_identity_get(self, role_id: str) -> None:
        try:
            identity = memory_store.role_identity(role_id)
            self.write_json(200, {"ok": True, "identity": identity})
        except ValueError as error:
            self.write_json(404, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Role identity load failed roleId=%s error=%s", role_id, error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_role_identity_put(self, role_id: str) -> None:
        try:
            body = self.read_json_body()
            identity = memory_store.update_role_identity(
                role_id,
                name=body.get("name") if isinstance(body.get("name"), str) else None,
                soul_text=body.get("soulText") if isinstance(body.get("soulText"), str) else None,
            )
            logger.info("Updated role identity roleId=%s", identity["roleId"])
            self.write_json(200, {"ok": True, "identity": identity})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Role identity update failed roleId=%s error=%s", role_id, error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_session_create(self) -> None:
        try:
            body = self.read_json_body()
            role_id = body.get("roleId")
            title = body.get("title")
            if not isinstance(role_id, str):
                self.write_json(400, {"ok": False, "error": "roleId must be a string"})
                return
            if title is not None and not isinstance(title, str):
                self.write_json(400, {"ok": False, "error": "title must be a string"})
                return
            session = memory_store.create_session(role_id, title)
            logger.info("Created session sessionId=%s roleId=%s", session["id"], session["roleId"])
            self.write_json(200, {"ok": True, "session": session})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Session create failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_session_update(self, session_id: str) -> None:
        try:
            body = self.read_json_body()
            title = body.get("title")
            if not isinstance(title, str):
                self.write_json(400, {"ok": False, "error": "title must be a string"})
                return
            session = memory_store.update_session(session_id, title=title)
            logger.info("Updated session sessionId=%s", session["id"])
            self.write_json(200, {"ok": True, "session": session})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Session update failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_session_delete(self, session_id: str) -> None:
        try:
            session = memory_store.archive_session(session_id)
            logger.info("Archived session sessionId=%s", session["id"])
            self.write_json(200, {"ok": True, "session": session})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Session delete failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_session_plan_get(self, session_id: str) -> None:
        try:
            if not session_id:
                self.write_json(400, {"ok": False, "error": "session id is required"})
                return
            plan = memory_store.load_session_plan(session_id)
            logger.info("Loaded session plan sessionId=%s itemCount=%s", plan["sessionId"], len(plan["items"]))
            self.write_json(200, {"ok": True, "plan": plan})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Session plan load failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_session_plan_runs_get(self, session_id: str, parsed: Any) -> None:
        try:
            if not session_id:
                self.write_json(400, {"ok": False, "error": "session id is required"})
                return
            query = parse_qs(parsed.query)
            limit = parse_int(query.get("limit", ["100"])[0], 100, 1, 200)
            result = memory_store.list_plan_runs(session_id=session_id, limit=limit)
            logger.info("Loaded session plan runs sessionId=%s count=%s", result["sessionId"], result["count"])
            self.write_json(200, {"ok": True, **result})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Session plan runs load failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_session_plan_put(self, session_id: str) -> None:
        try:
            if not session_id:
                self.write_json(400, {"ok": False, "error": "session id is required"})
                return
            body = self.read_json_body()
            items = body.get("items")
            merge = bool(body.get("merge")) if isinstance(body.get("merge"), bool) else False
            if not isinstance(items, list):
                self.write_json(400, {"ok": False, "error": "items must be an array"})
                return
            plan = memory_store.save_session_plan(session_id, items, merge=merge)
            logger.info("Saved session plan sessionId=%s itemCount=%s merge=%s", plan["sessionId"], len(plan["items"]), merge)
            self.write_json(200, {"ok": True, "plan": plan})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Session plan save failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_tasks_list(self, parsed: Any) -> None:
        try:
            query = parse_qs(parsed.query)
            session_id = optional_query_string(query, "sessionId")
            status = optional_query_string(query, "status")
            active_only = parse_optional_bool(optional_query_string(query, "activeOnly")) or False
            limit = parse_int(query.get("limit", ["50"])[0], 50, 1, 200)
            result = memory_store.list_tasks(
                session_id=session_id,
                status=status,
                active_only=active_only,
                limit=limit,
            )
            logger.info(
                "Listed tasks sessionId=%s status=%s activeOnly=%s count=%s",
                session_id,
                status,
                active_only,
                len(result["tasks"]),
            )
            self.write_json(200, {"ok": True, **result})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Tasks list failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_task_events_list(self, task_id: str, parsed: Any) -> None:
        try:
            if not task_id:
                self.write_json(400, {"ok": False, "error": "task id is required"})
                return
            query = parse_qs(parsed.query)
            limit = parse_int(query.get("limit", ["100"])[0], 100, 1, 500)
            events = memory_store.list_task_events(task_id, limit=limit)
            logger.info("Listed task events taskId=%s count=%s", task_id, len(events))
            self.write_json(200, {"ok": True, "taskId": task_id, "events": events, "eventCount": len(events)})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Task events list failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_task_graph_get(self, task_id: str) -> None:
        try:
            if not task_id:
                self.write_json(400, {"ok": False, "error": "task id is required"})
                return
            graph = memory_store.get_task_graph(task_id)
            logger.info("Loaded task graph taskId=%s taskCount=%s edgeCount=%s", task_id, len(graph["tasks"]), len(graph["edges"]))
            self.write_json(200, {"ok": True, **graph})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Task graph load failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_task_attempts_list(self, task_id: str, parsed: Any) -> None:
        try:
            if not task_id:
                self.write_json(400, {"ok": False, "error": "task id is required"})
                return
            query = parse_qs(parsed.query)
            limit = parse_int(query.get("limit", ["50"])[0], 50, 1, 200)
            attempts = memory_store.list_task_attempts(task_id, limit=limit)
            logger.info("Listed task attempts taskId=%s count=%s", task_id, len(attempts))
            self.write_json(200, {"ok": True, "taskId": task_id, "attempts": attempts, "attemptCount": len(attempts)})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Task attempts list failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_task_artifacts_list(self, task_id: str, parsed: Any) -> None:
        try:
            if not task_id:
                self.write_json(400, {"ok": False, "error": "task id is required"})
                return
            query = parse_qs(parsed.query)
            limit = parse_int(query.get("limit", ["100"])[0], 100, 1, 200)
            artifacts = memory_store.list_task_artifacts(task_id, limit=limit)
            logger.info("Listed task artifacts taskId=%s count=%s", task_id, len(artifacts))
            self.write_json(200, {"ok": True, "taskId": task_id, "artifacts": artifacts, "artifactCount": len(artifacts)})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Task artifacts list failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_task_decompose(self, task_id: str) -> None:
        try:
            if not task_id:
                self.write_json(400, {"ok": False, "error": "task id is required"})
                return
            body = self.read_json_body()
            auto = bool(body.get("auto"))
            if auto:
                max_children = parse_int(str(body.get("maxChildren") or "6"), 6, 1, 12)
                applied = orchestrator_service.plan_root(task_id, max_children=max_children)
            else:
                graph = body.get("graph")
                if not isinstance(graph, dict):
                    self.write_json(400, {"ok": False, "error": "graph is required"})
                    return
                applied = orchestrator_service.apply_task_graph(task_id, graph)
            if not isinstance(applied, dict):
                self.write_json(400, {"ok": False, "error": "graph is required"})
                return
            dispatched: list[str] = []
            if bool(body.get("dispatch")):
                limit = int(body.get("dispatchLimit") or 20)
                dispatched = orchestrator_service.dispatch_ready(str(applied["rootTaskId"]), limit=max(1, min(100, limit)))
            logger.info(
                "Applied task graph rootTaskId=%s taskCount=%s edgeCount=%s dispatched=%s",
                applied["rootTaskId"],
                len(applied["tasks"]),
                len(applied["edges"]),
                len(dispatched),
            )
            publish_task_graph_update(str(applied["rootTaskId"]), "graph_decomposed")
            if dispatched:
                publish_task_graph_update(str(applied["rootTaskId"]), "graph_dispatched")
            self.write_json(200, {"ok": True, **applied, "dispatchedTaskIds": dispatched})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Task decompose failed taskId=%s error=%s", task_id, error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_task_dispatch(self, task_id: str) -> None:
        try:
            if not task_id:
                self.write_json(400, {"ok": False, "error": "task id is required"})
                return
            body = self.read_json_body()
            limit = int(body.get("limit") or 20)
            task = memory_store.get_task(task_id)
            if task is None:
                self.write_json(400, {"ok": False, "error": "task not found"})
                return
            root_task_id = str(task.get("rootTaskId") or task.get("id"))
            dispatched = orchestrator_service.dispatch_ready(root_task_id, limit=max(1, min(100, limit)))
            logger.info("Dispatched ready task graph children rootTaskId=%s dispatched=%s", root_task_id, len(dispatched))
            if dispatched:
                publish_task_graph_update(root_task_id, "graph_dispatched")
            self.write_json(200, {"ok": True, "rootTaskId": root_task_id, "dispatchedTaskIds": dispatched, "dispatchCount": len(dispatched)})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Task dispatch failed taskId=%s error=%s", task_id, error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_task_synthesize(self, task_id: str) -> None:
        try:
            if not task_id:
                self.write_json(400, {"ok": False, "error": "task id is required"})
                return
            result = orchestrator_service.synthesize_root(task_id)
            logger.info(
                "Synthesized task graph rootTaskId=%s ready=%s completed=%s",
                result.get("rootTaskId"),
                result.get("ready"),
                result.get("completed"),
            )
            publish_task_graph_update(str(result.get("rootTaskId") or task_id), "graph_synthesized")
            self.write_json(200, {"ok": True, **result})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Task synthesize failed taskId=%s error=%s", task_id, error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_task_create(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId")
            title = body.get("title")
            if not isinstance(session_id, str) or not session_id.strip():
                self.write_json(400, {"ok": False, "error": "sessionId is required"})
                return
            if not isinstance(title, str) or not title.strip():
                self.write_json(400, {"ok": False, "error": "title is required"})
                return
            task = memory_store.create_task(
                session_id=session_id,
                title=title,
                body=body.get("body") if isinstance(body.get("body"), str) else None,
                kind=body.get("kind") if isinstance(body.get("kind"), str) else None,
                source=body.get("source") if isinstance(body.get("source"), str) else "api",
                parent_task_id=body.get("parentTaskId") if isinstance(body.get("parentTaskId"), str) else None,
                plan_item_id=body.get("planItemId") if isinstance(body.get("planItemId"), str) else None,
                worker_type=body.get("workerType") if isinstance(body.get("workerType"), str) else None,
                review_required=bool(body.get("reviewRequired")) if isinstance(body.get("reviewRequired"), bool) else False,
                artifacts=body.get("artifacts") if isinstance(body.get("artifacts"), list) else None,
                priority=body.get("priority") if body.get("priority") is not None else None,
                due_at=body.get("dueAt") if isinstance(body.get("dueAt"), str) else None,
                max_attempts=body.get("maxAttempts") if body.get("maxAttempts") is not None else None,
            )
            logger.info("Created task taskId=%s sessionId=%s", task["id"], task["sessionId"])
            task_worker.submit(str(task["id"]))
            self.write_json(201, {
                "ok": True,
                "task": task,
                "event": {
                    "type": "task.updated",
                    "sessionId": task["sessionId"],
                    "payload": {
                        "task": task,
                        "action": "created",
                    },
                },
            })
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Task create failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_task_cancel(self, task_id: str) -> None:
        try:
            if not task_id:
                self.write_json(400, {"ok": False, "error": "task id is required"})
                return
            body = self.read_json_body()
            reason = body.get("reason") if isinstance(body.get("reason"), str) else None
            task = task_worker.cancel(task_id, reason=reason)
            logger.info("Cancelled task taskId=%s sessionId=%s", task["id"], task["sessionId"])
            self.write_json(200, {
                "ok": True,
                "task": task,
                "event": {
                    "type": "task.updated",
                    "sessionId": task["sessionId"],
                    "payload": {
                        "task": task,
                        "action": "cancelled",
                    },
                },
            })
        except ValueError as error:
            status = 404 if str(error) == "task not found" else 400
            self.write_json(status, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Task cancel failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_task_resume(self, task_id: str) -> None:
        try:
            if not task_id:
                self.write_json(400, {"ok": False, "error": "task id is required"})
                return
            task = memory_store.resume_blocked_task(task_id)
            task_worker.submit(str(task["id"]))
            publish_task_update(task, "resumed")
            self.write_json(200, {"ok": True, "task": task})
        except ValueError as error:
            status = 404 if str(error) == "task not found" else 400
            self.write_json(status, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Task resume failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_task_approve(self, task_id: str) -> None:
        try:
            if not task_id:
                self.write_json(400, {"ok": False, "error": "task id is required"})
                return
            task = memory_store.approve_task_review(task_id)
            try:
                memory_store.update_plan_item_status(
                    session_id=str(task["sessionId"]),
                    plan_item_id=str(task.get("planItemId") or ""),
                    status="completed",
                )
            except Exception:
                logger.debug("Task approve failed to sync plan item taskId=%s", task.get("id"), exc_info=True)
            publish_task_update(task, "review_approved")
            self.write_json(200, {"ok": True, "task": task})
        except ValueError as error:
            status = 404 if str(error) == "task not found" else 400
            self.write_json(status, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Task approve failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_scheduled_jobs_list(self, parsed: Any) -> None:
        try:
            query = parse_qs(parsed.query)
            session_id = optional_query_string(query, "sessionId")
            status = optional_query_string(query, "status")
            active_only = parse_optional_bool(optional_query_string(query, "activeOnly")) or False
            limit = parse_int(query.get("limit", ["50"])[0], 50, 1, 200)
            result = memory_store.list_scheduled_jobs(
                session_id=session_id,
                status=status,
                active_only=active_only,
                limit=limit,
            )
            logger.info(
                "Listed scheduled jobs sessionId=%s status=%s activeOnly=%s count=%s",
                session_id,
                status,
                active_only,
                len(result["jobs"]),
            )
            self.write_json(200, {"ok": True, **result})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Scheduled jobs list failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_scheduled_job_events_list(self, job_id: str, parsed: Any) -> None:
        try:
            if not job_id:
                self.write_json(400, {"ok": False, "error": "scheduled job id is required"})
                return
            query = parse_qs(parsed.query)
            limit = parse_int(query.get("limit", ["100"])[0], 100, 1, 500)
            events = memory_store.list_scheduled_job_events(job_id, limit=limit)
            self.write_json(200, {"ok": True, "jobId": job_id, "events": events, "eventCount": len(events)})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Scheduled job events list failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_scheduled_job_create(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId")
            message = body.get("message")
            schedule = body.get("schedule")
            title = body.get("title") if isinstance(body.get("title"), str) else None
            mode = body.get("mode") if isinstance(body.get("mode"), str) else None
            repeat_count = body.get("repeatCount") if body.get("repeatCount") is not None else None
            if not isinstance(session_id, str) or not session_id.strip():
                self.write_json(400, {"ok": False, "error": "sessionId is required"})
                return
            if not isinstance(message, str) or not message.strip():
                self.write_json(400, {"ok": False, "error": "message is required"})
                return
            if not isinstance(schedule, str) or not schedule.strip():
                self.write_json(400, {"ok": False, "error": "schedule is required"})
                return
            job = memory_store.create_scheduled_job(
                session_id=session_id,
                title=title,
                message=message,
                schedule=schedule,
                mode=mode,
                repeat_count=repeat_count if isinstance(repeat_count, int) else None,
            )
            logger.info("Created scheduled job jobId=%s sessionId=%s", job["id"], job["sessionId"])
            self.write_json(201, {
                "ok": True,
                "job": job,
                "event": scheduled_job_event_payload(job, "created"),
            })
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Scheduled job create failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_scheduled_job_pause(self, job_id: str) -> None:
        self.handle_scheduled_job_status_update(job_id, "pause")

    def handle_scheduled_job_resume(self, job_id: str) -> None:
        self.handle_scheduled_job_status_update(job_id, "resume")

    def handle_scheduled_job_cancel(self, job_id: str) -> None:
        self.handle_scheduled_job_status_update(job_id, "cancel")

    def handle_scheduled_job_status_update(self, job_id: str, action: str) -> None:
        try:
            if not job_id:
                self.write_json(400, {"ok": False, "error": "scheduled job id is required"})
                return
            body = self.read_json_body()
            if action == "pause":
                job = memory_store.pause_scheduled_job(job_id)
                event_action = "paused"
            elif action == "resume":
                job = memory_store.resume_scheduled_job(job_id)
                event_action = "resumed"
            elif action == "cancel":
                reason = body.get("reason") if isinstance(body.get("reason"), str) else None
                job = memory_store.cancel_scheduled_job(job_id, reason=reason)
                event_action = "cancelled"
            else:
                self.write_json(400, {"ok": False, "error": "unsupported scheduled job action"})
                return
            self.write_json(200, {
                "ok": True,
                "job": job,
                "event": scheduled_job_event_payload(job, event_action),
            })
        except ValueError as error:
            status = 404 if str(error) == "scheduled job not found" else 400
            self.write_json(status, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Scheduled job status update failed action=%s error=%s", action, error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_todos_get(self, parsed: Any) -> None:
        try:
            query = parse_qs(parsed.query)
            session_id = query.get("sessionId", ["companion:default"])[0]
            active_only = parse_optional_bool(optional_query_string(query, "activeOnly")) or False
            limit = parse_int(query.get("limit", ["100"])[0], 100, 1, 256)
            result = memory_store.list_todos(session_id=session_id, active_only=active_only, limit=limit)
            self.write_json(200, {"ok": True, **result})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Todos get failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_todos_put(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId")
            todos = body.get("todos")
            merge = bool(body.get("merge")) if isinstance(body.get("merge"), bool) else False
            if not isinstance(session_id, str) or not session_id.strip():
                self.write_json(400, {"ok": False, "error": "sessionId is required"})
                return
            if not isinstance(todos, list):
                self.write_json(400, {"ok": False, "error": "todos must be an array"})
                return
            result = memory_store.save_todos(session_id=session_id, todos=todos, merge=merge)
            self.write_json(200, {"ok": True, **result})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Todos put failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_runtime_config_update(self) -> None:
        try:
            body = self.read_json_body()
            api_payload = body.get("api")
            runtime_payload = body.get("runtime")
            updated_api: dict[str, Any] | None = None

            if api_payload is not None:
                if not isinstance(api_payload, dict):
                    self.write_json(400, {"ok": False, "error": "api must be an object"})
                    return
                updated_api = update_api_config(api_payload)

            if runtime_payload is not None:
                if not isinstance(runtime_payload, dict):
                    self.write_json(400, {"ok": False, "error": "runtime must be an object"})
                    return
                update_runtime_config_file(runtime_payload)
                agent_runtime.reload_runtime_config()

            payload = build_runtime_config_payload()
            payload["updatedApi"] = updated_api
            self.write_json(200, payload)
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Runtime config update failed error=%s", error)
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

    def handle_agent_cancel(self) -> None:
        try:
            body = self.read_json_body()
            session_id = body.get("sessionId", "default")
            turn_id = body.get("turnId")
            if not isinstance(session_id, str):
                self.write_json(400, {"ok": False, "error": "sessionId must be a string"})
                return
            if turn_id is not None and not isinstance(turn_id, str):
                self.write_json(400, {"ok": False, "error": "turnId must be a string when provided"})
                return
            result = agent_runtime.cancel_turn(session_id, turn_id=turn_id)
            logger.info(
                "Handled agent cancel sessionId=%s turnId=%s cancelled=%s reason=%s",
                session_id,
                turn_id,
                result.get("cancelled"),
                result.get("reason"),
            )
            self.write_json(200, {"ok": True, **result})
        except Exception as error:
            logger.info("Agent cancel failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_tool_execute(self) -> None:
        try:
            body = self.read_json_body()
            tool_name = body.get("toolName")
            args = body.get("args") if isinstance(body.get("args"), dict) else {}
            session_id = body.get("sessionId") if isinstance(body.get("sessionId"), str) and body.get("sessionId").strip() else "default"

            if not isinstance(tool_name, str):
                logger.info("Rejecting malformed tool execute request toolNameType=%s", type(tool_name).__name__)
                self.write_json(400, {"ok": False, "error": "toolName must be a string"})
                return

            if not agent_runtime.role_allows_tool(session_id, tool_name):
                logger.info("Rejecting direct tool execute by role scope sessionId=%s toolName=%s", session_id, tool_name)
                self.write_json(403, {"ok": False, "error": f"Tool is not enabled for this role: {tool_name}"})
                return

            logger.info("Handling direct tool execute request sessionId=%s toolName=%s argKeys=%s", session_id, tool_name, sorted(args.keys()))
            result = agent_runtime.tool_registry.execute(
                tool_name,
                args,
                ToolContext(
                    session_id=session_id,
                    memory_store=memory_store,
                    memory_embedding_provider=agent_runtime.memory_embedding_provider,
                    memory_vector_candidate_limit=agent_runtime.memory_vector_candidate_limit,
                    task_worker=task_worker,
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
            memory_type = body.get("memoryType", body.get("memory_type"))
            metadata = body.get("metadata")

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
            if memory_type is not None and not isinstance(memory_type, str):
                self.write_json(400, {"ok": False, "error": "memoryType must be a string"})
                return
            if metadata is not None and not isinstance(metadata, dict):
                self.write_json(400, {"ok": False, "error": "metadata must be an object"})
                return

            item = memory_store.save_memory_item(
                scope,
                content,
                confidence=float(confidence),
                source_session_id=source_session_id,
                source_message_id=source_message_id,
                memory_type=memory_type,
                metadata=metadata,
                actor="api",
            )
            logger.info(
                "Saved memory item itemId=%s scope=%s memoryType=%s confidence=%s contentChars=%s",
                item["memoryItemId"],
                item["scope"],
                item["memoryType"],
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

            deleted = memory_store.delete_memory_item(memory_item_id, actor="api")
            logger.info("Deleted memory item itemId=%s deleted=%s", memory_item_id, deleted)
            self.write_json(200, {"ok": True, "deleted": deleted, "memoryItemId": memory_item_id})
        except Exception as error:
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_memory_embedding_deploy(self) -> None:
        try:
            body = self.read_json_body()
            local_dir = normalize_embedding_local_dir(body.get("localDir"), repo_root=REPO_ROOT)
            force = parse_bool_value(body.get("force"), False)
            config = write_local_bge_m3_embedding_config(local_dir)
            agent_runtime.reload_runtime_config()
            payload = embedding_deployment_manager.deploy(config, force=force)
            logger.info(
                "Started memory embedding deploy provider=%s modelId=%s localDir=%s force=%s status=%s phase=%s",
                config.provider,
                config.model_id,
                config.local_dir,
                force,
                payload.get("deployment", {}).get("status") if isinstance(payload.get("deployment"), dict) else "",
                payload.get("deployment", {}).get("phase") if isinstance(payload.get("deployment"), dict) else "",
            )
            self.write_json(202, {"ok": True, **build_embedding_config_payload()})
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Memory embedding deploy failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_memory_embedding_cancel(self) -> None:
        try:
            result = embedding_deployment_manager.cancel()
            logger.info(
                "Cancelled memory embedding deploy cancelled=%s status=%s",
                result.get("cancelled"),
                result.get("deployment", {}).get("status") if isinstance(result.get("deployment"), dict) else "",
            )
            payload = build_embedding_config_payload()
            payload["cancelResult"] = result
            self.write_json(200, payload)
        except Exception as error:
            logger.info("Memory embedding cancel failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_memory_embedding_backfill(self) -> None:
        try:
            body = self.read_json_body()
            limit = parse_int(body.get("limit", 50), 50, 1, 500)
            batch_size = parse_int(body.get("batchSize", 8), 8, 1, 64)
            sync = parse_bool_value(body.get("sync"), False)
            embedding_provider = create_local_bge_m3_embedding_provider(
                providers_config_path=PROVIDERS_CONFIG_PATH,
                repo_root=REPO_ROOT,
            )
            if embedding_provider is None:
                self.write_json(400, {"ok": False, "error": "local BGE-M3 embedding provider is not configured"})
                return
            service = MemoryEmbeddingBackfillService(memory_store, embedding_provider)
            if not embedding_provider.available():
                payload = build_embedding_config_payload()
                payload["ok"] = False
                payload["error"] = "local BGE-M3 embedding provider is not deployed"
                self.write_json(409, payload)
                return
            if sync:
                result = service.backfill(limit=limit, batch_size=batch_size)
                payload = build_embedding_config_payload()
                payload["backfillResult"] = result.to_payload()
                self.write_json(200, payload)
                return
            status = memory_embedding_backfill_runner.start(service, limit=limit, batch_size=batch_size)
            payload = build_embedding_config_payload()
            payload["backfill"] = status
            logger.info(
                "Started memory embedding backfill provider=%s model=%s limit=%s batchSize=%s status=%s",
                embedding_provider.provider,
                embedding_provider.model_id,
                limit,
                batch_size,
                status.get("status"),
            )
            self.write_json(202, payload)
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Memory embedding backfill failed error=%s", error)
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

    def handle_audio_transcribe(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0

        if length <= 0:
            self.write_json(400, {"ok": False, "error": "empty_audio"})
            return

        audio_bytes = self.rfile.read(length)
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        audio_format = query.get("format", ["webm"])[0] or "webm"
        language = query.get("language", [""])[0] or None

        try:
            result = asr_runtime.transcribe(AudioTranscriptCommand(
                audio_bytes=audio_bytes,
                audio_format=audio_format,
                language=language,
            ))
        except Exception as error:
            logger.info("Audio transcription failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})
            return

        if isinstance(result, AudioTranscriptFailure):
            self.write_json(200, {
                "ok": False,
                "text": "",
                "provider": result.provider,
                "reason": result.reason,
            })
            return

        self.write_json(200, {
            "ok": True,
            "text": result.text,
            "provider": result.provider,
            "language": result.language,
            "durationMs": result.duration_ms,
        })

    def handle_audio_file(self, relative_path: str) -> None:
        file_path = audio_library.resolve_public_path(unquote(relative_path))
        if not file_path:
            self.write_json(404, {"ok": False, "error": "audio_not_found"})
            return

        self.write_file_response(file_path)

    def handle_audio_voices(self) -> None:
        try:
            payload = audio_runtime.list_voices()
            self.write_json(200, {"ok": True, **payload})
        except Exception as error:
            logger.info("Audio voices list failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_audio_config_get(self) -> None:
        try:
            self.write_json(200, build_audio_config_payload())
        except Exception as error:
            logger.info("Audio config read failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_audio_config_update(self) -> None:
        try:
            body = self.read_json_body()
            if not isinstance(body, dict):
                self.write_json(400, {"ok": False, "error": "body must be an object"})
                return
            update_audio_config(body)
            payload = build_audio_config_payload()
            payload["voices"] = audio_runtime.list_voices()
            self.write_json(200, payload)
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            logger.info("Audio config update failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

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

        agent_runtime.reload_harness_registry()
        self.write_json(200, {
            "ok": True,
            "model": {
                "id": selection.model_id,
                "path": selection.relative_path,
                "url": live2d_library.model_url(selection),
                "manifest": live2d_library.read_manifest(selection.relative_path),
            },
        })

    def handle_live2d_import(self) -> None:
        try:
            payload = self.read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self.write_json(400, {"ok": False, "error": "invalid_json"})
            return

        source_dir = payload.get("sourceDir")
        if not isinstance(source_dir, str) or not source_dir.strip():
            self.write_json(400, {"ok": False, "error": "sourceDir is required"})
            return

        raw_model_id = payload.get("modelId")
        model_id = raw_model_id.strip() if isinstance(raw_model_id, str) and raw_model_id.strip() else None
        activate = parse_bool_value(payload.get("activate"), True)

        try:
            selection = live2d_library.import_model(source_dir.strip(), model_id=model_id)
            if activate:
                live2d_library.select_model(selection.model_id)
                agent_runtime.reload_harness_registry()
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
            return
        except Exception as error:
            logger.info("Live2D import failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})
            return

        self.write_json(200, {
            "ok": True,
            "model": {
                "id": selection.model_id,
                "path": selection.relative_path,
                "url": live2d_library.model_url(selection),
                "manifest": live2d_library.read_manifest(selection.relative_path),
            },
            "models": live2d_library.list_models(),
        })

    def handle_live2d_behaviors_get(self) -> None:
        try:
            self.write_json(200, build_live2d_behaviors_payload())
        except Exception as error:
            logger.info("Live2D behaviors read failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})

    def handle_live2d_behaviors_update(self) -> None:
        try:
            payload = self.read_json_body()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self.write_json(400, {"ok": False, "error": "invalid_json"})
            return

        behaviors = payload.get("audioPlaybackBehaviors")
        if not isinstance(behaviors, dict):
            self.write_json(400, {"ok": False, "error": "audioPlaybackBehaviors must be an object"})
            return

        try:
            live2d_library.persist_audio_playback_behaviors(behaviors)
            agent_runtime.reload_harness_registry()
        except ValueError as error:
            self.write_json(400, {"ok": False, "error": str(error)})
            return
        except Exception as error:
            logger.info("Live2D behaviors update failed error=%s", error)
            self.write_json(500, {"ok": False, "error": str(error)})
            return

        self.write_json(200, build_live2d_behaviors_payload())

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


def parse_metadata_filter_query(query: dict[str, list[str]]) -> dict[str, object]:
    metadata_filter: dict[str, object] = {}
    raw_filter = optional_query_string(query, "metadataFilter") or optional_query_string(query, "metadata_filter")
    if raw_filter:
        try:
            parsed = json.loads(raw_filter)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            metadata_filter.update(parsed)
    for key, values in query.items():
        if not key.startswith("metadata.") or not values:
            continue
        metadata_key = key[len("metadata."):].strip()
        if not metadata_key:
            continue
        value = values[0].strip()
        if value:
            metadata_filter[metadata_key] = value
    return metadata_filter


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
        "embedding": embedding_health_check(),
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
        "thinkingEnabled": agent_runtime.model_client.config.thinking_enabled,
        "reasoningEffort": agent_runtime.model_client.config.reasoning_effort,
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
            "rolesRoot": str(memory_store.roles_root),
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


def embedding_health_check() -> dict[str, Any]:
    try:
        payload = build_embedding_config_payload()
        embedding = payload["embedding"]
        deployed = bool(embedding.get("deployed")) if isinstance(embedding, dict) else False
        configured = bool(embedding.get("configured")) if isinstance(embedding, dict) else False
        deployment = embedding.get("deployment") if isinstance(embedding, dict) else {}
        active = bool(deployment.get("active")) if isinstance(deployment, dict) else False
        return {
            "status": "ok" if deployed else ("degraded" if configured or active else "disabled"),
            "configured": configured,
            "deployed": deployed,
            "provider": embedding.get("provider") if isinstance(embedding, dict) else "",
            "modelId": embedding.get("modelId") if isinstance(embedding, dict) else "",
            "localDir": embedding.get("localDir") if isinstance(embedding, dict) else "",
            "dependenciesInstalled": embedding.get("dependenciesInstalled") if isinstance(embedding, dict) else False,
            "modelInstalled": embedding.get("modelInstalled") if isinstance(embedding, dict) else False,
            "deployment": deployment,
        }
    except Exception as error:
        logger.info("Embedding health check failed error=%s", error)
        return {
            "status": "error",
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


def build_runtime_config_payload() -> dict[str, Any]:
    providers = configured_provider_profiles()
    active_provider = next(
        (provider for provider in providers if provider.get("id") == agent_runtime.model_client.provider),
        provider_profile(agent_runtime.model_client.provider).to_public_dict(),
    )
    requires_api_key = bool(active_provider.get("requiresApiKey"))
    return {
        "ok": True,
        "api": {
            "provider": agent_runtime.model_client.provider,
            "providerLabel": str(active_provider.get("label") or agent_runtime.model_client.provider),
            "envVar": str(active_provider.get("envVar") or "API_KEY"),
            "requiresApiKey": requires_api_key,
            "baseUrl": agent_runtime.base_url,
            "model": agent_runtime.model,
            "streaming": agent_runtime.model_client.config.streaming,
            "maxTokens": agent_runtime.model_client.max_tokens,
            "thinkingEnabled": agent_runtime.model_client.config.thinking_enabled,
            "reasoningEffort": agent_runtime.model_client.config.reasoning_effort,
            "apiKeyConfigured": bool(agent_runtime.api_key) and (requires_api_key or agent_runtime.api_key != "local"),
            "apiKeyPreview": "" if not requires_api_key and agent_runtime.api_key == "local" else mask_secret(agent_runtime.api_key),
        },
        "providers": providers,
        "presets": [preset.to_public_dict() for preset in PROVIDER_PRESETS.values()],
        "runtime": agent_runtime._runtime_config_snapshot(),
        "paths": {
            "env": str(ENV_CONFIG_PATH),
            "providersConfig": str(PROVIDERS_CONFIG_PATH),
            "runtimeConfig": str(agent_runtime.runtime_config_path),
        },
    }


def build_embedding_config_payload() -> dict[str, Any]:
    config = current_local_bge_m3_embedding_config()
    status = embedding_deployment_manager.status(config)
    status["configured"] = local_bge_m3_embedding_is_configured()
    index = memory_store.memory_item_embedding_coverage(
        provider=config.provider,
        model=config.model_id,
        dimensions=config.dimensions,
    )
    backfill = memory_embedding_backfill_runner.status(index)
    provider_types = [
        {
            "id": BGE_M3_PROVIDER_ID,
            "label": "BGE-M3 本地 Embedding",
            "type": "flag_embedding",
            "modelId": BGE_M3_MODEL_ID,
            "dimensions": BGE_M3_DIMENSIONS,
        },
        {
            "id": "disabled",
            "label": "关闭本地 Embedding",
            "type": "none",
            "modelId": "",
            "dimensions": 0,
        },
    ]
    return {
        "ok": True,
        "embedding": status,
        "index": index,
        "backfill": backfill,
        "providerTypes": provider_types,
        "paths": {
            "env": str(ENV_CONFIG_PATH),
            "providersConfig": str(PROVIDERS_CONFIG_PATH),
            "defaultModelDir": str(default_bge_m3_model_dir(REPO_ROOT)),
            "modelsRoot": str(EMBEDDING_MODELS_ROOT),
        },
    }


def local_bge_m3_embedding_is_configured() -> bool:
    return resolve_local_bge_m3_embedding_is_configured(providers_config_path=PROVIDERS_CONFIG_PATH)


def current_local_bge_m3_embedding_config() -> LocalEmbeddingConfig:
    return resolve_local_bge_m3_embedding_config(providers_config_path=PROVIDERS_CONFIG_PATH, repo_root=REPO_ROOT)


def write_local_bge_m3_embedding_config(local_dir: Path) -> LocalEmbeddingConfig:
    config = LocalEmbeddingConfig(
        provider=BGE_M3_PROVIDER_ID,
        model_id=BGE_M3_MODEL_ID,
        local_dir=local_dir,
        dimensions=BGE_M3_DIMENSIONS,
        normalize_embeddings=True,
        batch_size=8,
        device="auto",
    )
    env_updates = {
        "AMADEUS_EMBEDDING_PROVIDER": BGE_M3_PROVIDER_ID,
        "AMADEUS_BGE_M3_MODEL_ID": BGE_M3_MODEL_ID,
        "AMADEUS_BGE_M3_MODEL_DIR": str(local_dir),
    }
    update_env_file(ENV_CONFIG_PATH, env_updates)
    os.environ.update(env_updates)
    assemble_providers_config(embedding_lines=local_bge_m3_embedding_section_lines(config))
    return config


def local_bge_m3_embedding_section_lines(config: LocalEmbeddingConfig) -> list[str]:
    return [
        "embedding:",
        f"  default: {config.provider}",
        "  providers:",
        f"    {BGE_M3_PROVIDER_ID}:",
        "      label: BGE-M3 Local",
        "      type: flag_embedding",
        f"      model: {quote_yaml_value(config.model_id)}",
        f"      localPath: {quote_yaml_value(str(config.local_dir))}",
        f"      dimensions: {config.dimensions}",
        f"      normalizeEmbeddings: {str(config.normalize_embeddings).lower()}",
        f"      batchSize: {config.batch_size}",
        f"      device: {quote_yaml_value(config.device)}",
    ]


def build_tools_config_payload() -> dict[str, Any]:
    config = read_tools_config_file()
    tools = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    mcp = normalize_mcp_config_for_payload(tools.get("mcp") if isinstance(tools, dict) else None)
    return {
        "ok": True,
        "mcp": mcp,
        "paths": {
            "toolsConfig": str(agent_runtime.tools_config_path),
        },
        "tools": agent_runtime.tool_permission_state(),
        "schemas": agent_runtime.enabled_tool_schemas(),
    }


def read_tools_config_file() -> dict[str, Any]:
    path = agent_runtime.tools_config_path
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"tools": {}}
    except OSError as error:
        raise ValueError(f"failed to read tools config: {error}") from error

    try:
        parsed = yaml.safe_load(raw) if raw.strip() else {}
    except yaml.YAMLError as error:
        raise ValueError(f"failed to parse tools config: {error}") from error
    if parsed is None:
        return {"tools": {}}
    if not isinstance(parsed, dict):
        raise ValueError("tools config must be a YAML object")
    return parsed


def write_tools_config_file(config: dict[str, Any]) -> None:
    path = agent_runtime.tools_config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    path.write_text(content, encoding="utf-8")


def normalize_mcp_config_for_payload(raw_mcp: Any) -> dict[str, Any]:
    if not isinstance(raw_mcp, dict):
        raw_mcp = {}
    servers = raw_mcp.get("servers") if isinstance(raw_mcp.get("servers"), list) else []
    return {
        "enabled": bool(raw_mcp.get("enabled")),
        "permission": normalize_tool_permission(raw_mcp.get("permission"), fallback="ask"),
        "servers": [
            normalize_mcp_server_for_payload(server)
            for server in servers
            if isinstance(server, dict)
        ],
    }


def normalize_mcp_server_for_payload(raw_server: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(raw_server.get("name") or ""),
        "url": str(raw_server.get("url") or ""),
        "enabled": raw_server.get("enabled") is not False,
        "permission": normalize_tool_permission(raw_server.get("permission"), fallback=None),
        "timeoutSeconds": normalize_timeout_seconds(raw_server.get("timeoutSeconds"), fallback=10),
    }


def update_mcp_tools_config(payload: dict[str, Any]) -> None:
    config = read_tools_config_file()
    tools = config.get("tools")
    if not isinstance(tools, dict):
        tools = {}
    tools["mcp"] = validate_mcp_config_payload(payload)
    config["tools"] = tools
    write_tools_config_file(config)


def validate_mcp_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    enabled = parse_required_bool(payload.get("enabled"), "mcp.enabled")
    permission = normalize_tool_permission(payload.get("permission"), fallback="ask")
    servers_payload = payload.get("servers")
    if not isinstance(servers_payload, list):
        raise ValueError("mcp.servers must be an array")

    servers: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, raw_server in enumerate(servers_payload):
        if not isinstance(raw_server, dict):
            raise ValueError(f"mcp.servers[{index}] must be an object")
        server = validate_mcp_server_payload(raw_server, index)
        name_key = server["name"].lower()
        if name_key in seen_names:
            raise ValueError(f"duplicate MCP server name: {server['name']}")
        seen_names.add(name_key)
        servers.append(server)

    return {
        "enabled": enabled,
        "permission": permission,
        "servers": servers,
    }


def validate_mcp_server_payload(payload: dict[str, Any], index: int) -> dict[str, Any]:
    name = optional_non_empty_string(payload.get("name"), f"mcp.servers[{index}].name")
    url = optional_non_empty_string(payload.get("url"), f"mcp.servers[{index}].url")
    if not name:
        raise ValueError(f"mcp.servers[{index}].name is required")
    if not url:
        raise ValueError(f"mcp.servers[{index}].url is required")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"mcp.servers[{index}].url must start with http:// or https://")
    return {
        "name": name,
        "url": url,
        "enabled": parse_required_bool(payload.get("enabled", True), f"mcp.servers[{index}].enabled"),
        "permission": normalize_tool_permission(payload.get("permission"), fallback=None),
        "timeoutSeconds": normalize_timeout_seconds(payload.get("timeoutSeconds"), fallback=10),
    }


def parse_required_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} must be a boolean")


def normalize_tool_permission(value: Any, *, fallback: str | None) -> str | None:
    if value in {"allow", "ask", "deny"}:
        return str(value)
    if value in {None, ""}:
        return fallback
    raise ValueError("permission must be one of allow, ask, deny")


def normalize_timeout_seconds(value: Any, *, fallback: int) -> int:
    if value in {None, ""}:
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("timeoutSeconds must be an integer") from error
    if parsed < 1 or parsed > 120:
        raise ValueError("timeoutSeconds must be between 1 and 120")
    return parsed


def configured_provider_profiles() -> list[dict[str, Any]]:
    llm_config = parse_providers_config(PROVIDERS_CONFIG_PATH).get("llm", {})
    configured = llm_config.get("providers") if isinstance(llm_config.get("providers"), dict) else {}
    provider_ids = [str(provider_id) for provider_id in configured]
    if agent_runtime.model_client.provider not in provider_ids:
        provider_ids.append(agent_runtime.model_client.provider)

    profiles: list[dict[str, Any]] = []
    for provider_id in provider_ids:
        entry = configured.get(provider_id) if isinstance(configured.get(provider_id), dict) else {}
        preset = provider_profile(str(provider_id))
        requires_api_key = parse_bool_value(entry.get("requiresApiKey"), preset.requires_api_key) if entry else preset.requires_api_key
        profiles.append({
            "id": str(provider_id),
            "label": str(entry.get("label") or preset.label or provider_id),
            "apiMode": str(entry.get("apiMode") or preset.api_mode),
            "envVar": str(entry.get("envVar") or preset.env_var),
            "baseUrl": str(entry.get("baseUrl") or preset.base_url),
            "defaultModel": str(entry.get("model") or preset.default_model),
            "requiresApiKey": requires_api_key,
            "supportsStreaming": parse_bool_value(entry.get("streaming"), preset.supports_streaming) if entry else preset.supports_streaming,
            "maxTokens": parse_positive_int_value(entry.get("maxTokens")) if entry else 0,
            "thinkingEnabled": parse_bool_value(entry.get("thinkingEnabled"), False) if entry else False,
            "reasoningEffort": parse_reasoning_effort(entry.get("reasoningEffort")) if entry else "medium",
        })
    return profiles


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def update_api_config(payload: dict[str, Any]) -> dict[str, Any]:
    provider = optional_non_empty_string(payload.get("provider"), "provider") or agent_runtime.model_client.provider
    profile = provider_profile(provider)
    same_provider = profile.name == agent_runtime.model_client.provider
    label = optional_non_empty_string(payload.get("label"), "label") or profile.label or profile.name
    env_var = optional_non_empty_string(payload.get("envVar"), "envVar") or profile.env_var
    base_url = optional_non_empty_string(payload.get("baseUrl"), "baseUrl")
    model = optional_non_empty_string(payload.get("model"), "model")
    requires_api_key = parse_bool_value(payload.get("requiresApiKey"), profile.requires_api_key)
    streaming = parse_bool_value(payload.get("streaming"), profile.supports_streaming)
    max_tokens = parse_optional_non_negative_int(payload.get("maxTokens"), "maxTokens")
    thinking_enabled = parse_bool_value(payload.get("thinkingEnabled"), agent_runtime.model_client.config.thinking_enabled if same_provider else False)
    reasoning_effort = parse_reasoning_effort(payload.get("reasoningEffort") or (agent_runtime.model_client.config.reasoning_effort if same_provider else None))
    api_key = payload.get("apiKey")
    if api_key is not None and not isinstance(api_key, str):
        raise ValueError("apiKey must be a string")

    effective_max_tokens = max_tokens if max_tokens is not None else (agent_runtime.model_client.max_tokens if same_provider else 0)

    env_updates: dict[str, str] = {}
    env_updates["AMADEUS_LLM_PROVIDER"] = profile.name
    if base_url is not None:
        env_updates[f"{env_var.removesuffix('_API_KEY')}_BASE_URL"] = base_url.rstrip("/")
    if model is not None:
        env_updates[f"{env_var.removesuffix('_API_KEY')}_MODEL"] = model
    if max_tokens is not None:
        env_updates[f"{env_var.removesuffix('_API_KEY')}_MAX_TOKENS"] = str(effective_max_tokens)
    env_updates[f"{env_var.removesuffix('_API_KEY')}_THINKING_ENABLED"] = "true" if thinking_enabled else "false"
    env_updates[f"{env_var.removesuffix('_API_KEY')}_REASONING_EFFORT"] = reasoning_effort
    if isinstance(api_key, str) and api_key.strip():
        env_updates[env_var] = api_key.strip()

    if env_updates:
        update_env_file(ENV_CONFIG_PATH, env_updates)
        os.environ.update(env_updates)

    update_providers_config_file(
        provider=profile.name,
        label=label,
        base_url=base_url or (agent_runtime.base_url if same_provider else profile.base_url),
        model=model or (agent_runtime.model if same_provider else profile.default_model),
        env_var=env_var,
        requires_api_key=requires_api_key,
        streaming=streaming,
        max_tokens=effective_max_tokens,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
    )

    return agent_runtime.configure_model_api(
        provider=profile.name,
        base_url=base_url or (agent_runtime.base_url if same_provider else profile.base_url),
        model=model or (agent_runtime.model if same_provider else profile.default_model),
        api_key=env_updates.get(env_var)
        or os.environ.get(env_var)
        or (agent_runtime.api_key if same_provider else "")
        or ("" if requires_api_key else "local"),
        streaming=streaming,
        max_tokens=effective_max_tokens,
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
    )


def parse_optional_non_negative_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{field_name} must be an integer")
        value = int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        try:
            value = int(stripped)
        except ValueError as error:
            raise ValueError(f"{field_name} must be an integer") from error
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value


def optional_non_empty_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    except OSError as error:
        raise ValueError(f"failed to read env config: {error}") from error

    remaining = dict(updates)
    next_lines: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            next_lines.append(raw_line)
            continue

        key = raw_line.split("=", 1)[0].strip()
        if key in remaining:
            next_lines.append(f"{key}={quote_env_value(remaining.pop(key))}")
        else:
            next_lines.append(raw_line)

    for key, value in remaining.items():
        next_lines.append(f"{key}={quote_env_value(value)}")

    path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def quote_env_value(value: str) -> str:
    if not value or any(character.isspace() for character in value) or any(character in value for character in ['"', "'", "#"]):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def read_raw_top_level_sections(path: Path) -> dict[str, list[str]]:
    """Split a providers-style YAML into top-level sections keyed by header name.

    Returns each section's raw lines (including the ``name:`` header) so blocks
    like tts/asr can be re-emitted verbatim, preserving ``${VAR}`` templates that
    parse_providers_config would otherwise resolve at parse time.
    """
    if not path.exists():
        return {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in lines:
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent == 0 and stripped.endswith(":") and "#" not in stripped:
            current = stripped[:-1].strip()
            sections[current] = [raw_line.rstrip()]
            continue
        if current is not None:
            sections[current].append(raw_line.rstrip())
    return sections


def update_providers_config_file(
    *,
    provider: str,
    label: str,
    base_url: str,
    model: str,
    env_var: str,
    requires_api_key: bool,
    streaming: bool,
    max_tokens: int = 0,
    thinking_enabled: bool = False,
    reasoning_effort: str = "medium",
) -> None:
    llm_config = parse_providers_config(PROVIDERS_CONFIG_PATH).get("llm", {})
    configured = llm_config.get("providers") if isinstance(llm_config.get("providers"), dict) else {}
    provider_ids = [str(provider_id) for provider_id in configured]
    if provider not in provider_ids:
        provider_ids.append(provider)

    content = [
        "llm:",
        f"  default: {provider}",
        "  providers:",
    ]
    for provider_id in provider_ids:
        entry = configured.get(provider_id) if isinstance(configured.get(provider_id), dict) else {}
        profile = provider_profile(provider_id)
        active_label = label if provider_id == provider else str(entry.get("label") or profile.label or provider_id)
        active_base_url = base_url if provider_id == provider else str(entry.get("baseUrl") or profile.base_url)
        active_model = model if provider_id == provider else str(entry.get("model") or profile.default_model)
        active_env_var = env_var if provider_id == provider else str(entry.get("envVar") or profile.env_var)
        active_requires_api_key = requires_api_key if provider_id == provider else parse_bool_value(entry.get("requiresApiKey"), profile.requires_api_key)
        active_streaming = streaming if provider_id == provider else parse_bool_value(entry.get("streaming"), profile.supports_streaming)
        active_max_tokens = max_tokens if provider_id == provider else parse_positive_int_value(entry.get("maxTokens"))
        active_thinking_enabled = thinking_enabled if provider_id == provider else parse_bool_value(entry.get("thinkingEnabled"), False)
        active_reasoning_effort = reasoning_effort if provider_id == provider else parse_reasoning_effort(entry.get("reasoningEffort"))
        content.extend([
            f"    {provider_id}:",
            f"      label: {quote_yaml_value(active_label)}",
            f"      envVar: {active_env_var}",
            f"      baseUrl: {quote_yaml_value(active_base_url)}",
            f"      apiKey: ${{{active_env_var}}}",
            f"      model: {quote_yaml_value(active_model)}",
            f"      requiresApiKey: {str(active_requires_api_key).lower()}",
            f"      streaming: {str(active_streaming).lower()}",
            f"      thinkingEnabled: {str(active_thinking_enabled).lower()}",
            f"      reasoningEffort: {active_reasoning_effort}",
        ])
        if active_max_tokens > 0:
            content.append(f"      maxTokens: {active_max_tokens}")

    assemble_providers_config(llm_lines=content)


def assemble_providers_config(
    *,
    llm_lines: list[str] | None = None,
    tts_lines: list[str] | None = None,
    asr_lines: list[str] | None = None,
    embedding_lines: list[str] | None = None,
) -> None:
    """Rewrite providers.yaml, replacing only the supplied sections and keeping
    the rest verbatim so ``${VAR}`` templates and unrelated settings survive."""
    existing = read_raw_top_level_sections(PROVIDERS_CONFIG_PATH)
    llm = llm_lines if llm_lines is not None else (existing.get("llm") or DEFAULT_LLM_SECTION_LINES)
    tts = tts_lines if tts_lines is not None else (existing.get("tts") or DEFAULT_TTS_SECTION_LINES)
    asr = asr_lines if asr_lines is not None else (existing.get("asr") or DEFAULT_ASR_SECTION_LINES)
    embedding = embedding_lines if embedding_lines is not None else existing.get("embedding")

    content: list[str] = []
    for section in (llm, tts, asr, embedding):
        if not section:
            continue
        if content:
            content.append("")
        content.extend(section)
    for name, section in existing.items():
        if name in {"llm", "tts", "asr", "embedding"}:
            continue
        if content:
            content.append("")
        content.extend(section)
    PROVIDERS_CONFIG_PATH.write_text("\n".join(content).rstrip() + "\n", encoding="utf-8")


DEFAULT_LLM_SECTION_LINES = [
    "llm:",
    "  default: deepseek",
    "  providers:",
    "    deepseek:",
    "      label: DeepSeek",
    "      envVar: DEEPSEEK_API_KEY",
    "      baseUrl: https://api.deepseek.com/v1",
    "      apiKey: ${DEEPSEEK_API_KEY}",
    "      model: deepseek-v4-pro",
    "      streaming: true",
    "      thinkingEnabled: true",
    "      reasoningEffort: high",
]

DEFAULT_TTS_SECTION_LINES = [
    "tts:",
    "  default: auto",
    "  providers:",
    "    auto:",
    "      type: auto",
    "    disabled:",
    "      type: none",
    "    macos_say:",
    "      type: macos_say",
    "      voice: ${MACOS_SAY_VOICE}",
    "      rate: ${MACOS_SAY_RATE}",
    "      timeoutSeconds: 30",
    "    gpt_sovits:",
    "      type: gpt_sovits",
    "      baseUrl: ${GPT_SOVITS_BASE_URL}",
    "      endpoint: ${GPT_SOVITS_ENDPOINT}",
    "      textLang: ${GPT_SOVITS_TEXT_LANG}",
    "      promptLang: ${GPT_SOVITS_PROMPT_LANG}",
    "      promptText: ${GPT_SOVITS_PROMPT_TEXT}",
    "      refAudioPath: ${GPT_SOVITS_REF_AUDIO_PATH}",
    "      timeoutSeconds: ${GPT_SOVITS_TIMEOUT_SECONDS}",
    "      streamingMode: ${GPT_SOVITS_STREAMING_MODE}",
]

DEFAULT_ASR_SECTION_LINES = [
    "asr:",
    "  default: disabled",
    "  providers:",
    "    disabled:",
    "      type: none",
]


def quote_yaml_value(value: str) -> str:
    if not value or any(character in value for character in ":#{}[]&,*?|-<>=!%@\\\"'") or value.strip() != value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


TTS_PROVIDER_TYPES = [
    {"id": "auto", "label": "自动（优先 GPT-SoVITS，回退系统语音）", "type": "auto"},
    {"id": "macos_say", "label": "macOS 系统语音", "type": "macos_say"},
    {"id": "gpt_sovits", "label": "GPT-SoVITS", "type": "gpt_sovits"},
    {"id": "disabled", "label": "关闭语音合成", "type": "none"},
]


def build_audio_config_payload() -> dict[str, Any]:
    tts_config = parse_providers_config(PROVIDERS_CONFIG_PATH).get("tts", {})
    providers = tts_config.get("providers") if isinstance(tts_config.get("providers"), dict) else {}
    default_provider = str(tts_config.get("default") or "auto")
    gpt_config = providers.get("gpt_sovits") if isinstance(providers.get("gpt_sovits"), dict) else {}
    macos_config = providers.get("macos_say") if isinstance(providers.get("macos_say"), dict) else {}

    return {
        "ok": True,
        "activeProvider": default_provider,
        "runtimeProvider": audio_runtime.tts_provider.name,
        "providerTypes": TTS_PROVIDER_TYPES,
        "macosAvailable": MacOsSayTtsProvider.is_available(),
        "macos": {
            "voice": str(macos_config.get("voice") or os.environ.get("MACOS_SAY_VOICE") or ""),
            "rate": str(macos_config.get("rate") or os.environ.get("MACOS_SAY_RATE") or ""),
        },
        "gptSovits": {
            "baseUrl": str(gpt_config.get("baseUrl") or os.environ.get("GPT_SOVITS_BASE_URL") or ""),
            "endpoint": str(gpt_config.get("endpoint") or os.environ.get("GPT_SOVITS_ENDPOINT") or "/tts"),
            "textLang": str(gpt_config.get("textLang") or os.environ.get("GPT_SOVITS_TEXT_LANG") or "auto"),
            "promptLang": str(gpt_config.get("promptLang") or os.environ.get("GPT_SOVITS_PROMPT_LANG") or "auto"),
            "promptText": str(gpt_config.get("promptText") or os.environ.get("GPT_SOVITS_PROMPT_TEXT") or ""),
            "refAudioPath": str(gpt_config.get("refAudioPath") or os.environ.get("GPT_SOVITS_REF_AUDIO_PATH") or ""),
            "timeoutSeconds": str(gpt_config.get("timeoutSeconds") or os.environ.get("GPT_SOVITS_TIMEOUT_SECONDS") or "60"),
            "streamingMode": parse_bool_value(gpt_config.get("streamingMode"), parse_bool_value(os.environ.get("GPT_SOVITS_STREAMING_MODE"), False)),
        },
        "voices": MacOsSayTtsProvider(MacOsSayConfig(), audio_library).list_voices(),
        "paths": {
            "env": str(ENV_CONFIG_PATH),
            "providersConfig": str(PROVIDERS_CONFIG_PATH),
        },
    }


def update_audio_config(payload: dict[str, Any]) -> None:
    provider = optional_non_empty_string(payload.get("provider"), "provider")
    valid_ids = {entry["id"] for entry in TTS_PROVIDER_TYPES}
    if provider is None:
        provider = str(parse_providers_config(PROVIDERS_CONFIG_PATH).get("tts", {}).get("default") or "auto")
    if provider not in valid_ids:
        raise ValueError(f"unsupported tts provider: {provider}")

    env_updates: dict[str, str] = {}

    macos_payload = payload.get("macos")
    if macos_payload is not None:
        if not isinstance(macos_payload, dict):
            raise ValueError("macos must be an object")
        macos_voice = macos_payload.get("voice")
        if macos_voice is not None:
            env_updates["MACOS_SAY_VOICE"] = str(macos_voice).strip()
        macos_rate = macos_payload.get("rate")
        if macos_rate is not None:
            rate_text = str(macos_rate).strip()
            if rate_text and not rate_text.lstrip("-").isdigit():
                raise ValueError("macos.rate must be an integer")
            env_updates["MACOS_SAY_RATE"] = rate_text

    gpt_payload = payload.get("gptSovits")
    if gpt_payload is not None:
        if not isinstance(gpt_payload, dict):
            raise ValueError("gptSovits must be an object")
        gpt_field_env = {
            "baseUrl": "GPT_SOVITS_BASE_URL",
            "endpoint": "GPT_SOVITS_ENDPOINT",
            "textLang": "GPT_SOVITS_TEXT_LANG",
            "promptLang": "GPT_SOVITS_PROMPT_LANG",
            "promptText": "GPT_SOVITS_PROMPT_TEXT",
            "refAudioPath": "GPT_SOVITS_REF_AUDIO_PATH",
        }
        for field_name, env_key in gpt_field_env.items():
            value = gpt_payload.get(field_name)
            if value is not None:
                cleaned = str(value).strip()
                if field_name == "baseUrl":
                    cleaned = cleaned.rstrip("/")
                env_updates[env_key] = cleaned
        timeout_value = gpt_payload.get("timeoutSeconds")
        if timeout_value is not None:
            timeout_text = str(timeout_value).strip()
            if timeout_text and not timeout_text.isdigit():
                raise ValueError("gptSovits.timeoutSeconds must be a positive integer")
            env_updates["GPT_SOVITS_TIMEOUT_SECONDS"] = timeout_text or "60"
        streaming_value = gpt_payload.get("streamingMode")
        if streaming_value is not None:
            env_updates["GPT_SOVITS_STREAMING_MODE"] = "true" if parse_bool_value(streaming_value, False) else "false"

    if env_updates:
        update_env_file(ENV_CONFIG_PATH, env_updates)
        os.environ.update(env_updates)

    write_tts_default_provider(provider)

    audio_runtime.tts_provider = create_tts_provider_from_config(audio_library)


def write_tts_default_provider(provider: str) -> None:
    tts_lines = list(DEFAULT_TTS_SECTION_LINES)
    tts_lines[1] = f"  default: {provider}"
    assemble_providers_config(tts_lines=tts_lines)


LIVE2D_BEHAVIOR_STATES = [
    {"id": "started", "label": "开始播放语音"},
    {"id": "ended", "label": "语音播放结束"},
    {"id": "error", "label": "语音播放出错"},
]


def build_live2d_behaviors_payload() -> dict[str, Any]:
    behaviors = live2d_library.audio_playback_behaviors()
    manifest = None
    selection = live2d_library.configured_model()
    if selection:
        manifest = live2d_library.read_manifest(selection.relative_path)

    expressions: list[str] = []
    motions: list[str] = []
    if isinstance(manifest, dict):
        aliases = manifest.get("aliases") if isinstance(manifest.get("aliases"), dict) else {}
        expression_aliases = aliases.get("expressions") if isinstance(aliases.get("expressions"), dict) else {}
        motion_aliases = aliases.get("motions") if isinstance(aliases.get("motions"), dict) else {}
        expressions = sorted(str(key) for key in expression_aliases)
        motions = sorted(str(key) for key in motion_aliases)

    return {
        "ok": True,
        "states": LIVE2D_BEHAVIOR_STATES,
        "audioPlaybackBehaviors": behaviors,
        "defaults": live2d_library.default_state_behaviors(),
        "suggestions": {
            "expressions": expressions,
            "motions": motions,
        },
        "paths": {
            "harnessesConfig": str(HARNESSES_CONFIG_PATH),
        },
    }


def update_runtime_config_file(payload: dict[str, Any]) -> None:
    current = agent_runtime._runtime_config_snapshot()
    updates: dict[str, dict[str, int | float]] = {}
    for section, section_payload in payload.items():
        if section not in RUNTIME_CONFIG_FIELDS:
            raise ValueError(f"unsupported runtime config section: {section}")
        if not isinstance(section_payload, dict):
            raise ValueError(f"{section} must be an object")
        current_section = dict(current.get(section, {}))
        updates[section] = {}
        for key, value in section_payload.items():
            key = RUNTIME_CONFIG_FIELD_ALIASES.get((section, key), key)
            field_schema = RUNTIME_CONFIG_FIELDS[section].get(key)
            if field_schema is None:
                raise ValueError(f"unsupported runtime config field: {section}.{key}")
            parsed_value = coerce_runtime_config_value(section, key, value, field_schema)
            current_section[key] = parsed_value
            updates[section][key] = parsed_value
        current[section] = current_section

    try:
        existing_content = agent_runtime.runtime_config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing_content = ""
    except OSError as error:
        raise ValueError(f"failed to read runtime config: {error}") from error

    next_content = update_runtime_config_content(existing_content, current, updates)
    agent_runtime.runtime_config_path.write_text(next_content, encoding="utf-8")


def coerce_runtime_config_value(
    section: str,
    key: str,
    value: Any,
    field_schema: tuple[type, float | int | None, float | int | None],
) -> int | float:
    expected_type, minimum, maximum = field_schema
    try:
        parsed = expected_type(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{section}.{key} must be a {expected_type.__name__}") from error

    if expected_type is int and isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{section}.{key} must be an integer")
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{section}.{key} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{section}.{key} must be <= {maximum}")
    return parsed


def serialize_runtime_config(config: dict[str, dict[str, int | float]]) -> str:
    section_comments = {
        "context": "Context and memory injection budgets.",
        "summary": "Automatic conversation summary compaction.",
        "memoryReview": "Automatic durable memory candidate review.",
        "desktop": "Desktop companion display tuning.",
    }
    lines: list[str] = []
    for section in ("context", "summary", "memoryReview", "desktop"):
        if lines:
            lines.append("")
        comment = section_comments.get(section)
        if comment:
            lines.append(f"# {comment}")
        lines.append(f"{section}:")
        for key, value in config.get(section, {}).items():
            lines.append(f"  {key}: {value}")
    return "\n".join(lines).rstrip() + "\n"


def update_runtime_config_content(
    content: str,
    full_config: dict[str, dict[str, int | float]],
    updates: dict[str, dict[str, int | float]],
) -> str:
    if not content.strip():
        return serialize_runtime_config(full_config)

    remaining = {section: dict(values) for section, values in updates.items()}
    next_lines: list[str] = []
    current_section: str | None = None

    def append_missing_section_keys(section: str | None) -> None:
        if not section or not remaining.get(section):
            return
        for missing_key, missing_value in remaining[section].items():
            next_lines.append(f"  {missing_key}: {missing_value}")
        remaining[section].clear()

    for raw_line in content.splitlines():
        line_without_comment = raw_line.split("#", 1)[0].rstrip()
        indent = len(line_without_comment) - len(line_without_comment.lstrip(" "))
        trimmed = line_without_comment.strip()

        if indent == 0 and trimmed.endswith(":"):
            append_missing_section_keys(current_section)
            current_section = trimmed[:-1]
            next_lines.append(raw_line)
            continue

        if current_section in remaining and indent == 2 and ":" in trimmed:
            key = trimmed.split(":", 1)[0].strip()
            if key in remaining[current_section]:
                prefix = raw_line[: len(raw_line) - len(raw_line.lstrip(" "))]
                suffix = ""
                if "#" in raw_line:
                    suffix = "  #" + raw_line.split("#", 1)[1]
                next_lines.append(f"{prefix}{key}: {remaining[current_section].pop(key)}{suffix}")
                continue

        next_lines.append(raw_line)

    append_missing_section_keys(current_section)

    for section, values in remaining.items():
        if not values:
            continue
        if next_lines and next_lines[-1].strip():
            next_lines.append("")
        next_lines.append(f"{section}:")
        for key, value in values.items():
            next_lines.append(f"  {key}: {value}")

    return "\n".join(next_lines).rstrip() + "\n"


def scheduled_job_event_payload(job: dict[str, object], action: str) -> dict[str, object]:
    return {
        "type": "scheduled.updated",
        "sessionId": job.get("sessionId"),
        "payload": {
            "job": job,
            "action": action,
        },
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
