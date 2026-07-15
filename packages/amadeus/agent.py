from __future__ import annotations

import json
import logging
import os
import threading
import contextlib
import contextvars
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterable
from uuid import uuid4

from amadeus.audio import AudioFallbackResult, AudioOutputCommand, AudioRuntime
from amadeus.context import ContextAssembler, ContextAssemblerConfig, sanitize_context_markup
from amadeus.harness import DEFAULT_HARNESSES_CONFIG_PATH, HarnessContext, HarnessFeedbackPolicy, HarnessRegistry
from amadeus.memory import MessageMemoryStore
from amadeus.memory_embeddings import create_local_bge_m3_embedding_provider
from amadeus.memory_provider import (
    DEFAULT_RUNTIME_MEMORY_PROVIDER_NAME,
    ExternalMemoryProvider,
    RuntimeMemoryManager,
    create_runtime_memory_provider,
    normalize_runtime_memory_provider_name,
)
from amadeus.memory_safety import evaluate_memory_candidate
from amadeus.model import (
    ChatStreamDelta,
    DEFAULT_PROVIDERS_CONFIG_PATH,
    OpenAICompatibleChatModel,
    OpenAICompatibleConfig,
    first_choice_message,
    is_context_overflow_error,
    parse_json_object_from_text,
)
from amadeus.prompting import build_system_prompt
from amadeus.provider_reasoning import ReasoningConfig, assistant_history_message, prepare_messages_for_provider
from amadeus.role_scope import RoleRuntimeScope, normalize_role_runtime_scope, role_allows_tool
from amadeus.skills import SkillCatalog
from amadeus.tool_runtime import (
    DEFAULT_TOOLS_CONFIG_PATH,
    ToolAuditLog,
    ToolAuditRecord,
    ToolAuditStore,
    ToolContext,
    ToolLoopGuardrail,
    ToolRegistry,
)
from amadeus.worker_policy import WorkerRuntimeScope, worker_action_permission_decision


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_CONFIG_PATH = DEFAULT_TOOLS_CONFIG_PATH
RUNTIME_CONFIG_PATH = REPO_ROOT / "configs" / "runtime.yaml"
HARNESSES_CONFIG_PATH = DEFAULT_HARNESSES_CONFIG_PATH
SKILLS_ROOT = REPO_ROOT / "skills"
CONTEXT_MAX_TOKENS = 24000
CONTEXT_COMPACTION_TRIGGER_RATIO = 0.85
CONTEXT_RECENT_MESSAGE_TARGET_RATIO = 0.20
CONTEXT_SUMMARY_CHARS = 4000
CONTEXT_MEMORY_ITEM_LIMIT = 8
CONTEXT_MEMORY_ITEM_CHARS = 500
CONTEXT_RETRIEVAL_LIMIT = 3
CONTEXT_RETRIEVAL_SNIPPET_CHARS = 280
MEMORY_VECTOR_RETRIEVAL = True
MEMORY_VECTOR_CANDIDATE_LIMIT = 80
CONTEXT_TASK_LIMIT = 5
CONTEXT_RECENT_TASK_LIMIT = 3
CONTEXT_TASK_RESULT_CHARS = 280
CONTEXT_DIAGNOSTICS_LIMIT = 20
SUMMARY_TRIGGER_MESSAGE_COUNT = 40
SUMMARY_KEEP_RECENT_TURNS = 3
SUMMARY_MIN_KEEP_RECENT_TURNS = 1
SUMMARY_MAX_KEEP_RECENT_TURN_FLOOR = 3
SUMMARY_SOURCE_MAX_MESSAGES = 120
SUMMARY_FAILURE_COOLDOWN_SECONDS = 300
MEMORY_REVIEW_SOURCE_MAX_MESSAGES = 40
MEMORY_REVIEW_EXISTING_MEMORY_LIMIT = 40
MEMORY_REVIEW_PENDING_LIMIT = 40
MEMORY_REVIEW_MAX_CANDIDATES = 8
MEMORY_REVIEW_TRIGGER_MESSAGE_COUNT = 12
MEMORY_REVIEW_SUCCESS_COOLDOWN_SECONDS = 600
MEMORY_REVIEW_FAILURE_COOLDOWN_SECONDS = 300
DESKTOP_COMPANION_LIVE2D_SCALE = 0.92
DESKTOP_COMPANION_LIVE2D_OFFSET_X = 0
DESKTOP_COMPANION_LIVE2D_OFFSET_Y = 0
AGENT_MAX_TOOL_ITERATIONS = 90
WORKSPACE_MUTATING_TOOLS = {"patch", "write_file"}
POTENTIALLY_WORKSPACE_MUTATING_TOOLS = {"terminal", "execute_code"}
logger = logging.getLogger(__name__)
_WORKER_RUNTIME_SCOPE: contextvars.ContextVar[WorkerRuntimeScope | None] = contextvars.ContextVar(
    "amadeus_worker_runtime_scope",
    default=None,
)


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
class ContextBudgetReport:
    estimated_tokens: int
    max_tokens: int
    trigger_tokens: int
    over_budget: bool


@dataclass(frozen=True)
class PermissionRequest:
    request_id: str
    tool_name: str
    display_name: str
    reason: str


@dataclass(frozen=True)
class RoleScopedToolHints:
    registry: ToolRegistry
    allowed_names: set[str] | None = None

    def enabled_prompt_hints(self) -> list[dict[str, str]]:
        return self.registry.enabled_prompt_hints(self.allowed_names)


@dataclass
class RunningTurn:
    session_id: str
    turn_id: str
    cancel_event: threading.Event
    started_at: str


PermissionRequester = Callable[[PermissionRequest], bool]


class PermissionBroker:
    def __init__(self) -> None:
        self._pending: dict[str, tuple[threading.Event, bool | None]] = {}
        self._lock = threading.Lock()

    def register(self, request_id: str) -> None:
        with self._lock:
            self._pending[request_id] = (threading.Event(), None)
        logger.info("Registered tool permission request requestId=%s", request_id)

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
                logger.info("Resolved tool permission request requestId=%s approved=%s", request_id, approved)
            else:
                logger.info("Timed out waiting for tool permission request requestId=%s timeoutSeconds=%s", request_id, timeout_seconds)
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

        return approved

    def resolve(self, request_id: str, approved: bool) -> bool:
        with self._lock:
            pending = self._pending.get(request_id)
            if not pending:
                logger.info("Ignoring permission response for unknown request requestId=%s approved=%s", request_id, approved)
                return False

            event, _approval = pending
            self._pending[request_id] = (event, approved)
            event.set()
            logger.info("Accepted permission response requestId=%s approved=%s", request_id, approved)
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
        runtime_config_path: Path = RUNTIME_CONFIG_PATH,
        harnesses_config_path: Path = HARNESSES_CONFIG_PATH,
        skills_root: Path = SKILLS_ROOT,
        workspace_root: Path = REPO_ROOT,
        external_memory_providers: Iterable[ExternalMemoryProvider] | None = None,
    ) -> None:
        load_dotenv()
        self.memory_store = memory_store
        self.external_memory_providers = list(external_memory_providers or ())
        self.runtime_config_path = runtime_config_path
        initial_memory_config = parse_runtime_config(runtime_config_path).get("memory", {})
        self.memory_provider_name = configured_runtime_memory_provider_name(initial_memory_config)
        self.memory_global_retrieval_fallback = configured_runtime_memory_global_fallback(initial_memory_config)
        self.memory_vector_retrieval_enabled = configured_runtime_memory_vector_retrieval(initial_memory_config)
        self.memory_vector_candidate_limit = configured_runtime_memory_vector_candidate_limit(initial_memory_config)
        self.memory_embedding_provider = create_local_bge_m3_embedding_provider(
            providers_config_path=DEFAULT_PROVIDERS_CONFIG_PATH,
            repo_root=REPO_ROOT,
        ) if self.memory_vector_retrieval_enabled else None
        self.memory_manager = RuntimeMemoryManager(
            create_runtime_memory_provider(
                self.memory_store,
                provider_name=self.memory_provider_name,
                global_retrieval_fallback=self.memory_global_retrieval_fallback,
                embedding_provider=self.memory_embedding_provider,
                vector_retrieval_enabled=self.memory_vector_retrieval_enabled,
                vector_candidate_limit=self.memory_vector_candidate_limit,
            ),
            external_providers=self.external_memory_providers,
        )
        self.audio_runtime = audio_runtime
        self.task_worker: Any | None = None
        self.model_client = OpenAICompatibleChatModel()
        self.tools_config_path = tools_config_path
        self.harnesses_config_path = harnesses_config_path
        self.tool_registry = ToolRegistry(
            config_path=tools_config_path,
            memory_tool_specs=self.memory_manager.get_tool_specs(),
        )
        self.harness_registry = HarnessRegistry.from_config(
            harnesses_config_path,
            audio_library=audio_runtime.library if audio_runtime is not None else None,
        )
        self.skill_catalog = SkillCatalog(skills_root)
        self.harness_feedback_policy = HarnessFeedbackPolicy()
        self.tool_audit_log = ToolAuditLog()
        self.tool_audit_store = ToolAuditStore(memory_store.database_path)
        self.workspace_root = workspace_root.resolve()
        self._system_prompt_cache: dict[tuple[Any, ...], str] = {}
        self.context_max_tokens = CONTEXT_MAX_TOKENS
        self.system_prompt = self._build_system_prompt()
        self.context_assembler = self._build_context_assembler()
        self.context_diagnostics_limit = CONTEXT_DIAGNOSTICS_LIMIT
        self._context_diagnostics_by_session: dict[str, deque[dict[str, Any]]] = {}
        self._context_diagnostics_lock = threading.Lock()
        self._workspace_epoch_by_session: dict[str, int] = {}
        self._workspace_epoch_lock = threading.Lock()
        self._running_turns_by_session: dict[str, RunningTurn] = {}
        self._running_turns_lock = threading.Lock()
        self._load_runtime_config(reason="startup")
        self._summary_failure_until: dict[str, float] = {}
        self._memory_review_cooldown_until: dict[str, float] = {}
        self._memory_review_last_message_id: dict[str, int] = {}
        logger.info(
            "Initialized AgentRuntime model=%s baseUrl=%s toolsConfig=%s runtimeConfig=%s harnessesConfig=%s memoryDb=%s",
            self.model,
            self.base_url,
            tools_config_path,
            runtime_config_path,
            harnesses_config_path,
            memory_store.database_path,
        )

    def set_task_worker(self, task_worker: Any | None) -> None:
        self.task_worker = task_worker

    @property
    def summary_keep_recent_messages(self) -> int:
        return self.summary_keep_recent_turns

    @summary_keep_recent_messages.setter
    def summary_keep_recent_messages(self, value: int) -> None:
        self.summary_keep_recent_turns = max(1, int(value))

    @property
    def summary_min_keep_recent_messages(self) -> int:
        return self.summary_min_keep_recent_turns

    @summary_min_keep_recent_messages.setter
    def summary_min_keep_recent_messages(self, value: int) -> None:
        self.summary_min_keep_recent_turns = max(0, int(value))

    @property
    def base_url(self) -> str:
        return self.model_client.base_url

    @property
    def api_key(self) -> str:
        return self.model_client.api_key

    @api_key.setter
    def api_key(self, value: str) -> None:
        self.model_client.api_key = value

    @property
    def model(self) -> str:
        return self.model_client.model

    def configure_model_api(
        self,
        *,
        provider: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        streaming: bool | None = None,
        max_tokens: int | None = None,
        thinking_enabled: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        current = self.model_client.config
        self.model_client.config = OpenAICompatibleConfig(
            provider=provider or current.provider,
            base_url=(base_url or current.base_url).rstrip("/"),
            api_key=api_key if api_key is not None else current.api_key,
            model=model or current.model,
            streaming=streaming if streaming is not None else current.streaming,
            max_tokens=max_tokens if max_tokens is not None else current.max_tokens,
            thinking_enabled=thinking_enabled if thinking_enabled is not None else current.thinking_enabled,
            reasoning_effort=reasoning_effort or current.reasoning_effort,
            default_headers=current.default_headers,
            request_timeout_seconds=current.request_timeout_seconds,
            stream_timeout_seconds=current.stream_timeout_seconds,
        )
        return {
            "provider": self.model_client.provider,
            "baseUrl": self.base_url,
            "model": self.model,
            "maxTokens": self.model_client.max_tokens,
            "thinkingEnabled": self.model_client.config.thinking_enabled,
            "reasoningEffort": self.model_client.config.reasoning_effort,
            "apiKeyConfigured": bool(self.api_key),
        }

    def observe_harness_feedback(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        timestamp: str | None = None,
        client_id: str | None = None,
        surface: str | None = None,
    ) -> dict[str, Any]:
        return self.harness_feedback_policy.record_feedback(
            session_id,
            event_type,
            payload,
            timestamp=timestamp,
            client_id=client_id,
            surface=surface,
        )

    def harness_feedback_snapshot(self, session_id: str) -> dict[str, Any]:
        return self.harness_feedback_policy.snapshot(session_id)

    def harness_events_for_feedback(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> list[AgentEvent]:
        context = HarnessContext(
            session_id=session_id,
            runtime_state=self.harness_feedback_policy.runtime_state(session_id),
            client_capabilities=self.harness_feedback_policy.client_capabilities(session_id),
        )
        emitted_events: list[AgentEvent] = []
        for emitted in self.harness_registry.observe_event(context, {"type": event_type, "payload": payload}):
            emitted_type = emitted.get("type")
            emitted_payload = emitted.get("payload")
            if isinstance(emitted_type, str) and isinstance(emitted_payload, dict):
                emitted_events.append(AgentEvent(emitted_type, emitted_payload))
        return emitted_events

    def _load_runtime_config(self, *, reason: str) -> dict[str, dict[str, Any]]:
        runtime_config = parse_runtime_config(self.runtime_config_path)
        context_config = runtime_config.get("context", {})
        summary_config = runtime_config.get("summary", {})
        memory_config = runtime_config.get("memory", {})
        memory_review_config = runtime_config.get("memoryReview", {})
        tasks_config = runtime_config.get("tasks", {})
        desktop_config = runtime_config.get("desktop", {})
        agent_config = runtime_config.get("agent", {})
        vector_retrieval_enabled = configured_runtime_memory_vector_retrieval(memory_config)
        vector_candidate_limit = configured_runtime_memory_vector_candidate_limit(memory_config)
        self._configure_runtime_memory_provider(
            configured_runtime_memory_provider_name(memory_config),
            global_retrieval_fallback=configured_runtime_memory_global_fallback(memory_config),
            vector_retrieval_enabled=vector_retrieval_enabled,
            vector_candidate_limit=vector_candidate_limit,
        )
        self.agent_max_tool_iterations = parse_positive_int_env(
            "AMADEUS_AGENT_MAX_TOOL_ITERATIONS",
            parse_positive_int_value(agent_config.get("maxToolIterations"), AGENT_MAX_TOOL_ITERATIONS),
        )
        self.context_max_tokens = parse_positive_int_env(
            "AMADEUS_CONTEXT_MAX_TOKENS",
            parse_positive_int_value(context_config.get("maxTokens"), CONTEXT_MAX_TOKENS),
        )
        self.context_compaction_trigger_ratio = parse_float_env(
            "AMADEUS_CONTEXT_COMPACTION_TRIGGER_RATIO",
            parse_float_value(context_config.get("compactionTriggerRatio"), CONTEXT_COMPACTION_TRIGGER_RATIO, minimum=0.1, maximum=1.0),
            minimum=0.1,
            maximum=1.0,
        )
        self.context_recent_message_target_ratio = parse_float_env(
            "AMADEUS_CONTEXT_RECENT_MESSAGE_TARGET_RATIO",
            parse_float_value(context_config.get("recentMessageTargetRatio"), CONTEXT_RECENT_MESSAGE_TARGET_RATIO, minimum=0.1, maximum=0.9),
            minimum=0.1,
            maximum=0.9,
        )
        self.context_summary_chars = parse_positive_int_env(
            "AMADEUS_CONTEXT_SUMMARY_CHARS",
            parse_positive_int_value(context_config.get("summaryChars"), CONTEXT_SUMMARY_CHARS),
        )
        self.context_memory_item_limit = parse_positive_int_env(
            "AMADEUS_CONTEXT_MEMORY_ITEM_LIMIT",
            parse_positive_int_value(context_config.get("memoryItemLimit"), CONTEXT_MEMORY_ITEM_LIMIT),
        )
        self.context_memory_item_chars = parse_positive_int_env(
            "AMADEUS_CONTEXT_MEMORY_ITEM_CHARS",
            parse_positive_int_value(context_config.get("memoryItemChars"), CONTEXT_MEMORY_ITEM_CHARS),
        )
        self.context_retrieval_limit = parse_positive_int_env(
            "AMADEUS_CONTEXT_RETRIEVAL_LIMIT",
            parse_positive_int_value(context_config.get("retrievalLimit"), CONTEXT_RETRIEVAL_LIMIT),
        )
        self.context_retrieval_snippet_chars = parse_positive_int_env(
            "AMADEUS_CONTEXT_RETRIEVAL_SNIPPET_CHARS",
            parse_positive_int_value(context_config.get("retrievalSnippetChars"), CONTEXT_RETRIEVAL_SNIPPET_CHARS),
        )
        self.context_task_limit = parse_positive_int_env(
            "AMADEUS_CONTEXT_TASK_LIMIT",
            parse_positive_int_value(context_config.get("taskLimit"), CONTEXT_TASK_LIMIT),
        )
        self.context_recent_task_limit = parse_positive_int_env(
            "AMADEUS_CONTEXT_RECENT_TASK_LIMIT",
            parse_positive_int_value(context_config.get("recentTaskLimit"), CONTEXT_RECENT_TASK_LIMIT),
        )
        self.context_task_result_chars = parse_positive_int_env(
            "AMADEUS_CONTEXT_TASK_RESULT_CHARS",
            parse_positive_int_value(context_config.get("taskResultChars"), CONTEXT_TASK_RESULT_CHARS),
        )
        self.context_diagnostics_limit = parse_positive_int_env(
            "AMADEUS_CONTEXT_DIAGNOSTICS_LIMIT",
            parse_positive_int_value(context_config.get("diagnosticsLimit"), CONTEXT_DIAGNOSTICS_LIMIT),
        )
        self._resize_context_diagnostics_buffers(self.context_diagnostics_limit)
        self._system_prompt_cache.clear()
        self.system_prompt = self._build_system_prompt()
        self.context_assembler = self._build_context_assembler(
            ContextAssemblerConfig(
                summary_chars=self.context_summary_chars,
                memory_item_limit=self.context_memory_item_limit,
                memory_item_chars=self.context_memory_item_chars,
                retrieval_limit=self.context_retrieval_limit,
                retrieval_snippet_chars=self.context_retrieval_snippet_chars,
                task_limit=self.context_task_limit,
                recent_task_limit=self.context_recent_task_limit,
                task_result_chars=self.context_task_result_chars,
            ),
        )
        self.summary_trigger_message_count = parse_positive_int_env(
            "AMADEUS_SUMMARY_TRIGGER_MESSAGE_COUNT",
            parse_positive_int_value(summary_config.get("triggerMessageCount"), SUMMARY_TRIGGER_MESSAGE_COUNT),
        )
        keep_recent_turns_default = parse_positive_int_value(
            summary_config.get("keepRecentTurns", summary_config.get("keepRecentMessages")),
            SUMMARY_KEEP_RECENT_TURNS,
        )
        self.summary_keep_recent_turns = parse_positive_int_env(
            "AMADEUS_SUMMARY_KEEP_RECENT_TURNS",
            parse_positive_int_env("AMADEUS_SUMMARY_KEEP_RECENT_MESSAGES", keep_recent_turns_default),
        )
        min_keep_recent_turns_default = parse_non_negative_int_value(
            summary_config.get("minKeepRecentTurns", summary_config.get("minKeepRecentMessages")),
            SUMMARY_MIN_KEEP_RECENT_TURNS,
        )
        self.summary_min_keep_recent_turns = parse_non_negative_int_env(
            "AMADEUS_SUMMARY_MIN_KEEP_RECENT_TURNS",
            parse_non_negative_int_env("AMADEUS_SUMMARY_MIN_KEEP_RECENT_MESSAGES", min_keep_recent_turns_default),
        )
        self.summary_source_max_messages = parse_positive_int_env(
            "AMADEUS_SUMMARY_SOURCE_MAX_MESSAGES",
            parse_positive_int_value(summary_config.get("sourceMaxMessages"), SUMMARY_SOURCE_MAX_MESSAGES),
        )
        self.summary_failure_cooldown_seconds = parse_positive_int_env(
            "AMADEUS_SUMMARY_FAILURE_COOLDOWN_SECONDS",
            parse_positive_int_value(summary_config.get("failureCooldownSeconds"), SUMMARY_FAILURE_COOLDOWN_SECONDS),
        )
        self.memory_review_trigger_message_count = parse_positive_int_env(
            "AMADEUS_MEMORY_REVIEW_TRIGGER_MESSAGE_COUNT",
            parse_positive_int_value(memory_review_config.get("triggerMessageCount"), MEMORY_REVIEW_TRIGGER_MESSAGE_COUNT),
        )
        self.memory_review_source_max_messages = parse_positive_int_env(
            "AMADEUS_MEMORY_REVIEW_SOURCE_MAX_MESSAGES",
            parse_positive_int_value(memory_review_config.get("sourceMaxMessages"), MEMORY_REVIEW_SOURCE_MAX_MESSAGES),
        )
        self.memory_review_existing_memory_limit = parse_positive_int_env(
            "AMADEUS_MEMORY_REVIEW_EXISTING_MEMORY_LIMIT",
            parse_positive_int_value(memory_review_config.get("existingMemoryLimit"), MEMORY_REVIEW_EXISTING_MEMORY_LIMIT),
        )
        self.memory_review_pending_limit = parse_positive_int_env(
            "AMADEUS_MEMORY_REVIEW_PENDING_LIMIT",
            parse_positive_int_value(memory_review_config.get("pendingLimit"), MEMORY_REVIEW_PENDING_LIMIT),
        )
        self.memory_review_max_candidates = parse_positive_int_env(
            "AMADEUS_MEMORY_REVIEW_MAX_CANDIDATES",
            parse_positive_int_value(memory_review_config.get("maxCandidates"), MEMORY_REVIEW_MAX_CANDIDATES),
        )
        self.memory_review_success_cooldown_seconds = parse_positive_int_env(
            "AMADEUS_MEMORY_REVIEW_SUCCESS_COOLDOWN_SECONDS",
            parse_positive_int_value(memory_review_config.get("successCooldownSeconds"), MEMORY_REVIEW_SUCCESS_COOLDOWN_SECONDS),
        )
        self.memory_review_failure_cooldown_seconds = parse_positive_int_env(
            "AMADEUS_MEMORY_REVIEW_FAILURE_COOLDOWN_SECONDS",
            parse_positive_int_value(memory_review_config.get("failureCooldownSeconds"), MEMORY_REVIEW_FAILURE_COOLDOWN_SECONDS),
        )
        self.worker_approval_action_ttl_seconds = parse_positive_int_env(
            "AMADEUS_WORKER_APPROVAL_ACTION_TTL_SECONDS",
            parse_positive_int_value(
                tasks_config.get("workerApprovalActionTtlSeconds"),
                MessageMemoryStore.WORKER_APPROVAL_ACTION_TTL_SECONDS,
            ),
        )
        self.memory_store.set_worker_approval_action_ttl_seconds(self.worker_approval_action_ttl_seconds)
        self.desktop_companion_live2d_scale = parse_float_env(
            "AMADEUS_DESKTOP_COMPANION_LIVE2D_SCALE",
            parse_float_value(desktop_config.get("companionLive2dScale"), DESKTOP_COMPANION_LIVE2D_SCALE, minimum=0.25, maximum=2.5),
            minimum=0.25,
            maximum=2.5,
        )
        self.desktop_companion_live2d_offset_x = parse_int_env(
            "AMADEUS_DESKTOP_COMPANION_LIVE2D_OFFSET_X",
            parse_int_value(desktop_config.get("companionLive2dOffsetX"), DESKTOP_COMPANION_LIVE2D_OFFSET_X),
        )
        self.desktop_companion_live2d_offset_y = parse_int_env(
            "AMADEUS_DESKTOP_COMPANION_LIVE2D_OFFSET_Y",
            parse_int_value(desktop_config.get("companionLive2dOffsetY"), DESKTOP_COMPANION_LIVE2D_OFFSET_Y),
        )
        snapshot = self._runtime_config_snapshot()
        logger.info(
            "Loaded runtime configuration runtimeConfig=%s reason=%s effectiveConfig=%s",
            self.runtime_config_path,
            reason,
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
        )

        return snapshot

    def reload_runtime_config(self) -> dict[str, Any]:
        logger.info("Reloading runtime memory configuration runtimeConfig=%s", self.runtime_config_path)
        return {
            "runtimeConfig": str(self.runtime_config_path),
            "config": self._load_runtime_config(reason="reload"),
        }

    def reload_harness_registry(self) -> None:
        logger.info("Reloading harness registry harnessesConfig=%s", self.harnesses_config_path)
        self.harness_registry = HarnessRegistry.from_config(
            self.harnesses_config_path,
            audio_library=self.audio_runtime.library if self.audio_runtime is not None else None,
        )

    def _configure_runtime_memory_provider(
        self,
        provider_name: str,
        *,
        global_retrieval_fallback: bool,
        vector_retrieval_enabled: bool,
        vector_candidate_limit: int,
    ) -> None:
        normalized = normalize_runtime_memory_provider_name(provider_name)
        fallback_enabled = bool(global_retrieval_fallback)
        vector_enabled = bool(vector_retrieval_enabled)
        bounded_vector_candidate_limit = max(1, min(500, int(vector_candidate_limit)))
        embedding_provider = create_local_bge_m3_embedding_provider(
            providers_config_path=DEFAULT_PROVIDERS_CONFIG_PATH,
            repo_root=REPO_ROOT,
        ) if vector_enabled else None
        embedding_signature = runtime_embedding_provider_signature(embedding_provider)
        if (
            getattr(self, "memory_provider_name", None) == normalized
            and getattr(self, "memory_global_retrieval_fallback", None) == fallback_enabled
            and getattr(self, "memory_vector_retrieval_enabled", None) == vector_enabled
            and getattr(self, "memory_vector_candidate_limit", None) == bounded_vector_candidate_limit
            and getattr(self, "memory_embedding_provider_signature", None) == embedding_signature
            and getattr(getattr(self, "memory_manager", None), "runtime_provider", None) is not None
        ):
            return
        self.memory_provider_name = normalized
        self.memory_global_retrieval_fallback = fallback_enabled
        self.memory_vector_retrieval_enabled = vector_enabled
        self.memory_vector_candidate_limit = bounded_vector_candidate_limit
        self.memory_embedding_provider = embedding_provider
        self.memory_embedding_provider_signature = embedding_signature
        self.memory_manager = RuntimeMemoryManager(
            create_runtime_memory_provider(
                self.memory_store,
                provider_name=normalized,
                global_retrieval_fallback=fallback_enabled,
                embedding_provider=embedding_provider,
                vector_retrieval_enabled=vector_enabled,
                vector_candidate_limit=bounded_vector_candidate_limit,
            ),
            external_providers=self.external_memory_providers,
        )
        logger.info(
            "Configured runtime memory provider provider=%s globalRetrievalFallback=%s",
            self.memory_provider_name,
            self.memory_global_retrieval_fallback,
        )

    def reload_tool_registry(self) -> dict[str, Any]:
        logger.info("Reloading tool registry toolsConfig=%s", self.tools_config_path)
        self.tool_registry = ToolRegistry(
            config_path=self.tools_config_path,
            memory_tool_specs=self.memory_manager.get_tool_specs(),
        )
        self._system_prompt_cache.clear()
        self.system_prompt = self._build_system_prompt()
        self.context_assembler = self._build_context_assembler()
        return {
            "toolsConfig": str(self.tools_config_path),
            "toolCount": len(self.tool_permission_state()),
            "schemaCount": len(self.enabled_tool_schemas()),
        }

    def _build_context_assembler(self, config: ContextAssemblerConfig | None = None) -> ContextAssembler:
        return ContextAssembler(
            self.memory_store,
            self.system_prompt,
            config,
            memory_manager=self.memory_manager,
        )

    def running_turn_snapshot(self, session_id: str) -> dict[str, Any]:
        with self._running_turns_lock:
            running = self._running_turns_by_session.get(session_id)
            if not running:
                return {
                    "sessionId": session_id,
                    "running": False,
                    "turnId": None,
                    "startedAt": None,
                    "cancelRequested": False,
                }
            return {
                "sessionId": running.session_id,
                "running": True,
                "turnId": running.turn_id,
                "startedAt": running.started_at,
                "cancelRequested": running.cancel_event.is_set(),
            }

    def cancel_turn(self, session_id: str, turn_id: str | None = None) -> dict[str, Any]:
        with self._running_turns_lock:
            running = self._running_turns_by_session.get(session_id)
            if not running:
                return {
                    "sessionId": session_id,
                    "turnId": turn_id,
                    "cancelled": False,
                    "reason": "no_running_turn",
                }
            if turn_id and running.turn_id != turn_id:
                return {
                    "sessionId": session_id,
                    "turnId": turn_id,
                    "runningTurnId": running.turn_id,
                    "cancelled": False,
                    "reason": "turn_id_mismatch",
                }
            running.cancel_event.set()
            return {
                "sessionId": running.session_id,
                "turnId": running.turn_id,
                "cancelled": True,
                "reason": "cancel_requested",
            }

    def _register_running_turn(self, session_id: str, turn_id: str) -> threading.Event:
        cancel_event = threading.Event()
        running = RunningTurn(
            session_id=session_id,
            turn_id=turn_id,
            cancel_event=cancel_event,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._running_turns_lock:
            self._running_turns_by_session[session_id] = running
        return cancel_event

    def _finish_running_turn(self, session_id: str, turn_id: str) -> None:
        with self._running_turns_lock:
            running = self._running_turns_by_session.get(session_id)
            if running and running.turn_id == turn_id:
                self._running_turns_by_session.pop(session_id, None)

    def _turn_cancelled_event(self, session_id: str, turn_id: str, phase: str) -> AgentEvent:
        logger.info("Agent turn cancelled sessionId=%s turnId=%s phase=%s", session_id, turn_id, phase)
        return AgentEvent(
            "agent.turn.cancelled",
            {
                "sessionId": session_id,
                "turnId": turn_id,
                "phase": phase,
            },
        )

    def run_turn(
        self,
        session_id: str,
        user_text: str,
        request_permission: PermissionRequester,
        active_skills: list[str] | None = None,
    ) -> Iterable[AgentEvent]:
        normalized_text = user_text.strip()
        if not normalized_text:
            logger.info("Rejecting empty turn sessionId=%s", session_id)
            yield AgentEvent("error", {"code": "empty_message", "message": "Message text is required."})
            return
        if not self.api_key:
            logger.info("Rejecting turn due to missing API key sessionId=%s", session_id)
            yield AgentEvent("error", {"code": "missing_api_key", "message": "OPENAI_API_KEY is not configured."})
            return
        allowed_skills = self._role_allowed_skills(session_id)
        normalized_skills = normalize_requested_skills(active_skills)
        _skill_prompt_block, resolved_skills = self.skill_catalog.build_prompt_block(
            normalized_skills,
            allowed_skills=allowed_skills,
        )
        if not resolved_skills.ok:
            if resolved_skills.ambiguous:
                logger.info(
                    "Rejecting turn due to ambiguous skills before start sessionId=%s ambiguous=%s",
                    session_id,
                    list(resolved_skills.ambiguous),
                )
                yield AgentEvent(
                    "error",
                    {
                        "code": "ambiguous_skill",
                        "message": f"Ambiguous skills requested: {', '.join(resolved_skills.ambiguous)}",
                    },
                )
                return
            logger.info(
                "Rejecting turn due to missing skills before start sessionId=%s missing=%s",
                session_id,
                list(resolved_skills.missing),
            )
            yield AgentEvent(
                "error",
                {
                    "code": "skill_not_found",
                    "message": f"Unknown skills requested: {', '.join(resolved_skills.missing)}",
                },
            )
            return

        turn_id = str(uuid4())
        cancel_event = self._register_running_turn(session_id, turn_id)
        yield AgentEvent("agent.turn.started", {
            "sessionId": session_id,
            "turnId": turn_id,
            "startedAt": datetime.now(timezone.utc).isoformat(),
        })
        try:
            yield from self._run_turn_impl(session_id, user_text, request_permission, active_skills, turn_id, cancel_event)
        finally:
            self._finish_running_turn(session_id, turn_id)

    def _run_turn_impl(
        self,
        session_id: str,
        user_text: str,
        request_permission: PermissionRequester,
        active_skills: list[str] | None,
        turn_id: str,
        cancel_event: threading.Event,
    ) -> Iterable[AgentEvent]:
        normalized_text = user_text.strip()
        if not normalized_text:
            logger.info("Rejecting empty turn sessionId=%s", session_id)
            yield AgentEvent("error", {"code": "empty_message", "message": "Message text is required."})
            return

        if not self.api_key:
            logger.info("Rejecting turn due to missing API key sessionId=%s", session_id)
            yield AgentEvent("error", {"code": "missing_api_key", "message": "OPENAI_API_KEY is not configured."})
            return

        allowed_skills = self._role_allowed_skills(session_id)
        normalized_skills = normalize_requested_skills(active_skills)
        skill_prompt_block, resolved_skills = self.skill_catalog.build_prompt_block(
            normalized_skills,
            allowed_skills=allowed_skills,
        )
        if not resolved_skills.ok:
            if resolved_skills.ambiguous:
                logger.info(
                    "Rejecting turn due to ambiguous skills sessionId=%s turnId=%s ambiguous=%s",
                    session_id,
                    turn_id,
                    list(resolved_skills.ambiguous),
                )
                yield AgentEvent(
                    "error",
                    {
                        "code": "ambiguous_skill",
                        "message": f"Ambiguous skills requested: {', '.join(resolved_skills.ambiguous)}",
                    },
                )
                return
            logger.info(
                "Rejecting turn due to missing skills sessionId=%s turnId=%s missing=%s",
                session_id,
                turn_id,
                list(resolved_skills.missing),
            )
            yield AgentEvent(
                "error",
                {
                    "code": "skill_not_found",
                    "message": f"Unknown skills requested: {', '.join(resolved_skills.missing)}",
                },
            )
            return

        logger.info(
            "Starting agent turn sessionId=%s turnId=%s userTextChars=%s activeSkills=%s",
            session_id,
            turn_id,
            len(normalized_text),
            [skill.identifier for skill in resolved_skills.loaded],
        )
        history, context_diagnostics = self._load_turn_history(
            session_id,
            normalized_text,
            skill_prompt_block=skill_prompt_block,
        )
        budget_summary_event = self._maybe_compact_for_context_budget(session_id, history, phase="turn_start")
        if budget_summary_event:
            history, context_diagnostics = self._load_turn_history(
                session_id,
                normalized_text,
                skill_prompt_block=skill_prompt_block,
            )
        user_message_id = self.memory_store.save(session_id, "user", normalized_text)
        yield AgentEvent("memory.updated", {"memoryMessages": self.memory_store.count(session_id)})
        yield self._memory_context_used_event(session_id, turn_id, context_diagnostics)
        if budget_summary_event:
            yield budget_summary_event

        yield from self._emit_assistant_state(session_id, turn_id, "thinking")

        guardrail = ToolLoopGuardrail()
        tool_iterations = 0
        final_response_message: dict[str, Any] | None = None
        while True:
            try:
                tool_decision = self._request_tool_decision(session_id, history)
            except RuntimeError as error:
                phase = f"tool_decision_{tool_iterations + 1}"
                if self._handle_context_overflow(session_id, phase, error):
                    history, context_diagnostics = self._load_turn_history(
                        session_id,
                        normalized_text,
                        current_user_already_saved=True,
                        skill_prompt_block=skill_prompt_block,
                    )
                    yield self._memory_context_used_event(session_id, turn_id, context_diagnostics, phase=f"{phase}_retry")
                    try:
                        tool_decision = self._request_tool_decision(session_id, history)
                    except RuntimeError as retry_error:
                        logger.info("Tool-decision provider retry failed sessionId=%s turnId=%s iteration=%s error=%s", session_id, turn_id, tool_iterations + 1, retry_error)
                        yield from self._emit_assistant_state(session_id, turn_id, "error")
                        yield AgentEvent("error", {"code": "provider_error", "message": str(retry_error)})
                        return
                else:
                    logger.info("Tool-decision provider error sessionId=%s turnId=%s iteration=%s error=%s", session_id, turn_id, tool_iterations + 1, error)
                    yield from self._emit_assistant_state(session_id, turn_id, "error")
                    yield AgentEvent("error", {"code": "provider_error", "message": str(error)})
                    return

            tool_calls = tool_decision.get("tool_calls") or []
            logger.info(
                "Received tool decision sessionId=%s turnId=%s iteration=%s toolCallCount=%s",
                session_id,
                turn_id,
                tool_iterations + 1,
                len(tool_calls),
            )
            if cancel_event.is_set():
                yield self._turn_cancelled_event(session_id, turn_id, "tool_decision")
                yield from self._emit_assistant_state(session_id, turn_id, "idle")
                return
            if not tool_calls:
                final_response_message = tool_decision
                break
            if tool_iterations >= self.agent_max_tool_iterations:
                logger.info(
                    "Stopping agent turn at max tool iterations sessionId=%s turnId=%s maxToolIterations=%s",
                    session_id,
                    turn_id,
                    self.agent_max_tool_iterations,
                )
                yield from self._emit_assistant_state(session_id, turn_id, "error")
                yield AgentEvent("error", {
                    "code": "max_tool_iterations",
                    "message": f"Tool loop reached maxToolIterations={self.agent_max_tool_iterations}.",
                    "maxToolIterations": self.agent_max_tool_iterations,
                })
                return

            tool_reasoning_content = tool_decision.get("reasoning_content")
            if isinstance(tool_reasoning_content, str) and tool_reasoning_content:
                yield AgentEvent("assistant.reasoning.delta", {"text": tool_reasoning_content, "turnId": turn_id})
            assistant_tool_message = self._assistant_history_message(tool_decision)
            history.append(assistant_tool_message)
            self.memory_store.save(
                session_id,
                "assistant",
                str(assistant_tool_message.get("content") or ""),
                tool_calls=assistant_tool_message.get("tool_calls") if isinstance(assistant_tool_message.get("tool_calls"), list) else None,
            )
            tool_iterations += 1

            for tool_call in tool_calls:
                if cancel_event.is_set():
                    yield self._turn_cancelled_event(session_id, turn_id, "before_tool")
                    yield from self._emit_assistant_state(session_id, turn_id, "idle")
                    return
                for event in self._execute_tool_call(session_id, turn_id, user_message_id, tool_call, request_permission, history, guardrail, cancel_event):
                    yield event
                if cancel_event.is_set():
                    yield self._turn_cancelled_event(session_id, turn_id, "after_tool")
                    yield from self._emit_assistant_state(session_id, turn_id, "idle")
                    return

        yield from self._emit_assistant_state(session_id, turn_id, "speaking")
        assistant_text = ""
        final_response_content = str((final_response_message or {}).get("content") or "")
        final_reasoning_content = str((final_response_message or {}).get("reasoning_content") or "")
        if final_response_content:
            if final_reasoning_content:
                yield AgentEvent("assistant.reasoning.delta", {"text": final_reasoning_content, "turnId": turn_id})
            assistant_text = final_response_content
            yield AgentEvent("assistant.delta", {"text": final_response_content, "turnId": turn_id})
        else:
            try:
                for delta in self._stream_final_response(history):
                    if cancel_event.is_set():
                        yield self._turn_cancelled_event(session_id, turn_id, "final_response")
                        yield from self._emit_assistant_state(session_id, turn_id, "idle")
                        return
                    reasoning_delta = delta.reasoning_content if isinstance(delta, ChatStreamDelta) else ""
                    content_delta = delta.content if isinstance(delta, ChatStreamDelta) else str(delta)
                    if reasoning_delta:
                        yield AgentEvent("assistant.reasoning.delta", {"text": reasoning_delta, "turnId": turn_id})
                    if content_delta:
                        assistant_text += content_delta
                        yield AgentEvent("assistant.delta", {"text": content_delta, "turnId": turn_id})
            except RuntimeError as error:
                if self._handle_context_overflow(session_id, "final_response", error):
                    history, context_diagnostics = self._load_turn_history(
                        session_id,
                        normalized_text,
                        current_user_already_saved=True,
                        skill_prompt_block=skill_prompt_block,
                    )
                    yield self._memory_context_used_event(session_id, turn_id, context_diagnostics, phase="final_response_retry")
                    assistant_text = ""
                    try:
                        for delta in self._stream_final_response(history):
                            if cancel_event.is_set():
                                yield self._turn_cancelled_event(session_id, turn_id, "final_response_retry")
                                yield from self._emit_assistant_state(session_id, turn_id, "idle")
                                return
                            reasoning_delta = delta.reasoning_content if isinstance(delta, ChatStreamDelta) else ""
                            content_delta = delta.content if isinstance(delta, ChatStreamDelta) else str(delta)
                            if reasoning_delta:
                                yield AgentEvent("assistant.reasoning.delta", {"text": reasoning_delta, "turnId": turn_id})
                            if content_delta:
                                assistant_text += content_delta
                                yield AgentEvent("assistant.delta", {"text": content_delta, "turnId": turn_id})
                    except RuntimeError as retry_error:
                        logger.info("Final response provider retry failed sessionId=%s turnId=%s error=%s", session_id, turn_id, retry_error)
                        yield from self._emit_assistant_state(session_id, turn_id, "error")
                        yield AgentEvent("error", {"code": "provider_error", "message": str(retry_error)})
                        return
                else:
                    logger.info("Final response provider error sessionId=%s turnId=%s error=%s", session_id, turn_id, error)
                    yield from self._emit_assistant_state(session_id, turn_id, "error")
                    yield AgentEvent("error", {"code": "provider_error", "message": str(error)})
                    return

        history.append({"role": "assistant", "content": assistant_text})
        assistant_message_id = self.memory_store.save(session_id, "assistant", assistant_text)
        self.memory_store.finish_plan_run(
            session_id=session_id,
            turn_id=turn_id,
            assistant_message_id=assistant_message_id,
        )
        summary_event = self._maybe_compact_conversation(session_id, reason="turn_end")
        logger.info(
            "Completed agent turn sessionId=%s turnId=%s assistantTextChars=%s memoryMessages=%s",
            session_id,
            turn_id,
            len(assistant_text),
            self.memory_store.count(session_id),
        )
        yield AgentEvent("memory.updated", {"memoryMessages": self.memory_store.count(session_id)})
        yield AgentEvent("assistant.message", {"text": assistant_text, "turnId": turn_id})
        if summary_event:
            yield summary_event
        review_event = self._maybe_review_memory(session_id)
        if review_event:
            yield review_event

        if self.audio_runtime:
            audio_result = self.audio_runtime.speak(AudioOutputCommand(text=assistant_text, format="wav"))
            if not isinstance(audio_result, AudioFallbackResult):
                logger.info("Runtime audio ready sessionId=%s turnId=%s durationMs=%s", session_id, turn_id, audio_result.duration_ms)
                if audio_result.lipsync_cues:
                    yield AgentEvent("audio.lipsync-cues", {
                        "source": "runtime_audio",
                        "audioUrl": audio_result.audio_url,
                        "durationMs": audio_result.duration_ms,
                        "cues": audio_result.lipsync_cues,
                    })
                yield AgentEvent("audio.tts-ready", {
                    "audioUrl": audio_result.audio_url,
                    "durationMs": audio_result.duration_ms,
                })
            else:
                logger.info("Runtime audio fallback sessionId=%s turnId=%s fallback=%s reason=%s", session_id, turn_id, audio_result.fallback, audio_result.reason)

        yield from self._emit_assistant_state(session_id, turn_id, "idle")

    def tool_permission_state(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return self.tool_registry.permission_state(self._effective_allowed_tool_names(session_id))

    def enabled_tool_schemas(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return self.tool_registry.enabled_schemas(self._effective_allowed_tool_names(session_id))

    def skill_summaries(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return self.skill_catalog.skill_summaries(allowed_skills=self._role_allowed_skills(session_id))

    def view_skill(self, name: str, session_id: str | None = None) -> dict[str, Any] | None:
        return self.skill_catalog.view_skill(name, allowed_skills=self._role_allowed_skills(session_id))

    def role_allows_tool(self, session_id: str | None, tool_name: str) -> bool:
        allowed_names = self._effective_allowed_tool_names(session_id)
        return allowed_names is None or tool_name in allowed_names

    @contextlib.contextmanager
    def worker_tool_scope(self, allowed_tool_names: set[str] | frozenset[str] | None) -> Iterable[None]:
        scope = None
        if allowed_tool_names is not None:
            scope = WorkerRuntimeScope(
                worker_profile="worker",
                allowed_toolsets=(),
                allowed_tool_names=frozenset(allowed_tool_names),
            )
        with self.worker_runtime_scope(scope):
            yield

    @contextlib.contextmanager
    def worker_runtime_scope(self, scope: WorkerRuntimeScope | None) -> Iterable[None]:
        token = _WORKER_RUNTIME_SCOPE.set(scope)
        try:
            yield
        finally:
            _WORKER_RUNTIME_SCOPE.reset(token)

    def validate_worker_runtime_scope(self, session_id: str | None, scope: WorkerRuntimeScope) -> str | None:
        if not scope.workspace_path:
            return None
        worker_root = self._resolve_workspace_root(scope.workspace_path)
        if worker_root is None:
            return f"Worker workspace is not an existing directory: {scope.workspace_path}"
        session_root = self._role_workspace_root_for_session(session_id)
        if not self._path_is_inside(worker_root, session_root):
            return (
                "Worker workspace must be inside the session workspace: "
                f"{worker_root} is outside {session_root}"
            )
        return None

    def _role_runtime_scope(self, session_id: str | None = None) -> RoleRuntimeScope:
        return normalize_role_runtime_scope(self.memory_store.role_runtime_scope_for_session(session_id))

    def _role_allowed_tool_names(self, session_id: str | None = None) -> set[str] | None:
        scope = self._role_runtime_scope(session_id)
        if not scope.tools and not scope.mcp_servers:
            return None
        names = {
            item["name"]
            for item in self.tool_registry.permission_state()
            if isinstance(item.get("name"), str) and role_allows_tool(scope, str(item["name"]))
        }
        return names

    def _effective_allowed_tool_names(self, session_id: str | None = None) -> set[str] | None:
        role_names = self._role_allowed_tool_names(session_id)
        worker_scope = _WORKER_RUNTIME_SCOPE.get()
        if worker_scope is None:
            return role_names
        worker_names = worker_scope.allowed_tool_names
        if role_names is None:
            return set(worker_names)
        return set(role_names).intersection(worker_names)

    def _role_allowed_skills(self, session_id: str | None = None) -> set[str] | None:
        scope = self._role_runtime_scope(session_id)
        return set(scope.skills) if scope.skills else None

    def tool_audit_records(self) -> list[ToolAuditRecord]:
        return self.tool_audit_log.records()

    def persisted_tool_audit_records(self, session_id: str | None = None, limit: int = 100) -> list[ToolAuditRecord]:
        return self.tool_audit_store.load(session_id=session_id, limit=limit)

    def query_tool_audit_records(
        self,
        *,
        session_id: str | None = None,
        tool_name: str | None = None,
        decision: str | None = None,
        ok: bool | None = None,
        failure_code: str | None = None,
        limit: int = 100,
    ) -> list[ToolAuditRecord]:
        return self.tool_audit_store.query(
            session_id=session_id,
            tool_name=tool_name,
            decision=decision,
            ok=ok,
            failure_code=failure_code,
            limit=limit,
        )

    def memory_context_diagnostics(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        bounded_limit = self.context_diagnostics_limit if limit is None else max(1, min(self.context_diagnostics_limit, int(limit)))
        with self._context_diagnostics_lock:
            records = list(self._context_diagnostics_by_session.get(session_id, ()))
        return json.loads(json.dumps(records[-bounded_limit:]))

    def workspace_epoch(self, session_id: str) -> int:
        with self._workspace_epoch_lock:
            return self._workspace_epoch_by_session.get(session_id, 0)

    def _bump_workspace_epoch(
        self,
        session_id: str,
        *,
        reason: str,
        tool_name: str,
        tool_call_id: str | None = None,
    ) -> int:
        with self._workspace_epoch_lock:
            next_epoch = self._workspace_epoch_by_session.get(session_id, 0) + 1
            self._workspace_epoch_by_session[session_id] = next_epoch
        logger.info(
            "Bumped workspace epoch sessionId=%s epoch=%s reason=%s toolName=%s toolCallId=%s",
            session_id,
            next_epoch,
            reason,
            tool_name,
            tool_call_id,
        )
        return next_epoch

    def compact_conversation(self, session_id: str, force: bool = True) -> dict[str, Any]:
        event = self._maybe_compact_conversation(session_id, force=force)
        return {
            "compacted": event is not None,
            "event": event.to_runtime_event(session_id) if event else None,
            "summary": event.payload["summary"] if event else self.memory_store.load_conversation_summary(session_id),
        }

    def _memory_context_used_event(
        self,
        session_id: str,
        turn_id: str,
        diagnostics: dict[str, Any],
        *,
        phase: str = "turn_start",
    ) -> AgentEvent:
        payload = self._context_diagnostics_payload(session_id, turn_id, diagnostics, phase=phase)
        self._record_context_diagnostics(session_id, payload)
        return AgentEvent("memory.context.used", payload)

    def _context_diagnostics_payload(
        self,
        session_id: str,
        turn_id: str,
        diagnostics: dict[str, Any],
        *,
        phase: str,
    ) -> dict[str, Any]:
        payload = json.loads(json.dumps(diagnostics))
        payload["sessionId"] = session_id
        payload["turnId"] = turn_id
        payload["phase"] = phase
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        return payload

    def _record_context_diagnostics(self, session_id: str, payload: dict[str, Any]) -> None:
        record = json.loads(json.dumps(payload))
        with self._context_diagnostics_lock:
            buffer = self._context_diagnostics_by_session.get(session_id)
            if buffer is None or buffer.maxlen != self.context_diagnostics_limit:
                buffer = deque(list(buffer or ())[-self.context_diagnostics_limit:], maxlen=self.context_diagnostics_limit)
                self._context_diagnostics_by_session[session_id] = buffer
            buffer.append(record)

    def _resize_context_diagnostics_buffers(self, limit: int) -> None:
        bounded_limit = max(1, int(limit))
        with self._context_diagnostics_lock:
            self._context_diagnostics_by_session = {
                session_id: deque(list(records)[-bounded_limit:], maxlen=bounded_limit)
                for session_id, records in self._context_diagnostics_by_session.items()
            }

    def _runtime_config_snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            "agent": {
                "maxToolIterations": self.agent_max_tool_iterations,
            },
            "memory": {
                "provider": self.memory_provider_name,
                "activeProvider": self.memory_manager.active_provider_name,
                "globalRetrievalFallback": self.memory_global_retrieval_fallback,
                "vectorRetrieval": self.memory_vector_retrieval_enabled,
                "vectorCandidateLimit": self.memory_vector_candidate_limit,
                "embeddingProvider": getattr(self.memory_embedding_provider, "provider", ""),
                "embeddingModel": getattr(self.memory_embedding_provider, "model_id", ""),
            },
            "context": {
                "maxTokens": self.context_max_tokens,
                "compactionTriggerRatio": self.context_compaction_trigger_ratio,
                "recentMessageTargetRatio": self.context_recent_message_target_ratio,
                "summaryChars": self.context_summary_chars,
                "memoryItemLimit": self.context_memory_item_limit,
                "memoryItemChars": self.context_memory_item_chars,
                "retrievalLimit": self.context_retrieval_limit,
                "retrievalSnippetChars": self.context_retrieval_snippet_chars,
                "diagnosticsLimit": self.context_diagnostics_limit,
            },
            "summary": {
                "triggerMessageCount": self.summary_trigger_message_count,
                "keepRecentTurns": self.summary_keep_recent_turns,
                "minKeepRecentTurns": self.summary_min_keep_recent_turns,
                "sourceMaxMessages": self.summary_source_max_messages,
                "failureCooldownSeconds": self.summary_failure_cooldown_seconds,
            },
            "memoryReview": {
                "triggerMessageCount": self.memory_review_trigger_message_count,
                "sourceMaxMessages": self.memory_review_source_max_messages,
                "existingMemoryLimit": self.memory_review_existing_memory_limit,
                "pendingLimit": self.memory_review_pending_limit,
                "maxCandidates": self.memory_review_max_candidates,
                "successCooldownSeconds": self.memory_review_success_cooldown_seconds,
                "failureCooldownSeconds": self.memory_review_failure_cooldown_seconds,
            },
            "tasks": {
                "workerApprovalActionTtlSeconds": self.worker_approval_action_ttl_seconds,
            },
            "desktop": {
                "companionLive2dScale": self.desktop_companion_live2d_scale,
                "companionLive2dOffsetX": self.desktop_companion_live2d_offset_x,
                "companionLive2dOffsetY": self.desktop_companion_live2d_offset_y,
            },
        }

    def review_memory(self, session_id: str, force: bool = True) -> dict[str, Any]:
        trigger = "manual" if force else "auto"
        started_at = perf_counter()
        job = self.memory_store.start_memory_review_job(session_id, trigger)
        job_id = int(job["jobId"])

        def finish_job(
            status: str,
            result: dict[str, Any],
            *,
            reason: str | None = None,
            error: str | None = None,
            source_messages: list[dict[str, Any]] | None = None,
            proposed_candidate_count: int = 0,
            saved_candidate_count: int = 0,
            suppressed_candidate_count: int = 0,
        ) -> dict[str, Any]:
            source_start_id = None
            source_end_id = None
            source_message_count = 0
            if source_messages:
                message_ids = [int(message.get("id", 0)) for message in source_messages if int(message.get("id", 0)) > 0]
                if message_ids:
                    source_start_id = min(message_ids)
                    source_end_id = max(message_ids)
                source_message_count = len(source_messages)
            finished_job = self.memory_store.finish_memory_review_job(
                job_id,
                status,
                reason=reason,
                error=error,
                source_message_start_id=source_start_id,
                source_message_end_id=source_end_id,
                source_message_count=source_message_count,
                proposed_candidate_count=proposed_candidate_count,
                saved_candidate_count=saved_candidate_count,
                suppressed_candidate_count=suppressed_candidate_count,
                duration_ms=round((perf_counter() - started_at) * 1000),
            )
            result["job"] = finished_job
            result["jobId"] = finished_job["jobId"]
            return result

        if not self.api_key:
            logger.info("Skipping memory review due to missing API key sessionId=%s", session_id)
            return finish_job(
                "skipped",
                {"reviewed": False, "sessionId": session_id, "error": "OPENAI_API_KEY is not configured."},
                reason="missing_api_key",
                error="OPENAI_API_KEY is not configured.",
            )

        now = perf_counter()
        cooldown_until = self._memory_review_cooldown_until.get(session_id, 0)
        if not force and cooldown_until > now:
            cooldown_remaining_ms = round((cooldown_until - now) * 1000)
            logger.info("Skipping memory review during cooldown sessionId=%s cooldownRemainingMs=%s", session_id, cooldown_remaining_ms)
            return finish_job(
                "skipped",
                {
                    "reviewed": False,
                    "sessionId": session_id,
                    "reason": "cooldown",
                    "cooldownRemainingMs": cooldown_remaining_ms,
                    "candidates": [],
                    "candidateCount": 0,
                },
                reason="cooldown",
            )

        total_messages = self.memory_store.count(session_id)
        if not force and total_messages < self.memory_review_trigger_message_count:
            logger.info(
                "Skipping memory review below threshold sessionId=%s messageCount=%s threshold=%s",
                session_id,
                total_messages,
                self.memory_review_trigger_message_count,
            )
            return finish_job(
                "skipped",
                {
                    "reviewed": False,
                    "sessionId": session_id,
                    "reason": "below_threshold",
                    "messageCount": total_messages,
                    "threshold": self.memory_review_trigger_message_count,
                    "candidates": [],
                    "candidateCount": 0,
                },
                reason="below_threshold",
            )

        latest_message_id = self.memory_store.latest_message_id(session_id)
        if not force and latest_message_id <= self._memory_review_last_message_id.get(session_id, 0):
            logger.info("Skipping memory review no new messages sessionId=%s latestMessageId=%s", session_id, latest_message_id)
            return finish_job(
                "skipped",
                {
                    "reviewed": False,
                    "sessionId": session_id,
                    "reason": "no_new_messages",
                    "latestMessageId": latest_message_id,
                    "candidates": [],
                    "candidateCount": 0,
                },
                reason="no_new_messages",
            )

        messages = self.memory_store.load_detailed(session_id, limit=self.memory_review_source_max_messages)
        if not messages:
            logger.info("Skipping memory review because session has no messages sessionId=%s", session_id)
            return finish_job(
                "skipped",
                {
                    "reviewed": False,
                    "sessionId": session_id,
                    "reason": "no_messages",
                    "candidates": [],
                    "candidateCount": 0,
                },
                reason="no_messages",
            )

        existing_items = self.memory_store.list_memory_items(limit=self.memory_review_existing_memory_limit)
        pending_candidates = self.memory_store.list_memory_review_candidates(
            session_id=session_id,
            status="pending",
            limit=self.memory_review_pending_limit,
        )

        try:
            proposed_candidates = self._request_memory_review(
                session_id,
                messages,
                existing_items,
                pending_candidates,
            )
        except RuntimeError as error:
            logger.info("Memory review failed sessionId=%s error=%s", session_id, error)
            if not force:
                self._memory_review_cooldown_until[session_id] = perf_counter() + self.memory_review_failure_cooldown_seconds
            return finish_job(
                "failed",
                {"reviewed": False, "sessionId": session_id, "error": str(error), "candidates": [], "candidateCount": 0},
                error=str(error),
                source_messages=messages,
            )

        saved_candidates = []
        promoted_items = []
        suppressed_candidate_count = 0
        for proposed in proposed_candidates[:self.memory_review_max_candidates]:
            if not isinstance(proposed, dict):
                continue
            scope = proposed.get("scope")
            content = proposed.get("content")
            if not isinstance(scope, str) or not isinstance(content, str):
                continue
            confidence = proposed.get("confidence", 0.7)
            if not isinstance(confidence, (int, float)):
                confidence = 0.7
            reason = proposed.get("reason") if isinstance(proposed.get("reason"), str) else None
            scope_reason = proposed.get("scopeReason") if isinstance(proposed.get("scopeReason"), str) else None
            safety_labels_raw = proposed.get("safetyLabels")
            safety_labels = [
                label for label in safety_labels_raw if isinstance(label, str)
            ] if isinstance(safety_labels_raw, list) else None
            retention_type = proposed.get("retentionType") if isinstance(proposed.get("retentionType"), str) else None
            source_start = proposed.get("sourceMessageStartId")
            source_end = proposed.get("sourceMessageEndId")
            source_start_id = source_start if isinstance(source_start, int) else None
            source_end_id = source_end if isinstance(source_end, int) else None
            safety_decision = evaluate_memory_candidate(scope, content, reason)
            if not safety_decision.allowed:
                suppressed_candidate_count += 1
                logger.info(
                    "Suppressed unsafe memory review candidate sessionId=%s scope=%s reason=%s contentChars=%s",
                    session_id,
                    scope,
                    safety_decision.reason,
                    len(content),
                )
                continue

            try:
                candidate = self.memory_store.save_memory_review_candidate(
                    session_id,
                    scope,
                    content,
                    confidence=float(confidence),
                    reason=reason,
                    scope_reason=scope_reason,
                    safety_labels=safety_labels,
                    retention_type=retention_type,
                    source_message_start_id=source_start_id,
                    source_message_end_id=source_end_id,
                )
            except ValueError as error:
                logger.info("Skipping invalid memory review candidate sessionId=%s error=%s", session_id, error)
                continue
            if candidate.get("suppressed"):
                suppressed_candidate_count += 1
                continue
            accepted = self.memory_store.accept_memory_review_candidate(int(candidate["candidateId"]))
            accepted_candidate = accepted.get("candidate") if isinstance(accepted.get("candidate"), dict) else candidate
            saved_candidates.append(accepted_candidate)
            if accepted.get("accepted") and isinstance(accepted.get("item"), dict):
                promoted_items.append(accepted["item"])

        if not force:
            self._memory_review_cooldown_until[session_id] = perf_counter() + self.memory_review_success_cooldown_seconds
            self._memory_review_last_message_id[session_id] = latest_message_id
        logger.info(
            "Memory review completed sessionId=%s sourceMessages=%s proposedCandidates=%s savedCandidates=%s promotedItems=%s suppressedCandidates=%s",
            session_id,
            len(messages),
            len(proposed_candidates),
            len(saved_candidates),
            len(promoted_items),
            suppressed_candidate_count,
        )
        return finish_job(
            "completed",
            {
                "reviewed": True,
                "sessionId": session_id,
                "sourceMessageCount": len(messages),
                "proposedCandidateCount": len(proposed_candidates),
                "candidateCount": len(saved_candidates),
                "promotedItemCount": len(promoted_items),
                "suppressedCandidateCount": suppressed_candidate_count,
                "candidates": saved_candidates,
                "items": promoted_items,
            },
            source_messages=messages,
            proposed_candidate_count=len(proposed_candidates),
            saved_candidate_count=len(saved_candidates),
            suppressed_candidate_count=suppressed_candidate_count,
        )

    def _maybe_review_memory(self, session_id: str) -> AgentEvent | None:
        result = self.review_memory(session_id, force=False)
        if not result.get("reviewed"):
            return None
        return AgentEvent("memory.review.updated", result)

    def _load_history(
        self,
        session_id: str,
        *,
        system_context: str,
        covered_through_message_id: int,
        recent_turns: int | None = None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_context}]
        history_messages = self.memory_store.load_recent_turns(
            session_id,
            recent_turns if recent_turns is not None else self.summary_keep_recent_turns,
            after_message_id=covered_through_message_id or None,
        )
        messages.extend(self._sanitize_tool_pairs([self._provider_history_message(message) for message in history_messages]))
        logger.info(
            "Loaded agent history sessionId=%s coveredThroughMessageId=%s recentTurns=%s messageCount=%s",
            session_id,
            covered_through_message_id,
            recent_turns if recent_turns is not None else self.summary_keep_recent_turns,
            len(messages) - 1,
        )
        return messages

    def _load_turn_history(
        self,
        session_id: str,
        user_text: str,
        *,
        current_user_already_saved: bool = False,
        skill_prompt_block: str = "",
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assembled_context = self.context_assembler.assemble(
            session_id,
            user_text,
            base_system_prompt=self._build_system_prompt(session_id=session_id),
        )
        system_context = assembled_context.system_context
        role_prompt = self.memory_store.role_prompt_for_session(session_id)
        if role_prompt:
            system_context = f"{system_context}\n\n<role-context>\n{role_prompt}\n</role-context>"
        if skill_prompt_block:
            system_context = f"{system_context}\n\n{skill_prompt_block}"
        history = self._load_history(
            session_id,
            system_context=system_context,
            covered_through_message_id=assembled_context.covered_through_message_id,
        )
        if current_user_already_saved and len(history) > 1:
            last_message = history[-1]
            if last_message.get("role") == "user" and last_message.get("content") == user_text:
                history = history[:-1]
        diagnostics = assembled_context.diagnostics()
        history.append({"role": "user", "content": assembled_context.user_content})
        logger.info(
            "Assembled turn context sessionId=%s sourceCounts=%s coveredThroughMessageId=%s userContentChars=%s",
            session_id,
            diagnostics.get("sourceCounts", {}),
            assembled_context.covered_through_message_id,
            len(assembled_context.user_content),
        )
        return history, diagnostics

    def _load_history_for_budget(self, session_id: str) -> list[dict[str, Any]]:
        assembled_context = self.context_assembler.assemble(
            session_id,
            "",
            base_system_prompt=self._build_system_prompt(session_id=session_id),
        )
        return self._load_history(
            session_id,
            system_context=assembled_context.system_context,
            covered_through_message_id=assembled_context.covered_through_message_id,
        )

    def _maybe_compact_for_context_budget(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        *,
        phase: str,
    ) -> AgentEvent | None:
        report = self._context_budget_report(messages)
        logger.info(
            "Checked context budget sessionId=%s phase=%s estimatedTokens=%s triggerTokens=%s maxTokens=%s overBudget=%s",
            session_id,
            phase,
            report.estimated_tokens,
            report.trigger_tokens,
            report.max_tokens,
            report.over_budget,
        )
        if not report.over_budget:
            return None

        keep_recent_messages = self._budget_keep_recent_message_count(session_id)
        logger.info(
            "Forcing summary compaction for context budget sessionId=%s phase=%s keepRecentMessages=%s estimatedTokens=%s triggerTokens=%s",
            session_id,
            phase,
            keep_recent_messages,
            report.estimated_tokens,
            report.trigger_tokens,
        )
        return self._maybe_compact_conversation(
            session_id,
            force=True,
            keep_recent_messages=keep_recent_messages,
            reason=f"context_budget:{phase}",
        )

    def _context_budget_report(self, messages: list[dict[str, Any]]) -> ContextBudgetReport:
        max_tokens = max(1, int(self.context_max_tokens))
        trigger_tokens = max(1, int(max_tokens * self.context_compaction_trigger_ratio))
        estimated_tokens = estimate_messages_tokens(messages)
        return ContextBudgetReport(
            estimated_tokens=estimated_tokens,
            max_tokens=max_tokens,
            trigger_tokens=trigger_tokens,
            over_budget=estimated_tokens > trigger_tokens,
        )

    def _budget_keep_recent_message_count(self, session_id: str) -> int:
        trigger_tokens = max(1, int(self.context_max_tokens * self.context_compaction_trigger_ratio))
        target_tokens = max(1, int(trigger_tokens * self.context_recent_message_target_ratio))
        previous_summary = self.memory_store.load_conversation_summary(session_id)
        covered_through_id = int(previous_summary.get("coveredThroughMessageId", 0)) if previous_summary else 0
        uncovered_messages = self.memory_store.load_detailed(
            session_id,
            after_message_id=covered_through_id or None,
        )
        if not uncovered_messages:
            return 0

        user_turn_count = sum(1 for message in uncovered_messages if message.get("role") == "user")
        max_keep_turns = min(self.summary_keep_recent_turns, user_turn_count) if user_turn_count else 1
        min_keep_turns = min(
            max(0, self.summary_min_keep_recent_turns),
            SUMMARY_MAX_KEEP_RECENT_TURN_FLOOR,
            max_keep_turns,
        )
        keep_count = 0
        kept_tokens = 0
        first_candidate_turn = max(1, min_keep_turns)
        for turn_count in range(first_candidate_turn, max_keep_turns + 1):
            candidate_keep_count = self._recent_turn_message_count(uncovered_messages, turn_count)
            candidate_tail = uncovered_messages[-candidate_keep_count:] if candidate_keep_count else []
            candidate_tokens = estimate_messages_tokens([
                self._provider_history_message(message)
                for message in candidate_tail
            ])
            if turn_count > first_candidate_turn and candidate_tokens > target_tokens:
                break
            if min_keep_turns == 0 and candidate_tokens > target_tokens:
                break
            keep_count = candidate_keep_count
            kept_tokens = candidate_tokens
        if keep_count == 0 and min_keep_turns > 0:
            keep_count = self._recent_turn_message_count(uncovered_messages, first_candidate_turn)
            kept_tokens = estimate_messages_tokens([
                self._provider_history_message(message)
                for message in uncovered_messages[-keep_count:]
            ]) if keep_count else 0
        keep_count = self._aligned_keep_recent_message_count(uncovered_messages, keep_count)
        logger.info(
            "Selected budget-aware summary keep window sessionId=%s keepRecentMessages=%s keepRecentTurns=%s targetTokens=%s triggerTokens=%s keptTokens=%s uncoveredMessages=%s",
            session_id,
            keep_count,
            self.summary_keep_recent_turns,
            target_tokens,
            trigger_tokens,
            kept_tokens,
            len(uncovered_messages),
        )
        return keep_count

    def _maybe_compact_conversation(
        self,
        session_id: str,
        force: bool = False,
        *,
        keep_recent_messages: int | None = None,
        reason: str = "threshold",
    ) -> AgentEvent | None:
        cooldown_until = self._summary_failure_until.get(session_id, 0)
        now = perf_counter()
        if not force and cooldown_until > now:
            logger.info(
                "Skipping summary compaction during failure cooldown sessionId=%s cooldownRemainingMs=%s",
                session_id,
                round((cooldown_until - now) * 1000),
            )
            return None

        total_messages = self.memory_store.count(session_id)
        budget_report = self._context_budget_report(self._load_history_for_budget(session_id)) if not force else None
        should_compact_for_budget = bool(budget_report and budget_report.over_budget)
        if not force and total_messages <= self.summary_trigger_message_count and not should_compact_for_budget:
            logger.info(
                "Skipping summary compaction below thresholds sessionId=%s messageCount=%s threshold=%s estimatedTokens=%s triggerTokens=%s",
                session_id,
                total_messages,
                self.summary_trigger_message_count,
                budget_report.estimated_tokens if budget_report else None,
                budget_report.trigger_tokens if budget_report else None,
            )
            return None

        previous_summary = self.memory_store.load_conversation_summary(session_id)
        covered_through_id = int(previous_summary.get("coveredThroughMessageId", 0)) if previous_summary else 0
        if keep_recent_messages is None and should_compact_for_budget:
            keep_recent_messages = self._budget_keep_recent_message_count(session_id)
            reason = "context_budget:auto"
        uncovered_messages = self.memory_store.load_detailed(
            session_id,
            after_message_id=covered_through_id or None,
        )
        effective_keep_recent_messages = max(
            0,
            int(keep_recent_messages) if keep_recent_messages is not None else self._recent_turn_message_count(
                uncovered_messages,
                self.summary_keep_recent_turns,
            ),
        )
        if len(uncovered_messages) <= effective_keep_recent_messages:
            logger.info(
                "Skipping summary compaction no compactable window sessionId=%s uncoveredMessages=%s keepRecentMessages=%s reason=%s",
                session_id,
                len(uncovered_messages),
                effective_keep_recent_messages,
                reason,
            )
            return None

        compactable_messages = uncovered_messages[:-effective_keep_recent_messages] if effective_keep_recent_messages else uncovered_messages
        if len(compactable_messages) > self.summary_source_max_messages:
            compactable_messages = compactable_messages[-self.summary_source_max_messages:]
        compactable_messages, effective_keep_recent_messages = self._align_compaction_window_for_tool_pairs(
            uncovered_messages,
            compactable_messages,
            effective_keep_recent_messages,
        )
        if not compactable_messages:
            logger.info(
                "Skipping summary compaction after tool-pair window alignment sessionId=%s uncoveredMessages=%s keepRecentMessages=%s reason=%s",
                session_id,
                len(uncovered_messages),
                effective_keep_recent_messages,
                reason,
            )
            return None
        source_start_id = int(compactable_messages[0]["id"])
        source_end_id = int(compactable_messages[-1]["id"])

        try:
            summary_text = self._request_conversation_summary(previous_summary, compactable_messages)
        except RuntimeError as error:
            logger.info(
                "Summary compaction failed sessionId=%s sourceStartId=%s sourceEndId=%s error=%s",
                session_id,
                source_start_id,
                source_end_id,
                error,
            )
            if not force:
                self._summary_failure_until[session_id] = perf_counter() + self.summary_failure_cooldown_seconds
            return None

        summary = self.memory_store.save_conversation_summary(
            session_id,
            summary_text,
            summarized_message_count=total_messages,
            covered_message_count=int(previous_summary.get("coveredMessageCount", 0)) + len(compactable_messages) if previous_summary else len(compactable_messages),
            source_message_start_id=source_start_id,
            source_message_end_id=source_end_id,
            covered_through_message_id=source_end_id,
            model=self.model,
        )
        self._summary_failure_until.pop(session_id, None)
        logger.info(
            "Saved conversation summary sessionId=%s summaryId=%s sourceStartId=%s sourceEndId=%s coveredMessageCount=%s reason=%s keepRecentMessages=%s keepRecentTurns=%s",
            session_id,
            summary["summaryId"],
            source_start_id,
            source_end_id,
            summary["coveredMessageCount"],
            reason,
            effective_keep_recent_messages,
            self.summary_keep_recent_turns,
        )
        return AgentEvent("memory.summary.updated", {"summary": summary})

    def _handle_context_overflow(self, session_id: str, phase: str, error: RuntimeError) -> bool:
        if not is_context_overflow_error(error):
            return False
        keep_recent_messages = self._budget_keep_recent_message_count(session_id)
        logger.info(
            "Provider context overflow detected sessionId=%s phase=%s; forcing summary compaction keepRecentMessages=%s",
            session_id,
            phase,
            keep_recent_messages,
        )
        event = self._maybe_compact_conversation(
            session_id,
            force=True,
            keep_recent_messages=keep_recent_messages,
            reason=f"provider_overflow:{phase}",
        )
        logger.info("Context overflow compaction result sessionId=%s phase=%s compacted=%s", session_id, phase, event is not None)
        return event is not None

    def _request_conversation_summary(
        self,
        previous_summary: dict[str, str | int] | None,
        messages: list[dict[str, str | int]],
    ) -> str:
        transcript_lines = [
            self._summary_transcript_line(message, max_chars=1200)
            for message in messages
        ]
        previous = str(previous_summary.get("content", "")) if previous_summary else "None"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Summarize older conversation context for an agent handoff. "
                        "The summary is reference-only, not a user instruction. "
                        "Keep durable decisions, active task, completed actions, relevant files, blockers, and remaining work. "
                        "Be concise and do not invent facts."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Previous summary:\n{previous}\n\n"
                        "Messages to fold into the summary:\n"
                        + "\n".join(transcript_lines)
                    ),
                },
            ],
            "stream": False,
            "temperature": 0,
        }
        data = self.model_client.post_chat_completion(payload)
        message = first_choice_message(data)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("summary provider returned empty content")
        return content.strip()

    def _request_memory_review(
        self,
        session_id: str,
        messages: list[dict[str, str | int]],
        existing_items: list[dict[str, str | int | float | bool]],
        pending_candidates: list[dict[str, str | int | float | bool]],
    ) -> list[dict[str, Any]]:
        transcript_lines = [
            f"{message['id']}. {message['role']}: {sanitize_memory_context_text(str(message['content']), max_chars=1000, collapse_whitespace=False)}"
            for message in messages
        ]
        existing_lines = [
            f"- scope={item.get('scope', '')} confidence={item.get('confidence', '')}: "
            f"{sanitize_memory_context_text(str(item.get('content', '')), max_chars=300)}"
            for item in existing_items
            if not item.get("deleted")
        ]
        pending_lines = [
            f"- scope={candidate.get('scope', '')} confidence={candidate.get('confidence', '')}: "
            f"{sanitize_memory_context_text(str(candidate.get('content', '')), max_chars=300)}"
            for candidate in pending_candidates
        ]
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Review a recent conversation and propose durable structured memory candidates. "
                        "Return strict JSON only, with no Markdown. "
                        "Only propose stable user preferences, agent operating facts, project facts, or durable decisions explicitly supported by the messages. "
                        "Do not propose transient task progress, raw transcripts, secrets, credentials, API keys, private tokens, guesses, or sensitive personal data. "
                        "Classify each candidate into exactly one scope: user for stable user preferences or user-specific facts, "
                        "agent for durable operating instructions about how the assistant should behave, "
                        "and project for durable facts or decisions about the current codebase/project. "
                        "Perform safety self-checks before emitting candidates; omit unsafe candidates instead of labeling them for later storage. "
                        "Do not duplicate existing memory or pending candidates."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Session id: {session_id}\n\n"
                        "Existing durable memory:\n"
                        + ("\n".join(existing_lines) if existing_lines else "None")
                        + "\n\nPending memory candidates:\n"
                        + ("\n".join(pending_lines) if pending_lines else "None")
                        + "\n\nRecent messages:\n"
                        + "\n".join(transcript_lines)
                        + "\n\nCandidate schema rules:\n"
                        + "- scope must be one of user, agent, project.\n"
                        + "- scopeReason must briefly explain why the selected scope is correct.\n"
                        + "- retentionType must be one of long_term, stable_preference, durable_project_fact, agent_instruction.\n"
                        + "- safetyLabels must be an array of short labels describing completed checks, e.g. explicit, non_secret, non_transient, non_sensitive, non_speculative, correct_scope.\n"
                        + "- If a fact is uncertain, temporary, secret-bearing, path-only, or not clearly durable, do not emit it.\n"
                        + "\n\nReturn JSON in this exact shape:\n"
                        '{"candidates":[{"scope":"user|agent|project","content":"concise durable fact","confidence":0.0,"reason":"why this is durable","scopeReason":"why this scope is correct","safetyLabels":["explicit","non_secret","non_transient","correct_scope"],"retentionType":"long_term|stable_preference|durable_project_fact|agent_instruction","sourceMessageStartId":1,"sourceMessageEndId":2}]}'
                    ),
                },
            ],
            "stream": False,
            "temperature": 0,
        }
        data = self.model_client.post_chat_completion(payload)
        message = first_choice_message(data)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("memory review provider returned empty content")

        parsed = parse_json_object_from_text(content)
        candidates = parsed.get("candidates")
        if not isinstance(candidates, list):
            raise RuntimeError("memory review provider returned JSON without candidates array")
        return [candidate for candidate in candidates if isinstance(candidate, dict)]

    def _execute_tool_call(
        self,
        session_id: str,
        turn_id: str,
        user_message_id: int,
        tool_call: dict[str, Any],
        request_permission: PermissionRequester,
        history: list[dict[str, Any]],
        guardrail: ToolLoopGuardrail,
        cancel_event: threading.Event,
    ) -> Iterable[AgentEvent]:
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        tool_name = function.get("name") if isinstance(function.get("name"), str) else ""
        tool_call_id = tool_call.get("id") if isinstance(tool_call.get("id"), str) else str(uuid4())
        spec = self.tool_registry.get(tool_name)
        args = self._parse_tool_args(function.get("arguments"))
        logger.info(
            "Starting tool call sessionId=%s turnId=%s toolCallId=%s toolName=%s argKeys=%s",
            session_id,
            turn_id,
            tool_call_id,
            tool_name,
            sorted(args.keys()),
        )
        workspace_epoch = self.workspace_epoch(session_id)

        yield from self._emit_assistant_state(session_id, turn_id, "tool-running")
        yield AgentEvent("tool.started", {
            "toolName": tool_name,
            "displayName": spec.display_name if spec else f"Running {tool_name}",
        })
        yield self._audit_tool(
            session_id,
            tool_name,
            decision="started",
            metadata={
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "workspaceEpoch": workspace_epoch,
            },
        )

        guardrail_decision = guardrail.before_call(
            tool_name,
            args,
            workspace_epoch=workspace_epoch,
            file_resume_policies=self._current_worker_file_resume_policies(),
        )
        if not guardrail_decision.allowed:
            logger.info(
                "Tool call blocked by guardrail sessionId=%s turnId=%s toolCallId=%s toolName=%s failureCode=%s",
                session_id,
                turn_id,
                tool_call_id,
                tool_name,
                guardrail_decision.failure_code or "guardrail_blocked",
            )
            result = {"error": guardrail_decision.reason or "Tool call blocked by guardrail"}
            failure_code = guardrail_decision.failure_code or "guardrail_blocked"
            self._record_tool_result(history, session_id, tool_call_id, tool_name, result)
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
                metadata={
                    "turnId": turn_id,
                    "toolCallId": tool_call_id,
                    "workspaceEpoch": workspace_epoch,
                },
            )
            return

        if not spec:
            logger.info("Tool call failed: unknown tool sessionId=%s turnId=%s toolCallId=%s toolName=%s", session_id, turn_id, tool_call_id, tool_name)
            result = {"error": f"Unknown tool: {tool_name}"}
            guardrail.after_call(tool_name, args, result, False, workspace_epoch=workspace_epoch)
            self._record_tool_result(history, session_id, tool_call_id, tool_name, result)
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
                metadata={
                    "turnId": turn_id,
                    "toolCallId": tool_call_id,
                    "workspaceEpoch": workspace_epoch,
                },
            )
            return

        if not self.role_allows_tool(session_id, tool_name):
            logger.info("Tool call denied by role scope sessionId=%s turnId=%s toolCallId=%s toolName=%s", session_id, turn_id, tool_call_id, tool_name)
            result = {"error": f"Tool is not enabled for this role: {tool_name}"}
            guardrail.after_call(tool_name, args, result, False, workspace_epoch=workspace_epoch)
            self._record_tool_result(history, session_id, tool_call_id, tool_name, result)
            yield AgentEvent("tool.finished", self._tool_finished_payload(
                tool_name,
                ok=False,
                failure_code="role_scope_denied",
            ))
            yield self._audit_tool(
                session_id,
                tool_name,
                decision="denied",
                ok=False,
                failure_code="role_scope_denied",
                detail=result["error"],
                metadata={
                    "turnId": turn_id,
                    "toolCallId": tool_call_id,
                    "workspaceEpoch": workspace_epoch,
                },
            )
            return

        if not spec.enabled:
            logger.info("Tool call denied: disabled tool sessionId=%s turnId=%s toolCallId=%s toolName=%s", session_id, turn_id, tool_call_id, tool_name)
            result = {"error": f"Tool is disabled: {tool_name}"}
            guardrail.after_call(tool_name, args, result, False, workspace_epoch=workspace_epoch)
            self._record_tool_result(history, session_id, tool_call_id, tool_name, result)
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
                metadata={
                    "turnId": turn_id,
                    "toolCallId": tool_call_id,
                    "workspaceEpoch": workspace_epoch,
                },
            )
            return

        if spec.permission == "deny":
            logger.info("Tool call denied by policy sessionId=%s turnId=%s toolCallId=%s toolName=%s", session_id, turn_id, tool_call_id, tool_name)
            result = {"error": f"Permission denied for tool: {tool_name}"}
            guardrail.after_call(tool_name, args, result, False, workspace_epoch=workspace_epoch)
            self._record_tool_result(history, session_id, tool_call_id, tool_name, result)
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
                metadata={
                    "turnId": turn_id,
                    "toolCallId": tool_call_id,
                    "workspaceEpoch": workspace_epoch,
                },
            )
            return

        permission_request_id: str | None = None
        permission_decision = "allow"
        if spec.permission == "ask":
            worker_permission = worker_action_permission_decision(
                self._current_worker_scope(),
                tool_name,
                args,
                spec.permission,
            )
            worker_permission_preview = {
                "error": worker_permission.reason or f"Worker profile cannot request interactive permission for tool: {tool_name}",
                "approvalActionKey": worker_permission.action_key,
                "approvalActionLabel": worker_permission.action_label,
                "approvalRiskLevel": worker_permission.risk_level,
                "approvalRiskLabels": list(worker_permission.risk_labels),
            }
            worker_permission_preview = {key: value for key, value in worker_permission_preview.items() if value}
            worker_permission_preview_text = json.dumps(worker_permission_preview, ensure_ascii=False, sort_keys=True)
            if worker_permission.decision == "deny":
                logger.info(
                    "Tool permission denied by worker policy sessionId=%s turnId=%s toolCallId=%s toolName=%s workerProfile=%s approvalActionKey=%s",
                    session_id,
                    turn_id,
                    tool_call_id,
                    tool_name,
                    self._current_worker_profile(),
                    worker_permission.action_key,
                )
                result = {"error": worker_permission_preview["error"]}
                guardrail.after_call(tool_name, args, result, False, workspace_epoch=workspace_epoch)
                self._record_tool_result(history, session_id, tool_call_id, tool_name, result)
                yield AgentEvent("tool.finished", self._tool_finished_payload(
                    tool_name,
                    ok=False,
                    failure_code="worker_permission_denied",
                    result_preview=worker_permission_preview_text,
                ))
                yield self._audit_tool(
                    session_id,
                    tool_name,
                    decision="denied",
                    ok=False,
                    failure_code="worker_permission_denied",
                    detail=result["error"],
                    metadata={
                        "turnId": turn_id,
                        "toolCallId": tool_call_id,
                        "workspaceEpoch": workspace_epoch,
                        "workerProfile": self._current_worker_profile(),
                        "workerAllowedToolsets": list(self._current_worker_allowed_toolsets()),
                        "workerWorkspacePath": self._current_worker_workspace_path(),
                        "approvalActionKey": worker_permission.action_key,
                        "approvalActionLabel": worker_permission.action_label,
                        "approvalRiskLevel": worker_permission.risk_level,
                        "approvalRiskLabels": list(worker_permission.risk_labels),
                    },
                )
                return
            if worker_permission.decision == "auto_approve":
                permission_decision = "worker_auto_approved"
                logger.info(
                    "Tool permission auto-approved by worker policy sessionId=%s turnId=%s toolCallId=%s toolName=%s workerProfile=%s approvalActionKey=%s",
                    session_id,
                    turn_id,
                    tool_call_id,
                    tool_name,
                    self._current_worker_profile(),
                    worker_permission.action_key,
                )
            else:
                request = PermissionRequest(
                    request_id=str(uuid4()),
                    tool_name=spec.name,
                    display_name=spec.display_name,
                    reason=spec.describe_request(args),
                )
                permission_request_id = request.request_id
                logger.info(
                    "Requesting tool permission sessionId=%s turnId=%s toolCallId=%s requestId=%s toolName=%s",
                    session_id,
                    turn_id,
                    tool_call_id,
                    permission_request_id,
                    tool_name,
                )
                approved = request_permission(request)
                if not approved:
                    logger.info(
                        "Tool permission denied sessionId=%s turnId=%s toolCallId=%s requestId=%s toolName=%s",
                        session_id,
                        turn_id,
                        tool_call_id,
                        permission_request_id,
                        tool_name,
                    )
                    result = {"error": f"Permission denied for tool: {tool_name}"}
                    guardrail.after_call(tool_name, args, result, False, workspace_epoch=workspace_epoch)
                    self._record_tool_result(history, session_id, tool_call_id, tool_name, result)
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
                        metadata={
                            "turnId": turn_id,
                            "toolCallId": tool_call_id,
                            "workspaceEpoch": workspace_epoch,
                        },
                    )
                    return
                permission_decision = "approved"
                logger.info(
                    "Tool permission approved sessionId=%s turnId=%s toolCallId=%s requestId=%s toolName=%s",
                    session_id,
                    turn_id,
                    tool_call_id,
                    permission_request_id,
                    tool_name,
                )

        result = self.tool_registry.execute(
            tool_name,
            args,
            ToolContext(
                session_id=session_id,
                cwd=self._workspace_root_for_session(session_id),
                memory_store=self.memory_store,
                memory_embedding_provider=self.memory_embedding_provider,
                memory_vector_candidate_limit=self.memory_vector_candidate_limit,
                task_worker=self.task_worker,
                turn_id=turn_id,
                user_message_id=user_message_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                worker_profile=self._current_worker_profile(),
                worker_allowed_toolsets=self._current_worker_allowed_toolsets(),
                worker_workspace_path=self._current_worker_workspace_path(),
                worker_file_resume_policies=self._current_worker_file_resume_policies(),
                workspace_epoch=workspace_epoch,
                cancel_event=cancel_event,
                permission_request_id=permission_request_id,
                permission_decision=permission_decision,
                audit_metadata={
                    "turnId": turn_id,
                    "toolCallId": tool_call_id,
                    "permission": spec.permission,
                    "permissionDecision": permission_decision,
                    "workspaceEpoch": workspace_epoch,
                    "workerProfile": self._current_worker_profile(),
                    "workerAllowedToolsets": list(self._current_worker_allowed_toolsets()),
                    "workerWorkspacePath": self._current_worker_workspace_path(),
                    "workerFileResumePolicyCount": len(self._current_worker_file_resume_policies()),
                },
            ),
        )
        guardrail.after_call(tool_name, args, result.output, result.ok, workspace_epoch=workspace_epoch)
        workspace_epoch_after = workspace_epoch
        if self._tool_result_mutated_workspace(tool_name, result.output, result.ok):
            workspace_epoch_after = self._bump_workspace_epoch(
                session_id,
                reason="tool_mutation",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )
        logger.info(
            "Finished tool call sessionId=%s turnId=%s toolCallId=%s toolName=%s ok=%s failureCode=%s durationMs=%s outputTruncated=%s workspaceEpoch=%s workspaceEpochAfter=%s",
            session_id,
            turn_id,
            tool_call_id,
            tool_name,
            result.ok,
            result.failure_code,
            result.duration_ms,
            result.output_truncated,
            workspace_epoch,
            workspace_epoch_after,
        )
        self._record_tool_result(history, session_id, tool_call_id, tool_name, result.model_output)
        for event in self._maybe_inject_loaded_skill(
            history,
            session_id,
            turn_id,
            tool_call_id,
            tool_name,
            args,
            result.ok,
            result.output,
        ):
            yield event
        if tool_name == "update_plan" and result.ok:
            plan_payload = dict(result.output)
            plan_payload["turnId"] = turn_id
            yield AgentEvent("task.plan.updated", plan_payload)
        if tool_name in {"create_task", "cancel_task"} and result.ok:
            task = result.output.get("task")
            action = result.output.get("action")
            if isinstance(task, dict) and isinstance(action, str):
                yield AgentEvent("task.updated", {"task": task, "action": action})
        if tool_name == "schedule_message" and result.ok:
            job = result.output.get("job")
            action = result.output.get("action")
            if isinstance(job, dict) and isinstance(action, str):
                yield AgentEvent("scheduled.updated", {"job": job, "action": action})
        result_preview = result.output_preview
        if result_preview is None and result.ok:
            result_preview = json.dumps(result.model_output, ensure_ascii=False, sort_keys=True)
        yield AgentEvent("tool.finished", self._tool_finished_payload(
            tool_name,
            ok=result.ok,
            duration_ms=result.duration_ms,
            failure_code=result.failure_code,
            result_preview=result_preview,
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
            metadata={
                "turnId": turn_id,
                "toolCallId": tool_call_id,
                "workspaceEpoch": workspace_epoch,
                "workspaceEpochAfter": workspace_epoch_after,
                "workspaceMutated": workspace_epoch_after != workspace_epoch,
            },
        )

    def _request_tool_decision(self, session_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": self._prepare_messages_for_provider(messages),
            "tools": self.enabled_tool_schemas(session_id),
            "tool_choice": "auto",
            "stream": False,
            "temperature": 0,
        }
        if self.model_client.max_tokens > 0:
            payload["max_tokens"] = self.model_client.max_tokens
        self.model_client.apply_reasoning_options(payload)
        data = self.model_client.post_chat_completion(payload)
        return first_choice_message(data)

    def _stream_final_response(self, messages: list[dict[str, Any]]) -> Iterable[str | ChatStreamDelta]:
        payload = {
            "model": self.model,
            "messages": self._prepare_messages_for_provider(messages),
            "stream": True,
            "temperature": 0.7,
        }
        if self.model_client.max_tokens > 0:
            payload["max_tokens"] = self.model_client.max_tokens
        self.model_client.apply_reasoning_options(payload)
        yield from self.model_client.stream_chat_completion(payload)

    def _reasoning_config(self) -> ReasoningConfig:
        return ReasoningConfig(
            enabled=self.model_client.config.thinking_enabled,
            effort=self.model_client.config.reasoning_effort,
        )

    def _prepare_messages_for_provider(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return prepare_messages_for_provider(
            messages,
            provider=self.model_client.config.provider,
            model=self.model,
            base_url=self.model_client.config.base_url,
            reasoning=self._reasoning_config(),
        )

    def _assistant_history_message(self, message: dict[str, Any]) -> dict[str, Any]:
        return assistant_history_message(
            message,
            provider=self.model_client.config.provider,
            model=self.model,
            base_url=self.model_client.config.base_url,
            reasoning=self._reasoning_config(),
        )

    @staticmethod
    def _provider_history_message(message: dict[str, Any]) -> dict[str, Any]:
        role = str(message.get("role") or "")
        provider_message: dict[str, Any] = {
            "role": role,
            "content": str(message.get("content") or ""),
        }
        if role == "assistant":
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                provider_message["tool_calls"] = tool_calls
        if role == "tool":
            tool_call_id = message.get("tool_call_id") or message.get("toolCallId")
            if tool_call_id:
                provider_message["tool_call_id"] = str(tool_call_id)
        return provider_message

    @staticmethod
    def _message_tool_call_ids(message: dict[str, Any]) -> list[str]:
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            return []
        call_ids: list[str] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            call_id = tool_call.get("id")
            if isinstance(call_id, str) and call_id:
                call_ids.append(call_id)
        return call_ids

    @staticmethod
    def _tool_message_call_id(message: dict[str, Any]) -> str:
        call_id = message.get("tool_call_id") or message.get("toolCallId")
        return str(call_id) if call_id else ""

    def _sanitize_tool_pairs(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        valid_call_ids = {
            call_id
            for message in messages
            if message.get("role") == "assistant"
            for call_id in self._message_tool_call_ids(message)
        }
        result_ids = {
            self._tool_message_call_id(message)
            for message in messages
            if message.get("role") == "tool" and self._tool_message_call_id(message) in valid_call_ids
        }
        sanitized: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "tool":
                call_id = self._tool_message_call_id(message)
                if not call_id or call_id not in valid_call_ids:
                    continue
            sanitized.append(message)
            if message.get("role") == "assistant":
                for call_id in self._message_tool_call_ids(message):
                    if call_id not in result_ids:
                        sanitized.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": json.dumps({
                                "summary": "Result from earlier conversation; see conversation summary above.",
                            }, ensure_ascii=False),
                        })
        return sanitized

    def _aligned_keep_recent_message_count(self, messages: list[dict[str, Any]], keep_count: int) -> int:
        keep_count = max(0, min(int(keep_count), len(messages)))
        if keep_count <= 0 or keep_count >= len(messages):
            return keep_count
        start = len(messages) - keep_count
        while start > 0 and messages[start].get("role") == "tool":
            start -= 1
        return len(messages) - start

    def _recent_turn_message_count(self, messages: list[dict[str, Any]], turn_count: int) -> int:
        bounded_turn_count = max(0, int(turn_count))
        if bounded_turn_count <= 0 or not messages:
            return 0

        seen_user_turns = 0
        start_index = 0
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                seen_user_turns += 1
                if seen_user_turns == bounded_turn_count:
                    start_index = index
                    break
        else:
            return len(messages)

        return self._aligned_keep_recent_message_count(messages, len(messages) - start_index)

    def _align_compaction_window_for_tool_pairs(
        self,
        uncovered_messages: list[dict[str, Any]],
        compactable_messages: list[dict[str, Any]],
        keep_recent_messages: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if not compactable_messages:
            return compactable_messages, keep_recent_messages
        aligned = list(compactable_messages)
        while aligned and aligned[0].get("role") == "tool":
            aligned = aligned[1:]
        if not aligned:
            return [], len(uncovered_messages)

        last_id = aligned[-1].get("id")
        boundary_index = next(
            (index + 1 for index, message in enumerate(uncovered_messages) if message.get("id") == last_id),
            len(aligned),
        )
        while aligned and boundary_index < len(uncovered_messages):
            boundary_message = uncovered_messages[boundary_index]
            previous_message = aligned[-1]
            if boundary_message.get("role") == "tool" or self._message_tool_call_ids(previous_message):
                aligned = aligned[:-1]
                if not aligned:
                    return [], len(uncovered_messages)
                last_id = aligned[-1].get("id")
                boundary_index = next(
                    (index + 1 for index, message in enumerate(uncovered_messages) if message.get("id") == last_id),
                    boundary_index - 1,
                )
                continue
            break
        return aligned, max(0, len(uncovered_messages) - boundary_index)

    def _summary_transcript_line(self, message: dict[str, Any], *, max_chars: int) -> str:
        message_id = message.get("id", "")
        role = str(message.get("role") or "")
        content = sanitize_memory_context_text(str(message.get("content", "")), max_chars=max_chars, collapse_whitespace=False)
        if role == "assistant":
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                call_names = []
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                    name = function.get("name") if isinstance(function.get("name"), str) else "unknown"
                    call_id = tool_call.get("id") if isinstance(tool_call.get("id"), str) else ""
                    call_names.append(f"{name}#{call_id}" if call_id else name)
                suffix = f" tool_calls={', '.join(call_names)}" if call_names else ""
                return f"{message_id}. assistant{suffix}: {content}"
        if role == "tool":
            tool_name = message.get("tool_name") or message.get("toolName") or "tool"
            tool_call_id = message.get("tool_call_id") or message.get("toolCallId") or ""
            suffix = f" {tool_name}#{tool_call_id}" if tool_call_id else f" {tool_name}"
            return f"{message_id}. tool{suffix}: {content}"
        return f"{message_id}. {role}: {content}"

    def _emit_assistant_state(self, session_id: str, turn_id: str | None, state: str) -> Iterable[AgentEvent]:
        payload: dict[str, Any] = {"state": state}
        if turn_id:
            payload["turnId"] = turn_id
        event = AgentEvent("assistant.state", payload)
        yield event
        runtime_state = {"assistantState": state}
        runtime_state.update(self.harness_feedback_policy.runtime_state(session_id))
        context = HarnessContext(
            session_id=session_id,
            turn_id=turn_id,
            runtime_state=runtime_state,
            client_capabilities=self.harness_feedback_policy.client_capabilities(session_id),
        )
        harness_event = {"type": event.type, "payload": event.payload}
        for emitted in self.harness_registry.observe_event(context, harness_event):
            event_type = emitted.get("type")
            payload = emitted.get("payload")
            if isinstance(event_type, str) and isinstance(payload, dict):
                yield AgentEvent(event_type, payload)

    def _workspace_root_for_session(self, session_id: str | None = None) -> Path:
        worker_workspace_path = self._current_worker_workspace_path()
        if worker_workspace_path:
            worker_root = self._resolve_workspace_root(worker_workspace_path)
            if worker_root is not None and self._path_is_inside(worker_root, self._role_workspace_root_for_session(session_id)):
                return worker_root
        return self._role_workspace_root_for_session(session_id)

    def _role_workspace_root_for_session(self, session_id: str | None = None) -> Path:
        if not session_id:
            return self.workspace_root
        workspace_path = self.memory_store.role_workspace_path_for_session(session_id)
        if not workspace_path:
            return self.workspace_root
        resolved = self._resolve_workspace_root(workspace_path)
        return resolved or self.workspace_root

    def _resolve_workspace_root(self, workspace_path: str) -> Path | None:
        candidate = Path(workspace_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        return resolved if resolved.is_dir() else None

    @staticmethod
    def _path_is_inside(path: Path, parent: Path) -> bool:
        try:
            path.resolve().relative_to(parent.resolve())
            return True
        except ValueError:
            return False

    def _current_worker_scope(self) -> WorkerRuntimeScope | None:
        return _WORKER_RUNTIME_SCOPE.get()

    def _current_worker_profile(self) -> str | None:
        scope = self._current_worker_scope()
        return scope.worker_profile if scope else None

    def _current_worker_allowed_toolsets(self) -> tuple[str, ...]:
        scope = self._current_worker_scope()
        return scope.allowed_toolsets if scope else ()

    def _current_worker_workspace_path(self) -> str | None:
        scope = self._current_worker_scope()
        return scope.workspace_path if scope else None

    def _current_worker_file_resume_policies(self) -> tuple[dict[str, Any], ...]:
        scope = self._current_worker_scope()
        return scope.file_resume_policies if scope else ()

    def _build_system_prompt(self, *, session_id: str | None = None) -> str:
        workspace_root = self._workspace_root_for_session(session_id)
        stable_memory = self._format_stable_memory_for_prompt(session_id=session_id)
        identity_prompt = self._identity_prompt_for_session(session_id)
        allowed_tool_names = self._effective_allowed_tool_names(session_id)
        allowed_skills = self._role_allowed_skills(session_id)
        enabled_tools = {
            schema.get("function", {}).get("name", "")
            for schema in self.enabled_tool_schemas(session_id)
            if isinstance(schema, dict)
        }
        cache_key = (
            session_id or "",
            str(workspace_root),
            self.context_max_tokens,
            tuple(sorted(tool for tool in enabled_tools if tool)),
            tuple(sorted(allowed_skills or ())),
            identity_prompt,
            stable_memory,
        )
        cached = self._system_prompt_cache.get(cache_key)
        if cached is not None:
            return cached
        prompt = build_system_prompt(
            identity_prompt=identity_prompt,
            stable_memory=stable_memory,
            skill_catalog=self.skill_catalog,
            tool_hints=RoleScopedToolHints(self.tool_registry, allowed_tool_names),
            workspace_root=workspace_root,
            context_max_tokens=self.context_max_tokens,
            runtime_surface="desktop",
            available_tools=enabled_tools,
            allowed_skills=allowed_skills,
        )
        self._system_prompt_cache[cache_key] = prompt
        if len(self._system_prompt_cache) > 16:
            oldest_key = next(iter(self._system_prompt_cache))
            self._system_prompt_cache.pop(oldest_key, None)
        return prompt

    def _maybe_inject_loaded_skill(
        self,
        history: list[dict[str, Any]],
        session_id: str,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
        args: dict[str, Any],
        ok: bool,
        output: dict[str, Any],
    ) -> Iterable[AgentEvent]:
        if tool_name != "skill_view":
            return

        name = args.get("name")
        if not isinstance(name, str) or not name.strip():
            return
        requested_name = name.strip()
        logger.info(
            "Skill activation requested sessionId=%s turnId=%s toolCallId=%s requestedName=%s source=%s toolOk=%s",
            session_id,
            turn_id,
            tool_call_id,
            requested_name,
            "skill_view",
            ok,
        )

        yield AgentEvent("skill.started", {
            "skillName": requested_name,
            "displayName": requested_name,
            "source": "skill_view",
        })

        error_message = output.get("error") if isinstance(output.get("error"), str) else None
        if not ok:
            failure_code = "skill_view_error"
            if error_message and error_message.startswith("Skill not found:"):
                failure_code = "skill_not_found"
            logger.info(
                "Skill activation failed sessionId=%s turnId=%s toolCallId=%s requestedName=%s failureCode=%s toolError=%s",
                session_id,
                turn_id,
                tool_call_id,
                requested_name,
                failure_code,
                error_message,
            )
            yield AgentEvent("skill.finished", {
                "skillName": requested_name,
                "displayName": requested_name,
                "ok": False,
                "source": "skill_view",
                "failureCode": failure_code,
            })
            return

        if error_message:
            failure_code = "skill_view_error"
            if error_message.startswith("Skill not found:"):
                failure_code = "skill_not_found"
            yield AgentEvent("skill.finished", {
                "skillName": requested_name,
                "displayName": requested_name,
                "ok": False,
                "source": "skill_view",
                "failureCode": failure_code,
            })
            return

        prompt_block, resolved = self.skill_catalog.build_loaded_skill_prompt_block(
            requested_name,
            allowed_skills=self._role_allowed_skills(session_id),
        )
        if not prompt_block or not resolved.ok:
            failure_code = "skill_activation_unavailable"
            if resolved.ambiguous:
                failure_code = "ambiguous_skill"
            elif resolved.missing:
                failure_code = "skill_not_found"
            logger.info(
                "Skill activation unresolved sessionId=%s turnId=%s toolCallId=%s requestedName=%s failureCode=%s ambiguous=%s missing=%s",
                session_id,
                turn_id,
                tool_call_id,
                requested_name,
                failure_code,
                bool(resolved.ambiguous),
                ",".join(resolved.missing),
            )
            yield AgentEvent("skill.finished", {
                "skillName": requested_name,
                "displayName": requested_name,
                "ok": False,
                "source": "skill_view",
                "failureCode": failure_code,
            })
            return

        history.append({"role": "system", "content": prompt_block})
        activated_skill = resolved.loaded[0]
        logger.info(
            "Skill activation succeeded sessionId=%s turnId=%s toolCallId=%s requestedName=%s identifier=%s category=%s",
            session_id,
            turn_id,
            tool_call_id,
            requested_name,
            activated_skill.identifier,
            activated_skill.category,
        )
        yield AgentEvent("skill.finished", {
            "skillName": activated_skill.name,
            "displayName": activated_skill.identifier,
            "identifier": activated_skill.identifier,
            "ok": True,
            "source": "skill_view",
        })

    def _identity_prompt_for_session(self, session_id: str | None = None) -> str:
        identity = self.memory_store.role_identity_for_session(session_id)
        return sanitize_memory_context_text(
            str(identity.get("content", "")).strip(),
            max_chars=12000,
            collapse_whitespace=False,
        )

    def _format_stable_memory_for_prompt(self, *, session_id: str | None = None) -> str:
        snapshot = self.memory_store.stable_memory_snapshot(session_id=session_id)
        sections: list[str] = []
        for target, label in (("agent", "Agent Stable Memory"), ("user", "User Profile And Preferences")):
            content = sanitize_memory_context_text(
                str(snapshot[target]["content"]).strip(),
                max_chars=5000,
                collapse_whitespace=False,
            )
            sections.append(f"<stable_memory target=\"{target}\" label=\"{label}\">\n{content}\n</stable_memory>")

        return "\n\n".join(sections)

    @staticmethod
    def _parse_tool_args(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, str):
            return {}

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}

        return parsed if isinstance(parsed, dict) else {}

    def _record_tool_result(
        self,
        history: list[dict[str, Any]],
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        result: dict[str, Any],
    ) -> None:
        content = json.dumps(result, ensure_ascii=False)
        history.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })
        self.memory_store.save(
            session_id,
            "tool",
            content,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )

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

    @staticmethod
    def _tool_result_mutated_workspace(tool_name: str, output: dict[str, Any], ok: bool) -> bool:
        if tool_name in WORKSPACE_MUTATING_TOOLS:
            return ok and output.get("changed") is True
        if tool_name in POTENTIALLY_WORKSPACE_MUTATING_TOOLS:
            return ok and "error" not in output and "exitCode" in output
        return False

    def _audit_tool(
        self,
        session_id: str,
        tool_name: str,
        decision: str,
        ok: bool | None = None,
        duration_ms: int | None = None,
        failure_code: str | None = None,
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentEvent:
        record = self.tool_audit_log.append(
            session_id=session_id,
            tool_name=tool_name,
            decision=decision,
            ok=ok,
            duration_ms=duration_ms,
            failure_code=failure_code,
            detail=detail,
            metadata=metadata,
        )
        self.tool_audit_store.save(record)
        logger.info(
            "Recorded tool audit sessionId=%s toolName=%s decision=%s ok=%s failureCode=%s recordId=%s",
            session_id,
            tool_name,
            decision,
            ok,
            failure_code,
            record.record_id,
        )
        return AgentEvent("tool.audit", record.to_payload())


def sanitize_memory_context_text(text: str, max_chars: int, collapse_whitespace: bool = True) -> str:
    sanitized = sanitize_context_markup(text)
    if collapse_whitespace:
        sanitized = " ".join(sanitized.split())
    if len(sanitized) > max_chars:
        return sanitized[:max_chars].rstrip() + "..."
    return sanitized


def normalize_requested_skills(value: list[str] | tuple[str, ...] | None) -> list[str]:
    if not value:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        skill_name = item.strip()
        if not skill_name:
            continue
        dedupe_key = skill_name.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(skill_name)
    return normalized


def estimate_message_tokens(message: dict[str, Any]) -> int:
    content = message.get("content", "")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    role = str(message.get("role", ""))
    tool_calls = message.get("tool_calls")
    tool_call_chars = len(json.dumps(tool_calls, ensure_ascii=False)) if tool_calls else 0
    # Conservative tokenizer-free estimate: most supported providers use BPE-like
    # tokenization where English averages near 4 chars/token, while CJK can be
    # closer to 1 char/token. The 2 chars/token heuristic intentionally errs high.
    return max(1, (len(role) + len(content) + tool_call_chars + 1) // 2) + 8


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def parse_runtime_config(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    config: dict[str, dict[str, Any]] = {}
    current_section: str | None = None
    for raw_line in lines:
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        trimmed = line.strip()
        if indent == 0 and trimmed.endswith(":"):
            current_section = trimmed[:-1]
            config[current_section] = {}
            continue

        if indent != 2 or not current_section or ":" not in trimmed:
            continue

        key, raw_value = trimmed.split(":", 1)
        config[current_section][key.strip()] = parse_scalar_config_value(raw_value.strip())

    return config


def parse_scalar_config_value(value: str) -> Any:
    if value == "":
        return ""
    if value in {"true", "false"}:
        return value == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value.strip('"').strip("'")


def configured_runtime_memory_provider_name(config: dict[str, Any]) -> str:
    raw_value = os.environ.get("AMADEUS_MEMORY_PROVIDER")
    if raw_value is None:
        raw_value = str(config.get("provider") or DEFAULT_RUNTIME_MEMORY_PROVIDER_NAME)
    return normalize_runtime_memory_provider_name(raw_value)


def configured_runtime_memory_global_fallback(config: dict[str, Any]) -> bool:
    return parse_bool_env(
        "AMADEUS_MEMORY_GLOBAL_RETRIEVAL_FALLBACK",
        parse_bool_value(config.get("globalRetrievalFallback"), True),
    )


def configured_runtime_memory_vector_retrieval(config: dict[str, Any]) -> bool:
    return parse_bool_env(
        "AMADEUS_MEMORY_VECTOR_RETRIEVAL",
        parse_bool_value(config.get("vectorRetrieval"), MEMORY_VECTOR_RETRIEVAL),
    )


def configured_runtime_memory_vector_candidate_limit(config: dict[str, Any]) -> int:
    return parse_positive_int_env(
        "AMADEUS_MEMORY_VECTOR_CANDIDATE_LIMIT",
        parse_positive_int_value(config.get("vectorCandidateLimit"), MEMORY_VECTOR_CANDIDATE_LIMIT),
    )


def runtime_embedding_provider_signature(provider: Any | None) -> tuple[str, str, str]:
    if provider is None:
        return ("", "", "")
    config = getattr(provider, "config", None)
    local_dir = getattr(config, "local_dir", "") if config is not None else ""
    return (
        str(getattr(provider, "provider", "")),
        str(getattr(provider, "model_id", "")),
        str(local_dir),
    )


def parse_bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def parse_positive_int_value(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def parse_non_negative_int_value(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def parse_int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_float_value(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return parsed


def parse_positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def parse_non_negative_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def parse_bool_env(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return parse_bool_value(raw_value, default)


def parse_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def parse_float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = float(raw_value)
    except ValueError:
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return parsed
