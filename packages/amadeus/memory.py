from __future__ import annotations

import json
import hashlib
import math
import sqlite3
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from amadeus.identity import (
    ensure_role_soul,
    normalize_soul_text,
    read_soul,
    role_home_path,
    role_soul_path,
)
from amadeus.memory_query import build_fts_index_content
from amadeus.memory_query import make_fts_query, memory_item_query_terms
from amadeus.planning import empty_plan_response, merge_plan_items, plan_response
from amadeus.role_scope import role_runtime_scope_json, role_runtime_scope_payload
from amadeus.scheduling import compute_next_run_at, parse_schedule
from amadeus.tasks import (
    MAX_TASK_ERROR_CHARS,
    MAX_TASK_EVENT_MESSAGE_CHARS,
    MAX_TASK_RESULT_CHARS,
    normalize_task_artifact,
    normalize_optional_text,
    normalize_task_attempt_status,
    normalize_task_body,
    normalize_task_edge_type,
    normalize_task_artifacts,
    normalize_task_json_array,
    normalize_task_json_object,
    normalize_task_max_attempts,
    normalize_task_event_type,
    normalize_task_priority,
    normalize_task_status,
    normalize_task_title,
    task_summary,
)


MessageRole = Literal["user", "assistant", "tool"]
StableMemoryTarget = Literal["agent", "user"]
MemoryItemScope = Literal["user", "agent", "project"]
MemoryItemType = Literal["semantic", "episodic", "procedural", "preference", "project_fact", "agent_instruction"]
MemoryItemHistoryEvent = Literal["ADD", "UPDATE", "DELETE"]
MemoryReviewCandidateStatus = Literal["pending", "accepted", "rejected", "superseded"]
MemoryReviewRetentionType = Literal["long_term", "stable_preference", "durable_project_fact", "agent_instruction"]
MemoryReviewJobStatus = Literal["running", "completed", "skipped", "failed"]
MemoryReviewJobTrigger = Literal["manual", "auto", "compaction"]
ScheduledJobStatus = Literal["scheduled", "running", "paused", "completed", "cancelled", "failed"]
TodoStatus = Literal["pending", "in_progress", "completed", "cancelled"]
MemoryReviewCandidatePayload = dict[str, str | int | float | bool | list[str]]
CONVERSATION_SUMMARY_LIMIT = 12000
MEMORY_ITEM_LIMIT = 2000
MEMORY_ITEM_METADATA_LIMIT = 4000
MEMORY_REVIEW_REASON_LIMIT = 1000
MEMORY_REVIEW_LABEL_LIMIT = 64
MEMORY_REVIEW_MAX_SAFETY_LABELS = 8
STABLE_MEMORY_FILES: dict[str, str] = {
    "agent": "MEMORY.md",
    "user": "USER.md",
}
STABLE_MEMORY_TITLES: dict[str, str] = {
    "agent": "Amadeus Stable Memory",
    "user": "User Profile And Preferences",
}
STABLE_MEMORY_LIMITS: dict[str, int] = {
    "agent": 4000,
    "user": 2500,
}
DEFAULT_ROLE_ID = "amadeus"
DEFAULT_SESSION_ID = "companion:default"
DEFAULT_ROLE_NAME = "Amadeus"
DEFAULT_ROLE_PERSONA = (
    "A calm, precise, and practical desktop Live2D companion. "
    "Help the user think, plan, search, remember, and execute tasks."
)


class MessageMemoryStore:
    WORKER_APPROVAL_ACTION_TTL_SECONDS = 15 * 60

    def __init__(
        self,
        database_path: Path,
        stable_memory_dir: Path | None = None,
        default_workspace_path: Path | str | None = None,
        worker_approval_action_ttl_seconds: int | None = None,
    ) -> None:
        self.database_path = database_path
        self.stable_memory_dir = stable_memory_dir or database_path.parent / "memory"
        self.roles_root = self.database_path.parent / "roles"
        self.default_workspace_path = normalize_default_workspace_path(default_workspace_path)
        self.worker_approval_action_ttl_seconds = self._normalize_worker_approval_action_ttl_seconds(
            worker_approval_action_ttl_seconds,
        )
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.stable_memory_dir.mkdir(parents=True, exist_ok=True)
        self.roles_root.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @classmethod
    def _normalize_worker_approval_action_ttl_seconds(cls, value: int | None) -> int:
        try:
            parsed = int(value) if value is not None else cls.WORKER_APPROVAL_ACTION_TTL_SECONDS
        except (TypeError, ValueError):
            return cls.WORKER_APPROVAL_ACTION_TTL_SECONDS
        return parsed if parsed > 0 else cls.WORKER_APPROVAL_ACTION_TTL_SECONDS

    def set_worker_approval_action_ttl_seconds(self, value: int | None) -> int:
        self.worker_approval_action_ttl_seconds = self._normalize_worker_approval_action_ttl_seconds(value)
        return self.worker_approval_action_ttl_seconds

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS roles (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  description TEXT NOT NULL DEFAULT '',
                  persona TEXT NOT NULL DEFAULT '',
                  style TEXT NOT NULL DEFAULT '',
                  provider TEXT,
                  model TEXT,
                  live2d_model TEXT,
                  tts_voice TEXT,
                  workspace_path TEXT,
                  runtime_scope_json TEXT NOT NULL DEFAULT '{}',
                  archived INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                  id TEXT PRIMARY KEY,
                  role_id TEXT NOT NULL,
                  title TEXT NOT NULL,
                  archived INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  FOREIGN KEY(role_id) REFERENCES roles(id)
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_role_updated
                ON sessions(role_id, archived, updated_at);
                CREATE TABLE IF NOT EXISTS messages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_id TEXT NOT NULL,
                  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
                  content TEXT NOT NULL,
                  tool_call_id TEXT,
                  tool_name TEXT,
                  tool_calls TEXT,
                  created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session_created
                ON messages(session_id, created_at);
                  CREATE TABLE IF NOT EXISTS conversation_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summarized_message_count INTEGER NOT NULL,
                    covered_message_count INTEGER NOT NULL DEFAULT 0,
                    source_message_start_id INTEGER,
                    source_message_end_id INTEGER,
                    covered_through_message_id INTEGER,
                    model TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                  );
                  CREATE INDEX IF NOT EXISTS idx_conversation_summaries_session_updated
                  ON conversation_summaries(session_id, updated_at);
                  CREATE TABLE IF NOT EXISTS memory_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL CHECK (scope IN ('user', 'agent', 'project')),
                    content TEXT NOT NULL,
                    memory_type TEXT NOT NULL DEFAULT 'semantic' CHECK (memory_type IN ('semantic', 'episodic', 'procedural', 'preference', 'project_fact', 'agent_instruction')),
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    content_hash TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    source_session_id TEXT,
                    source_message_id INTEGER,
                    last_accessed_at TEXT,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                  );
                  CREATE INDEX IF NOT EXISTS idx_memory_items_scope_updated
                  ON memory_items(scope, deleted_at, updated_at);
                  CREATE TABLE IF NOT EXISTS memory_item_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_item_id INTEGER NOT NULL,
                    event TEXT NOT NULL CHECK (event IN ('ADD', 'UPDATE', 'DELETE')),
                    old_content TEXT,
                    new_content TEXT,
                    old_metadata_json TEXT,
                    new_metadata_json TEXT,
                    actor TEXT NOT NULL DEFAULT 'runtime',
                    source_session_id TEXT,
                    source_message_id INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(memory_item_id) REFERENCES memory_items(id)
                  );
                  CREATE INDEX IF NOT EXISTS idx_memory_item_history_item_created
                  ON memory_item_history(memory_item_id, created_at);
                  CREATE TABLE IF NOT EXISTS memory_item_embeddings (
                    memory_item_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(memory_item_id, provider, model),
                    FOREIGN KEY(memory_item_id) REFERENCES memory_items(id)
                  );
                  CREATE INDEX IF NOT EXISTS idx_memory_item_embeddings_provider_model
                  ON memory_item_embeddings(provider, model, dimensions, updated_at);
                  CREATE TABLE IF NOT EXISTS memory_review_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK (scope IN ('user', 'agent', 'project')),
                    content TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.7,
                    reason TEXT,
                    scope_reason TEXT,
                    safety_labels TEXT,
                    retention_type TEXT,
                    source_message_start_id INTEGER,
                    source_message_end_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected', 'superseded')),
                    memory_item_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                  );
                  CREATE INDEX IF NOT EXISTS idx_memory_review_candidates_session_status
                  ON memory_review_candidates(session_id, status, updated_at);
                  CREATE TABLE IF NOT EXISTS memory_review_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    trigger TEXT NOT NULL CHECK (trigger IN ('manual', 'auto', 'compaction')),
                    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'skipped', 'failed')),
                    reason TEXT,
                    error TEXT,
                    source_message_start_id INTEGER,
                    source_message_end_id INTEGER,
                    source_message_count INTEGER NOT NULL DEFAULT 0,
                    proposed_candidate_count INTEGER NOT NULL DEFAULT 0,
                    saved_candidate_count INTEGER NOT NULL DEFAULT 0,
                    suppressed_candidate_count INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_ms INTEGER
                  );
                  CREATE INDEX IF NOT EXISTS idx_memory_review_jobs_session_started
                  ON memory_review_jobs(session_id, started_at);
                  CREATE TABLE IF NOT EXISTS session_plans (
                    session_id TEXT PRIMARY KEY,
                    items_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                  );
                  CREATE TABLE IF NOT EXISTS plan_runs (
                    turn_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_message_id INTEGER,
                    assistant_message_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'active',
                    items_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id),
                    FOREIGN KEY(user_message_id) REFERENCES messages(id),
                    FOREIGN KEY(assistant_message_id) REFERENCES messages(id)
                  );
                  CREATE INDEX IF NOT EXISTS idx_plan_runs_session_user_message
                  ON plan_runs(session_id, user_message_id, updated_at);
                  CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    root_task_id TEXT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'agent_turn',
                    source TEXT NOT NULL DEFAULT 'manual',
                    parent_task_id TEXT,
                    plan_run_id TEXT,
                    plan_item_id TEXT,
                    worker_type TEXT NOT NULL DEFAULT 'agent',
                    worker_profile TEXT,
                    blocked_reason TEXT,
                    review_required INTEGER NOT NULL DEFAULT 0,
                    acceptance_criteria_json TEXT NOT NULL DEFAULT '[]',
                    context_hints_json TEXT NOT NULL DEFAULT '{}',
                    allowed_toolsets_json TEXT NOT NULL DEFAULT '[]',
                    disallowed_tools_json TEXT NOT NULL DEFAULT '[]',
                    depends_on_policy TEXT NOT NULL DEFAULT 'all_succeeded',
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    handoff_summary TEXT,
                    artifacts_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'blocked', 'succeeded', 'failed', 'cancelled')),
                    priority INTEGER NOT NULL DEFAULT 0,
                    ready_at TEXT,
                    due_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    next_run_at TEXT,
                    claim_lock TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    runner_kind TEXT NOT NULL DEFAULT 'in_process',
                    last_heartbeat TEXT,
                    result TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                  );
                  CREATE INDEX IF NOT EXISTS idx_tasks_session_status_updated
                  ON tasks(session_id, status, updated_at);
                  CREATE TABLE IF NOT EXISTS task_edges (
                    id TEXT PRIMARY KEY,
                    from_task_id TEXT NOT NULL,
                    to_task_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL DEFAULT 'blocks',
                    required_status TEXT NOT NULL DEFAULT 'succeeded',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(from_task_id) REFERENCES tasks(id),
                    FOREIGN KEY(to_task_id) REFERENCES tasks(id)
                  );
                  CREATE UNIQUE INDEX IF NOT EXISTS idx_task_edges_unique
                  ON task_edges(from_task_id, to_task_id, edge_type);
                  CREATE INDEX IF NOT EXISTS idx_task_edges_to
                  ON task_edges(to_task_id, edge_type);
                  CREATE TABLE IF NOT EXISTS task_attempts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    worker_id TEXT,
                    worker_profile TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT,
                    finished_at TEXT,
                    input_context_json TEXT NOT NULL DEFAULT '{}',
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    result TEXT,
                    error TEXT,
                    token_usage_json TEXT NOT NULL DEFAULT '{}',
                    tool_usage_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                  );
                  CREATE INDEX IF NOT EXISTS idx_task_attempts_task_started
                  ON task_attempts(task_id, started_at);
                  CREATE TABLE IF NOT EXISTS task_artifacts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    attempt_id TEXT,
                    type TEXT NOT NULL DEFAULT 'summary',
                    title TEXT NOT NULL,
                    path TEXT,
                    url TEXT,
                    content TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id),
                    FOREIGN KEY(attempt_id) REFERENCES task_attempts(id)
                  );
                  CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_created
                  ON task_artifacts(task_id, created_at);
                  CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT,
                    message TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id),
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                  );
                  CREATE INDEX IF NOT EXISTS idx_task_events_task_created
                  ON task_events(task_id, created_at);
                  CREATE TABLE IF NOT EXISTS supervisor_leases (
                    name TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    pid INTEGER,
                    acquired_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                  );
                  CREATE TABLE IF NOT EXISTS task_processes (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    supervisor_id TEXT NOT NULL,
                    pid INTEGER NOT NULL,
                    process_group_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'running',
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    exited_at TEXT,
                    return_code INTEGER,
                    workspace_path TEXT,
                    log_path TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                  );
                  CREATE INDEX IF NOT EXISTS idx_task_processes_task_started
                  ON task_processes(task_id, started_at);
                  CREATE INDEX IF NOT EXISTS idx_task_processes_status_heartbeat
                  ON task_processes(status, heartbeat_at);
                  CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'message',
                    last_task_id TEXT,
                    schedule_json TEXT NOT NULL,
                    schedule_display TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('scheduled', 'running', 'paused', 'completed', 'cancelled', 'failed')),
                    repeat_count INTEGER,
                    completed_runs INTEGER NOT NULL DEFAULT 0,
                    next_run_at TEXT,
                    last_run_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                  );
                  CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_session_status_updated
                  ON scheduled_jobs(session_id, status, updated_at);
                  CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_status_next_run
                  ON scheduled_jobs(status, next_run_at);
                  CREATE TABLE IF NOT EXISTS scheduled_job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT,
                    message TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES scheduled_jobs(id),
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                  );
                  CREATE INDEX IF NOT EXISTS idx_scheduled_job_events_job_created
                  ON scheduled_job_events(job_id, created_at);
                  CREATE TABLE IF NOT EXISTS todo_items (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'in_progress', 'completed', 'cancelled')),
                    order_index INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                  );
                  CREATE INDEX IF NOT EXISTS idx_todo_items_session_status_order
                  ON todo_items(session_id, status, order_index, updated_at);
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                USING fts5(
                  content,
                  session_id UNINDEXED,
                  role UNINDEXED,
                  created_at UNINDEXED
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts
                USING fts5(
                  content,
                  metadata,
                  scope,
                  memory_type,
                  updated_at UNINDEXED
                );
                """
            )
            self._migrate_roles_and_sessions(connection)
            self._migrate_messages_for_tool_transcript(connection)
            self._migrate_conversation_summaries(connection)
            self._migrate_memory_items(connection)
            self._migrate_memory_review_candidates(connection)
            self._migrate_task_statuses(connection)
            self._migrate_task_reliability_columns(connection)
            self._migrate_scheduled_job_task_columns(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_summaries_session_covered
                ON conversation_summaries(session_id, covered_through_message_id)
                """
            )
            connection.execute("DELETE FROM messages_fts")
            rows = connection.execute(
                """
                SELECT id, content, session_id, role, created_at, tool_name, tool_calls
                FROM messages
                """
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO messages_fts(rowid, content, session_id, role, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (int(row[0]), build_fts_index_content(message_search_text(row[1], row[5], row[6])), row[2], row[3], row[4]),
                )
            self._rebuild_memory_items_fts(connection)

    def _migrate_messages_for_tool_transcript(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(messages)").fetchall()
        }
        create_sql_row = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'messages'
            """
        ).fetchone()
        create_sql = str(create_sql_row[0] or "") if create_sql_row else ""
        needs_rebuild = "tool" not in create_sql or not {"tool_call_id", "tool_name", "tool_calls"}.issubset(columns)
        if not needs_rebuild:
            return

        connection.execute("DROP TABLE IF EXISTS messages_fts")
        connection.execute(
            """
            CREATE TABLE messages_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
              content TEXT NOT NULL,
              tool_call_id TEXT,
              tool_name TEXT,
              tool_calls TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        select_columns = [
            "id",
            "session_id",
            "role",
            "content",
            "tool_call_id" if "tool_call_id" in columns else "NULL AS tool_call_id",
            "tool_name" if "tool_name" in columns else "NULL AS tool_name",
            "tool_calls" if "tool_calls" in columns else "NULL AS tool_calls",
            "created_at",
        ]
        connection.execute(
            f"""
            INSERT INTO messages_new (id, session_id, role, content, tool_call_id, tool_name, tool_calls, created_at)
            SELECT {', '.join(select_columns)}
            FROM messages
            """
        )
        connection.execute("DROP TABLE messages")
        connection.execute("ALTER TABLE messages_new RENAME TO messages")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_session_created
            ON messages(session_id, created_at)
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
            USING fts5(
              content,
              session_id UNINDEXED,
              role UNINDEXED,
              created_at UNINDEXED
            )
            """
        )

    def _migrate_roles_and_sessions(self, connection: sqlite3.Connection) -> None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(roles)").fetchall()
        }
        if "tts_voice" not in columns:
            connection.execute("ALTER TABLE roles ADD COLUMN tts_voice TEXT")
        if "workspace_path" not in columns:
            connection.execute("ALTER TABLE roles ADD COLUMN workspace_path TEXT")
        if "runtime_scope_json" not in columns:
            connection.execute("ALTER TABLE roles ADD COLUMN runtime_scope_json TEXT NOT NULL DEFAULT '{}'")
        default_workspace_path = self.default_workspace_path
        connection.execute(
            """
            INSERT OR IGNORE INTO roles (
              id, name, description, persona, style, provider, model, live2d_model, tts_voice, workspace_path, runtime_scope_json, archived, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, '{}', 0, ?, ?)
            """,
            (
                DEFAULT_ROLE_ID,
                DEFAULT_ROLE_NAME,
                "Default desktop companion role.",
                DEFAULT_ROLE_PERSONA,
                "concise, warm, technically capable",
                default_workspace_path,
                now,
                now,
            ),
        )
        if default_workspace_path:
            connection.execute(
                """
                UPDATE roles
                SET workspace_path = ?
                WHERE workspace_path IS NULL OR trim(workspace_path) = ''
                """,
                (default_workspace_path,),
            )
        ensure_role_soul(
            self.roles_root,
            DEFAULT_ROLE_ID,
            role_name=DEFAULT_ROLE_NAME,
            persona=DEFAULT_ROLE_PERSONA,
            style="concise, warm, technically capable",
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO sessions (id, role_id, title, archived, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?)
            """,
            (DEFAULT_SESSION_ID, DEFAULT_ROLE_ID, "Default", now, now),
        )
        rows = connection.execute(
            """
            SELECT
              m.session_id,
              MIN(m.created_at) AS created_at,
              MAX(m.created_at) AS updated_at
            FROM messages m
            LEFT JOIN sessions s ON s.id = m.session_id
            WHERE s.id IS NULL
            GROUP BY m.session_id
            """
        ).fetchall()
        for row in rows:
            session_id = normalize_session_id(str(row[0]))
            connection.execute(
                """
                INSERT OR IGNORE INTO sessions (id, role_id, title, archived, created_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (
                    session_id,
                    DEFAULT_ROLE_ID,
                    session_title_from_id(session_id),
                    str(row[1]) if row[1] else now,
                    str(row[2]) if row[2] else now,
                ),
            )

    def _migrate_task_statuses(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
        ).fetchone()
        schema_sql = str(row[0] or "") if row else ""
        if "'succeeded'" in schema_sql and "'done'" not in schema_sql:
            return

        connection.execute("ALTER TABLE tasks RENAME TO tasks_legacy")
        connection.execute(
            """
            CREATE TABLE tasks (
              id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              title TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '',
              kind TEXT NOT NULL DEFAULT 'agent_turn',
              source TEXT NOT NULL DEFAULT 'manual',
              parent_task_id TEXT,
              plan_item_id TEXT,
              worker_type TEXT NOT NULL DEFAULT 'agent',
              blocked_reason TEXT,
              review_required INTEGER NOT NULL DEFAULT 0,
              artifacts_json TEXT NOT NULL DEFAULT '[]',
              status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'blocked', 'succeeded', 'failed', 'cancelled')),
              priority INTEGER NOT NULL DEFAULT 0,
              due_at TEXT,
              attempt_count INTEGER NOT NULL DEFAULT 0,
              max_attempts INTEGER NOT NULL DEFAULT 3,
              next_run_at TEXT,
              claim_lock TEXT,
              lease_owner TEXT,
              lease_expires_at TEXT,
              runner_kind TEXT NOT NULL DEFAULT 'in_process',
              last_heartbeat TEXT,
              result TEXT,
              error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              finished_at TEXT,
              FOREIGN KEY(session_id) REFERENCES sessions(id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO tasks (
              id, session_id, title, body, status, priority, due_at, claim_lock,
              last_heartbeat, result, error, created_at, updated_at, finished_at
            )
            SELECT
              id, session_id, title, body,
              CASE status WHEN 'done' THEN 'succeeded' ELSE status END,
              priority, due_at, claim_lock, last_heartbeat, result, error,
              created_at, updated_at, finished_at
            FROM tasks_legacy
            """
        )
        connection.execute("DROP TABLE tasks_legacy")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_session_status_updated
            ON tasks(session_id, status, updated_at)
            """
        )

    def _migrate_task_reliability_columns(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "attempt_count" not in columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0")
        if "max_attempts" not in columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3")
        if "next_run_at" not in columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN next_run_at TEXT")
        task_defaults = {
            "root_task_id": "TEXT",
            "kind": "TEXT NOT NULL DEFAULT 'agent_turn'",
            "source": "TEXT NOT NULL DEFAULT 'manual'",
            "parent_task_id": "TEXT",
            "plan_run_id": "TEXT",
            "plan_item_id": "TEXT",
            "worker_type": "TEXT NOT NULL DEFAULT 'agent'",
            "worker_profile": "TEXT",
            "blocked_reason": "TEXT",
            "review_required": "INTEGER NOT NULL DEFAULT 0",
            "acceptance_criteria_json": "TEXT NOT NULL DEFAULT '[]'",
            "context_hints_json": "TEXT NOT NULL DEFAULT '{}'",
            "allowed_toolsets_json": "TEXT NOT NULL DEFAULT '[]'",
            "disallowed_tools_json": "TEXT NOT NULL DEFAULT '[]'",
            "depends_on_policy": "TEXT NOT NULL DEFAULT 'all_succeeded'",
            "checkpoint_json": "TEXT NOT NULL DEFAULT '{}'",
            "handoff_summary": "TEXT",
            "artifacts_json": "TEXT NOT NULL DEFAULT '[]'",
            "ready_at": "TEXT",
            "lease_owner": "TEXT",
            "lease_expires_at": "TEXT",
            "runner_kind": "TEXT NOT NULL DEFAULT 'in_process'",
        }
        for name, definition in task_defaults.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_status_next_run
            ON tasks(status, next_run_at, due_at, priority)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_plan_item
            ON tasks(session_id, plan_item_id, status)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_root_status_updated
            ON tasks(root_task_id, status, updated_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_edges (
              id TEXT PRIMARY KEY,
              from_task_id TEXT NOT NULL,
              to_task_id TEXT NOT NULL,
              edge_type TEXT NOT NULL DEFAULT 'blocks',
              required_status TEXT NOT NULL DEFAULT 'succeeded',
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              FOREIGN KEY(from_task_id) REFERENCES tasks(id),
              FOREIGN KEY(to_task_id) REFERENCES tasks(id)
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_task_edges_unique
            ON task_edges(from_task_id, to_task_id, edge_type)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_task_edges_to
            ON task_edges(to_task_id, edge_type)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_attempts (
              id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              worker_id TEXT,
              worker_profile TEXT,
              status TEXT NOT NULL DEFAULT 'running',
              started_at TEXT NOT NULL,
              heartbeat_at TEXT,
              finished_at TEXT,
              input_context_json TEXT NOT NULL DEFAULT '{}',
              checkpoint_json TEXT NOT NULL DEFAULT '{}',
              result TEXT,
              error TEXT,
              token_usage_json TEXT NOT NULL DEFAULT '{}',
              tool_usage_json TEXT NOT NULL DEFAULT '{}',
              FOREIGN KEY(task_id) REFERENCES tasks(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_task_attempts_task_started
            ON task_attempts(task_id, started_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS task_artifacts (
              id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              attempt_id TEXT,
              type TEXT NOT NULL DEFAULT 'summary',
              title TEXT NOT NULL,
              path TEXT,
              url TEXT,
              content TEXT,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              FOREIGN KEY(task_id) REFERENCES tasks(id),
              FOREIGN KEY(attempt_id) REFERENCES task_attempts(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_created
            ON task_artifacts(task_id, created_at)
            """
        )

    def _migrate_scheduled_job_task_columns(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(scheduled_jobs)").fetchall()
        }
        if "mode" not in columns:
            connection.execute("ALTER TABLE scheduled_jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'message'")
        if "last_task_id" not in columns:
            connection.execute("ALTER TABLE scheduled_jobs ADD COLUMN last_task_id TEXT")

    def list_roles(self, include_archived: bool = False) -> list[dict[str, Any]]:
        where = "1 = 1" if include_archived else "archived = 0"
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, name, description, persona, style, provider, model, live2d_model, tts_voice, workspace_path, runtime_scope_json, archived, created_at, updated_at
                FROM roles
                WHERE {where}
                ORDER BY archived ASC, updated_at DESC, name ASC
                """
            ).fetchall()
        return [role_response(row) for row in rows]

    def create_role(
        self,
        name: str,
        *,
        description: str | None = None,
        persona: str | None = None,
        style: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        live2d_model: str | None = None,
        tts_voice: str | None = None,
        workspace_path: str | None = None,
        runtime_scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_name = normalize_role_name(name)
        normalized_description = normalize_optional_text(description, "description", 500) or ""
        normalized_persona = normalize_optional_text(persona, "persona", 4000) or ""
        normalized_style = normalize_optional_text(style, "style", 1000) or ""
        normalized_provider = normalize_optional_text(provider, "provider", 120)
        normalized_model = normalize_optional_text(model, "model", 160)
        normalized_live2d_model = normalize_optional_text(live2d_model, "live2d_model", 160)
        normalized_tts_voice = normalize_optional_text(tts_voice, "tts_voice", 160)
        normalized_workspace_path = normalize_optional_text(workspace_path, "workspace_path", 1000) or self.default_workspace_path
        normalized_runtime_scope_json = role_runtime_scope_json(runtime_scope)
        role_id = f"role-{uuid4().hex[:12]}"
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO roles (
                  id, name, description, persona, style, provider, model, live2d_model, tts_voice, workspace_path, runtime_scope_json, archived, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    role_id,
                    normalized_name,
                    normalized_description,
                    normalized_persona,
                    normalized_style,
                    normalized_provider,
                    normalized_model,
                    normalized_live2d_model,
                    normalized_tts_voice,
                    normalized_workspace_path,
                    normalized_runtime_scope_json,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT id, name, description, persona, style, provider, model, live2d_model, tts_voice, workspace_path, runtime_scope_json, archived, created_at, updated_at
                FROM roles
                WHERE id = ?
                """,
                (role_id,),
            ).fetchone()
        ensure_role_soul(
            self.roles_root,
            role_id,
            role_name=normalized_name,
            persona=normalized_persona,
            style=normalized_style,
        )
        return role_response(row)

    def get_role(self, role_id: str) -> dict[str, Any] | None:
        normalized_role_id = normalize_role_id(role_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, description, persona, style, provider, model, live2d_model, tts_voice, workspace_path, runtime_scope_json, archived, created_at, updated_at
                FROM roles
                WHERE id = ?
                """,
                (normalized_role_id,),
            ).fetchone()
        return role_response(row) if row else None

    def update_role(
        self,
        role_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        persona: str | None = None,
        style: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        live2d_model: str | None = None,
        tts_voice: str | None = None,
        workspace_path: str | None = None,
        runtime_scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_role_id = normalize_role_id(role_id)
        updates: dict[str, str | None] = {}
        if name is not None:
            updates["name"] = normalize_role_name(name)
        if description is not None:
            updates["description"] = normalize_optional_text(description, "description", 500) or ""
        if persona is not None:
            updates["persona"] = normalize_optional_text(persona, "persona", 4000) or ""
        if style is not None:
            updates["style"] = normalize_optional_text(style, "style", 1000) or ""
        if provider is not None:
            updates["provider"] = normalize_optional_text(provider, "provider", 120)
        if model is not None:
            updates["model"] = normalize_optional_text(model, "model", 160)
        if live2d_model is not None:
            updates["live2d_model"] = normalize_optional_text(live2d_model, "live2d_model", 160)
        if tts_voice is not None:
            updates["tts_voice"] = normalize_optional_text(tts_voice, "tts_voice", 160)
        if workspace_path is not None:
            updates["workspace_path"] = normalize_optional_text(workspace_path, "workspace_path", 1000) or self.default_workspace_path
        if runtime_scope is not None:
            updates["runtime_scope_json"] = role_runtime_scope_json(runtime_scope)

        now = datetime.now(timezone.utc).isoformat()
        updates["updated_at"] = now
        assignments = ", ".join(f"{column} = ?" for column in updates)
        with self.connect() as connection:
            exists = connection.execute("SELECT id FROM roles WHERE id = ?", (normalized_role_id,)).fetchone()
            if not exists:
                raise ValueError("role not found")
            connection.execute(
                f"UPDATE roles SET {assignments} WHERE id = ?",
                [*updates.values(), normalized_role_id],
            )
            row = connection.execute(
                """
                SELECT id, name, description, persona, style, provider, model, live2d_model, tts_voice, workspace_path, runtime_scope_json, archived, created_at, updated_at
                FROM roles
                WHERE id = ?
                """,
                (normalized_role_id,),
            ).fetchone()
        return role_response(row)

    def list_sessions(self, role_id: str | None = None, include_archived: bool = False) -> list[dict[str, str | int | bool]]:
        where = ["s.archived = 0"] if not include_archived else ["1 = 1"]
        params: list[object] = []
        if role_id:
            where.append("s.role_id = ?")
            params.append(normalize_role_id(role_id))
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                  s.id,
                  s.role_id,
                  s.title,
                  s.archived,
                  s.created_at,
                  s.updated_at,
                  r.name,
                  COUNT(m.id) AS message_count
                FROM sessions s
                JOIN roles r ON r.id = s.role_id
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE {' AND '.join(where)}
                GROUP BY s.id
                ORDER BY s.updated_at DESC, s.created_at DESC
                """,
                params,
            ).fetchall()
        return [session_response(row) for row in rows]

    def create_session(self, role_id: str, title: str | None = None) -> dict[str, str | int | bool]:
        normalized_role_id = normalize_role_id(role_id)
        normalized_title = normalize_session_title(title) if title else default_session_title()
        session_id = f"session-{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            role = connection.execute("SELECT id FROM roles WHERE id = ? AND archived = 0", (normalized_role_id,)).fetchone()
            if not role:
                raise ValueError("role not found")
            connection.execute(
                """
                INSERT INTO sessions (id, role_id, title, archived, created_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (session_id, normalized_role_id, normalized_title, now, now),
            )
            row = connection.execute(
                """
                SELECT
                  s.id, s.role_id, s.title, s.archived, s.created_at, s.updated_at, r.name, COUNT(m.id)
                FROM sessions s
                JOIN roles r ON r.id = s.role_id
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE s.id = ?
                GROUP BY s.id
                """,
                (session_id,),
            ).fetchone()
        return session_response(row)

    def update_session(self, session_id: str, *, title: str | None = None) -> dict[str, str | int | bool]:
        normalized_session_id = normalize_session_id(session_id)
        updates: dict[str, str] = {}
        if title is not None:
            updates["title"] = normalize_session_title(title)
        now = datetime.now(timezone.utc).isoformat()
        updates["updated_at"] = now
        assignments = ", ".join(f"{column} = ?" for column in updates)
        with self.connect() as connection:
            exists = connection.execute("SELECT id FROM sessions WHERE id = ?", (normalized_session_id,)).fetchone()
            if not exists:
                raise ValueError("session not found")
            connection.execute(
                f"UPDATE sessions SET {assignments} WHERE id = ?",
                [*updates.values(), normalized_session_id],
            )
            row = connection.execute(
                """
                SELECT
                  s.id, s.role_id, s.title, s.archived, s.created_at, s.updated_at, r.name, COUNT(m.id)
                FROM sessions s
                JOIN roles r ON r.id = s.role_id
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE s.id = ?
                GROUP BY s.id
                """,
                (normalized_session_id,),
            ).fetchone()
        return session_response(row)

    def archive_session(self, session_id: str) -> dict[str, str | int | bool]:
        normalized_session_id = normalize_session_id(session_id)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            exists = connection.execute("SELECT id FROM sessions WHERE id = ?", (normalized_session_id,)).fetchone()
            if not exists:
                raise ValueError("session not found")
            connection.execute(
                """
                UPDATE sessions
                SET archived = 1, updated_at = ?
                WHERE id = ?
                """,
                (now, normalized_session_id),
            )
            row = connection.execute(
                """
                SELECT
                  s.id, s.role_id, s.title, s.archived, s.created_at, s.updated_at, r.name, COUNT(m.id)
                FROM sessions s
                JOIN roles r ON r.id = s.role_id
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE s.id = ?
                GROUP BY s.id
                """,
                (normalized_session_id,),
            ).fetchone()
        return session_response(row)

    def get_session(self, session_id: str) -> dict[str, str | int | bool] | None:
        normalized_session_id = normalize_session_id(session_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                  s.id, s.role_id, s.title, s.archived, s.created_at, s.updated_at, r.name, COUNT(m.id)
                FROM sessions s
                JOIN roles r ON r.id = s.role_id
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE s.id = ?
                GROUP BY s.id
                """,
                (normalized_session_id,),
            ).fetchone()
        return session_response(row) if row else None

    def ensure_session(self, session_id: str, role_id: str | None = None, title: str | None = None) -> dict[str, str | int | bool]:
        normalized_session_id = normalize_session_id(session_id)
        existing = self.get_session(normalized_session_id)
        if existing:
            return existing
        normalized_role_id = normalize_role_id(role_id) if role_id else DEFAULT_ROLE_ID
        normalized_title = normalize_session_title(title) if title else session_title_from_id(normalized_session_id)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            role = connection.execute("SELECT id FROM roles WHERE id = ?", (normalized_role_id,)).fetchone()
            if not role:
                normalized_role_id = DEFAULT_ROLE_ID
            connection.execute(
                """
                INSERT OR IGNORE INTO sessions (id, role_id, title, archived, created_at, updated_at)
                VALUES (?, ?, ?, 0, ?, ?)
                """,
                (normalized_session_id, normalized_role_id, normalized_title, now, now),
            )
        return self.get_session(normalized_session_id) or {
            "id": normalized_session_id,
            "roleId": normalized_role_id,
            "title": normalized_title,
            "archived": False,
            "createdAt": now,
            "updatedAt": now,
            "roleName": DEFAULT_ROLE_NAME,
            "messageCount": 0,
        }

    def role_prompt_for_session(self, session_id: str) -> str:
        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT r.name, r.description, r.persona, r.style
                FROM sessions s
                JOIN roles r ON r.id = s.role_id
                WHERE s.id = ?
                """,
                (normalized_session_id,),
            ).fetchone()
        if not row:
            return ""
        parts = [f"Current role: {row[0]}."]
        if row[1]:
            parts.append(f"Role description: {row[1]}")
        if row[2]:
            parts.append(f"Role persona: {row[2]}")
        if row[3]:
            parts.append(f"Role style: {row[3]}")
        return "\n".join(parts)

    def role_id_for_session(self, session_id: str) -> str:
        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT role_id
                FROM sessions
                WHERE id = ?
                """,
                (normalized_session_id,),
            ).fetchone()
        return str(row[0]) if row else DEFAULT_ROLE_ID

    def role_identity_for_session(self, session_id: str | None = None) -> dict[str, str | int | bool]:
        role_id = self.role_id_for_session(session_id) if session_id else DEFAULT_ROLE_ID
        return self.role_identity(role_id)

    def role_identity(self, role_id: str) -> dict[str, str | int | bool]:
        normalized_role_id = normalize_role_id(role_id)
        role = self.get_role(normalized_role_id)
        if role is None:
            raise ValueError("role not found")
        path = ensure_role_soul(
            self.roles_root,
            normalized_role_id,
            role_name=str(role["name"]),
            persona=str(role["persona"]),
            style=str(role["style"]),
        )
        content = read_soul(path)
        return {
            "roleId": normalized_role_id,
            "roleName": str(role["name"]),
            "path": str(path),
            "content": content,
            "charCount": len(content),
            "defaulted": not bool(content.strip()),
        }

    def update_role_identity(
        self,
        role_id: str,
        *,
        name: str | None = None,
        soul_text: str | None = None,
    ) -> dict[str, str | int | bool]:
        normalized_role_id = normalize_role_id(role_id)
        role = self.get_role(normalized_role_id)
        if role is None:
            raise ValueError("role not found")
        normalized_soul = normalize_soul_text(soul_text)
        if name is not None:
            role = self.update_role(normalized_role_id, name=name)
        path = ensure_role_soul(
            self.roles_root,
            normalized_role_id,
            role_name=str(role["name"]),
            persona=str(role["persona"]),
            style=str(role["style"]),
        )
        if normalized_soul is not None:
            atomic_write_text(path, normalized_soul)
        return self.role_identity(normalized_role_id)

    def update_role_identity_for_session(
        self,
        session_id: str,
        *,
        name: str | None = None,
        soul_text: str | None = None,
    ) -> dict[str, str | int | bool]:
        return self.update_role_identity(
            self.role_id_for_session(session_id),
            name=name,
            soul_text=soul_text,
        )

    def role_workspace_path_for_session(self, session_id: str) -> str:
        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT r.workspace_path
                FROM sessions s
                JOIN roles r ON r.id = s.role_id
                WHERE s.id = ?
                """,
                (normalized_session_id,),
            ).fetchone()
        return str(row[0]).strip() if row and row[0] else ""

    def role_runtime_scope_for_session(self, session_id: str | None = None) -> dict[str, list[str]]:
        role_id = self.role_id_for_session(session_id) if session_id else DEFAULT_ROLE_ID
        role = self.get_role(role_id)
        if not role:
            return {"tools": [], "skills": [], "mcpServers": []}
        return role_runtime_scope_payload(role.get("runtimeScope"))

    def _migrate_conversation_summaries(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(conversation_summaries)").fetchall()
        }
        migrations = {
            "covered_message_count": "ALTER TABLE conversation_summaries ADD COLUMN covered_message_count INTEGER NOT NULL DEFAULT 0",
            "source_message_start_id": "ALTER TABLE conversation_summaries ADD COLUMN source_message_start_id INTEGER",
            "source_message_end_id": "ALTER TABLE conversation_summaries ADD COLUMN source_message_end_id INTEGER",
            "covered_through_message_id": "ALTER TABLE conversation_summaries ADD COLUMN covered_through_message_id INTEGER",
            "model": "ALTER TABLE conversation_summaries ADD COLUMN model TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(statement)

    def _migrate_memory_items(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(memory_items)").fetchall()
        }
        migrations = {
            "memory_type": "ALTER TABLE memory_items ADD COLUMN memory_type TEXT NOT NULL DEFAULT 'semantic'",
            "metadata_json": "ALTER TABLE memory_items ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
            "content_hash": "ALTER TABLE memory_items ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''",
            "last_accessed_at": "ALTER TABLE memory_items ADD COLUMN last_accessed_at TEXT",
            "access_count": "ALTER TABLE memory_items ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0",
        }
        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(statement)
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memory_items_hash_scope
            ON memory_items(scope, content_hash, deleted_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memory_items_type_updated
            ON memory_items(memory_type, deleted_at, updated_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_item_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              memory_item_id INTEGER NOT NULL,
              event TEXT NOT NULL CHECK (event IN ('ADD', 'UPDATE', 'DELETE')),
              old_content TEXT,
              new_content TEXT,
              old_metadata_json TEXT,
              new_metadata_json TEXT,
              actor TEXT NOT NULL DEFAULT 'runtime',
              source_session_id TEXT,
              source_message_id INTEGER,
              created_at TEXT NOT NULL,
              FOREIGN KEY(memory_item_id) REFERENCES memory_items(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memory_item_history_item_created
            ON memory_item_history(memory_item_id, created_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_item_embeddings (
              memory_item_id INTEGER NOT NULL,
              provider TEXT NOT NULL,
              model TEXT NOT NULL,
              dimensions INTEGER NOT NULL,
              content_hash TEXT NOT NULL,
              embedding BLOB NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(memory_item_id, provider, model),
              FOREIGN KEY(memory_item_id) REFERENCES memory_items(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memory_item_embeddings_provider_model
            ON memory_item_embeddings(provider, model, dimensions, updated_at)
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts
            USING fts5(
              content,
              metadata,
              scope,
              memory_type,
              updated_at UNINDEXED
            )
            """
        )
        rows = connection.execute(
            """
            SELECT id, content
            FROM memory_items
            WHERE content_hash IS NULL OR content_hash = ''
            """
        ).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE memory_items SET content_hash = ? WHERE id = ?",
                (compute_memory_item_hash(str(row[1])), int(row[0])),
            )

    def _rebuild_memory_items_fts(self, connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM memory_items_fts")
        rows = connection.execute(
            """
            SELECT
              id,
              content,
              metadata_json,
              scope,
              memory_type,
              updated_at
            FROM memory_items
            WHERE deleted_at IS NULL
            """
        ).fetchall()
        for row in rows:
            self._upsert_memory_item_fts(
                connection,
                memory_item_id=int(row[0]),
                content=str(row[1]),
                metadata_json=str(row[2] or "{}"),
                scope=str(row[3]),
                memory_type=str(row[4]),
                updated_at=str(row[5]),
            )

    def _upsert_memory_item_fts(
        self,
        connection: sqlite3.Connection,
        *,
        memory_item_id: int,
        content: str,
        metadata_json: str,
        scope: str,
        memory_type: str,
        updated_at: str,
    ) -> None:
        connection.execute("DELETE FROM memory_items_fts WHERE rowid = ?", (int(memory_item_id),))
        connection.execute(
            """
            INSERT INTO memory_items_fts(rowid, content, metadata, scope, memory_type, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(memory_item_id),
                build_fts_index_content(content),
                build_fts_index_content(memory_item_metadata_search_text(metadata_json)),
                build_fts_index_content(scope),
                build_fts_index_content(memory_type),
                updated_at,
            ),
        )

    def _delete_memory_item_fts(self, connection: sqlite3.Connection, memory_item_id: int) -> None:
        connection.execute("DELETE FROM memory_items_fts WHERE rowid = ?", (int(memory_item_id),))

    def _migrate_memory_review_candidates(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(memory_review_candidates)").fetchall()
        }
        migrations = {
            "scope_reason": "ALTER TABLE memory_review_candidates ADD COLUMN scope_reason TEXT",
            "safety_labels": "ALTER TABLE memory_review_candidates ADD COLUMN safety_labels TEXT",
            "retention_type": "ALTER TABLE memory_review_candidates ADD COLUMN retention_type TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(statement)

    def save(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> int:
        if role not in ("user", "assistant", "tool"):
            raise ValueError("role must be user, assistant, or tool")

        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        now = datetime.now(timezone.utc).isoformat()
        normalized_tool_call_id = normalize_optional_message_metadata(tool_call_id)
        normalized_tool_name = normalize_optional_message_metadata(tool_name)
        normalized_tool_calls = normalize_tool_calls_json(tool_calls)
        with self.connect() as connection:
            session_row = connection.execute(
                """
                SELECT title, COUNT(m.id)
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id AND m.role = 'user'
                WHERE s.id = ?
                GROUP BY s.id
                """,
                (normalized_session_id,),
            ).fetchone()
            cursor = connection.execute(
                """
                INSERT INTO messages (session_id, role, content, tool_call_id, tool_name, tool_calls, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_session_id,
                    role,
                    content,
                    normalized_tool_call_id,
                    normalized_tool_name,
                    normalized_tool_calls,
                    now,
                ),
            )
            row_id = cursor.lastrowid
            connection.execute(
                """
                UPDATE sessions
                SET updated_at = ?
                WHERE id = ?
                """,
                (now, normalized_session_id),
            )
            if (
                role == "user"
                and session_row
                and int(session_row[1] or 0) == 0
                and is_auto_session_title(str(session_row[0] or ""))
            ):
                connection.execute(
                    """
                    UPDATE sessions
                    SET title = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (session_title_from_query(content), now, normalized_session_id),
                )
            row = connection.execute(
                """
                SELECT content, session_id, role, created_at, tool_name, tool_calls
                FROM messages
                WHERE id = ?
                """,
                (row_id,),
            ).fetchone()
            if row:
                connection.execute(
                    """
                    INSERT INTO messages_fts(rowid, content, session_id, role, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (row_id, build_fts_index_content(message_search_text(row[0], row[4], row[5])), row[1], row[2], row[3]),
                )
        return int(row_id)

    def search(self, query: str, session_id: str | None = None, limit: int = 10) -> list[dict[str, str | int]]:
        normalized_query = query.strip()
        if not normalized_query:
            return []

        fts_query = make_fts_query(normalized_query)
        bounded_limit = max(1, min(50, int(limit)))

        where = "messages_fts MATCH ?"
        params: list[object] = [fts_query]
        if session_id:
            where += " AND m.session_id = ?"
            params.append(session_id)
        params.append(bounded_limit)

        with self.connect() as connection:
            try:
                rows = connection.execute(
                    f"""
                    SELECT
                      messages_fts.rowid,
                      m.session_id,
                      m.role,
                      m.content,
                      m.created_at,
                      m.content AS snippet
                    FROM messages_fts
                    JOIN messages m ON m.id = messages_fts.rowid
                    WHERE {where}
                    ORDER BY bm25(messages_fts)
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            except sqlite3.OperationalError:
                rows = self._search_like(connection, normalized_query, session_id, bounded_limit)

        return [
            {
                "id": int(row[0]),
                "sessionId": str(row[1]),
                "role": str(row[2]),
                "content": str(row[3]),
                "createdAt": str(row[4]),
                "snippet": str(row[5]),
            }
            for row in rows
        ]

    def _search_like(
        self,
        connection: sqlite3.Connection,
        query: str,
        session_id: str | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        pattern = f"%{query}%"
        if session_id:
            return connection.execute(
                """
                SELECT id, session_id, role, content, created_at, content AS snippet
                FROM messages
                WHERE session_id = ? AND content LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, pattern, limit),
            ).fetchall()

        return connection.execute(
            """
            SELECT id, session_id, role, content, created_at, content AS snippet
            FROM messages
            WHERE content LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (pattern, limit),
        ).fetchall()

    def load(self, session_id: str, limit: int = 40, after_message_id: int | None = None) -> list[dict[str, str | int]]:
        normalized_after_id = normalize_optional_non_negative_int(after_message_id, "after_message_id")
        where = "session_id = ?"
        params: list[object] = [session_id]
        if normalized_after_id:
            where += " AND id > ?"
            params.append(normalized_after_id)
        params.append(limit)

        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, role, content, created_at, tool_call_id, tool_name, tool_calls
                FROM messages
                WHERE {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [message_row_response(row) for row in reversed(rows)]

    def load_recent_turns(
        self,
        session_id: str,
        turn_count: int,
        after_message_id: int | None = None,
    ) -> list[dict[str, str | int]]:
        normalized_after_id = normalize_optional_non_negative_int(after_message_id, "after_message_id")
        bounded_turn_count = max(1, int(turn_count))
        where = "session_id = ?"
        params: list[object] = [session_id]
        if normalized_after_id:
            where += " AND id > ?"
            params.append(normalized_after_id)

        with self.connect() as connection:
            user_rows = connection.execute(
                f"""
                SELECT id
                FROM messages
                WHERE {where} AND role = 'user'
                ORDER BY id DESC
                LIMIT ?
                """,
                [*params, bounded_turn_count],
            ).fetchall()
            if user_rows:
                start_id = min(int(row[0]) for row in user_rows)
            else:
                latest_row = connection.execute(
                    f"""
                    SELECT id
                    FROM messages
                    WHERE {where}
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    params,
                ).fetchone()
                start_id = int(latest_row[0]) if latest_row else None
            if start_id is None:
                return []

            rows = connection.execute(
                """
                SELECT id, role, content, created_at, tool_call_id, tool_name, tool_calls
                FROM messages
                WHERE session_id = ? AND id >= ?
                ORDER BY id ASC
                """,
                (session_id, start_id),
            ).fetchall()

        return [message_row_response(row) for row in rows]

    def load_detailed(
        self,
        session_id: str,
        *,
        after_message_id: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, str | int]]:
        normalized_after_id = normalize_optional_non_negative_int(after_message_id, "after_message_id")
        where = "session_id = ?"
        params: list[object] = [session_id]
        if normalized_after_id:
            where += " AND id > ?"
            params.append(normalized_after_id)

        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            params.append(max(1, int(limit)))

        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, role, content, created_at, tool_call_id, tool_name, tool_calls
                FROM messages
                WHERE {where}
                ORDER BY id ASC
                {limit_clause}
                """,
                params,
            ).fetchall()

        return [message_row_response(row) for row in rows]

    def latest_message_id(self, session_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()

        return int(row[0]) if row else 0

    def count(self, session_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) as count
                FROM messages
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()

        return int(row[0]) if row else 0

    def load_session_plan(self, session_id: str) -> dict[str, object]:
        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT items_json, updated_at
                FROM session_plans
                WHERE session_id = ?
                """,
                (normalized_session_id,),
            ).fetchone()

        if not row:
            return empty_plan_response(normalized_session_id)

        try:
            items = json.loads(str(row[0]))
        except json.JSONDecodeError:
            items = []
        return plan_response(normalized_session_id, items, updated_at=str(row[1]))

    def save_session_plan(
        self,
        session_id: str,
        items: list[dict[str, object]],
        *,
        merge: bool = False,
        turn_id: str | None = None,
        user_message_id: int | None = None,
    ) -> dict[str, object]:
        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        current = self.load_session_plan(normalized_session_id)
        normalized_items = merge_plan_items(current["items"], items) if merge else merge_plan_items([], items)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT created_at
                FROM session_plans
                WHERE session_id = ?
                """,
                (normalized_session_id,),
            ).fetchone()
            created_at = str(existing[0]) if existing else now
            connection.execute(
                """
                INSERT INTO session_plans (session_id, items_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                  items_json = excluded.items_json,
                  updated_at = excluded.updated_at
                """,
                (
                    normalized_session_id,
                    json.dumps(normalized_items, ensure_ascii=False),
                    created_at,
                    now,
                ),
            )
            normalized_turn_id = normalize_optional_text(turn_id, max_chars=120, field_name="turn_id")
            if normalized_turn_id:
                normalized_user_message_id = normalize_optional_non_negative_int(user_message_id, "user_message_id")
                connection.execute(
                    """
                    INSERT INTO plan_runs (
                      turn_id, session_id, user_message_id, status, items_json,
                      created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'active', ?, ?, ?)
                    ON CONFLICT(turn_id) DO UPDATE SET
                      user_message_id = COALESCE(excluded.user_message_id, plan_runs.user_message_id),
                      status = CASE
                        WHEN plan_runs.status IN ('completed', 'incomplete', 'cancelled') THEN plan_runs.status
                        ELSE 'active'
                      END,
                      items_json = excluded.items_json,
                      updated_at = excluded.updated_at
                    """,
                    (
                        normalized_turn_id,
                        normalized_session_id,
                        normalized_user_message_id,
                        json.dumps(normalized_items, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
        return plan_response(normalized_session_id, normalized_items, updated_at=now)

    def _update_plan_runs_containing_item(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        plan_item_id: str,
        status: str,
        updated_at: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT turn_id, items_json, status
            FROM plan_runs
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchall()
        for row in rows:
            try:
                items = json.loads(str(row[1]))
            except json.JSONDecodeError:
                continue
            if not isinstance(items, list):
                continue
            changed = False
            updated_items: list[dict[str, object]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                next_item = dict(item)
                if str(next_item.get("id") or "") == plan_item_id:
                    next_item["status"] = status
                    changed = True
                elif status == "in_progress" and str(next_item.get("status") or "") == "in_progress":
                    next_item["status"] = "pending"
                updated_items.append(next_item)
            if not changed:
                continue
            current_status = str(row[2] or "active")
            next_status = current_status
            archived_at: str | None = None
            if current_status in {"completed", "incomplete"}:
                next_status = "completed" if plan_items_are_complete(updated_items) else "incomplete"
                archived_at = updated_at
            connection.execute(
                """
                UPDATE plan_runs
                SET items_json = ?,
                    status = ?,
                    updated_at = ?,
                    archived_at = COALESCE(?, archived_at)
                WHERE turn_id = ?
                """,
                (
                    json.dumps(updated_items, ensure_ascii=False),
                    next_status,
                    updated_at,
                    archived_at,
                    str(row[0]),
                ),
            )

    def finish_plan_run(
        self,
        *,
        session_id: str,
        turn_id: str,
        assistant_message_id: int | None = None,
        status: str | None = None,
    ) -> dict[str, object] | None:
        normalized_session_id = normalize_session_id(session_id)
        normalized_turn_id = normalize_optional_text(turn_id, max_chars=120, field_name="turn_id")
        if not normalized_turn_id:
            return None
        normalized_assistant_message_id = normalize_optional_non_negative_int(assistant_message_id, "assistant_message_id")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT items_json
                FROM plan_runs
                WHERE turn_id = ? AND session_id = ?
                """,
                (normalized_turn_id, normalized_session_id),
            ).fetchone()
            if not row:
                return None
            try:
                items = json.loads(str(row[0]))
            except json.JSONDecodeError:
                items = []
            normalized_status = str(status or "").strip().lower()
            if normalized_status not in {"completed", "incomplete", "cancelled"}:
                normalized_status = "completed" if plan_items_are_complete(items) else "incomplete"
            connection.execute(
                """
                UPDATE plan_runs
                SET status = ?,
                    assistant_message_id = COALESCE(?, assistant_message_id),
                    updated_at = ?,
                    archived_at = ?
                WHERE turn_id = ? AND session_id = ?
                """,
                (
                    normalized_status,
                    normalized_assistant_message_id,
                    now,
                    now,
                    normalized_turn_id,
                    normalized_session_id,
                ),
            )
        return self.get_plan_run(session_id=normalized_session_id, turn_id=normalized_turn_id)

    def get_plan_run(self, *, session_id: str, turn_id: str) -> dict[str, object] | None:
        normalized_session_id = normalize_session_id(session_id)
        normalized_turn_id = normalize_optional_text(turn_id, max_chars=120, field_name="turn_id")
        if not normalized_turn_id:
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT turn_id, session_id, user_message_id, assistant_message_id, status,
                       items_json, created_at, updated_at, archived_at
                FROM plan_runs
                WHERE turn_id = ? AND session_id = ?
                """,
                (normalized_turn_id, normalized_session_id),
            ).fetchone()
        return plan_run_response(row) if row else None

    def list_plan_runs(self, *, session_id: str, limit: int = 100) -> dict[str, object]:
        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        normalized_limit = max(1, min(200, int(limit)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT turn_id, session_id, user_message_id, assistant_message_id, status,
                       items_json, created_at, updated_at, archived_at
                FROM plan_runs
                WHERE session_id = ?
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (normalized_session_id, normalized_limit),
            ).fetchall()
        runs = [plan_run_response(row) for row in rows]
        return {"sessionId": normalized_session_id, "planRuns": runs, "count": len(runs)}

    def update_plan_item_status(
        self,
        *,
        session_id: str,
        plan_item_id: str,
        status: str,
    ) -> dict[str, object]:
        normalized_session_id = normalize_session_id(session_id)
        normalized_plan_item_id = normalize_optional_text(plan_item_id, max_chars=120, field_name="plan_item_id")
        if not normalized_plan_item_id:
            return self.load_session_plan(normalized_session_id)
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"pending", "in_progress", "completed", "cancelled"}:
            raise ValueError("invalid plan item status")
        current = self.load_session_plan(normalized_session_id)
        raw_items = current.get("items") if isinstance(current, dict) else []
        if not isinstance(raw_items, list):
            return current
        found = False
        updated_items: list[dict[str, object]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            next_item = dict(item)
            if item_id == normalized_plan_item_id:
                next_item["status"] = normalized_status
                found = True
            elif normalized_status == "in_progress" and str(item.get("status") or "") == "in_progress":
                next_item["status"] = "pending"
            updated_items.append(next_item)
        if not found:
            return current
        updated = self.save_session_plan(normalized_session_id, updated_items, merge=False)
        now = str(updated.get("updatedAt") or datetime.now(timezone.utc).isoformat())
        with self.connect() as connection:
            self._update_plan_runs_containing_item(
                connection,
                session_id=normalized_session_id,
                plan_item_id=normalized_plan_item_id,
                status=normalized_status,
                updated_at=now,
            )
        return updated

    def create_task(
        self,
        *,
        session_id: str,
        title: str,
        body: str | None = None,
        kind: str | None = None,
        source: str | None = None,
        root_task_id: str | None = None,
        parent_task_id: str | None = None,
        plan_run_id: str | None = None,
        plan_item_id: str | None = None,
        worker_type: str | None = None,
        worker_profile: str | None = None,
        acceptance_criteria: list[object] | None = None,
        context_hints: dict[str, object] | None = None,
        allowed_toolsets: list[object] | None = None,
        disallowed_tools: list[object] | None = None,
        depends_on_policy: str | None = None,
        checkpoint: dict[str, object] | None = None,
        handoff_summary: str | None = None,
        review_required: bool = False,
        artifacts: list[dict[str, object]] | None = None,
        priority: int | None = None,
        ready_at: str | None = None,
        due_at: str | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, object]:
        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        normalized_title = normalize_task_title(title)
        normalized_body = normalize_task_body(body)
        normalized_kind = normalize_task_kind(kind)
        normalized_source = normalize_task_source(source)
        normalized_root_task_id = normalize_optional_text(root_task_id, max_chars=80, field_name="root_task_id")
        normalized_parent_task_id = normalize_optional_text(parent_task_id, max_chars=80, field_name="parent_task_id")
        normalized_plan_run_id = normalize_optional_text(plan_run_id, max_chars=120, field_name="plan_run_id")
        normalized_plan_item_id = normalize_optional_text(plan_item_id, max_chars=120, field_name="plan_item_id")
        normalized_worker_type = normalize_task_worker_type(worker_type)
        normalized_worker_profile = normalize_optional_text(worker_profile, max_chars=120, field_name="worker_profile")
        acceptance_criteria_json = normalize_task_json_array(acceptance_criteria, field_name="acceptance_criteria")
        context_hints_json = normalize_task_json_object(context_hints, field_name="context_hints")
        allowed_toolsets_json = normalize_task_json_array(allowed_toolsets, field_name="allowed_toolsets")
        disallowed_tools_json = normalize_task_json_array(disallowed_tools, field_name="disallowed_tools")
        normalized_depends_on_policy = normalize_optional_text(
            depends_on_policy or "all_succeeded",
            max_chars=80,
            field_name="depends_on_policy",
        ) or "all_succeeded"
        checkpoint_json = normalize_task_json_object(checkpoint, field_name="checkpoint")
        normalized_handoff_summary = normalize_optional_text(
            handoff_summary,
            max_chars=MAX_TASK_RESULT_CHARS,
            field_name="handoff_summary",
        )
        artifacts_json = normalize_task_artifacts(artifacts)
        normalized_priority = normalize_task_priority(priority)
        normalized_ready_at = normalize_optional_text(ready_at, max_chars=80, field_name="ready_at")
        normalized_due_at = normalize_optional_text(due_at, max_chars=80, field_name="due_at")
        normalized_max_attempts = normalize_task_max_attempts(max_attempts)
        task_id = uuid4().hex
        if normalized_root_task_id is None:
            normalized_root_task_id = normalized_parent_task_id or task_id
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                  id, session_id, root_task_id, title, body, kind, source, parent_task_id,
                  plan_run_id, plan_item_id, worker_type, worker_profile, review_required,
                  acceptance_criteria_json, context_hints_json, allowed_toolsets_json,
                  disallowed_tools_json, depends_on_policy, checkpoint_json, handoff_summary,
                  artifacts_json, status, priority, ready_at, due_at, max_attempts,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    normalized_session_id,
                    normalized_root_task_id,
                    normalized_title,
                    normalized_body,
                    normalized_kind,
                    normalized_source,
                    normalized_parent_task_id,
                    normalized_plan_run_id,
                    normalized_plan_item_id,
                    normalized_worker_type,
                    normalized_worker_profile,
                    1 if review_required else 0,
                    acceptance_criteria_json,
                    context_hints_json,
                    allowed_toolsets_json,
                    disallowed_tools_json,
                    normalized_depends_on_policy,
                    checkpoint_json,
                    normalized_handoff_summary,
                    artifacts_json,
                    normalized_priority,
                    normalized_ready_at,
                    normalized_due_at,
                    normalized_max_attempts,
                    now,
                    now,
                ),
            )
            self._insert_task_event(
                connection,
                task_id=task_id,
                session_id=normalized_session_id,
                event_type="created",
                status="queued",
                message="Task created",
                metadata={
                    "kind": normalized_kind,
                    "source": normalized_source,
                    "rootTaskId": normalized_root_task_id,
                    "planItemId": normalized_plan_item_id,
                    "parentTaskId": normalized_parent_task_id,
                    "workerType": normalized_worker_type,
                    "workerProfile": normalized_worker_profile,
                },
                created_at=now,
            )
        task = self.get_task(task_id)
        if task is None:
            raise RuntimeError("created task could not be loaded")
        return task

    def list_tasks(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int = 50,
    ) -> dict[str, object]:
        normalized_session_id = normalize_session_id(session_id) if session_id else None
        normalized_status = normalize_task_status(status) if status else None
        normalized_limit = max(1, min(200, int(limit)))
        clauses: list[str] = []
        params: list[object] = []
        if normalized_session_id:
            self.ensure_session(normalized_session_id)
            clauses.append("session_id = ?")
            params.append(normalized_session_id)
        if normalized_status:
            clauses.append("status = ?")
            params.append(normalized_status)
        elif active_only:
            clauses.append("status IN ('queued', 'running', 'blocked')")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, session_id, title, body, status, priority, due_at, claim_lock,
                       last_heartbeat, result, error, created_at, updated_at, finished_at,
                       attempt_count, max_attempts, next_run_at, kind, source, parent_task_id,
                       plan_item_id, worker_type, blocked_reason, review_required, artifacts_json,
                       lease_owner, lease_expires_at, runner_kind, root_task_id, plan_run_id,
                       worker_profile, acceptance_criteria_json, context_hints_json,
                       allowed_toolsets_json, disallowed_tools_json, depends_on_policy,
                       checkpoint_json, handoff_summary, ready_at
                FROM tasks
                {where}
                ORDER BY
                  CASE status
                    WHEN 'running' THEN 0
                    WHEN 'blocked' THEN 1
                    WHEN 'queued' THEN 2
                    WHEN 'failed' THEN 3
                    WHEN 'succeeded' THEN 4
                    WHEN 'cancelled' THEN 5
                    ELSE 6
                  END,
                  priority DESC,
                  updated_at DESC
                LIMIT ?
                """,
                (*params, normalized_limit),
            ).fetchall()
        tasks = [task_response(row) for row in rows]
        return {
            "sessionId": normalized_session_id,
            "tasks": tasks,
            "summary": task_summary(tasks),
            "filters": {
                "sessionId": normalized_session_id,
                "status": normalized_status,
                "activeOnly": active_only,
                "limit": normalized_limit,
            },
        }

    def get_task(self, task_id: str) -> dict[str, object] | None:
        normalized_task_id = normalize_task_id(task_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, session_id, title, body, status, priority, due_at, claim_lock,
                       last_heartbeat, result, error, created_at, updated_at, finished_at,
                       attempt_count, max_attempts, next_run_at, kind, source, parent_task_id,
                       plan_item_id, worker_type, blocked_reason, review_required, artifacts_json,
                       lease_owner, lease_expires_at, runner_kind, root_task_id, plan_run_id,
                       worker_profile, acceptance_criteria_json, context_hints_json,
                       allowed_toolsets_json, disallowed_tools_json, depends_on_policy,
                       checkpoint_json, handoff_summary, ready_at
                FROM tasks
                WHERE id = ?
                """,
                (normalized_task_id,),
            ).fetchone()
        return task_response(row) if row else None

    def list_recent_terminal_tasks(self, *, session_id: str, limit: int = 5) -> dict[str, object]:
        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        normalized_limit = max(1, min(50, int(limit)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, title, body, status, priority, due_at, claim_lock,
                       last_heartbeat, result, error, created_at, updated_at, finished_at,
                       attempt_count, max_attempts, next_run_at, kind, source, parent_task_id,
                       plan_item_id, worker_type, blocked_reason, review_required, artifacts_json,
                       lease_owner, lease_expires_at, runner_kind, root_task_id, plan_run_id,
                       worker_profile, acceptance_criteria_json, context_hints_json,
                       allowed_toolsets_json, disallowed_tools_json, depends_on_policy,
                       checkpoint_json, handoff_summary, ready_at
                FROM tasks
                WHERE session_id = ? AND status IN ('succeeded', 'failed', 'cancelled')
                ORDER BY COALESCE(finished_at, updated_at) DESC
                LIMIT ?
                """,
                (normalized_session_id, normalized_limit),
            ).fetchall()
        tasks = [task_response(row) for row in rows]
        return {
            "sessionId": normalized_session_id,
            "tasks": tasks,
            "summary": task_summary(tasks),
            "filters": {
                "sessionId": normalized_session_id,
                "terminalOnly": True,
                "limit": normalized_limit,
            },
        }

    def add_task_edge(
        self,
        *,
        from_task_id: str,
        to_task_id: str,
        edge_type: str | None = None,
        required_status: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        normalized_from_task_id = normalize_task_id(from_task_id)
        normalized_to_task_id = normalize_task_id(to_task_id)
        if normalized_from_task_id == normalized_to_task_id:
            raise ValueError("task edge cannot point to itself")
        normalized_edge_type = normalize_task_edge_type(edge_type)
        normalized_required_status = normalize_task_status(required_status or "succeeded")
        metadata_json = normalize_task_json_object(metadata, field_name="metadata")
        edge_id = uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            from_row = connection.execute("SELECT id FROM tasks WHERE id = ?", (normalized_from_task_id,)).fetchone()
            to_row = connection.execute("SELECT id FROM tasks WHERE id = ?", (normalized_to_task_id,)).fetchone()
            if not from_row or not to_row:
                raise ValueError("task not found")
            connection.execute(
                """
                INSERT INTO task_edges (
                  id, from_task_id, to_task_id, edge_type, required_status, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(from_task_id, to_task_id, edge_type) DO UPDATE SET
                  required_status = excluded.required_status,
                  metadata_json = excluded.metadata_json
                """,
                (
                    edge_id,
                    normalized_from_task_id,
                    normalized_to_task_id,
                    normalized_edge_type,
                    normalized_required_status,
                    metadata_json,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT id, from_task_id, to_task_id, edge_type, required_status, metadata_json, created_at
                FROM task_edges
                WHERE from_task_id = ? AND to_task_id = ? AND edge_type = ?
                """,
                (normalized_from_task_id, normalized_to_task_id, normalized_edge_type),
            ).fetchone()
        if row is None:
            raise RuntimeError("task edge could not be loaded")
        return task_edge_response(row)

    def list_task_edges(self, task_id: str | None = None, *, direction: str = "both") -> list[dict[str, object]]:
        normalized_task_id = normalize_task_id(task_id) if task_id else None
        normalized_direction = str(direction or "both").strip().lower()
        clauses: list[str] = []
        params: list[object] = []
        if normalized_task_id:
            if normalized_direction == "incoming":
                clauses.append("to_task_id = ?")
                params.append(normalized_task_id)
            elif normalized_direction == "outgoing":
                clauses.append("from_task_id = ?")
                params.append(normalized_task_id)
            else:
                clauses.append("(from_task_id = ? OR to_task_id = ?)")
                params.extend([normalized_task_id, normalized_task_id])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, from_task_id, to_task_id, edge_type, required_status, metadata_json, created_at
                FROM task_edges
                {where}
                ORDER BY created_at ASC
                """,
                params,
            ).fetchall()
        return [task_edge_response(row) for row in rows]

    def rewire_task_dependencies(
        self,
        failed_task_id: str,
        replacement_task_id: str,
    ) -> dict[str, object]:
        normalized_failed_id = normalize_task_id(failed_task_id)
        normalized_replacement_id = normalize_task_id(replacement_task_id)
        if normalized_failed_id == normalized_replacement_id:
            raise ValueError("replacement task must be different from failed task")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task_rows = connection.execute(
                """
                SELECT id, root_task_id, status
                FROM tasks
                WHERE id IN (?, ?)
                """,
                (normalized_failed_id, normalized_replacement_id),
            ).fetchall()
            tasks = {
                str(row[0]): {
                    "rootTaskId": str(row[1] or row[0]),
                    "status": str(row[2]),
                }
                for row in task_rows
            }
            failed = tasks.get(normalized_failed_id)
            replacement = tasks.get(normalized_replacement_id)
            if failed is None or replacement is None:
                raise ValueError("task not found")
            if failed["rootTaskId"] != replacement["rootTaskId"]:
                raise ValueError("replacement task must belong to the same task graph")
            if failed["status"] not in {"failed", "cancelled"}:
                raise ValueError("task must be failed or cancelled before replanning")
            if replacement["status"] != "queued":
                raise ValueError("replacement task must be queued")

            incoming = connection.execute(
                """
                SELECT from_task_id, edge_type, required_status, metadata_json
                FROM task_edges
                WHERE to_task_id = ?
                ORDER BY created_at ASC
                """,
                (normalized_failed_id,),
            ).fetchall()
            outgoing = connection.execute(
                """
                SELECT id, to_task_id, edge_type, required_status, metadata_json
                FROM task_edges
                WHERE from_task_id = ?
                ORDER BY created_at ASC
                """,
                (normalized_failed_id,),
            ).fetchall()

            copied_incoming = 0
            for edge in incoming:
                metadata = json_payload(edge[3], default={})
                connection.execute(
                    """
                    INSERT INTO task_edges (
                      id, from_task_id, to_task_id, edge_type, required_status,
                      metadata_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(from_task_id, to_task_id, edge_type) DO UPDATE SET
                      required_status = excluded.required_status,
                      metadata_json = excluded.metadata_json
                    """,
                    (
                        uuid4().hex,
                        str(edge[0]),
                        normalized_replacement_id,
                        str(edge[1]),
                        str(edge[2]),
                        normalize_task_json_object(
                            {
                                **(metadata if isinstance(metadata, dict) else {}),
                                "source": "orchestrator_replan",
                                "replanOfTaskId": normalized_failed_id,
                            },
                            field_name="metadata",
                        ),
                        now,
                    ),
                )
                copied_incoming += 1

            rewired_outgoing = 0
            for edge in outgoing:
                dependent = connection.execute(
                    "SELECT status FROM tasks WHERE id = ?",
                    (str(edge[1]),),
                ).fetchone()
                if not dependent or str(dependent[0]) not in {"queued", "blocked"}:
                    continue
                metadata = json_payload(edge[4], default={})
                connection.execute(
                    """
                    INSERT INTO task_edges (
                      id, from_task_id, to_task_id, edge_type, required_status,
                      metadata_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(from_task_id, to_task_id, edge_type) DO UPDATE SET
                      required_status = excluded.required_status,
                      metadata_json = excluded.metadata_json
                    """,
                    (
                        uuid4().hex,
                        normalized_replacement_id,
                        str(edge[1]),
                        str(edge[2]),
                        str(edge[3]),
                        normalize_task_json_object(
                            {
                                **(metadata if isinstance(metadata, dict) else {}),
                                "source": "orchestrator_replan",
                                "replanOfTaskId": normalized_failed_id,
                            },
                            field_name="metadata",
                        ),
                        now,
                    ),
                )
                connection.execute(
                    "DELETE FROM task_edges WHERE id = ?",
                    (str(edge[0]),),
                )
                rewired_outgoing += 1
            cursor = connection.execute(
                """
                UPDATE tasks
                SET ready_at = NULL,
                    updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, normalized_replacement_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("replacement task could not be activated")

        return {
            "failedTaskId": normalized_failed_id,
            "replacementTaskId": normalized_replacement_id,
            "copiedIncomingEdgeCount": copied_incoming,
            "rewiredOutgoingEdgeCount": rewired_outgoing,
        }

    def create_task_attempt(
        self,
        task_id: str,
        *,
        run_id: str | None = None,
        worker_id: str | None = None,
        worker_profile: str | None = None,
        input_context: dict[str, object] | None = None,
        checkpoint: dict[str, object] | None = None,
    ) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_run_id = normalize_optional_text(run_id or uuid4().hex, max_chars=120, field_name="run_id") or uuid4().hex
        normalized_worker_id = normalize_optional_text(worker_id, max_chars=120, field_name="worker_id")
        normalized_worker_profile = normalize_optional_text(worker_profile, max_chars=120, field_name="worker_profile")
        input_context_json = normalize_task_json_object(input_context, field_name="input_context")
        checkpoint_json = normalize_task_json_object(checkpoint, field_name="checkpoint")
        attempt_id = uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            if not connection.execute("SELECT id FROM tasks WHERE id = ?", (normalized_task_id,)).fetchone():
                raise ValueError("task not found")
            connection.execute(
                """
                INSERT INTO task_attempts (
                  id, task_id, run_id, worker_id, worker_profile, status, started_at,
                  heartbeat_at, input_context_json, checkpoint_json
                )
                VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    normalized_task_id,
                    normalized_run_id,
                    normalized_worker_id,
                    normalized_worker_profile,
                    now,
                    now,
                    input_context_json,
                    checkpoint_json,
                ),
            )
            row = connection.execute(
                """
                SELECT id, task_id, run_id, worker_id, worker_profile, status, started_at,
                       heartbeat_at, finished_at, input_context_json, checkpoint_json,
                       result, error, token_usage_json, tool_usage_json
                FROM task_attempts
                WHERE id = ?
                """,
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("task attempt could not be loaded")
        return task_attempt_response(row)

    def heartbeat_task_attempt(self, attempt_id: str, *, checkpoint: dict[str, object] | None = None) -> dict[str, object]:
        normalized_attempt_id = normalize_task_id(attempt_id)
        checkpoint_json = normalize_task_json_object(checkpoint, field_name="checkpoint") if checkpoint is not None else None
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = connection.execute("SELECT id FROM task_attempts WHERE id = ?", (normalized_attempt_id,)).fetchone()
            if not row:
                raise ValueError("task attempt not found")
            if checkpoint_json is None:
                connection.execute(
                    "UPDATE task_attempts SET heartbeat_at = ? WHERE id = ?",
                    (now, normalized_attempt_id),
                )
            else:
                connection.execute(
                    "UPDATE task_attempts SET heartbeat_at = ?, checkpoint_json = ? WHERE id = ?",
                    (now, checkpoint_json, normalized_attempt_id),
                )
        attempt = self.get_task_attempt(normalized_attempt_id)
        if attempt is None:
            raise ValueError("task attempt not found")
        return attempt

    def finish_task_attempt(
        self,
        attempt_id: str,
        *,
        status: str,
        result: str | None = None,
        error: str | None = None,
        checkpoint: dict[str, object] | None = None,
        token_usage: dict[str, object] | None = None,
        tool_usage: dict[str, object] | None = None,
    ) -> dict[str, object]:
        normalized_attempt_id = normalize_task_id(attempt_id)
        normalized_status = normalize_task_attempt_status(status)
        if normalized_status == "running":
            raise ValueError("finished attempt status cannot be running")
        normalized_result = normalize_optional_text(result, max_chars=MAX_TASK_RESULT_CHARS, field_name="result")
        normalized_error = normalize_optional_text(error, max_chars=MAX_TASK_ERROR_CHARS, field_name="error")
        checkpoint_json = normalize_task_json_object(checkpoint, field_name="checkpoint") if checkpoint is not None else None
        token_usage_json = normalize_task_json_object(token_usage, field_name="token_usage")
        tool_usage_json = normalize_task_json_object(tool_usage, field_name="tool_usage")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = connection.execute("SELECT id FROM task_attempts WHERE id = ?", (normalized_attempt_id,)).fetchone()
            if not row:
                raise ValueError("task attempt not found")
            if checkpoint_json is None:
                connection.execute(
                    """
                    UPDATE task_attempts
                    SET status = ?, finished_at = ?, result = ?, error = ?,
                        token_usage_json = ?, tool_usage_json = ?
                    WHERE id = ?
                    """,
                    (
                        normalized_status,
                        now,
                        normalized_result,
                        normalized_error,
                        token_usage_json,
                        tool_usage_json,
                        normalized_attempt_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE task_attempts
                    SET status = ?, finished_at = ?, result = ?, error = ?,
                        checkpoint_json = ?, token_usage_json = ?, tool_usage_json = ?
                    WHERE id = ?
                    """,
                    (
                        normalized_status,
                        now,
                        normalized_result,
                        normalized_error,
                        checkpoint_json,
                        token_usage_json,
                        tool_usage_json,
                        normalized_attempt_id,
                    ),
                )
        attempt = self.get_task_attempt(normalized_attempt_id)
        if attempt is None:
            raise ValueError("task attempt not found")
        return attempt

    def get_task_attempt(self, attempt_id: str) -> dict[str, object] | None:
        normalized_attempt_id = normalize_task_id(attempt_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, task_id, run_id, worker_id, worker_profile, status, started_at,
                       heartbeat_at, finished_at, input_context_json, checkpoint_json,
                       result, error, token_usage_json, tool_usage_json
                FROM task_attempts
                WHERE id = ?
                """,
                (normalized_attempt_id,),
            ).fetchone()
        return task_attempt_response(row) if row else None

    def list_task_attempts(self, task_id: str, *, limit: int = 50) -> list[dict[str, object]]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_limit = max(1, min(200, int(limit)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, task_id, run_id, worker_id, worker_profile, status, started_at,
                       heartbeat_at, finished_at, input_context_json, checkpoint_json,
                       result, error, token_usage_json, tool_usage_json
                FROM task_attempts
                WHERE task_id = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (normalized_task_id, normalized_limit),
            ).fetchall()
        return [task_attempt_response(row) for row in rows]

    def add_task_artifact(
        self,
        task_id: str,
        artifact: dict[str, object],
        *,
        attempt_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_attempt_id = normalize_task_id(attempt_id) if attempt_id else None
        normalized_artifact = normalize_task_artifact(artifact)
        metadata_json = normalize_task_json_object(metadata, field_name="metadata")
        artifact_id = uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            if not connection.execute("SELECT id FROM tasks WHERE id = ?", (normalized_task_id,)).fetchone():
                raise ValueError("task not found")
            if normalized_attempt_id and not connection.execute(
                "SELECT id FROM task_attempts WHERE id = ? AND task_id = ?",
                (normalized_attempt_id, normalized_task_id),
            ).fetchone():
                raise ValueError("task attempt not found")
            connection.execute(
                """
                INSERT INTO task_artifacts (
                  id, task_id, attempt_id, type, title, path, url, content, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    normalized_task_id,
                    normalized_attempt_id,
                    str(normalized_artifact.get("type") or "summary"),
                    str(normalized_artifact.get("title") or "Artifact"),
                    normalized_artifact.get("path") if isinstance(normalized_artifact.get("path"), str) else None,
                    normalized_artifact.get("url") if isinstance(normalized_artifact.get("url"), str) else None,
                    normalized_artifact.get("content") if isinstance(normalized_artifact.get("content"), str) else normalized_artifact.get("summary") if isinstance(normalized_artifact.get("summary"), str) else None,
                    metadata_json,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT id, task_id, attempt_id, type, title, path, url, content, metadata_json, created_at
                FROM task_artifacts
                WHERE id = ?
                """,
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("task artifact could not be loaded")
        return task_artifact_response(row)

    def list_task_artifacts(self, task_id: str, *, limit: int = 100) -> list[dict[str, object]]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_limit = max(1, min(200, int(limit)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, task_id, attempt_id, type, title, path, url, content, metadata_json, created_at
                FROM task_artifacts
                WHERE task_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (normalized_task_id, normalized_limit),
            ).fetchall()
        return [task_artifact_response(row) for row in rows]

    def set_task_artifact_file_resume_override(
        self,
        task_id: str,
        artifact_id: str,
        override: str | None,
        *,
        audit_source: str = "memory_store",
        audit_actor: str | None = None,
    ) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_artifact_id = str(artifact_id or "").strip()
        allowed_overrides = {"force_rerun", "ignore_artifact", "accept_current_state"}
        normalized_override = str(override or "").strip()
        normalized_audit_source = normalize_optional_text(
            audit_source,
            max_chars=120,
            field_name="audit_source",
        ) or "memory_store"
        normalized_audit_actor = normalize_optional_text(
            audit_actor,
            max_chars=120,
            field_name="audit_actor",
        )
        if not normalized_artifact_id:
            raise ValueError("artifact id is required")
        if normalized_override and normalized_override not in allowed_overrides:
            raise ValueError("unsupported file resume override")

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            task_row = connection.execute(
                "SELECT session_id, status FROM tasks WHERE id = ?",
                (normalized_task_id,),
            ).fetchone()
            if task_row is None:
                raise ValueError("task not found")
            row = connection.execute(
                """
                SELECT id, task_id, attempt_id, type, title, path, url, content, metadata_json, created_at
                FROM task_artifacts
                WHERE id = ? AND task_id = ?
                """,
                (normalized_artifact_id, normalized_task_id),
            ).fetchone()
            if row is None:
                raise ValueError("task artifact not found")
            metadata = json_payload(row[8], default={})
            if not isinstance(metadata, dict):
                metadata = {}
            policy = metadata.get("fileResumePolicy")
            if not isinstance(policy, dict):
                raise ValueError("task artifact has no file resume policy")
            previous_override = str(policy.get("override") or "").strip() or None
            next_policy = dict(policy)
            if normalized_override:
                next_policy["override"] = normalized_override
            else:
                next_policy.pop("override", None)
            next_metadata = dict(metadata)
            next_metadata["fileResumePolicy"] = next_policy
            metadata_json = normalize_task_json_object(next_metadata, field_name="metadata")
            connection.execute(
                "UPDATE task_artifacts SET metadata_json = ? WHERE id = ? AND task_id = ?",
                (metadata_json, normalized_artifact_id, normalized_task_id),
            )
            updated = connection.execute(
                """
                SELECT id, task_id, attempt_id, type, title, path, url, content, metadata_json, created_at
                FROM task_artifacts
                WHERE id = ? AND task_id = ?
                """,
                (normalized_artifact_id, normalized_task_id),
            ).fetchone()
            event_type = "file_resume_override_set" if normalized_override else "file_resume_override_cleared"
            self._insert_task_event(
                connection,
                task_id=normalized_task_id,
                session_id=str(task_row[0]),
                event_type=event_type,
                status=str(task_row[1]),
                message=(
                    f"File resume override set to {normalized_override}"
                    if normalized_override
                    else "File resume override cleared"
                ),
                metadata={
                    "artifactId": normalized_artifact_id,
                    "artifactPath": str(row[5]) if row[5] else None,
                    "toolName": metadata.get("toolName"),
                    "policyAction": policy.get("action"),
                    "policyPaths": policy.get("paths"),
                    "previousOverride": previous_override,
                    "override": normalized_override or None,
                    "changed": previous_override != (normalized_override or None),
                    "auditSource": normalized_audit_source,
                    "auditActor": normalized_audit_actor,
                },
                created_at=now,
            )
        if updated is None:
            raise RuntimeError("task artifact could not be loaded")
        return task_artifact_response(updated)

    def get_task_graph(self, task_id: str) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        root = self.get_task(normalized_task_id)
        if root is None:
            raise ValueError("task not found")
        root_task_id = str(root.get("rootTaskId") or root.get("id"))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, title, body, status, priority, due_at, claim_lock,
                       last_heartbeat, result, error, created_at, updated_at, finished_at,
                       attempt_count, max_attempts, next_run_at, kind, source, parent_task_id,
                       plan_item_id, worker_type, blocked_reason, review_required, artifacts_json,
                       lease_owner, lease_expires_at, runner_kind, root_task_id, plan_run_id,
                       worker_profile, acceptance_criteria_json, context_hints_json,
                       allowed_toolsets_json, disallowed_tools_json, depends_on_policy,
                       checkpoint_json, handoff_summary, ready_at
                FROM tasks
                WHERE id = ? OR root_task_id = ?
                ORDER BY created_at ASC
                """,
                (root_task_id, root_task_id),
            ).fetchall()
        task_ids = {str(row[0]) for row in rows}
        edges = [
            edge
            for edge in self.list_task_edges()
            if str(edge.get("fromTaskId")) in task_ids or str(edge.get("toTaskId")) in task_ids
        ]
        return {
            "rootTaskId": root_task_id,
            "tasks": [task_response(row) for row in rows],
            "edges": edges,
        }

    def list_todos(
        self,
        *,
        session_id: str,
        active_only: bool = False,
        limit: int = 100,
    ) -> dict[str, object]:
        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        normalized_limit = max(1, min(256, int(limit)))
        where = "session_id = ?"
        params: list[object] = [normalized_session_id]
        if active_only:
            where += " AND status IN ('pending', 'in_progress')"
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, session_id, content, status, order_index, created_at, updated_at, completed_at
                FROM todo_items
                WHERE {where}
                ORDER BY order_index ASC, created_at ASC
                LIMIT ?
                """,
                (*params, normalized_limit),
            ).fetchall()
        todos = [todo_response(row) for row in rows]
        return {
            "sessionId": normalized_session_id,
            "todos": todos,
            "summary": todo_summary(todos),
            "filters": {
                "sessionId": normalized_session_id,
                "activeOnly": active_only,
                "limit": normalized_limit,
            },
        }

    def save_todos(
        self,
        *,
        session_id: str,
        todos: list[dict[str, object]],
        merge: bool = False,
    ) -> dict[str, object]:
        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        if not isinstance(todos, list):
            raise ValueError("todos must be an array")
        normalized_items = [normalize_todo_item(item, index) for index, item in enumerate(dedupe_todos_by_id(todos[:256]))]
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            if not merge:
                connection.execute("DELETE FROM todo_items WHERE session_id = ?", (normalized_session_id,))
            else:
                existing_rows = connection.execute(
                    """
                    SELECT id, content, status, order_index, created_at, completed_at
                    FROM todo_items
                    WHERE session_id = ?
                    """,
                    (normalized_session_id,),
                ).fetchall()
                existing_by_id = {str(row[0]): row for row in existing_rows}
                max_order = max((int(row[3] or 0) for row in existing_rows), default=-1)
                merged_items: list[dict[str, object]] = []
                for item in normalized_items:
                    existing = existing_by_id.get(str(item["id"]))
                    if existing:
                        merged_items.append({
                            **item,
                            "orderIndex": int(existing[3] or 0),
                            "createdAt": str(existing[4]),
                            "completedAt": str(existing[5]) if existing[5] else None,
                        })
                    else:
                        max_order += 1
                        merged_items.append({**item, "orderIndex": max_order, "createdAt": now, "completedAt": None})
                normalized_items = merged_items

            for index, item in enumerate(normalized_items):
                item_id = str(item["id"])
                status = normalize_todo_status(item["status"])
                completed_at = now if status == "completed" and not item.get("completedAt") else item.get("completedAt")
                order_index = int(item.get("orderIndex") if item.get("orderIndex") is not None else index)
                created_at = str(item.get("createdAt") or now)
                connection.execute(
                    """
                    INSERT INTO todo_items (id, session_id, content, status, order_index, created_at, updated_at, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                      content = excluded.content,
                      status = excluded.status,
                      order_index = excluded.order_index,
                      updated_at = excluded.updated_at,
                      completed_at = excluded.completed_at
                    """,
                    (
                        item_id,
                        normalized_session_id,
                        str(item["content"]),
                        status,
                        order_index,
                        created_at,
                        now,
                        completed_at,
                    ),
                )
        return self.list_todos(session_id=normalized_session_id, active_only=False, limit=256)

    def create_scheduled_job(
        self,
        *,
        session_id: str,
        title: str | None,
        message: str,
        schedule: str,
        mode: str | None = None,
        repeat_count: int | None = None,
    ) -> dict[str, object]:
        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        normalized_message = normalize_scheduled_message(message)
        normalized_title = normalize_scheduled_title(title or normalized_message)
        normalized_mode = normalize_scheduled_mode(mode)
        parsed_schedule = parse_schedule(schedule)
        normalized_repeat_count = normalize_scheduled_repeat_count(repeat_count)
        if parsed_schedule.kind == "once":
            normalized_repeat_count = 1
        elif repeat_count is not None and normalized_repeat_count == 1:
            normalized_repeat_count = 1
        job_id = uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        schedule_payload = parsed_schedule.to_payload()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO scheduled_jobs (
                  id, session_id, title, message, mode, schedule_json, schedule_display,
                  status, repeat_count, completed_runs, next_run_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, 0, ?, ?, ?)
                """,
                (
                    job_id,
                    normalized_session_id,
                    normalized_title,
                    normalized_message,
                    normalized_mode,
                    json.dumps(schedule_payload, ensure_ascii=False),
                    parsed_schedule.display,
                    normalized_repeat_count,
                    parsed_schedule.next_run_at,
                    now,
                    now,
                ),
            )
            self._insert_scheduled_job_event(
                connection,
                job_id=job_id,
                session_id=normalized_session_id,
                event_type="created",
                status="scheduled",
                message="Scheduled job created",
                metadata={"schedule": schedule_payload, "repeatCount": normalized_repeat_count, "mode": normalized_mode},
                created_at=now,
            )
        job = self.get_scheduled_job(job_id)
        if job is None:
            raise RuntimeError("created scheduled job could not be loaded")
        return job

    def list_scheduled_jobs(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        active_only: bool = False,
        limit: int = 50,
    ) -> dict[str, object]:
        normalized_session_id = normalize_session_id(session_id) if session_id else None
        normalized_status = normalize_scheduled_status(status) if status else None
        normalized_limit = max(1, min(200, int(limit)))
        clauses: list[str] = []
        params: list[object] = []
        if normalized_session_id:
            self.ensure_session(normalized_session_id)
            clauses.append("session_id = ?")
            params.append(normalized_session_id)
        if normalized_status:
            clauses.append("status = ?")
            params.append(normalized_status)
        elif active_only:
            clauses.append("status IN ('scheduled', 'running', 'paused', 'failed')")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                  SELECT id, session_id, title, message, schedule_json, schedule_display, status,
                         repeat_count, completed_runs, next_run_at, last_run_at, last_error,
                         created_at, updated_at, finished_at, mode, last_task_id
                FROM scheduled_jobs
                {where}
                ORDER BY
                  CASE status
                    WHEN 'running' THEN 0
                    WHEN 'scheduled' THEN 1
                    WHEN 'paused' THEN 2
                    WHEN 'failed' THEN 3
                    WHEN 'completed' THEN 4
                    WHEN 'cancelled' THEN 5
                    ELSE 6
                  END,
                  COALESCE(next_run_at, updated_at) ASC
                LIMIT ?
                """,
                (*params, normalized_limit),
            ).fetchall()
        jobs = [scheduled_job_response(row) for row in rows]
        return {
            "sessionId": normalized_session_id,
            "jobs": jobs,
            "summary": scheduled_job_summary(jobs),
            "filters": {
                "sessionId": normalized_session_id,
                "status": normalized_status,
                "activeOnly": active_only,
                "limit": normalized_limit,
            },
        }

    def get_scheduled_job(self, job_id: str) -> dict[str, object] | None:
        normalized_job_id = normalize_scheduled_job_id(job_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                  SELECT id, session_id, title, message, schedule_json, schedule_display, status,
                         repeat_count, completed_runs, next_run_at, last_run_at, last_error,
                         created_at, updated_at, finished_at, mode, last_task_id
                FROM scheduled_jobs
                WHERE id = ?
                """,
                (normalized_job_id,),
            ).fetchone()
        return scheduled_job_response(row) if row else None

    def pause_scheduled_job(self, job_id: str) -> dict[str, object]:
        return self._set_scheduled_job_status(job_id, "paused", "paused", "Scheduled job paused")

    def resume_scheduled_job(self, job_id: str) -> dict[str, object]:
        normalized_job_id = normalize_scheduled_job_id(job_id)
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, session_id, status, schedule_json
                FROM scheduled_jobs
                WHERE id = ?
                """,
                (normalized_job_id,),
            ).fetchone()
            if not row:
                raise ValueError("scheduled job not found")
            current_status = str(row[2])
            if current_status not in {"paused", "failed"}:
                job = self.get_scheduled_job(normalized_job_id)
                if job is None:
                    raise ValueError("scheduled job not found")
                return job
            schedule_payload = json.loads(str(row[3]))
            next_run_at = compute_next_run_at(schedule_payload, now=now_dt) or now
            connection.execute(
                """
                UPDATE scheduled_jobs
                SET status = 'scheduled', next_run_at = ?, last_error = NULL, updated_at = ?, finished_at = NULL
                WHERE id = ? AND status IN ('paused', 'failed')
                """,
                (next_run_at, now, normalized_job_id),
            )
            self._insert_scheduled_job_event(
                connection,
                job_id=normalized_job_id,
                session_id=str(row[1]),
                event_type="resumed",
                status="scheduled",
                message="Scheduled job resumed",
                metadata={"previousStatus": current_status, "nextRunAt": next_run_at},
                created_at=now,
            )
        job = self.get_scheduled_job(normalized_job_id)
        if job is None:
            raise ValueError("scheduled job not found")
        return job

    def cancel_scheduled_job(self, job_id: str, *, reason: str | None = None) -> dict[str, object]:
        return self._set_scheduled_job_status(
            job_id,
            "cancelled",
            "cancelled",
            normalize_optional_text(reason, max_chars=500, field_name="reason") or "Scheduled job cancelled",
            terminal=True,
        )

    def list_due_scheduled_jobs(self, *, limit: int = 50) -> list[dict[str, object]]:
        normalized_limit = max(1, min(200, int(limit)))
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            rows = connection.execute(
                """
                  SELECT id, session_id, title, message, schedule_json, schedule_display, status,
                         repeat_count, completed_runs, next_run_at, last_run_at, last_error,
                         created_at, updated_at, finished_at, mode, last_task_id
                FROM scheduled_jobs
                WHERE status = 'scheduled' AND next_run_at IS NOT NULL AND next_run_at <= ?
                ORDER BY next_run_at ASC, created_at ASC
                LIMIT ?
                """,
                (now, normalized_limit),
            ).fetchall()
        return [scheduled_job_response(row) for row in rows]

    def claim_scheduled_job(self, job_id: str) -> dict[str, object] | None:
        normalized_job_id = normalize_scheduled_job_id(job_id)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, session_id, status, next_run_at
                FROM scheduled_jobs
                WHERE id = ?
                """,
                (normalized_job_id,),
            ).fetchone()
            if not row:
                raise ValueError("scheduled job not found")
            if str(row[2]) != "scheduled" or not row[3] or str(row[3]) > now:
                return self.get_scheduled_job(normalized_job_id)
            connection.execute(
                """
                UPDATE scheduled_jobs
                SET status = 'running', last_run_at = ?, updated_at = ?
                WHERE id = ? AND status = 'scheduled'
                """,
                (now, now, normalized_job_id),
            )
            self._insert_scheduled_job_event(
                connection,
                job_id=normalized_job_id,
                session_id=str(row[1]),
                event_type="running",
                status="running",
                message="Scheduled job fired",
                metadata={"dueAt": str(row[3])},
                created_at=now,
            )
        return self.get_scheduled_job(normalized_job_id)

    def complete_scheduled_job_run(
        self,
        job_id: str,
        *,
        message: str = "Scheduled message delivered",
        last_task_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        normalized_job_id = normalize_scheduled_job_id(job_id)
        normalized_last_task_id = normalize_optional_text(last_task_id, max_chars=80, field_name="last_task_id")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, session_id, status, schedule_json, repeat_count, completed_runs
                FROM scheduled_jobs
                WHERE id = ?
                """,
                (normalized_job_id,),
            ).fetchone()
            if not row:
                raise ValueError("scheduled job not found")
            if str(row[2]) != "running":
                job = self.get_scheduled_job(normalized_job_id)
                if job is None:
                    raise ValueError("scheduled job not found")
                return job
            schedule_payload = json.loads(str(row[3]))
            completed_runs = int(row[5] or 0) + 1
            repeat_count = int(row[4]) if row[4] is not None else None
            next_run_at = compute_next_run_at(schedule_payload, now=now_dt)
            terminal = (
                str(schedule_payload.get("kind") or "") == "once"
                or (repeat_count is not None and completed_runs >= repeat_count)
                or next_run_at is None
            )
            next_status = "completed" if terminal else "scheduled"
            connection.execute(
                """
                UPDATE scheduled_jobs
                SET status = ?, completed_runs = ?, next_run_at = ?, last_error = NULL,
                    last_task_id = COALESCE(?, last_task_id),
                    updated_at = ?, finished_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    next_status,
                    completed_runs,
                    None if terminal else next_run_at,
                    normalized_last_task_id,
                    now,
                    now if terminal else None,
                    normalized_job_id,
                ),
            )
            self._insert_scheduled_job_event(
                connection,
                job_id=normalized_job_id,
                session_id=str(row[1]),
                event_type="completed" if terminal else "scheduled",
                status=next_status,
                message=message,
                metadata={
                    "completedRuns": completed_runs,
                    "repeatCount": repeat_count,
                    "nextRunAt": None if terminal else next_run_at,
                    **(metadata or {}),
                },
                created_at=now,
            )
        job = self.get_scheduled_job(normalized_job_id)
        if job is None:
            raise ValueError("scheduled job not found")
        return job

    def fail_scheduled_job_run(self, job_id: str, error: str) -> dict[str, object]:
        normalized_job_id = normalize_scheduled_job_id(job_id)
        normalized_error = normalize_optional_text(error, max_chars=1000, field_name="error") or "Scheduled job failed"
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = connection.execute("SELECT session_id FROM scheduled_jobs WHERE id = ?", (normalized_job_id,)).fetchone()
            if not row:
                raise ValueError("scheduled job not found")
            connection.execute(
                """
                UPDATE scheduled_jobs
                SET status = 'failed', last_error = ?, next_run_at = NULL, updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (normalized_error, now, now, normalized_job_id),
            )
            self._insert_scheduled_job_event(
                connection,
                job_id=normalized_job_id,
                session_id=str(row[0]),
                event_type="failed",
                status="failed",
                message=normalized_error,
                metadata=None,
                created_at=now,
            )
        job = self.get_scheduled_job(normalized_job_id)
        if job is None:
            raise ValueError("scheduled job not found")
        return job

    def list_scheduled_job_events(self, job_id: str, *, limit: int = 100) -> list[dict[str, object]]:
        normalized_job_id = normalize_scheduled_job_id(job_id)
        normalized_limit = max(1, min(500, int(limit)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, job_id, session_id, type, status, message, metadata_json, created_at
                FROM scheduled_job_events
                WHERE job_id = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (normalized_job_id, normalized_limit),
            ).fetchall()
        return [scheduled_job_event_response(row) for row in rows]

    def _set_scheduled_job_status(
        self,
        job_id: str,
        status: ScheduledJobStatus,
        event_type: str,
        message: str,
        *,
        terminal: bool = False,
    ) -> dict[str, object]:
        normalized_job_id = normalize_scheduled_job_id(job_id)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = connection.execute("SELECT session_id, status FROM scheduled_jobs WHERE id = ?", (normalized_job_id,)).fetchone()
            if not row:
                raise ValueError("scheduled job not found")
            current_status = str(row[1])
            if current_status in {"completed", "cancelled"}:
                job = self.get_scheduled_job(normalized_job_id)
                if job is None:
                    raise ValueError("scheduled job not found")
                return job
            connection.execute(
                """
                UPDATE scheduled_jobs
                SET status = ?, updated_at = ?, finished_at = ?, next_run_at = CASE WHEN ? THEN NULL ELSE next_run_at END
                WHERE id = ?
                """,
                (status, now, now if terminal else None, 1 if terminal else 0, normalized_job_id),
            )
            self._insert_scheduled_job_event(
                connection,
                job_id=normalized_job_id,
                session_id=str(row[0]),
                event_type=event_type,
                status=status,
                message=message,
                metadata={"previousStatus": current_status},
                created_at=now,
            )
        job = self.get_scheduled_job(normalized_job_id)
        if job is None:
            raise ValueError("scheduled job not found")
        return job

    def start_task(
        self,
        task_id: str,
        *,
        claim_lock: str,
        lease_owner: str | None = None,
        lease_seconds: float = 300.0,
        runner_kind: str | None = None,
    ) -> dict[str, object] | None:
        normalized_task_id = normalize_task_id(task_id)
        normalized_claim_lock = normalize_optional_text(claim_lock, max_chars=120, field_name="claim_lock")
        if not normalized_claim_lock:
            raise ValueError("claim_lock is required")
        normalized_lease_owner = normalize_optional_text(
            lease_owner or claim_lock,
            max_chars=120,
            field_name="lease_owner",
        )
        normalized_runner_kind = normalize_optional_text(
            runner_kind or "in_process",
            max_chars=80,
            field_name="runner_kind",
        ) or "in_process"
        lease_ttl = max(1.0, float(lease_seconds))
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_expires_at = (now_dt + timedelta(seconds=lease_ttl)).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id, session_id, status, due_at, next_run_at, ready_at,
                       root_task_id
                FROM tasks
                WHERE id = ?
                """,
                (normalized_task_id,),
            ).fetchone()
            if not row:
                raise ValueError("task not found")
            if str(row[2]) != "queued":
                return self.get_task(normalized_task_id)
            if (
                not task_time_is_due(str(row[3] or ""), now_dt)
                or not task_time_is_due(str(row[4] or ""), now_dt)
                or not task_time_is_due(str(row[5] or ""), now_dt)
                or self._task_has_unsatisfied_dependencies(connection, normalized_task_id)
            ):
                return self.get_task(normalized_task_id)
            root_task_id = str(row[6] or normalized_task_id)
            if root_task_id != normalized_task_id:
                root_row = connection.execute(
                    "SELECT checkpoint_json FROM tasks WHERE id = ?",
                    (root_task_id,),
                ).fetchone()
                root_checkpoint = (
                    json_payload(root_row[0], default={})
                    if root_row
                    else {}
                )
                if (
                    isinstance(root_checkpoint, dict)
                    and str(root_checkpoint.get("phase") or "")
                    == "orchestrator_waiting"
                ):
                    try:
                        max_concurrency = max(
                            1,
                            min(
                                16,
                                int(
                                    root_checkpoint.get("maxConcurrency")
                                    or 2
                                ),
                            ),
                        )
                    except (TypeError, ValueError):
                        max_concurrency = 2
                    running_count = int(
                        connection.execute(
                            """
                            SELECT COUNT(*)
                            FROM tasks
                            WHERE root_task_id = ?
                              AND id != root_task_id
                              AND status = 'running'
                            """,
                            (root_task_id,),
                        ).fetchone()[0]
                    )
                    if running_count >= max_concurrency:
                        return self.get_task(normalized_task_id)
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = 'running',
                    attempt_count = attempt_count + 1,
                    next_run_at = NULL,
                    claim_lock = ?,
                    lease_owner = ?,
                    lease_expires_at = ?,
                    runner_kind = ?,
                    last_heartbeat = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (
                    normalized_claim_lock,
                    normalized_lease_owner,
                    lease_expires_at,
                    normalized_runner_kind,
                    now,
                    now,
                    normalized_task_id,
                ),
            )
            if cursor.rowcount != 1:
                return self.get_task(normalized_task_id)
            self._insert_task_event(
                connection,
                task_id=normalized_task_id,
                session_id=str(row[1]),
                event_type="running",
                status="running",
                message="Task worker started",
                metadata={
                    "claimLock": normalized_claim_lock,
                    "leaseOwner": normalized_lease_owner,
                    "leaseExpiresAt": lease_expires_at,
                    "runnerKind": normalized_runner_kind,
                },
                created_at=now,
            )
        return self.get_task(normalized_task_id)

    def heartbeat_task(self, task_id: str, *, claim_lock: str, lease_seconds: float = 300.0) -> dict[str, object] | None:
        normalized_task_id = normalize_task_id(task_id)
        normalized_claim_lock = normalize_optional_text(claim_lock, max_chars=120, field_name="claim_lock")
        if not normalized_claim_lock:
            raise ValueError("claim_lock is required")
        lease_ttl = max(1.0, float(lease_seconds))
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_expires_at = (now_dt + timedelta(seconds=lease_ttl)).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET last_heartbeat = ?,
                    lease_expires_at = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'running' AND claim_lock = ?
                """,
                (now, lease_expires_at, now, normalized_task_id, normalized_claim_lock),
            )
        return self.get_task(normalized_task_id)

    def complete_task(self, task_id: str, *, claim_lock: str, result: str | None = None) -> dict[str, object]:
        return self.finish_task(
            task_id,
            claim_lock=claim_lock,
            status="succeeded",
            result=result,
            error=None,
            event_type="succeeded",
            default_message="Task succeeded",
        )

    def fail_task(self, task_id: str, *, claim_lock: str, error: str | None = None) -> dict[str, object]:
        return self.finish_task(
            task_id,
            claim_lock=claim_lock,
            status="failed",
            result=None,
            error=error,
            event_type="failed",
            default_message="Task failed",
        )

    def complete_orchestrated_task(
        self,
        task_id: str,
        *,
        result: str,
    ) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_result = normalize_optional_text(
            result,
            max_chars=MAX_TASK_RESULT_CHARS,
            field_name="result",
        )
        if not normalized_result:
            raise ValueError("result is required")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT session_id, status, checkpoint_json
                FROM tasks
                WHERE id = ?
                """,
                (normalized_task_id,),
            ).fetchone()
            if not row:
                raise ValueError("task not found")
            if str(row[1]) == "succeeded":
                task = self.get_task(normalized_task_id)
                if task is None:
                    raise ValueError("task not found")
                return task
            checkpoint = json_payload(row[2], default={})
            if (
                str(row[1]) != "blocked"
                or not isinstance(checkpoint, dict)
                or str(checkpoint.get("phase") or "") != "orchestrator_waiting"
            ):
                raise ValueError("task is not waiting for orchestrator synthesis")
            completed_checkpoint = {
                "status": "succeeded",
                "phase": "orchestrator_synthesized",
                "reason": "child_graph_completed",
            }
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = 'succeeded',
                    result = ?,
                    error = NULL,
                    blocked_reason = NULL,
                    checkpoint_json = ?,
                    claim_lock = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat = NULL,
                    next_run_at = NULL,
                    updated_at = ?,
                    finished_at = ?
                WHERE id = ? AND status = 'blocked'
                """,
                (
                    normalized_result,
                    normalize_task_json_object(
                        completed_checkpoint,
                        field_name="checkpoint",
                    ),
                    now,
                    now,
                    normalized_task_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("task is not waiting for orchestrator synthesis")
            self._insert_task_event(
                connection,
                task_id=normalized_task_id,
                session_id=str(row[0]),
                event_type="succeeded",
                status="succeeded",
                message=normalized_result,
                metadata={"source": "orchestrator"},
                created_at=now,
            )
        task = self.get_task(normalized_task_id)
        if task is None:
            raise ValueError("task not found")
        return task

    def block_task(
        self,
        task_id: str,
        *,
        reason: str,
        claim_lock: str | None = None,
        result: str | None = None,
        checkpoint: dict[str, object] | None = None,
        handoff_summary: str | None = None,
        expected_status: str | None = None,
    ) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_claim_lock = normalize_optional_text(claim_lock, max_chars=120, field_name="claim_lock")
        normalized_reason = normalize_optional_text(reason, max_chars=MAX_TASK_ERROR_CHARS, field_name="reason") or "Task blocked"
        normalized_result = normalize_optional_text(result, max_chars=MAX_TASK_RESULT_CHARS, field_name="result")
        checkpoint_json = normalize_task_json_object(checkpoint, field_name="checkpoint") if checkpoint is not None else None
        normalized_handoff_summary = normalize_optional_text(
            handoff_summary,
            max_chars=MAX_TASK_RESULT_CHARS,
            field_name="handoff_summary",
        ) if handoff_summary is not None else None
        normalized_expected_status = (
            normalize_task_status(expected_status)
            if expected_status is not None
            else None
        )
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id, session_id, status, claim_lock
                FROM tasks
                WHERE id = ?
                """,
                (normalized_task_id,),
            ).fetchone()
            if not row:
                raise ValueError("task not found")
            current_status = str(row[2])
            if (
                normalized_expected_status is not None
                and current_status != normalized_expected_status
            ):
                raise ValueError(
                    f"task status changed before block: expected "
                    f"{normalized_expected_status}, got {current_status}"
                )
            if current_status in {"succeeded", "failed", "cancelled"}:
                task = self.get_task(normalized_task_id)
                if task is None:
                    raise ValueError("task not found")
                return task
            if normalized_claim_lock and (current_status != "running" or str(row[3] or "") != normalized_claim_lock):
                raise ValueError("task is not claimed by this worker")
            status_guard = (
                " AND status = ?"
                if normalized_expected_status is not None
                else ""
            )
            params: list[object] = [
                normalized_reason,
                normalized_result,
                checkpoint_json,
                normalized_handoff_summary,
                now,
                normalized_task_id,
            ]
            if normalized_expected_status is not None:
                params.append(normalized_expected_status)
            cursor = connection.execute(
                f"""
                UPDATE tasks
                SET status = 'blocked',
                    blocked_reason = ?,
                    result = COALESCE(?, result),
                    checkpoint_json = COALESCE(?, checkpoint_json),
                    handoff_summary = COALESCE(?, handoff_summary),
                    claim_lock = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat = NULL,
                    next_run_at = NULL,
                    updated_at = ?
                WHERE id = ?{status_guard}
                """,
                params,
            )
            if cursor.rowcount != 1:
                raise ValueError("task status changed before block")
            self._insert_task_event(
                connection,
                task_id=normalized_task_id,
                session_id=str(row[1]),
                event_type="blocked",
                status="blocked",
                message=normalized_reason,
                metadata={
                    "previousStatus": current_status,
                    "claimLock": normalized_claim_lock,
                    "checkpointPhase": checkpoint.get("phase") if checkpoint else None,
                    "checkpointReason": checkpoint.get("reason") if checkpoint else None,
                    "toolName": checkpoint.get("toolName") if checkpoint else None,
                    "approvalActionKey": checkpoint.get("approvalActionKey") if checkpoint else None,
                    "approvalActionLabel": checkpoint.get("approvalActionLabel") if checkpoint else None,
                    "approvalRiskLevel": checkpoint.get("approvalRiskLevel") if checkpoint else None,
                    "approvalRiskLabels": checkpoint.get("approvalRiskLabels") if checkpoint else None,
                },
                created_at=now,
            )
        task = self.get_task(normalized_task_id)
        if task is None:
            raise ValueError("task not found")
        return task

    def resume_blocked_task(
        self,
        task_id: str,
        *,
        audit_source: str = "memory_store",
        audit_actor: str | None = None,
    ) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_audit_source = normalize_optional_text(
            audit_source,
            max_chars=120,
            field_name="audit_source",
        ) or "memory_store"
        normalized_audit_actor = normalize_optional_text(
            audit_actor,
            max_chars=120,
            field_name="audit_actor",
        )
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT session_id, status, review_required, checkpoint_json FROM tasks WHERE id = ?",
                (normalized_task_id,),
            ).fetchone()
            if not row:
                raise ValueError("task not found")
            if str(row[1]) != "blocked":
                task = self.get_task(normalized_task_id)
                if task is None:
                    raise ValueError("task not found")
                return task
            previous_checkpoint: dict[str, object] = {}
            try:
                decoded_checkpoint = json.loads(str(row[3] or "{}"))
                if isinstance(decoded_checkpoint, dict):
                    previous_checkpoint = decoded_checkpoint
            except json.JSONDecodeError:
                previous_checkpoint = {}
            was_approval_checkpoint = bool(row[2]) or str(previous_checkpoint.get("phase") or "") == "approval_required"
            approved_tool_name = str(previous_checkpoint.get("toolName") or "").strip()
            approved_action_key = str(previous_checkpoint.get("approvalActionKey") or "").strip()
            approved_worker_action = bool(approved_tool_name or approved_action_key)
            approval_action_expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=self.worker_approval_action_ttl_seconds)
            ).isoformat()
            checkpoint = {
                "status": "queued",
                "phase": "approval_resume_requested" if was_approval_checkpoint else "blocked_resume_requested",
                "reason": "human_approved_worker_action" if approved_worker_action else "human_review_resumed" if was_approval_checkpoint else "blocked_task_resumed",
            }
            if approved_tool_name:
                checkpoint["approvedToolName"] = approved_tool_name
                checkpoint["approvedTools"] = [approved_tool_name]
            if approved_action_key:
                checkpoint["approvedToolAction"] = approved_action_key
                checkpoint["approvedToolActions"] = [approved_action_key]
                checkpoint["approvedToolActionExpiresAt"] = approval_action_expires_at
                checkpoint["approvedToolActionExpirations"] = {approved_action_key: approval_action_expires_at}
            if previous_checkpoint:
                checkpoint["resumeFrom"] = {
                    key: previous_checkpoint.get(key)
                    for key in (
                        "status",
                        "phase",
                        "reason",
                        "toolName",
                        "approvalActionKey",
                        "approvalActionLabel",
                        "approvalRiskLevel",
                        "approvalRiskLabels",
                        "lastEventType",
                        "resultPreview",
                        "errorPreview",
                    )
                    if previous_checkpoint.get(key) is not None
                }
            checkpoint_json = normalize_task_json_object(checkpoint, field_name="checkpoint")
            connection.execute(
                """
                UPDATE tasks
                SET status = 'queued',
                    blocked_reason = NULL,
                    checkpoint_json = ?,
                    claim_lock = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat = NULL,
                    updated_at = ?,
                    finished_at = NULL
                WHERE id = ? AND status = 'blocked'
                """,
                (checkpoint_json, now, normalized_task_id),
            )
            self._insert_task_event(
                connection,
                task_id=normalized_task_id,
                session_id=str(row[0]),
                event_type="resumed",
                status="queued",
                message="Task resumed from blocked state",
                metadata={
                    "checkpointPhase": checkpoint["phase"],
                    "checkpointReason": checkpoint["reason"],
                    "approvalGranted": approved_worker_action,
                    "approvalScope": "action" if approved_action_key else "legacy_tool" if approved_tool_name else None,
                    "approvedToolName": approved_tool_name or None,
                    "approvedToolAction": approved_action_key or None,
                    "approvedToolActionLabel": previous_checkpoint.get("approvalActionLabel"),
                    "approvalRiskLevel": previous_checkpoint.get("approvalRiskLevel"),
                    "approvalRiskLabels": previous_checkpoint.get("approvalRiskLabels"),
                    "approvedToolActionExpiresAt": approval_action_expires_at if approved_action_key else None,
                    "approvalTtlSeconds": self.worker_approval_action_ttl_seconds if approved_action_key else None,
                    "auditSource": normalized_audit_source,
                    "auditActor": normalized_audit_actor,
                },
                created_at=now,
            )
        task = self.get_task(normalized_task_id)
        if task is None:
            raise ValueError("task not found")
        return task

    def approve_task_review(
        self,
        task_id: str,
        *,
        audit_source: str = "memory_store",
        audit_actor: str | None = None,
    ) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_audit_source = normalize_optional_text(
            audit_source,
            max_chars=120,
            field_name="audit_source",
        ) or "memory_store"
        normalized_audit_actor = normalize_optional_text(
            audit_actor,
            max_chars=120,
            field_name="audit_actor",
        )
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT session_id, status, review_required FROM tasks WHERE id = ?",
                (normalized_task_id,),
            ).fetchone()
            if not row:
                raise ValueError("task not found")
            if str(row[1]) != "blocked" or not bool(row[2]):
                task = self.get_task(normalized_task_id)
                if task is None:
                    raise ValueError("task not found")
                return task
            checkpoint = {
                "status": "succeeded",
                "phase": "approved",
                "reason": "human_approved",
            }
            checkpoint_json = normalize_task_json_object(checkpoint, field_name="checkpoint")
            connection.execute(
                """
                UPDATE tasks
                SET status = 'succeeded',
                    blocked_reason = NULL,
                    error = NULL,
                    checkpoint_json = ?,
                    claim_lock = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat = NULL,
                    updated_at = ?,
                    finished_at = ?
                WHERE id = ? AND status = 'blocked' AND review_required = 1
                """,
                (checkpoint_json, now, now, normalized_task_id),
            )
            self._insert_task_event(
                connection,
                task_id=normalized_task_id,
                session_id=str(row[0]),
                event_type="review_approved",
                status="succeeded",
                message="Review approved; task marked succeeded",
                metadata={
                    "checkpointPhase": checkpoint["phase"],
                    "checkpointReason": checkpoint["reason"],
                    "approvalScope": "task_review",
                    "auditSource": normalized_audit_source,
                    "auditActor": normalized_audit_actor,
                },
                created_at=now,
            )
        task = self.get_task(normalized_task_id)
        if task is None:
            raise ValueError("task not found")
        return task

    def retry_task(
        self,
        task_id: str,
        *,
        claim_lock: str,
        error: str | None = None,
        next_run_at: str | None = None,
        checkpoint: dict[str, object] | None = None,
        handoff_summary: str | None = None,
    ) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_claim_lock = normalize_optional_text(claim_lock, max_chars=120, field_name="claim_lock")
        if not normalized_claim_lock:
            raise ValueError("claim_lock is required")
        normalized_error = normalize_optional_text(error, max_chars=MAX_TASK_ERROR_CHARS, field_name="error")
        normalized_next_run_at = normalize_optional_text(next_run_at, max_chars=80, field_name="next_run_at")
        checkpoint_json = normalize_task_json_object(checkpoint, field_name="checkpoint") if checkpoint is not None else None
        normalized_handoff_summary = normalize_optional_text(
            handoff_summary,
            max_chars=MAX_TASK_RESULT_CHARS,
            field_name="handoff_summary",
        ) if handoff_summary is not None else None
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, session_id, status, claim_lock, attempt_count, max_attempts
                FROM tasks
                WHERE id = ?
                """,
                (normalized_task_id,),
            ).fetchone()
            if not row:
                raise ValueError("task not found")
            current_status = str(row[2])
            if current_status in {"succeeded", "failed", "cancelled"}:
                task = self.get_task(normalized_task_id)
                if task is None:
                    raise ValueError("task not found")
                return task
            if current_status != "running" or str(row[3] or "") != normalized_claim_lock:
                raise ValueError("task is not claimed by this worker")
            connection.execute(
                """
                UPDATE tasks
                SET status = 'queued',
                    claim_lock = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat = NULL,
                    next_run_at = ?,
                    error = ?,
                    checkpoint_json = COALESCE(?, checkpoint_json),
                    handoff_summary = COALESCE(?, handoff_summary),
                    updated_at = ?
                WHERE id = ? AND status = 'running' AND claim_lock = ?
                """,
                (
                    normalized_next_run_at,
                    normalized_error,
                    checkpoint_json,
                    normalized_handoff_summary,
                    now,
                    normalized_task_id,
                    normalized_claim_lock,
                ),
            )
            self._insert_task_event(
                connection,
                task_id=normalized_task_id,
                session_id=str(row[1]),
                event_type="retry_scheduled",
                status="queued",
                message=normalized_error or "Task retry scheduled",
                metadata={
                    "claimLock": normalized_claim_lock,
                    "attemptCount": int(row[4] or 0),
                    "maxAttempts": int(row[5] or 0),
                    "nextRunAt": normalized_next_run_at,
                    "checkpoint": checkpoint if checkpoint is not None else None,
                },
                created_at=now,
            )
        task = self.get_task(normalized_task_id)
        if task is None:
            raise ValueError("task not found")
        return task

    def list_runnable_tasks(self, *, limit: int = 20) -> list[dict[str, object]]:
        normalized_limit = max(1, min(100, int(limit)))
        now_dt = datetime.now(timezone.utc)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, title, body, status, priority, due_at, claim_lock,
                       last_heartbeat, result, error, created_at, updated_at, finished_at,
                       attempt_count, max_attempts, next_run_at, kind, source, parent_task_id,
                       plan_item_id, worker_type, blocked_reason, review_required, artifacts_json,
                       lease_owner, lease_expires_at, runner_kind, root_task_id, plan_run_id,
                       worker_profile, acceptance_criteria_json, context_hints_json,
                       allowed_toolsets_json, disallowed_tools_json, depends_on_policy,
                       checkpoint_json, handoff_summary, ready_at
                FROM tasks
                WHERE status = 'queued'
                ORDER BY priority DESC, updated_at ASC
                LIMIT 200
                """,
            ).fetchall()
            root_ids = sorted({str(row[28] or row[0]) for row in rows})
            root_checkpoints: dict[str, dict[str, object]] = {}
            active_counts: dict[str, int] = {}
            if root_ids:
                placeholders = ", ".join("?" for _ in root_ids)
                root_rows = connection.execute(
                    f"SELECT id, checkpoint_json FROM tasks WHERE id IN ({placeholders})",
                    root_ids,
                ).fetchall()
                root_checkpoints = {
                    str(row[0]): json_payload(row[1], default={})
                    for row in root_rows
                }
                active_rows = connection.execute(
                    f"""
                    SELECT root_task_id, COUNT(*)
                    FROM tasks
                    WHERE status = 'running'
                      AND root_task_id IN ({placeholders})
                      AND id != root_task_id
                    GROUP BY root_task_id
                    """,
                    root_ids,
                ).fetchall()
                active_counts = {
                    str(row[0]): int(row[1])
                    for row in active_rows
                    if row[0]
                }
        runnable: list[dict[str, object]] = []
        selected_counts: dict[str, int] = {}
        for row in rows:
            task_id = str(row[0])
            if not (
                task_time_is_due(str(row[6] or ""), now_dt)
                and task_time_is_due(str(row[16] or ""), now_dt)
                and task_time_is_due(str(row[38] or ""), now_dt)
                and not self._task_has_unsatisfied_dependencies(None, task_id)
            ):
                continue
            root_id = str(row[28] or task_id)
            checkpoint = root_checkpoints.get(root_id) or {}
            if task_id != root_id and str(checkpoint.get("phase") or "") == "orchestrator_waiting":
                try:
                    max_concurrency = max(
                        1,
                        min(16, int(checkpoint.get("maxConcurrency") or 2)),
                    )
                except (TypeError, ValueError):
                    max_concurrency = 2
                occupied = active_counts.get(root_id, 0) + selected_counts.get(root_id, 0)
                if occupied >= max_concurrency:
                    continue
                selected_counts[root_id] = selected_counts.get(root_id, 0) + 1
            runnable.append(task_response(row))
            if len(runnable) >= normalized_limit:
                break
        return runnable[:normalized_limit]

    def _task_has_unsatisfied_dependencies(self, connection: sqlite3.Connection | None, task_id: str) -> bool:
        normalized_task_id = normalize_task_id(task_id)

        def _query(active_connection: sqlite3.Connection) -> bool:
            row = active_connection.execute(
                """
                SELECT 1
                FROM task_edges edge
                LEFT JOIN tasks dependency ON dependency.id = edge.from_task_id
                WHERE edge.to_task_id = ?
                  AND COALESCE(dependency.status, '') != edge.required_status
                LIMIT 1
                """,
                (normalized_task_id,),
            ).fetchone()
            return row is not None

        if connection is not None:
            return _query(connection)
        with self.connect() as new_connection:
            return _query(new_connection)

    def recover_stale_running_tasks(self, *, stale_after_seconds: float = 300.0, limit: int = 50) -> list[dict[str, object]]:
        normalized_limit = max(1, min(200, int(limit)))
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        stale_after = max(1.0, float(stale_after_seconds))
        stale: list[tuple[str, str, str | None, str | None, str | None, str | None, dict[str, object] | None, str | None]] = []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, claim_lock, last_heartbeat, lease_owner, lease_expires_at
                FROM tasks
                WHERE status = 'running'
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (normalized_limit,),
            ).fetchall()
            for row in rows:
                heartbeat = parse_iso_datetime(str(row[3])) if row[3] else None
                lease_expires_at = parse_iso_datetime(str(row[5])) if row[5] else None
                if lease_expires_at is not None and lease_expires_at > now_dt:
                    continue
                if lease_expires_at is None and heartbeat is not None and (now_dt - heartbeat).total_seconds() < stale_after:
                    continue
                latest_checkpoint = self._latest_running_attempt_checkpoint(connection, str(row[0]))
                recovery_checkpoint = self._recovery_checkpoint(
                    reason="stale_running_recovered",
                    previous_checkpoint=latest_checkpoint,
                    recovered_at=now,
                )
                stale.append((
                    str(row[0]),
                    str(row[1]),
                    str(row[2]) if row[2] else None,
                    str(row[3]) if row[3] else None,
                    str(row[4]) if row[4] else None,
                    str(row[5]) if row[5] else None,
                    recovery_checkpoint,
                    self._recovery_handoff_summary(
                        "Task worker recovered stale running task",
                        latest_checkpoint,
                    ),
                ))
            for task_id, session_id, claim_lock, heartbeat, lease_owner, lease_expires_at, recovery_checkpoint, handoff_summary in stale:
                checkpoint_json = normalize_task_json_object(recovery_checkpoint, field_name="checkpoint")
                normalized_handoff_summary = normalize_optional_text(
                    handoff_summary,
                    max_chars=MAX_TASK_RESULT_CHARS,
                    field_name="handoff_summary",
                )
                connection.execute(
                    """
                    UPDATE tasks
                    SET status = 'queued',
                        claim_lock = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_heartbeat = NULL,
                        next_run_at = ?,
                        error = ?,
                        checkpoint_json = ?,
                        handoff_summary = ?,
                        updated_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (
                        now,
                        "Task worker recovered stale running task",
                        checkpoint_json,
                        normalized_handoff_summary,
                        now,
                        task_id,
                    ),
                )
                self._insert_task_event(
                    connection,
                    task_id=task_id,
                    session_id=session_id,
                    event_type="recovered",
                    status="queued",
                    message="Task worker recovered stale running task",
                    metadata={
                        "previousStatus": "running",
                        "claimLock": claim_lock,
                        "leaseOwner": lease_owner,
                        "leaseExpiresAt": lease_expires_at,
                        "lastHeartbeat": heartbeat,
                        "checkpoint": recovery_checkpoint,
                    },
                    created_at=now,
                )
        return [task for task_id, _, _, _, _, _, _, _ in stale if (task := self.get_task(task_id)) is not None]

    def _latest_running_attempt_checkpoint(self, connection: sqlite3.Connection, task_id: str) -> dict[str, object] | None:
        row = connection.execute(
            """
            SELECT checkpoint_json
            FROM task_attempts
            WHERE task_id = ? AND status = 'running'
            ORDER BY heartbeat_at DESC, started_at DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if not row:
            return None
        checkpoint = json_payload(row[0], default={})
        return checkpoint if isinstance(checkpoint, dict) and checkpoint else None

    @staticmethod
    def _recovery_checkpoint(
        *,
        reason: str,
        previous_checkpoint: dict[str, object] | None,
        recovered_at: str,
    ) -> dict[str, object]:
        checkpoint: dict[str, object] = {
            "status": "queued",
            "phase": "retry_ready",
            "reason": reason,
            "recoveredAt": recovered_at,
        }
        if previous_checkpoint:
            checkpoint["resumeFrom"] = {
                "status": previous_checkpoint.get("status"),
                "phase": previous_checkpoint.get("phase"),
                "turnId": previous_checkpoint.get("turnId"),
                "lastEventType": previous_checkpoint.get("lastEventType"),
                "workerProfile": previous_checkpoint.get("workerProfile"),
                "allowedToolsets": previous_checkpoint.get("allowedToolsets"),
                "resultPreview": previous_checkpoint.get("resultPreview"),
                "errorPreview": previous_checkpoint.get("errorPreview"),
            }
        return checkpoint

    @staticmethod
    def _recovery_handoff_summary(message: str, previous_checkpoint: dict[str, object] | None) -> str:
        if not previous_checkpoint:
            return message
        phase = str(previous_checkpoint.get("phase") or "unknown")
        last_event = str(previous_checkpoint.get("lastEventType") or "none")
        preview = previous_checkpoint.get("errorPreview") or previous_checkpoint.get("resultPreview")
        summary = f"{message}. Resume from previous worker phase={phase}, lastEventType={last_event}."
        if preview:
            summary += f" Preview: {str(preview)[:500]}"
        return summary

    def finish_task(
        self,
        task_id: str,
        *,
        claim_lock: str,
        status: str,
        result: str | None,
        error: str | None,
        event_type: str,
        default_message: str,
    ) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_claim_lock = normalize_optional_text(claim_lock, max_chars=120, field_name="claim_lock")
        if not normalized_claim_lock:
            raise ValueError("claim_lock is required")
        normalized_status = normalize_task_status(status)
        if normalized_status not in {"succeeded", "failed"}:
            raise ValueError("finish status must be succeeded or failed")
        normalized_result = normalize_optional_text(result, max_chars=MAX_TASK_RESULT_CHARS, field_name="result")
        normalized_error = normalize_optional_text(error, max_chars=MAX_TASK_ERROR_CHARS, field_name="error")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, session_id, status, claim_lock
                FROM tasks
                WHERE id = ?
                """,
                (normalized_task_id,),
            ).fetchone()
            if not row:
                raise ValueError("task not found")
            current_status = str(row[2])
            if current_status in {"succeeded", "failed", "cancelled"}:
                task = self.get_task(normalized_task_id)
                if task is None:
                    raise ValueError("task not found")
                return task
            if current_status != "running" or str(row[3] or "") != normalized_claim_lock:
                raise ValueError("task is not claimed by this worker")
            connection.execute(
                """
                UPDATE tasks
                SET status = ?,
                    result = ?,
                    error = ?,
                    claim_lock = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat = NULL,
                    next_run_at = NULL,
                    updated_at = ?,
                    finished_at = ?
                WHERE id = ? AND status = 'running' AND claim_lock = ?
                """,
                (
                    normalized_status,
                    normalized_result,
                    normalized_error,
                    now,
                    now,
                    normalized_task_id,
                    normalized_claim_lock,
                ),
            )
            self._insert_task_event(
                connection,
                task_id=normalized_task_id,
                session_id=str(row[1]),
                event_type=event_type,
                status=normalized_status,
                message=normalized_error or normalized_result or default_message,
                metadata={"claimLock": normalized_claim_lock},
                created_at=now,
            )
        task = self.get_task(normalized_task_id)
        if task is None:
            raise ValueError("task not found")
        return task

    def cancel_task(self, task_id: str, *, reason: str | None = None) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_reason = normalize_optional_text(
            reason,
            max_chars=MAX_TASK_EVENT_MESSAGE_CHARS,
            field_name="reason",
        )
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, session_id, status
                FROM tasks
                WHERE id = ?
                """,
                (normalized_task_id,),
            ).fetchone()
            if not row:
                raise ValueError("task not found")
            current_status = str(row[2])
            if current_status in {"succeeded", "failed", "cancelled"}:
                task = self.get_task(normalized_task_id)
                if task is None:
                    raise ValueError("task not found")
                return task
            connection.execute(
                """
                UPDATE tasks
                SET status = 'cancelled',
                    error = ?,
                    claim_lock = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_heartbeat = NULL,
                    updated_at = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (normalized_reason, now, now, normalized_task_id),
            )
            self._insert_task_event(
                connection,
                task_id=normalized_task_id,
                session_id=str(row[1]),
                event_type="cancelled",
                status="cancelled",
                message=normalized_reason or "Task cancelled",
                metadata={"previousStatus": current_status},
                created_at=now,
            )
        task = self.get_task(normalized_task_id)
        if task is None:
            raise ValueError("task not found")
        return task

    def list_task_events(self, task_id: str, *, limit: int = 100) -> list[dict[str, object]]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_limit = max(1, min(500, int(limit)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, task_id, session_id, type, status, message, metadata_json, created_at
                FROM task_events
                WHERE task_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (normalized_task_id, normalized_limit),
            ).fetchall()
        return [task_event_response(row) for row in rows]

    def record_task_event(
        self,
        task_id: str,
        *,
        event_type: str,
        status: str | None = None,
        message: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT session_id, status FROM tasks WHERE id = ?",
                (normalized_task_id,),
            ).fetchone()
            if not row:
                raise ValueError("task not found")
            self._insert_task_event(
                connection,
                task_id=normalized_task_id,
                session_id=str(row[0]),
                event_type=event_type,
                status=status or str(row[1]),
                message=message,
                metadata=metadata,
                created_at=now,
            )
            event_row = connection.execute(
                """
                SELECT id, task_id, session_id, type, status, message, metadata_json, created_at
                FROM task_events
                WHERE task_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (normalized_task_id,),
            ).fetchone()
        if event_row is None:
            raise RuntimeError("task event could not be loaded")
        return task_event_response(event_row)

    def acquire_supervisor_lease(
        self,
        name: str,
        *,
        owner_id: str,
        pid: int | None,
        lease_seconds: float,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        normalized_name = normalize_optional_text(name, max_chars=120, field_name="name")
        normalized_owner = normalize_optional_text(owner_id, max_chars=160, field_name="owner_id")
        if not normalized_name or not normalized_owner:
            raise ValueError("supervisor lease name and owner_id are required")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(seconds=max(1.0, float(lease_seconds)))).isoformat()
        metadata_json = normalize_task_json_object(metadata, field_name="metadata")
        acquired = False
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT name, owner_id, pid, acquired_at, heartbeat_at, expires_at, metadata_json
                FROM supervisor_leases
                WHERE name = ?
                """,
                (normalized_name,),
            ).fetchone()
            current_expires_at = parse_iso_datetime(str(row[5])) if row and row[5] else None
            if row is None:
                connection.execute(
                    """
                    INSERT INTO supervisor_leases (
                      name, owner_id, pid, acquired_at, heartbeat_at, expires_at, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (normalized_name, normalized_owner, pid, now, now, expires_at, metadata_json),
                )
                acquired = True
            elif str(row[1]) == normalized_owner or current_expires_at is None or current_expires_at <= now_dt:
                acquired_at = str(row[3]) if str(row[1]) == normalized_owner else now
                connection.execute(
                    """
                    UPDATE supervisor_leases
                    SET owner_id = ?, pid = ?, acquired_at = ?, heartbeat_at = ?,
                        expires_at = ?, metadata_json = ?
                    WHERE name = ?
                    """,
                    (normalized_owner, pid, acquired_at, now, expires_at, metadata_json, normalized_name),
                )
                acquired = True
            lease_row = connection.execute(
                """
                SELECT name, owner_id, pid, acquired_at, heartbeat_at, expires_at, metadata_json
                FROM supervisor_leases
                WHERE name = ?
                """,
                (normalized_name,),
            ).fetchone()
        if lease_row is None:
            raise RuntimeError("supervisor lease could not be loaded")
        return {
            "acquired": acquired,
            "lease": supervisor_lease_response(lease_row),
        }

    def heartbeat_supervisor_lease(
        self,
        name: str,
        *,
        owner_id: str,
        lease_seconds: float,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        normalized_name = normalize_optional_text(name, max_chars=120, field_name="name")
        normalized_owner = normalize_optional_text(owner_id, max_chars=160, field_name="owner_id")
        if not normalized_name or not normalized_owner:
            raise ValueError("supervisor lease name and owner_id are required")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(seconds=max(1.0, float(lease_seconds)))).isoformat()
        with self.connect() as connection:
            if metadata is None:
                connection.execute(
                    """
                    UPDATE supervisor_leases
                    SET heartbeat_at = ?, expires_at = ?
                    WHERE name = ? AND owner_id = ?
                    """,
                    (now, expires_at, normalized_name, normalized_owner),
                )
            else:
                connection.execute(
                    """
                    UPDATE supervisor_leases
                    SET heartbeat_at = ?, expires_at = ?, metadata_json = ?
                    WHERE name = ? AND owner_id = ?
                    """,
                    (
                        now,
                        expires_at,
                        normalize_task_json_object(metadata, field_name="metadata"),
                        normalized_name,
                        normalized_owner,
                    ),
                )
            row = connection.execute(
                """
                SELECT name, owner_id, pid, acquired_at, heartbeat_at, expires_at, metadata_json
                FROM supervisor_leases
                WHERE name = ? AND owner_id = ?
                """,
                (normalized_name, normalized_owner),
            ).fetchone()
        return supervisor_lease_response(row) if row else None

    def get_supervisor_lease(self, name: str) -> dict[str, object] | None:
        normalized_name = normalize_optional_text(name, max_chars=120, field_name="name")
        if not normalized_name:
            raise ValueError("supervisor lease name is required")
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT name, owner_id, pid, acquired_at, heartbeat_at, expires_at, metadata_json
                FROM supervisor_leases
                WHERE name = ?
                """,
                (normalized_name,),
            ).fetchone()
        return supervisor_lease_response(row) if row else None

    def release_supervisor_lease(self, name: str, *, owner_id: str) -> bool:
        normalized_name = normalize_optional_text(name, max_chars=120, field_name="name")
        normalized_owner = normalize_optional_text(owner_id, max_chars=160, field_name="owner_id")
        if not normalized_name or not normalized_owner:
            raise ValueError("supervisor lease name and owner_id are required")
        with self.connect() as connection:
            result = connection.execute(
                "DELETE FROM supervisor_leases WHERE name = ? AND owner_id = ?",
                (normalized_name, normalized_owner),
            )
        return bool(result.rowcount)

    def register_task_process(
        self,
        *,
        task_id: str,
        run_id: str,
        supervisor_id: str,
        pid: int,
        process_group_id: int | None = None,
        workspace_path: str | None = None,
        log_path: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_run_id = normalize_task_id(run_id)
        normalized_supervisor_id = normalize_optional_text(
            supervisor_id,
            max_chars=160,
            field_name="supervisor_id",
        )
        if not normalized_supervisor_id:
            raise ValueError("supervisor_id is required")
        normalized_pid = int(pid)
        if normalized_pid <= 0:
            raise ValueError("pid must be positive")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            if not connection.execute("SELECT id FROM tasks WHERE id = ?", (normalized_task_id,)).fetchone():
                raise ValueError("task not found")
            connection.execute(
                """
                INSERT INTO task_processes (
                  run_id, task_id, supervisor_id, pid, process_group_id, status,
                  started_at, heartbeat_at, workspace_path, log_path, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                  supervisor_id = excluded.supervisor_id,
                  pid = excluded.pid,
                  process_group_id = excluded.process_group_id,
                  status = 'running',
                  heartbeat_at = excluded.heartbeat_at,
                  exited_at = NULL,
                  return_code = NULL,
                  workspace_path = excluded.workspace_path,
                  log_path = excluded.log_path,
                  metadata_json = excluded.metadata_json
                """,
                (
                    normalized_run_id,
                    normalized_task_id,
                    normalized_supervisor_id,
                    normalized_pid,
                    process_group_id,
                    now,
                    now,
                    normalize_optional_text(workspace_path, max_chars=2000, field_name="workspace_path"),
                    normalize_optional_text(log_path, max_chars=2000, field_name="log_path"),
                    normalize_task_json_object(metadata, field_name="metadata"),
                ),
            )
            row = connection.execute(
                """
                SELECT run_id, task_id, supervisor_id, pid, process_group_id, status,
                       started_at, heartbeat_at, exited_at, return_code,
                       workspace_path, log_path, metadata_json
                FROM task_processes
                WHERE run_id = ?
                """,
                (normalized_run_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("task process could not be loaded")
        return task_process_response(row)

    def update_task_process(
        self,
        run_id: str,
        *,
        supervisor_id: str | None = None,
        status: str | None = None,
        return_code: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        normalized_run_id = normalize_task_id(run_id)
        now = datetime.now(timezone.utc).isoformat()
        fields = ["heartbeat_at = ?"]
        values: list[object] = [now]
        if supervisor_id is not None:
            normalized_supervisor_id = normalize_optional_text(
                supervisor_id,
                max_chars=160,
                field_name="supervisor_id",
            )
            if not normalized_supervisor_id:
                raise ValueError("supervisor_id must not be empty")
            fields.append("supervisor_id = ?")
            values.append(normalized_supervisor_id)
        if status is not None:
            normalized_status = normalize_optional_text(status, max_chars=80, field_name="status")
            if not normalized_status:
                raise ValueError("status must not be empty")
            fields.append("status = ?")
            values.append(normalized_status)
            if normalized_status in {"exited", "lost", "terminated"}:
                fields.append("exited_at = ?")
                values.append(now)
        if return_code is not None:
            fields.append("return_code = ?")
            values.append(int(return_code))
        if metadata is not None:
            fields.append("metadata_json = ?")
            values.append(normalize_task_json_object(metadata, field_name="metadata"))
        values.append(normalized_run_id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE task_processes SET {', '.join(fields)} WHERE run_id = ?",
                values,
            )
            row = connection.execute(
                """
                SELECT run_id, task_id, supervisor_id, pid, process_group_id, status,
                       started_at, heartbeat_at, exited_at, return_code,
                       workspace_path, log_path, metadata_json
                FROM task_processes
                WHERE run_id = ?
                """,
                (normalized_run_id,),
            ).fetchone()
        return task_process_response(row) if row else None

    def list_task_processes(
        self,
        *,
        task_id: str | None = None,
        active_only: bool = False,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        normalized_limit = max(1, min(1000, int(limit)))
        clauses: list[str] = []
        values: list[object] = []
        if task_id:
            clauses.append("task_id = ?")
            values.append(normalize_task_id(task_id))
        if active_only:
            clauses.append("status IN ('running', 'adopted', 'termination_requested')")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(normalized_limit)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT run_id, task_id, supervisor_id, pid, process_group_id, status,
                       started_at, heartbeat_at, exited_at, return_code,
                       workspace_path, log_path, metadata_json
                FROM task_processes
                {where}
                ORDER BY started_at DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [task_process_response(row) for row in rows]

    def _insert_task_event(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        session_id: str,
        event_type: str,
        status: str | None,
        message: str | None,
        metadata: dict[str, object] | None,
        created_at: str,
    ) -> None:
        normalized_event_type = normalize_task_event_type(event_type)
        normalized_status = normalize_task_status(status) if status else None
        normalized_message = normalize_optional_text(
            message,
            max_chars=MAX_TASK_EVENT_MESSAGE_CHARS,
            field_name="message",
        )
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        connection.execute(
            """
            INSERT INTO task_events (task_id, session_id, type, status, message, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                session_id,
                normalized_event_type,
                normalized_status,
                normalized_message,
                metadata_json,
                created_at,
            ),
        )

    def _insert_scheduled_job_event(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        session_id: str,
        event_type: str,
        status: str | None,
        message: str | None,
        metadata: dict[str, object] | None,
        created_at: str,
    ) -> None:
        normalized_event_type = normalize_scheduled_event_type(event_type)
        normalized_status = normalize_scheduled_status(status) if status else None
        normalized_message = normalize_optional_text(message, max_chars=1000, field_name="message")
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        connection.execute(
            """
            INSERT INTO scheduled_job_events (job_id, session_id, type, status, message, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                session_id,
                normalized_event_type,
                normalized_status,
                normalized_message,
                metadata_json,
                created_at,
            ),
        )

    def save_conversation_summary(
        self,
        session_id: str,
        content: str,
        summarized_message_count: int | None = None,
        *,
        covered_message_count: int | None = None,
        source_message_start_id: int | None = None,
        source_message_end_id: int | None = None,
        covered_through_message_id: int | None = None,
        model: str | None = None,
    ) -> dict[str, str | int]:
        normalized_session_id = normalize_session_id(session_id)
        normalized_content = normalize_conversation_summary(content)
        message_count = self.count(normalized_session_id) if summarized_message_count is None else int(summarized_message_count)
        if message_count < 0:
            raise ValueError("summarized_message_count must be non-negative")
        normalized_covered_count = message_count if covered_message_count is None else int(covered_message_count)
        if normalized_covered_count < 0:
            raise ValueError("covered_message_count must be non-negative")
        normalized_start_id = normalize_optional_non_negative_int(source_message_start_id, "source_message_start_id")
        normalized_end_id = normalize_optional_non_negative_int(source_message_end_id, "source_message_end_id")
        normalized_covered_through_id = normalize_optional_non_negative_int(covered_through_message_id, "covered_through_message_id")
        if normalized_start_id is not None and normalized_end_id is not None and normalized_start_id > normalized_end_id:
            raise ValueError("source_message_start_id must be less than or equal to source_message_end_id")
        normalized_model = normalize_optional_text(model, "model", max_chars=120)

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO conversation_summaries (
                  session_id,
                  content,
                    summarized_message_count,
                    covered_message_count,
                    source_message_start_id,
                    source_message_end_id,
                    covered_through_message_id,
                    model,
                  created_at,
                  updated_at
                )
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_session_id,
                    normalized_content,
                    message_count,
                    normalized_covered_count,
                    normalized_start_id,
                    normalized_end_id,
                    normalized_covered_through_id,
                    normalized_model,
                    now,
                    now,
                ),
            )
            summary_id = cursor.lastrowid

        return {
            "summaryId": int(summary_id),
            "sessionId": normalized_session_id,
            "content": normalized_content,
            "charCount": len(normalized_content),
            "summarizedMessageCount": message_count,
            "coveredMessageCount": normalized_covered_count,
            "sourceMessageStartId": normalized_start_id or 0,
            "sourceMessageEndId": normalized_end_id or 0,
            "coveredThroughMessageId": normalized_covered_through_id or 0,
            "model": normalized_model or "",
            "createdAt": now,
            "updatedAt": now,
        }

    def load_conversation_summary(self, session_id: str) -> dict[str, str | int] | None:
        normalized_session_id = normalize_session_id(session_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                  SELECT
                    id,
                    session_id,
                    content,
                    summarized_message_count,
                    covered_message_count,
                    source_message_start_id,
                    source_message_end_id,
                    covered_through_message_id,
                    model,
                    created_at,
                    updated_at
                FROM conversation_summaries
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (normalized_session_id,),
            ).fetchone()

        if not row:
            return None
        content = str(row[2])
        return {
            "summaryId": int(row[0]),
            "sessionId": str(row[1]),
            "content": content,
            "charCount": len(content),
            "summarizedMessageCount": int(row[3]),
            "coveredMessageCount": int(row[4]),
            "sourceMessageStartId": int(row[5]) if row[5] is not None else 0,
            "sourceMessageEndId": int(row[6]) if row[6] is not None else 0,
            "coveredThroughMessageId": int(row[7]) if row[7] is not None else 0,
            "model": str(row[8]) if row[8] is not None else "",
            "createdAt": str(row[9]),
            "updatedAt": str(row[10]),
        }

    def save_memory_item(
        self,
        scope: str,
        content: str,
        *,
        confidence: float = 1.0,
        source_session_id: str | None = None,
        source_message_id: int | None = None,
        memory_type: str | None = None,
        metadata: dict[str, object] | None = None,
        actor: str = "runtime",
    ) -> dict[str, Any]:
        normalized_scope = normalize_memory_item_scope(scope)
        normalized_content = normalize_memory_item_content(content)
        normalized_memory_type = normalize_memory_item_type(memory_type)
        normalized_metadata_json = normalize_memory_item_metadata(metadata)
        content_hash = compute_memory_item_hash(normalized_content)
        normalized_confidence = normalize_confidence(confidence)
        normalized_source_session_id = normalize_optional_text(source_session_id, "source_session_id", max_chars=200)
        normalized_source_message_id = normalize_optional_non_negative_int(source_message_id, "source_message_id")
        normalized_actor = normalize_memory_item_actor(actor)
        now = datetime.now(timezone.utc).isoformat()

        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memory_items (
                  scope,
                  content,
                  memory_type,
                  metadata_json,
                  content_hash,
                  confidence,
                  source_session_id,
                  source_message_id,
                  created_at,
                  updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_scope,
                    normalized_content,
                    normalized_memory_type,
                    normalized_metadata_json,
                    content_hash,
                    normalized_confidence,
                    normalized_source_session_id,
                    normalized_source_message_id,
                    now,
                    now,
                ),
            )
            item_id = int(cursor.lastrowid)
            self._record_memory_item_history(
                connection,
                item_id,
                "ADD",
                new_content=normalized_content,
                new_metadata_json=normalized_metadata_json,
                actor=normalized_actor,
                source_session_id=normalized_source_session_id,
                source_message_id=normalized_source_message_id,
                created_at=now,
            )
            self._upsert_memory_item_fts(
                connection,
                memory_item_id=item_id,
                content=normalized_content,
                metadata_json=normalized_metadata_json,
                scope=normalized_scope,
                memory_type=normalized_memory_type,
                updated_at=now,
            )
            row = self._load_memory_item_row(connection, item_id)

        return memory_item_response(row)

    def list_memory_items(
        self,
        *,
        scope: str | None = None,
        memory_type: str | None = None,
        query: str | None = None,
        metadata_filter: dict[str, object] | None = None,
        include_deleted: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_scope = normalize_memory_item_scope(scope) if scope else None
        normalized_memory_type = normalize_memory_item_type(memory_type) if memory_type else None
        normalized_query = query.strip() if isinstance(query, str) else ""
        normalized_metadata_filter = normalize_memory_item_metadata_filter(metadata_filter)
        bounded_limit = max(1, min(100, int(limit)))
        if normalized_query and not include_deleted:
            try:
                return self.search_memory_items_bm25(
                    query=normalized_query,
                    scope=normalized_scope,
                    memory_type=normalized_memory_type,
                    metadata_filter=normalized_metadata_filter,
                    limit=bounded_limit,
                )
            except sqlite3.OperationalError:
                pass
        return self._list_memory_items_sql(
            scope=normalized_scope,
            memory_type=normalized_memory_type,
            query=normalized_query,
            metadata_filter=normalized_metadata_filter,
            include_deleted=include_deleted,
            limit=bounded_limit,
        )

    def search_memory_items_bm25(
        self,
        *,
        query: str,
        scope: str | None = None,
        memory_type: str | None = None,
        metadata_filter: dict[str, object] | None = None,
        include_deleted: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_query = query.strip() if isinstance(query, str) else ""
        if not normalized_query:
            return []
        normalized_scope = normalize_memory_item_scope(scope) if scope else None
        normalized_memory_type = normalize_memory_item_type(memory_type) if memory_type else None
        normalized_metadata_filter = normalize_memory_item_metadata_filter(metadata_filter)
        bounded_limit = max(1, min(100, int(limit)))
        scan_limit = MEMORY_ITEM_LIMIT if normalized_metadata_filter else bounded_limit
        fts_query = make_fts_query(normalized_query)
        where = "memory_items_fts MATCH ?"
        params: list[object] = [fts_query]
        if normalized_scope:
            where += " AND i.scope = ?"
            params.append(normalized_scope)
        if normalized_memory_type:
            where += " AND i.memory_type = ?"
            params.append(normalized_memory_type)
        if not include_deleted:
            where += " AND i.deleted_at IS NULL"
        params.append(scan_limit)

        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                  i.id,
                  i.scope,
                  i.content,
                  i.memory_type,
                  i.metadata_json,
                  i.content_hash,
                  i.confidence,
                  i.source_session_id,
                  i.source_message_id,
                  i.last_accessed_at,
                  i.access_count,
                  i.created_at,
                  i.updated_at,
                  i.deleted_at,
                  bm25(memory_items_fts, 8.0, 2.0, 0.8, 0.8) AS bm25_score
                FROM memory_items_fts
                JOIN memory_items i ON i.id = memory_items_fts.rowid
                WHERE {where}
                ORDER BY bm25(memory_items_fts, 8.0, 2.0, 0.8, 0.8), i.confidence DESC, i.access_count DESC, i.updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        items: list[dict[str, Any]] = []
        total_rows = max(1, len(rows))
        for index, row in enumerate(rows):
            item = memory_item_response(row[:14])
            if not memory_item_matches_metadata_filter(item, normalized_metadata_filter):
                continue
            rank_score = 1.0 - (index / total_rows)
            item["bm25Score"] = round(rank_score, 6)
            item["bm25RawScore"] = float(row[14] or 0.0)
            item["retrievalProvider"] = "memory_items_bm25"
            items.append(item)
            if len(items) >= bounded_limit:
                break
        return items

    def _list_memory_items_sql(
        self,
        *,
        scope: str | None = None,
        memory_type: str | None = None,
        query: str | None = None,
        metadata_filter: dict[str, object] | None = None,
        include_deleted: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_scope = normalize_memory_item_scope(scope) if scope else None
        normalized_memory_type = normalize_memory_item_type(memory_type) if memory_type else None
        normalized_query = query.strip() if isinstance(query, str) else ""
        normalized_metadata_filter = normalize_memory_item_metadata_filter(metadata_filter)
        query_terms = memory_item_query_terms(normalized_query)
        bounded_limit = max(1, min(100, int(limit)))
        scan_limit = MEMORY_ITEM_LIMIT if normalized_metadata_filter else bounded_limit
        where = "1 = 1"
        params: list[object] = []
        if normalized_scope:
            where += " AND scope = ?"
            params.append(normalized_scope)
        if normalized_memory_type:
            where += " AND memory_type = ?"
            params.append(normalized_memory_type)
        if query_terms:
            clauses = ["content LIKE ?", "metadata_json LIKE ?"]
            params.append(f"%{normalized_query}%")
            params.append(f"%{normalized_query}%")
            for term in query_terms:
                clauses.append("content LIKE ?")
                params.append(f"%{term}%")
                clauses.append("metadata_json LIKE ?")
                params.append(f"%{term}%")
            where += " AND (" + " OR ".join(clauses) + ")"
        if not include_deleted:
            where += " AND deleted_at IS NULL"
        params.append(scan_limit)

        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                  id,
                  scope,
                  content,
                  memory_type,
                  metadata_json,
                  content_hash,
                  confidence,
                  source_session_id,
                  source_message_id,
                  last_accessed_at,
                  access_count,
                  created_at,
                  updated_at,
                  deleted_at
                FROM memory_items
                WHERE {where}
                ORDER BY confidence DESC, access_count DESC, updated_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        items = [memory_item_response(row) for row in rows]
        if normalized_metadata_filter:
            items = [
                item for item in items
                if memory_item_matches_metadata_filter(item, normalized_metadata_filter)
            ]
        return items[:bounded_limit]

    def delete_memory_item(self, memory_item_id: int, *, actor: str = "runtime") -> bool:
        normalized_id = int(memory_item_id)
        if normalized_id <= 0:
            raise ValueError("memory_item_id must be positive")
        normalized_actor = normalize_memory_item_actor(actor)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            existing = self._load_memory_item_row(connection, normalized_id, active_only=True)
            if existing is None:
                return False
            cursor = connection.execute(
                """
                UPDATE memory_items
                SET deleted_at = ?, updated_at = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (now, now, normalized_id),
            )
            if cursor.rowcount <= 0:
                return False
            self._delete_memory_item_fts(connection, normalized_id)
            self._record_memory_item_history(
                connection,
                normalized_id,
                "DELETE",
                old_content=str(existing[2]),
                old_metadata_json=str(existing[4] or "{}"),
                actor=normalized_actor,
                source_session_id=str(existing[7]) if existing[7] is not None else None,
                source_message_id=int(existing[8]) if existing[8] is not None else None,
                created_at=now,
            )
        return cursor.rowcount > 0

    def replace_memory_item(
        self,
        memory_item_id: int,
        content: str,
        *,
        scope: str | None = None,
        confidence: float | None = None,
        memory_type: str | None = None,
        metadata: dict[str, object] | None = None,
        actor: str = "runtime",
    ) -> dict[str, Any] | None:
        normalized_id = int(memory_item_id)
        if normalized_id <= 0:
            raise ValueError("memory_item_id must be positive")
        normalized_content = normalize_memory_item_content(content)
        normalized_scope = normalize_memory_item_scope(scope) if scope else None
        normalized_confidence = normalize_confidence(confidence) if confidence is not None else None
        normalized_memory_type = normalize_memory_item_type(memory_type) if memory_type else None
        normalized_metadata_json = normalize_memory_item_metadata(metadata) if metadata is not None else None
        normalized_actor = normalize_memory_item_actor(actor)
        content_hash = compute_memory_item_hash(normalized_content)
        now = datetime.now(timezone.utc).isoformat()

        assignments = ["content = ?", "content_hash = ?", "updated_at = ?"]
        params: list[object] = [normalized_content, content_hash, now]
        if normalized_scope:
            assignments.append("scope = ?")
            params.append(normalized_scope)
        if normalized_confidence is not None:
            assignments.append("confidence = ?")
            params.append(normalized_confidence)
        if normalized_memory_type is not None:
            assignments.append("memory_type = ?")
            params.append(normalized_memory_type)
        if normalized_metadata_json is not None:
            assignments.append("metadata_json = ?")
            params.append(normalized_metadata_json)
        params.append(normalized_id)

        with self.connect() as connection:
            existing = self._load_memory_item_row(connection, normalized_id, active_only=True)
            if existing is None:
                return None
            cursor = connection.execute(
                f"""
                UPDATE memory_items
                SET {", ".join(assignments)}
                WHERE id = ? AND deleted_at IS NULL
                """,
                params,
            )
            if cursor.rowcount <= 0:
                return None
            row = self._load_memory_item_row(connection, normalized_id)
            self._upsert_memory_item_fts(
                connection,
                memory_item_id=normalized_id,
                content=str(row[2]),
                metadata_json=str(row[4] or "{}"),
                scope=str(row[1]),
                memory_type=str(row[3]),
                updated_at=str(row[12]),
            )
            self._record_memory_item_history(
                connection,
                normalized_id,
                "UPDATE",
                old_content=str(existing[2]),
                new_content=normalized_content,
                old_metadata_json=str(existing[4] or "{}"),
                new_metadata_json=str(row[4] or "{}"),
                actor=normalized_actor,
                source_session_id=str(row[7]) if row[7] is not None else None,
                source_message_id=int(row[8]) if row[8] is not None else None,
                created_at=now,
            )

        return memory_item_response(row)

    def record_memory_item_access(self, memory_item_ids: list[int] | tuple[int, ...]) -> None:
        normalized_ids = [int(memory_item_id) for memory_item_id in memory_item_ids if int(memory_item_id) > 0]
        if not normalized_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        placeholders = ", ".join("?" for _ in normalized_ids)
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE memory_items
                SET access_count = access_count + 1,
                    last_accessed_at = ?
                WHERE id IN ({placeholders}) AND deleted_at IS NULL
                """,
                [now, *normalized_ids],
            )

    def list_memory_item_history(self, memory_item_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        normalized_id = int(memory_item_id)
        if normalized_id <= 0:
            raise ValueError("memory_item_id must be positive")
        bounded_limit = max(1, min(200, int(limit)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                  id,
                  memory_item_id,
                  event,
                  old_content,
                  new_content,
                  old_metadata_json,
                  new_metadata_json,
                  actor,
                  source_session_id,
                  source_message_id,
                  created_at
                FROM memory_item_history
                WHERE memory_item_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (normalized_id, bounded_limit),
            ).fetchall()
        return [memory_item_history_response(row) for row in rows]

    def upsert_memory_item_embedding(
        self,
        memory_item_id: int,
        *,
        provider: str,
        model: str,
        dimensions: int,
        vector: list[float] | tuple[float, ...],
    ) -> dict[str, Any]:
        normalized_id = int(memory_item_id)
        if normalized_id <= 0:
            raise ValueError("memory_item_id must be positive")
        normalized_provider = normalize_memory_embedding_provider(provider)
        normalized_model = normalize_memory_embedding_model(model)
        normalized_dimensions = normalize_memory_embedding_dimensions(dimensions)
        normalized_vector = normalize_memory_embedding_vector(vector, dimensions=normalized_dimensions)
        serialized_vector = serialize_memory_embedding_vector(normalized_vector)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            row = self._load_memory_item_row(connection, normalized_id, active_only=True)
            if row is None:
                raise ValueError("memory item not found")
            content_hash = str(row[5] or "")
            connection.execute(
                """
                INSERT INTO memory_item_embeddings (
                  memory_item_id,
                  provider,
                  model,
                  dimensions,
                  content_hash,
                  embedding,
                  created_at,
                  updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_item_id, provider, model) DO UPDATE SET
                  dimensions = excluded.dimensions,
                  content_hash = excluded.content_hash,
                  embedding = excluded.embedding,
                  updated_at = excluded.updated_at
                """,
                (
                    normalized_id,
                    normalized_provider,
                    normalized_model,
                    normalized_dimensions,
                    content_hash,
                    serialized_vector,
                    now,
                    now,
                ),
            )
        return {
            "memoryItemId": normalized_id,
            "provider": normalized_provider,
            "model": normalized_model,
            "dimensions": normalized_dimensions,
            "contentHash": content_hash,
            "updatedAt": now,
        }

    def list_memory_items_needing_embeddings(
        self,
        *,
        provider: str,
        model: str,
        dimensions: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        normalized_provider = normalize_memory_embedding_provider(provider)
        normalized_model = normalize_memory_embedding_model(model)
        normalized_dimensions = normalize_memory_embedding_dimensions(dimensions)
        bounded_limit = max(1, min(500, int(limit)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                  i.id,
                  i.scope,
                  i.content,
                  i.memory_type,
                  i.metadata_json,
                  i.content_hash,
                  i.confidence,
                  i.source_session_id,
                  i.source_message_id,
                  i.last_accessed_at,
                  i.access_count,
                  i.created_at,
                  i.updated_at,
                  i.deleted_at
                FROM memory_items i
                LEFT JOIN memory_item_embeddings e
                  ON e.memory_item_id = i.id
                 AND e.provider = ?
                 AND e.model = ?
                WHERE i.deleted_at IS NULL
                  AND (
                    e.memory_item_id IS NULL
                    OR e.content_hash != i.content_hash
                    OR e.dimensions != ?
                  )
                ORDER BY i.updated_at ASC, i.id ASC
                LIMIT ?
                """,
                (normalized_provider, normalized_model, normalized_dimensions, bounded_limit),
            ).fetchall()
        return [memory_item_response(row) for row in rows]

    def memory_item_embedding_coverage(
        self,
        *,
        provider: str,
        model: str,
        dimensions: int,
    ) -> dict[str, Any]:
        normalized_provider = normalize_memory_embedding_provider(provider)
        normalized_model = normalize_memory_embedding_model(model)
        normalized_dimensions = normalize_memory_embedding_dimensions(dimensions)
        with self.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM memory_items WHERE deleted_at IS NULL").fetchone()[0])
            ready = int(connection.execute(
                """
                SELECT COUNT(*)
                FROM memory_items i
                JOIN memory_item_embeddings e
                  ON e.memory_item_id = i.id
                 AND e.provider = ?
                 AND e.model = ?
                 AND e.dimensions = ?
                 AND e.content_hash = i.content_hash
                WHERE i.deleted_at IS NULL
                """,
                (normalized_provider, normalized_model, normalized_dimensions),
            ).fetchone()[0])
            stale = int(connection.execute(
                """
                SELECT COUNT(*)
                FROM memory_items i
                JOIN memory_item_embeddings e
                  ON e.memory_item_id = i.id
                 AND e.provider = ?
                 AND e.model = ?
                WHERE i.deleted_at IS NULL
                  AND (e.dimensions != ? OR e.content_hash != i.content_hash)
                """,
                (normalized_provider, normalized_model, normalized_dimensions),
            ).fetchone()[0])
            missing = int(connection.execute(
                """
                SELECT COUNT(*)
                FROM memory_items i
                LEFT JOIN memory_item_embeddings e
                  ON e.memory_item_id = i.id
                 AND e.provider = ?
                 AND e.model = ?
                WHERE i.deleted_at IS NULL
                  AND e.memory_item_id IS NULL
                """,
                (normalized_provider, normalized_model),
            ).fetchone()[0])
        return {
            "provider": normalized_provider,
            "model": normalized_model,
            "dimensions": normalized_dimensions,
            "total": total,
            "ready": ready,
            "missing": missing,
            "stale": stale,
            "coverageRatio": round(ready / total, 4) if total else 1.0,
        }

    def search_memory_items_hybrid(
        self,
        *,
        query: str,
        query_embedding: list[float] | tuple[float, ...] | None,
        provider: str,
        model: str,
        dimensions: int,
        scope: str | None = None,
        memory_type: str | None = None,
        metadata_filter: dict[str, object] | None = None,
        limit: int = 8,
        candidate_limit: int = 80,
    ) -> list[dict[str, Any]]:
        normalized_query = query.strip() if isinstance(query, str) else ""
        normalized_provider = normalize_memory_embedding_provider(provider)
        normalized_model = normalize_memory_embedding_model(model)
        normalized_dimensions = normalize_memory_embedding_dimensions(dimensions)
        normalized_scope = normalize_memory_item_scope(scope) if scope else None
        normalized_memory_type = normalize_memory_item_type(memory_type) if memory_type else None
        normalized_metadata_filter = normalize_memory_item_metadata_filter(metadata_filter)
        bounded_limit = max(1, min(100, int(limit)))
        bounded_candidate_limit = max(bounded_limit, min(500, int(candidate_limit)))
        normalized_query_embedding = (
            normalize_memory_embedding_vector(query_embedding, dimensions=normalized_dimensions)
            if query_embedding is not None
            else None
        )

        vector_rows: list[tuple[object, ...]] = []
        where = "i.deleted_at IS NULL"
        params: list[object] = [normalized_provider, normalized_model, normalized_dimensions]
        if normalized_scope:
            where += " AND i.scope = ?"
            params.append(normalized_scope)
        if normalized_memory_type:
            where += " AND i.memory_type = ?"
            params.append(normalized_memory_type)
        params.append(bounded_candidate_limit)
        if normalized_query_embedding is not None:
            with self.connect() as connection:
                vector_rows = connection.execute(
                    f"""
                    SELECT
                      i.id,
                      i.scope,
                      i.content,
                      i.memory_type,
                      i.metadata_json,
                      i.content_hash,
                      i.confidence,
                      i.source_session_id,
                      i.source_message_id,
                      i.last_accessed_at,
                      i.access_count,
                      i.created_at,
                      i.updated_at,
                      i.deleted_at,
                      e.embedding
                    FROM memory_items i
                    JOIN memory_item_embeddings e
                      ON e.memory_item_id = i.id
                     AND e.provider = ?
                     AND e.model = ?
                     AND e.dimensions = ?
                     AND e.content_hash = i.content_hash
                    WHERE {where}
                    ORDER BY i.updated_at DESC, i.id DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()

        candidates: dict[int, dict[str, Any]] = {}
        vectors_by_id: dict[int, list[float]] = {}
        for row in vector_rows:
            item = memory_item_response(row[:14])
            if not memory_item_matches_metadata_filter(item, normalized_metadata_filter):
                continue
            item_id = int(item["memoryItemId"])
            candidates[item_id] = item
            vectors_by_id[item_id] = deserialize_memory_embedding_vector(row[14], dimensions=normalized_dimensions)

        bm25_items: list[dict[str, Any]]
        try:
            bm25_items = self.search_memory_items_bm25(
                query=normalized_query,
                scope=normalized_scope,
                memory_type=normalized_memory_type,
                metadata_filter=normalized_metadata_filter,
                limit=bounded_candidate_limit,
            )
        except sqlite3.OperationalError:
            bm25_items = self._list_memory_items_sql(
                scope=normalized_scope,
                memory_type=normalized_memory_type,
                query=normalized_query,
                metadata_filter=normalized_metadata_filter,
                limit=bounded_candidate_limit,
            )
        for item in bm25_items:
            item_id = int(item["memoryItemId"])
            if item_id in candidates:
                candidates[item_id].update({
                    key: value
                    for key, value in item.items()
                    if key in {"bm25Score", "bm25RawScore"}
                })
            else:
                candidates[item_id] = item

        scored: list[dict[str, Any]] = []
        for item_id, item in candidates.items():
            vector_score = 0.0
            if normalized_query_embedding is not None and item_id in vectors_by_id:
                vector_score = memory_embedding_cosine_similarity(normalized_query_embedding, vectors_by_id[item_id])
            bm25_score = max(0.0, min(1.0, float(item.get("bm25Score") or 0.0)))
            keyword_score = max(bm25_score, memory_item_text_match_score(item, normalized_query))
            confidence_score = max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
            access_score = memory_item_access_score(int(item.get("accessCount") or 0))
            recency_score = memory_item_recency_score(str(item.get("updatedAt") or ""))
            hybrid_score = (
                0.55 * vector_score
                + 0.25 * keyword_score
                + 0.10 * confidence_score
                + 0.05 * access_score
                + 0.05 * recency_score
            )
            enriched = dict(item)
            enriched["hybridScore"] = round(hybrid_score, 6)
            enriched["vectorScore"] = round(vector_score, 6)
            enriched["bm25Score"] = round(bm25_score, 6)
            enriched["keywordScore"] = round(keyword_score, 6)
            enriched["retrievalProvider"] = "memory_items_hybrid"
            enriched["embeddingProvider"] = normalized_provider
            enriched["embeddingModel"] = normalized_model
            scored.append(enriched)

        scored.sort(
            key=lambda item: (
                float(item.get("hybridScore") or 0.0),
                float(item.get("confidence") or 0.0),
                str(item.get("updatedAt") or ""),
                int(item.get("memoryItemId") or 0),
            ),
            reverse=True,
        )
        return scored[:bounded_limit]

    def _load_memory_item_row(
        self,
        connection: sqlite3.Connection,
        memory_item_id: int,
        *,
        active_only: bool = False,
    ) -> sqlite3.Row | None:
        where = "id = ?"
        if active_only:
            where += " AND deleted_at IS NULL"
        return connection.execute(
            f"""
            SELECT
              id,
              scope,
              content,
              memory_type,
              metadata_json,
              content_hash,
              confidence,
              source_session_id,
              source_message_id,
              last_accessed_at,
              access_count,
              created_at,
              updated_at,
              deleted_at
            FROM memory_items
            WHERE {where}
            """,
            (memory_item_id,),
        ).fetchone()

    def _record_memory_item_history(
        self,
        connection: sqlite3.Connection,
        memory_item_id: int,
        event: str,
        *,
        old_content: str | None = None,
        new_content: str | None = None,
        old_metadata_json: str | None = None,
        new_metadata_json: str | None = None,
        actor: str = "runtime",
        source_session_id: str | None = None,
        source_message_id: int | None = None,
        created_at: str | None = None,
    ) -> None:
        normalized_event = normalize_memory_item_history_event(event)
        normalized_actor = normalize_memory_item_actor(actor)
        normalized_source_session_id = normalize_optional_text(source_session_id, "source_session_id", max_chars=200)
        normalized_source_message_id = normalize_optional_non_negative_int(source_message_id, "source_message_id")
        connection.execute(
            """
            INSERT INTO memory_item_history (
              memory_item_id,
              event,
              old_content,
              new_content,
              old_metadata_json,
              new_metadata_json,
              actor,
              source_session_id,
              source_message_id,
              created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(memory_item_id),
                normalized_event,
                old_content,
                new_content,
                old_metadata_json,
                new_metadata_json,
                normalized_actor,
                normalized_source_session_id,
                normalized_source_message_id,
                created_at or datetime.now(timezone.utc).isoformat(),
            ),
        )

    def save_memory_review_candidate(
        self,
        session_id: str,
        scope: str,
        content: str,
        *,
        confidence: float = 0.7,
        reason: str | None = None,
        scope_reason: str | None = None,
        safety_labels: list[str] | tuple[str, ...] | None = None,
        retention_type: str | None = None,
        source_message_start_id: int | None = None,
        source_message_end_id: int | None = None,
    ) -> MemoryReviewCandidatePayload:
        normalized_session_id = normalize_session_id(session_id)
        normalized_scope = normalize_memory_item_scope(scope)
        normalized_content = normalize_memory_item_content(content)
        normalized_confidence = normalize_confidence(confidence)
        normalized_reason = normalize_optional_text(reason, "reason", max_chars=MEMORY_REVIEW_REASON_LIMIT)
        normalized_scope_reason = normalize_optional_text(scope_reason, "scope_reason", max_chars=MEMORY_REVIEW_REASON_LIMIT)
        normalized_safety_labels = normalize_memory_review_safety_labels(safety_labels)
        normalized_retention_type = normalize_memory_review_retention_type(retention_type)
        normalized_start_id = normalize_optional_non_negative_int(source_message_start_id, "source_message_start_id")
        normalized_end_id = normalize_optional_non_negative_int(source_message_end_id, "source_message_end_id")
        if normalized_start_id is not None and normalized_end_id is not None and normalized_start_id > normalized_end_id:
            raise ValueError("source_message_start_id must be less than or equal to source_message_end_id")

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            rejected = connection.execute(
                """
                SELECT
                  id,
                  session_id,
                  scope,
                  content,
                  confidence,
                  reason,
                  scope_reason,
                  safety_labels,
                  retention_type,
                  source_message_start_id,
                  source_message_end_id,
                  status,
                  memory_item_id,
                  created_at,
                  updated_at
                FROM memory_review_candidates
                WHERE session_id = ? AND scope = ? AND content = ? AND status = 'rejected'
                ORDER BY id DESC
                LIMIT 1
                """,
                (normalized_session_id, normalized_scope, normalized_content),
            ).fetchone()
            if rejected:
                response = memory_review_candidate_response(rejected)
                response["duplicate"] = True
                response["suppressed"] = True
                return response

            existing = connection.execute(
                """
                SELECT
                  id,
                  session_id,
                  scope,
                  content,
                  confidence,
                  reason,
                  scope_reason,
                  safety_labels,
                  retention_type,
                  source_message_start_id,
                  source_message_end_id,
                  status,
                  memory_item_id,
                  created_at,
                  updated_at
                FROM memory_review_candidates
                WHERE session_id = ? AND scope = ? AND content = ? AND status = 'pending'
                ORDER BY id DESC
                LIMIT 1
                """,
                (normalized_session_id, normalized_scope, normalized_content),
            ).fetchone()
            if existing:
                response = memory_review_candidate_response(existing)
                response["duplicate"] = True
                return response

            cursor = connection.execute(
                """
                INSERT INTO memory_review_candidates (
                  session_id,
                  scope,
                  content,
                  confidence,
                  reason,
                  scope_reason,
                  safety_labels,
                  retention_type,
                  source_message_start_id,
                  source_message_end_id,
                  status,
                  created_at,
                  updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    normalized_session_id,
                    normalized_scope,
                    normalized_content,
                    normalized_confidence,
                    normalized_reason,
                    normalized_scope_reason,
                    json.dumps(normalized_safety_labels, ensure_ascii=False),
                    normalized_retention_type,
                    normalized_start_id,
                    normalized_end_id,
                    now,
                    now,
                ),
            )
            candidate_id = int(cursor.lastrowid)

            row = connection.execute(
                """
                SELECT
                  id,
                  session_id,
                  scope,
                  content,
                  confidence,
                  reason,
                  scope_reason,
                  safety_labels,
                  retention_type,
                  source_message_start_id,
                  source_message_end_id,
                  status,
                  memory_item_id,
                  created_at,
                  updated_at
                FROM memory_review_candidates
                WHERE id = ?
                """,
                (candidate_id,),
            ).fetchone()

        response = memory_review_candidate_response(row)
        response["duplicate"] = False
        return response

    def list_memory_review_candidates(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        scope: str | None = None,
        limit: int = 50,
    ) -> list[MemoryReviewCandidatePayload]:
        normalized_session_id = normalize_session_id(session_id) if session_id else None
        normalized_status = normalize_memory_review_status(status) if status else None
        normalized_scope = normalize_memory_item_scope(scope) if scope else None
        bounded_limit = max(1, min(200, int(limit)))
        where = "1 = 1"
        params: list[object] = []
        if normalized_session_id:
            where += " AND session_id = ?"
            params.append(normalized_session_id)
        if normalized_status:
            where += " AND status = ?"
            params.append(normalized_status)
        if normalized_scope:
            where += " AND scope = ?"
            params.append(normalized_scope)
        params.append(bounded_limit)

        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                  id,
                  session_id,
                  scope,
                  content,
                  confidence,
                  reason,
                  scope_reason,
                  safety_labels,
                  retention_type,
                  source_message_start_id,
                  source_message_end_id,
                  status,
                  memory_item_id,
                  created_at,
                  updated_at
                FROM memory_review_candidates
                WHERE {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [memory_review_candidate_response(row) for row in rows]

    def start_memory_review_job(self, session_id: str, trigger: str) -> dict[str, str | int]:
        normalized_session_id = normalize_session_id(session_id)
        normalized_trigger = normalize_memory_review_job_trigger(trigger)
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memory_review_jobs (
                  session_id,
                  trigger,
                  status,
                  started_at
                )
                VALUES (?, ?, 'running', ?)
                """,
                (normalized_session_id, normalized_trigger, now),
            )
            job_id = int(cursor.lastrowid)
            row = self._load_memory_review_job_row(connection, job_id)

        return memory_review_job_response(row)

    def finish_memory_review_job(
        self,
        job_id: int,
        status: str,
        *,
        reason: str | None = None,
        error: str | None = None,
        source_message_start_id: int | None = None,
        source_message_end_id: int | None = None,
        source_message_count: int = 0,
        proposed_candidate_count: int = 0,
        saved_candidate_count: int = 0,
        suppressed_candidate_count: int = 0,
        duration_ms: int | None = None,
    ) -> dict[str, str | int]:
        normalized_id = int(job_id)
        if normalized_id <= 0:
            raise ValueError("job_id must be positive")
        normalized_status = normalize_memory_review_job_status(status)
        normalized_reason = normalize_optional_text(reason, "reason", max_chars=MEMORY_REVIEW_REASON_LIMIT)
        normalized_error = normalize_optional_text(error, "error", max_chars=MEMORY_REVIEW_REASON_LIMIT)
        normalized_start_id = normalize_optional_non_negative_int(source_message_start_id, "source_message_start_id")
        normalized_end_id = normalize_optional_non_negative_int(source_message_end_id, "source_message_end_id")
        if normalized_start_id is not None and normalized_end_id is not None and normalized_start_id > normalized_end_id:
            raise ValueError("source_message_start_id must be less than or equal to source_message_end_id")
        normalized_duration_ms = normalize_optional_non_negative_int(duration_ms, "duration_ms")
        now = datetime.now(timezone.utc).isoformat()

        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_review_jobs
                SET
                  status = ?,
                  reason = ?,
                  error = ?,
                  source_message_start_id = ?,
                  source_message_end_id = ?,
                  source_message_count = ?,
                  proposed_candidate_count = ?,
                  saved_candidate_count = ?,
                  suppressed_candidate_count = ?,
                  finished_at = ?,
                  duration_ms = ?
                WHERE id = ?
                """,
                (
                    normalized_status,
                    normalized_reason,
                    normalized_error,
                    normalized_start_id,
                    normalized_end_id,
                    max(0, int(source_message_count)),
                    max(0, int(proposed_candidate_count)),
                    max(0, int(saved_candidate_count)),
                    max(0, int(suppressed_candidate_count)),
                    now,
                    normalized_duration_ms,
                    normalized_id,
                ),
            )
            if cursor.rowcount <= 0:
                raise ValueError("memory review job not found")
            row = self._load_memory_review_job_row(connection, normalized_id)

        return memory_review_job_response(row)

    def list_memory_review_jobs(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, str | int]]:
        normalized_session_id = normalize_session_id(session_id) if session_id else None
        normalized_status = normalize_memory_review_job_status(status) if status else None
        bounded_limit = max(1, min(200, int(limit)))
        where = "1 = 1"
        params: list[object] = []
        if normalized_session_id:
            where += " AND session_id = ?"
            params.append(normalized_session_id)
        if normalized_status:
            where += " AND status = ?"
            params.append(normalized_status)
        params.append(bounded_limit)

        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                  id,
                  session_id,
                  trigger,
                  status,
                  reason,
                  error,
                  source_message_start_id,
                  source_message_end_id,
                  source_message_count,
                  proposed_candidate_count,
                  saved_candidate_count,
                  suppressed_candidate_count,
                  started_at,
                  finished_at,
                  duration_ms
                FROM memory_review_jobs
                WHERE {where}
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [memory_review_job_response(row) for row in rows]

    def _load_memory_review_job_row(self, connection: sqlite3.Connection, job_id: int) -> sqlite3.Row:
        return connection.execute(
            """
            SELECT
              id,
              session_id,
              trigger,
              status,
              reason,
              error,
              source_message_start_id,
              source_message_end_id,
              source_message_count,
              proposed_candidate_count,
              saved_candidate_count,
              suppressed_candidate_count,
              started_at,
              finished_at,
              duration_ms
            FROM memory_review_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()

    def accept_memory_review_candidate(self, candidate_id: int) -> dict[str, object]:
        normalized_id = int(candidate_id)
        if normalized_id <= 0:
            raise ValueError("candidate_id must be positive")

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                  id,
                  session_id,
                  scope,
                  content,
                  confidence,
                  reason,
                  scope_reason,
                  safety_labels,
                  retention_type,
                  source_message_start_id,
                  source_message_end_id,
                  status,
                  memory_item_id,
                  created_at,
                  updated_at
                FROM memory_review_candidates
                WHERE id = ?
                """,
                (normalized_id,),
            ).fetchone()

        if not row:
            return {"accepted": False, "candidateId": normalized_id, "error": "candidate not found"}

        candidate = memory_review_candidate_response(row)
        if candidate["status"] != "pending":
            return {
                "accepted": False,
                "candidateId": normalized_id,
                "candidate": candidate,
                "error": "candidate is not pending",
            }

        existing_items = self.list_memory_items(
            scope=str(candidate["scope"]),
            query=str(candidate["content"]),
            limit=10,
        )
        duplicate_item = next(
            (
                item
                for item in existing_items
                if str(item.get("content", "")).strip() == str(candidate["content"]).strip()
            ),
            None,
        )
        item = duplicate_item or self.save_memory_item(
            str(candidate["scope"]),
            str(candidate["content"]),
            confidence=float(candidate["confidence"]),
            source_session_id=str(candidate["sessionId"]),
            source_message_id=int(candidate["sourceMessageEndId"]) or None,
            memory_type=memory_type_from_review_retention(str(candidate["retentionType"])),
            metadata={
                "source": "memory_review",
                "reason": str(candidate["reason"]),
                "scopeReason": str(candidate["scopeReason"]),
                "safetyLabels": candidate["safetyLabels"],
                "retentionType": str(candidate["retentionType"]),
                "sourceMessageStartId": int(candidate["sourceMessageStartId"]),
                "sourceMessageEndId": int(candidate["sourceMessageEndId"]),
            },
            actor="memory_review",
        )

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE memory_review_candidates
                SET status = 'accepted', memory_item_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(item["memoryItemId"]), now, normalized_id),
            )
            updated = connection.execute(
                """
                SELECT
                  id,
                  session_id,
                  scope,
                  content,
                  confidence,
                  reason,
                  scope_reason,
                  safety_labels,
                  retention_type,
                  source_message_start_id,
                  source_message_end_id,
                  status,
                  memory_item_id,
                  created_at,
                  updated_at
                FROM memory_review_candidates
                WHERE id = ?
                """,
                (normalized_id,),
            ).fetchone()

        return {
            "accepted": True,
            "candidate": memory_review_candidate_response(updated),
            "item": item,
            "duplicateMemoryItem": duplicate_item is not None,
        }

    def reject_memory_review_candidate(self, candidate_id: int) -> dict[str, object]:
        normalized_id = int(candidate_id)
        if normalized_id <= 0:
            raise ValueError("candidate_id must be positive")

        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_review_candidates
                SET status = 'rejected', updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now, normalized_id),
            )
            row = connection.execute(
                """
                SELECT
                  id,
                  session_id,
                  scope,
                  content,
                  confidence,
                  reason,
                  scope_reason,
                  safety_labels,
                  retention_type,
                  source_message_start_id,
                  source_message_end_id,
                  status,
                  memory_item_id,
                  created_at,
                  updated_at
                FROM memory_review_candidates
                WHERE id = ?
                """,
                (normalized_id,),
            ).fetchone()

        if not row:
            return {"rejected": False, "candidateId": normalized_id, "error": "candidate not found"}

        return {
            "rejected": cursor.rowcount > 0,
            "candidate": memory_review_candidate_response(row),
        }

    def reset(self, session_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                DELETE FROM messages
                WHERE session_id = ?
                """,
                (session_id,),
            )
            connection.execute(
                """
                DELETE FROM messages_fts
                WHERE session_id = ?
                """,
                (session_id,),
            )
            connection.execute(
                """
                DELETE FROM conversation_summaries
                WHERE session_id = ?
                """,
                (session_id,),
            )
            connection.execute(
                """
                DELETE FROM memory_review_candidates
                WHERE session_id = ?
                """,
                (session_id,),
            )
            connection.execute(
                """
                DELETE FROM memory_review_jobs
                WHERE session_id = ?
                """,
                (session_id,),
            )
            connection.execute(
                """
                DELETE FROM session_plans
                WHERE session_id = ?
                """,
                (session_id,),
            )
            connection.execute(
                """
                DELETE FROM task_events
                WHERE session_id = ?
                """,
                (session_id,),
            )
            connection.execute(
                """
                DELETE FROM task_artifacts
                WHERE task_id IN (SELECT id FROM tasks WHERE session_id = ?)
                """,
                (session_id,),
            )
            connection.execute(
                """
                DELETE FROM task_attempts
                WHERE task_id IN (SELECT id FROM tasks WHERE session_id = ?)
                """,
                (session_id,),
            )
            connection.execute(
                """
                DELETE FROM task_edges
                WHERE from_task_id IN (SELECT id FROM tasks WHERE session_id = ?)
                   OR to_task_id IN (SELECT id FROM tasks WHERE session_id = ?)
                """,
                (session_id, session_id),
            )
            connection.execute(
                """
                DELETE FROM tasks
                WHERE session_id = ?
                """,
                (session_id,),
            )

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def read_stable_memory(self, target: str, session_id: str | None = None) -> dict[str, str | int]:
        target = normalize_stable_memory_target(target)
        path = self._stable_memory_path(target, session_id=session_id)
        ensure_stable_memory_file(path, target)
        content = path.read_text(encoding="utf-8")
        return stable_memory_response(target, path, content)

    def stable_memory_snapshot(self, session_id: str | None = None) -> dict[str, dict[str, str | int]]:
        return {
            target: self.read_stable_memory(target, session_id=session_id)
            for target in STABLE_MEMORY_FILES
        }

    def update_stable_memory(
        self,
        target: str,
        action: str,
        content: str | None = None,
        old_text: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, str | int | bool]:
        target = normalize_stable_memory_target(target)
        normalized_action = action.strip().lower()
        if normalized_action not in {"add", "replace", "remove"}:
            raise ValueError("action must be add, replace, or remove")

        path = self._stable_memory_path(target, session_id=session_id)
        ensure_stable_memory_file(path, target)
        before = path.read_text(encoding="utf-8")
        new_content = build_stable_memory_update(
            before,
            target=target,
            action=normalized_action,
            content=content,
            old_text=old_text,
        )

        limit = STABLE_MEMORY_LIMITS[target]
        if len(new_content) > limit:
            raise ValueError(f"{target} memory exceeds {limit} characters")

        atomic_write_text(path, new_content)
        response = stable_memory_response(target, path, new_content)
        response.update({
            "action": normalized_action,
            "beforeCharCount": len(before),
            "changed": before != new_content,
        })
        return response

    def _stable_memory_path(self, target: str, session_id: str | None = None) -> Path:
        role_id = self.role_id_for_session(session_id) if session_id else DEFAULT_ROLE_ID
        role_memory_dir = role_home_path(self.roles_root, role_id) / "memory"
        role_memory_dir.mkdir(parents=True, exist_ok=True)
        path = role_memory_dir / STABLE_MEMORY_FILES[target]
        if not path.exists() and role_id == DEFAULT_ROLE_ID:
            legacy_path = self.stable_memory_dir / STABLE_MEMORY_FILES[target]
            if legacy_path.exists():
                path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
        return path

    def role_home(self, role_id: str) -> Path:
        normalized_role_id = normalize_role_id(role_id)
        return role_home_path(self.roles_root, normalized_role_id)

    def role_soul_file(self, role_id: str) -> Path:
        normalized_role_id = normalize_role_id(role_id)
        return role_soul_path(self.roles_root, normalized_role_id)


def role_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "name": str(row[1]),
        "description": str(row[2]) if row[2] is not None else "",
        "persona": str(row[3]) if row[3] is not None else "",
        "style": str(row[4]) if row[4] is not None else "",
        "provider": str(row[5]) if row[5] is not None else "",
        "model": str(row[6]) if row[6] is not None else "",
        "live2dModel": str(row[7]) if row[7] is not None else "",
        "ttsVoice": str(row[8]) if row[8] is not None else "",
        "workspacePath": str(row[9]) if row[9] is not None else "",
        "runtimeScope": role_runtime_scope_payload(row[10] if len(row) > 10 else None),
        "archived": bool(row[11]),
        "createdAt": str(row[12]),
        "updatedAt": str(row[13]),
    }


def session_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, str | int | bool]:
    return {
        "id": str(row[0]),
        "roleId": str(row[1]),
        "title": str(row[2]),
        "archived": bool(row[3]),
        "createdAt": str(row[4]),
        "updatedAt": str(row[5]),
        "roleName": str(row[6]) if len(row) > 6 and row[6] is not None else "",
        "messageCount": int(row[7]) if len(row) > 7 and row[7] is not None else 0,
    }


def normalize_role_id(role_id: str) -> str:
    normalized = role_id.strip() if isinstance(role_id, str) else ""
    if not normalized:
        raise ValueError("role_id is required")
    if "\x00" in normalized:
        raise ValueError("role_id must be UTF-8 text")
    if len(normalized) > 120:
        raise ValueError("role_id must be at most 120 characters")
    return normalized


def normalize_role_name(name: str) -> str:
    normalized = name.strip() if isinstance(name, str) else ""
    if not normalized:
        raise ValueError("name is required")
    if "\x00" in normalized:
        raise ValueError("name must be UTF-8 text")
    if len(normalized) > 120:
        raise ValueError("name must be at most 120 characters")
    return normalized


def normalize_session_title(title: str) -> str:
    normalized = title.strip() if isinstance(title, str) else ""
    if not normalized:
        raise ValueError("title is required")
    if "\x00" in normalized:
        raise ValueError("title must be UTF-8 text")
    if len(normalized) > 160:
        raise ValueError("title must be at most 160 characters")
    return normalized


def default_session_title() -> str:
    return f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}"


def is_auto_session_title(title: str) -> bool:
    normalized = title.strip()
    return (
        not normalized
        or normalized in {"Default", "New chat", "Imported chat"}
        or normalized.startswith("Chat 20")
    )


def session_title_from_query(query: str, max_chars: int = 24) -> str:
    normalized = " ".join(query.strip().split())
    if not normalized:
        return default_session_title()
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars].rstrip()}..."


def session_title_from_id(session_id: str) -> str:
    normalized = session_id.strip()
    if normalized == DEFAULT_SESSION_ID:
        return "Default"
    return normalized.rsplit(":", 1)[-1].replace("-", " ").replace("_", " ").strip().title() or "Imported chat"


def parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def task_time_is_due(value: str, now: datetime) -> bool:
    if not value:
        return True
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return True
    return parsed <= now


def normalize_stable_memory_target(target: str) -> StableMemoryTarget:
    normalized = target.strip().lower()
    if normalized in {"memory", "agent", "amadeus"}:
        return "agent"
    if normalized in {"user", "profile", "preferences"}:
        return "user"
    raise ValueError("target must be agent or user")


def normalize_memory_item_scope(scope: str) -> MemoryItemScope:
    normalized = scope.strip().lower() if isinstance(scope, str) else ""
    if normalized in {"user", "agent", "project"}:
        return normalized  # type: ignore[return-value]
    raise ValueError("scope must be user, agent, or project")


def normalize_memory_review_status(status: str) -> MemoryReviewCandidateStatus:
    normalized = status.strip().lower() if isinstance(status, str) else ""
    if normalized in {"pending", "accepted", "rejected", "superseded"}:
        return normalized  # type: ignore[return-value]
    raise ValueError("status must be pending, accepted, rejected, or superseded")


def normalize_memory_review_job_status(status: str) -> MemoryReviewJobStatus:
    normalized = status.strip().lower() if isinstance(status, str) else ""
    if normalized in {"running", "completed", "skipped", "failed"}:
        return normalized  # type: ignore[return-value]
    raise ValueError("status must be running, completed, skipped, or failed")


def normalize_memory_review_job_trigger(trigger: str) -> MemoryReviewJobTrigger:
    normalized = trigger.strip().lower() if isinstance(trigger, str) else ""
    if normalized in {"manual", "auto", "compaction"}:
        return normalized  # type: ignore[return-value]
    raise ValueError("trigger must be manual, auto, or compaction")


def normalize_session_id(session_id: str) -> str:
    normalized = session_id.strip() if isinstance(session_id, str) else ""
    if not normalized:
        raise ValueError("session_id is required")
    if "\x00" in normalized:
        raise ValueError("session_id must be UTF-8 text")
    if len(normalized) > 200:
        raise ValueError("session_id must be at most 200 characters")
    return normalized


def normalize_task_id(task_id: str) -> str:
    normalized = task_id.strip() if isinstance(task_id, str) else ""
    if not normalized:
        raise ValueError("task id is required")
    if "\x00" in normalized:
        raise ValueError("task id must be UTF-8 text")
    if len(normalized) > 80:
        raise ValueError("task id must be at most 80 characters")
    return normalized


def normalize_task_kind(kind: object) -> str:
    normalized = str(kind or "agent_turn").strip().lower()
    if normalized in {"agent_turn", "scheduled_prompt", "script", "review", "delegated"}:
        return normalized
    raise ValueError("task kind must be agent_turn, scheduled_prompt, script, review, or delegated")


def normalize_task_source(source: object) -> str:
    normalized = str(source or "manual").strip().lower()
    if normalized in {"manual", "model", "scheduled_job", "plan", "api", "system"}:
        return normalized
    raise ValueError("task source must be manual, model, scheduled_job, plan, api, or system")


def normalize_task_worker_type(worker_type: object) -> str:
    normalized = str(worker_type or "agent").strip().lower()
    if normalized in {"agent", "script", "review", "delegated"}:
        return normalized
    raise ValueError("task worker_type must be agent, script, review, or delegated")


def normalize_scheduled_job_id(job_id: str) -> str:
    normalized = job_id.strip() if isinstance(job_id, str) else ""
    if not normalized:
        raise ValueError("scheduled job id is required")
    if "\x00" in normalized:
        raise ValueError("scheduled job id must be UTF-8 text")
    if len(normalized) > 80:
        raise ValueError("scheduled job id must be at most 80 characters")
    return normalized


def normalize_scheduled_status(status: object) -> ScheduledJobStatus:
    normalized = str(status or "").strip().lower()
    if normalized in {"scheduled", "running", "paused", "completed", "cancelled", "failed"}:
        return normalized  # type: ignore[return-value]
    raise ValueError("scheduled job status is invalid")


def normalize_scheduled_mode(mode: object) -> str:
    normalized = str(mode or "message").strip().lower()
    if normalized in {"message", "agent_task"}:
        return normalized
    raise ValueError("scheduled job mode must be message or agent_task")


def normalize_scheduled_title(title: object) -> str:
    normalized = " ".join(str(title or "").strip().split())
    if not normalized:
        raise ValueError("scheduled job title is required")
    if "\x00" in normalized:
        raise ValueError("scheduled job title must be UTF-8 text")
    if len(normalized) > 160:
        return normalized[:157].rstrip() + "..."
    return normalized


def normalize_scheduled_message(message: object) -> str:
    normalized = str(message or "").strip()
    if not normalized:
        raise ValueError("scheduled message is required")
    if "\x00" in normalized:
        raise ValueError("scheduled message must be UTF-8 text")
    if len(normalized) > 4000:
        raise ValueError("scheduled message must be at most 4000 characters")
    return normalized


def normalize_scheduled_repeat_count(repeat_count: object) -> int | None:
    if repeat_count is None:
        return None
    try:
        parsed = int(repeat_count)
    except (TypeError, ValueError):
        raise ValueError("repeatCount must be a number") from None
    if parsed < 1:
        return None
    if parsed > 10000:
        raise ValueError("repeatCount must be at most 10000")
    return parsed


def normalize_scheduled_event_type(event_type: object) -> str:
    normalized = str(event_type or "").strip().lower()
    if not normalized:
        raise ValueError("scheduled job event type is required")
    if "\x00" in normalized:
        raise ValueError("scheduled job event type must be UTF-8 text")
    if len(normalized) > 80:
        raise ValueError("scheduled job event type must be at most 80 characters")
    return normalized


def normalize_todo_status(status: object) -> TodoStatus:
    normalized = str(status or "pending").strip().lower()
    if normalized in {"pending", "in_progress", "completed", "cancelled"}:
        return normalized  # type: ignore[return-value]
    return "pending"


def normalize_todo_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return f"todo-{uuid4().hex[:12]}"
    if "\x00" in normalized:
        raise ValueError("todo id must be UTF-8 text")
    if len(normalized) > 80:
        raise ValueError("todo id must be at most 80 characters")
    return normalized


def normalize_todo_content(value: object) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        normalized = "(no description)"
    if "\x00" in normalized:
        raise ValueError("todo content must be UTF-8 text")
    if len(normalized) > 1000:
        marker = "... [truncated]"
        return normalized[: 1000 - len(marker)].rstrip() + marker
    return normalized


def normalize_todo_item(item: object, index: int) -> dict[str, object]:
    payload = item if isinstance(item, dict) else {}
    return {
        "id": normalize_todo_id(payload.get("id")),
        "content": normalize_todo_content(payload.get("content")),
        "status": normalize_todo_status(payload.get("status")),
        "orderIndex": index,
    }


def dedupe_todos_by_id(todos: list[dict[str, object]]) -> list[dict[str, object]]:
    last_index: dict[str, int] = {}
    for index, item in enumerate(todos):
        item_id = str(item.get("id") or "").strip()
        if item_id:
            last_index[item_id] = index
        else:
            last_index[f"__empty_{index}"] = index
    return [todos[index] for index in sorted(last_index.values())]


def todo_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    return {
        "id": str(row[0]),
        "sessionId": str(row[1]),
        "content": str(row[2] or ""),
        "status": normalize_todo_status(row[3]),
        "orderIndex": int(row[4] or 0),
        "createdAt": str(row[5]),
        "updatedAt": str(row[6]),
        "completedAt": str(row[7]) if row[7] else None,
    }


def todo_summary(todos: list[dict[str, object]]) -> dict[str, int]:
    counts = {
        "total": len(todos),
        "pending": 0,
        "inProgress": 0,
        "completed": 0,
        "cancelled": 0,
    }
    for item in todos:
        status = str(item.get("status") or "")
        if status == "in_progress":
            counts["inProgress"] += 1
        elif status in counts:
            counts[status] += 1
    return counts


def scheduled_job_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    try:
        schedule = json.loads(str(row[4] or "{}"))
    except json.JSONDecodeError:
        schedule = {}
    mode = normalize_scheduled_mode(row[15]) if len(row) > 15 and row[15] else "message"
    last_task_id = str(row[16]) if len(row) > 16 and row[16] else None
    return {
        "id": str(row[0]),
        "sessionId": str(row[1]),
        "title": str(row[2]),
        "message": str(row[3] or ""),
        "mode": mode,
        "lastTaskId": last_task_id,
        "schedule": schedule if isinstance(schedule, dict) else {},
        "scheduleDisplay": str(row[5] or ""),
        "status": normalize_scheduled_status(row[6]),
        "repeatCount": int(row[7]) if row[7] is not None else None,
        "completedRuns": int(row[8] or 0),
        "nextRunAt": str(row[9]) if row[9] else None,
        "lastRunAt": str(row[10]) if row[10] else None,
        "lastError": str(row[11]) if row[11] else None,
        "createdAt": str(row[12]),
        "updatedAt": str(row[13]),
        "finishedAt": str(row[14]) if row[14] else None,
    }


def scheduled_job_event_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    metadata: object | None = None
    if row[6]:
        try:
            metadata = json.loads(str(row[6]))
        except json.JSONDecodeError:
            metadata = None
    return {
        "eventId": int(row[0]),
        "jobId": str(row[1]),
        "sessionId": str(row[2]),
        "type": str(row[3]),
        "status": str(row[4]) if row[4] else None,
        "message": str(row[5]) if row[5] else None,
        "metadata": metadata,
        "createdAt": str(row[7]),
    }


def scheduled_job_summary(jobs: list[dict[str, object]]) -> dict[str, int]:
    counts = {
        "total": len(jobs),
        "scheduled": 0,
        "running": 0,
        "paused": 0,
        "completed": 0,
        "cancelled": 0,
        "failed": 0,
    }
    for job in jobs:
        status = str(job.get("status") or "")
        if status in counts:
            counts[status] += 1
    return counts


def plan_items_are_complete(items: object) -> bool:
    if not isinstance(items, list) or not items:
        return False
    return all(isinstance(item, dict) and str(item.get("status") or "") == "completed" for item in items)


def plan_run_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    try:
        items = json.loads(str(row[5] or "[]"))
    except json.JSONDecodeError:
        items = []
    normalized_items = merge_plan_items([], items if isinstance(items, list) else [])
    return {
        "turnId": str(row[0]),
        "sessionId": str(row[1]),
        "userMessageId": int(row[2]) if row[2] is not None else None,
        "assistantMessageId": int(row[3]) if row[3] is not None else None,
        "status": str(row[4] or "active"),
        "items": normalized_items,
        "summary": plan_response(str(row[1]), normalized_items).get("summary", {}),
        "createdAt": str(row[6]),
        "updatedAt": str(row[7]),
        "archivedAt": str(row[8]) if row[8] else None,
    }


def json_payload(value: object, *, default: object) -> object:
    if value is None:
        return default
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return default
    return parsed


def task_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    attempt_count = int(row[14] or 0) if len(row) > 14 else 0
    max_attempts = int(row[15] or 3) if len(row) > 15 else 3
    next_run_at = str(row[16]) if len(row) > 16 and row[16] else None
    try:
        artifacts = json.loads(str(row[24] or "[]")) if len(row) > 24 else []
    except json.JSONDecodeError:
        artifacts = []
    try:
        normalized_artifacts = json.loads(normalize_task_artifacts(artifacts))
    except ValueError:
        normalized_artifacts = []
    return {
        "id": str(row[0]),
        "sessionId": str(row[1]),
        "title": str(row[2]),
        "body": str(row[3] or ""),
        "kind": normalize_task_kind(row[17]) if len(row) > 17 and row[17] else "agent_turn",
        "source": normalize_task_source(row[18]) if len(row) > 18 and row[18] else "manual",
        "parentTaskId": str(row[19]) if len(row) > 19 and row[19] else None,
        "planItemId": str(row[20]) if len(row) > 20 and row[20] else None,
        "workerType": normalize_task_worker_type(row[21]) if len(row) > 21 and row[21] else "agent",
        "blockedReason": str(row[22]) if len(row) > 22 and row[22] else None,
        "reviewRequired": bool(row[23]) if len(row) > 23 else False,
        "artifacts": normalized_artifacts if isinstance(normalized_artifacts, list) else [],
        "status": normalize_task_status(row[4]),
        "priority": int(row[5] or 0),
        "dueAt": str(row[6]) if row[6] else None,
        "claimLock": str(row[7]) if row[7] else None,
        "lastHeartbeat": str(row[8]) if row[8] else None,
        "result": normalize_optional_text(row[9], max_chars=MAX_TASK_RESULT_CHARS, field_name="result"),
        "error": normalize_optional_text(row[10], max_chars=MAX_TASK_ERROR_CHARS, field_name="error"),
        "createdAt": str(row[11]),
        "updatedAt": str(row[12]),
        "finishedAt": str(row[13]) if row[13] else None,
        "attemptCount": attempt_count,
        "maxAttempts": max_attempts,
        "nextRunAt": next_run_at,
        "leaseOwner": str(row[25]) if len(row) > 25 and row[25] else None,
        "leaseExpiresAt": str(row[26]) if len(row) > 26 and row[26] else None,
        "runnerKind": str(row[27]) if len(row) > 27 and row[27] else "in_process",
        "rootTaskId": str(row[28]) if len(row) > 28 and row[28] else None,
        "planRunId": str(row[29]) if len(row) > 29 and row[29] else None,
        "workerProfile": str(row[30]) if len(row) > 30 and row[30] else None,
        "acceptanceCriteria": json_payload(row[31], default=[]) if len(row) > 31 else [],
        "contextHints": json_payload(row[32], default={}) if len(row) > 32 else {},
        "allowedToolsets": json_payload(row[33], default=[]) if len(row) > 33 else [],
        "disallowedTools": json_payload(row[34], default=[]) if len(row) > 34 else [],
        "dependsOnPolicy": str(row[35]) if len(row) > 35 and row[35] else "all_succeeded",
        "checkpoint": json_payload(row[36], default={}) if len(row) > 36 else {},
        "handoffSummary": normalize_optional_text(row[37], max_chars=MAX_TASK_RESULT_CHARS, field_name="handoff_summary") if len(row) > 37 else None,
        "readyAt": str(row[38]) if len(row) > 38 and row[38] else None,
    }


def task_edge_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    return {
        "id": str(row[0]),
        "fromTaskId": str(row[1]),
        "toTaskId": str(row[2]),
        "edgeType": normalize_task_edge_type(row[3]),
        "requiredStatus": normalize_task_status(row[4] or "succeeded"),
        "metadata": json_payload(row[5], default={}),
        "createdAt": str(row[6]),
    }


def task_attempt_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    return {
        "id": str(row[0]),
        "taskId": str(row[1]),
        "runId": str(row[2]),
        "workerId": str(row[3]) if row[3] else None,
        "workerProfile": str(row[4]) if row[4] else None,
        "status": normalize_task_attempt_status(row[5]),
        "startedAt": str(row[6]),
        "heartbeatAt": str(row[7]) if row[7] else None,
        "finishedAt": str(row[8]) if row[8] else None,
        "inputContext": json_payload(row[9], default={}),
        "checkpoint": json_payload(row[10], default={}),
        "result": normalize_optional_text(row[11], max_chars=MAX_TASK_RESULT_CHARS, field_name="result"),
        "error": normalize_optional_text(row[12], max_chars=MAX_TASK_ERROR_CHARS, field_name="error"),
        "tokenUsage": json_payload(row[13], default={}),
        "toolUsage": json_payload(row[14], default={}),
    }


def task_artifact_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": str(row[0]),
        "taskId": str(row[1]),
        "attemptId": str(row[2]) if row[2] else None,
        "type": str(row[3] or "summary"),
        "title": str(row[4] or "Artifact"),
        "metadata": json_payload(row[8], default={}),
        "createdAt": str(row[9]),
    }
    if row[5]:
        payload["path"] = str(row[5])
    if row[6]:
        payload["url"] = str(row[6])
    if row[7]:
        payload["content"] = str(row[7])
    return payload


def task_event_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    metadata: object | None = None
    if row[6]:
        try:
            metadata = json.loads(str(row[6]))
        except json.JSONDecodeError:
            metadata = None
    return {
        "eventId": int(row[0]),
        "taskId": str(row[1]),
        "sessionId": str(row[2]),
        "type": str(row[3]),
        "status": str(row[4]) if row[4] else None,
        "message": str(row[5]) if row[5] else None,
        "metadata": metadata,
        "createdAt": str(row[7]),
    }


def supervisor_lease_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    expires_at = str(row[5])
    expires = parse_iso_datetime(expires_at)
    return {
        "name": str(row[0]),
        "ownerId": str(row[1]),
        "pid": int(row[2]) if row[2] is not None else None,
        "acquiredAt": str(row[3]),
        "heartbeatAt": str(row[4]),
        "expiresAt": expires_at,
        "active": bool(expires and expires > datetime.now(timezone.utc)),
        "metadata": json_payload(row[6], default={}),
    }


def task_process_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    return {
        "runId": str(row[0]),
        "taskId": str(row[1]),
        "supervisorId": str(row[2]),
        "pid": int(row[3]),
        "processGroupId": int(row[4]) if row[4] is not None else None,
        "status": str(row[5]),
        "startedAt": str(row[6]),
        "heartbeatAt": str(row[7]),
        "exitedAt": str(row[8]) if row[8] else None,
        "returnCode": int(row[9]) if row[9] is not None else None,
        "workspacePath": str(row[10]) if row[10] else None,
        "logPath": str(row[11]) if row[11] else None,
        "metadata": json_payload(row[12], default={}),
    }


def normalize_conversation_summary(content: str) -> str:
    normalized = content.strip() if isinstance(content, str) else ""
    if not normalized:
        raise ValueError("content is required")
    if "\x00" in normalized:
        raise ValueError("content must be UTF-8 text")
    if len(normalized) > CONVERSATION_SUMMARY_LIMIT:
        raise ValueError(f"content must be at most {CONVERSATION_SUMMARY_LIMIT} characters")
    return normalized


def normalize_memory_item_content(content: str) -> str:
    normalized = content.strip() if isinstance(content, str) else ""
    if not normalized:
        raise ValueError("content is required")
    if "\x00" in normalized:
        raise ValueError("content must be UTF-8 text")
    if len(normalized) > MEMORY_ITEM_LIMIT:
        raise ValueError(f"content must be at most {MEMORY_ITEM_LIMIT} characters")
    return normalized


def compute_memory_item_hash(content: str) -> str:
    normalized = content.strip() if isinstance(content, str) else str(content or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_memory_embedding_provider(provider: object) -> str:
    normalized = str(provider or "").strip()
    if not normalized:
        raise ValueError("embedding provider is required")
    if "\x00" in normalized:
        raise ValueError("embedding provider must be UTF-8 text")
    if len(normalized) > 120:
        raise ValueError("embedding provider must be at most 120 characters")
    return normalized


def normalize_memory_embedding_model(model: object) -> str:
    normalized = str(model or "").strip()
    if not normalized:
        raise ValueError("embedding model is required")
    if "\x00" in normalized:
        raise ValueError("embedding model must be UTF-8 text")
    if len(normalized) > 240:
        raise ValueError("embedding model must be at most 240 characters")
    return normalized


def normalize_memory_embedding_dimensions(dimensions: object) -> int:
    try:
        parsed = int(dimensions)
    except (TypeError, ValueError):
        raise ValueError("embedding dimensions must be an integer") from None
    if parsed <= 0 or parsed > 16384:
        raise ValueError("embedding dimensions must be between 1 and 16384")
    return parsed


def normalize_memory_embedding_vector(
    vector: list[float] | tuple[float, ...] | None,
    *,
    dimensions: int,
) -> list[float]:
    normalized_dimensions = normalize_memory_embedding_dimensions(dimensions)
    if vector is None:
        raise ValueError("embedding vector is required")
    if len(vector) != normalized_dimensions:
        raise ValueError(f"embedding vector must have {normalized_dimensions} dimensions")
    normalized: list[float] = []
    for value in vector:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise ValueError("embedding vector values must be numeric") from None
        if not math.isfinite(parsed):
            raise ValueError("embedding vector values must be finite")
        normalized.append(parsed)
    return normalized


def serialize_memory_embedding_vector(vector: list[float]) -> bytes:
    if not vector:
        raise ValueError("embedding vector is required")
    return struct.pack(f"<{len(vector)}f", *vector)


def deserialize_memory_embedding_vector(value: object, *, dimensions: int) -> list[float]:
    normalized_dimensions = normalize_memory_embedding_dimensions(dimensions)
    raw = bytes(value or b"")
    expected_size = normalized_dimensions * 4
    if len(raw) != expected_size:
        raise ValueError(f"embedding blob must be {expected_size} bytes")
    return list(struct.unpack(f"<{normalized_dimensions}f", raw))


def memory_embedding_cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def memory_item_text_match_score(item: dict[str, Any], query: str) -> float:
    normalized_query = query.strip().lower() if isinstance(query, str) else ""
    if not normalized_query:
        return 0.0
    terms = memory_item_query_terms(normalized_query)
    if not terms:
        return 0.0
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    haystack = " ".join([
        str(item.get("content") or ""),
        str(item.get("scope") or ""),
        str(item.get("memoryType") or ""),
        json.dumps(metadata, ensure_ascii=False, sort_keys=True),
    ]).lower()
    if not haystack:
        return 0.0
    matched = sum(1 for term in terms if term.lower() in haystack)
    exact_bonus = 0.25 if normalized_query in haystack else 0.0
    return max(0.0, min(1.0, matched / max(1, len(terms)) + exact_bonus))


def memory_item_access_score(access_count: int) -> float:
    return max(0.0, min(1.0, math.log1p(max(0, int(access_count))) / math.log(11)))


def memory_item_recency_score(updated_at: str) -> float:
    try:
        updated = datetime.fromisoformat(str(updated_at))
    except ValueError:
        return 0.0
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (datetime.now(timezone.utc) - updated).total_seconds() / 86400)
    return 1.0 / (1.0 + age_days / 30.0)


def normalize_memory_item_type(value: str | None) -> MemoryItemType:
    normalized = str(value or "semantic").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "long_term": "semantic",
        "fact": "semantic",
        "stable_preference": "preference",
        "durable_project_fact": "project_fact",
    }
    normalized = aliases.get(normalized, normalized)
    allowed: set[MemoryItemType] = {
        "semantic",
        "episodic",
        "procedural",
        "preference",
        "project_fact",
        "agent_instruction",
    }
    if normalized not in allowed:
        raise ValueError("memory_type must be semantic, episodic, procedural, preference, project_fact, or agent_instruction")
    return normalized  # type: ignore[return-value]


def memory_type_from_review_retention(value: str | None) -> MemoryItemType:
    return normalize_memory_item_type(value)


def normalize_memory_item_metadata(metadata: dict[str, object] | None) -> str:
    if metadata is None:
        return "{}"
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    try:
        encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("metadata must be JSON serializable") from error
    if len(encoded) > MEMORY_ITEM_METADATA_LIMIT:
        raise ValueError(f"metadata must be at most {MEMORY_ITEM_METADATA_LIMIT} characters")
    return encoded


def parse_memory_item_metadata(raw: object) -> dict[str, object]:
    if raw is None:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_memory_item_metadata_filter(metadata_filter: dict[str, object] | None) -> dict[str, object]:
    if metadata_filter is None:
        return {}
    if not isinstance(metadata_filter, dict):
        raise ValueError("metadataFilter must be an object")
    normalized: dict[str, object] = {}
    for raw_key, value in metadata_filter.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        if "\x00" in key:
            raise ValueError("metadataFilter keys must be UTF-8 text")
        if len(key) > 120:
            raise ValueError("metadataFilter keys must be at most 120 characters")
        normalized[key] = value
    return normalized


def memory_item_metadata_search_text(metadata_json: object) -> str:
    metadata = parse_memory_item_metadata(metadata_json)
    if not metadata:
        return ""
    parts: list[str] = []

    def visit(value: object, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized_key = str(key)
                parts.append(normalized_key)
                visit(nested, f"{prefix}.{normalized_key}" if prefix else normalized_key)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                visit(item, prefix)
            return
        if value is None:
            return
        if prefix:
            parts.append(prefix)
        parts.append(str(value))

    visit(metadata)
    return " ".join(parts)


def memory_item_matches_metadata_filter(item: dict[str, Any], metadata_filter: dict[str, object] | None) -> bool:
    normalized_filter = normalize_memory_item_metadata_filter(metadata_filter)
    if not normalized_filter:
        return True
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    if not isinstance(metadata, dict):
        return False
    for key, expected in normalized_filter.items():
        actual = memory_item_metadata_value(metadata, key)
        if not memory_item_metadata_value_matches(actual, expected):
            return False
    return True


def memory_item_metadata_value(metadata: dict[str, object], key: str) -> object:
    current: object = metadata
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def memory_item_metadata_value_matches(actual: object, expected: object) -> bool:
    if isinstance(actual, list):
        if isinstance(expected, list):
            return all(any(memory_item_metadata_value_matches(candidate, item) for candidate in actual) for item in expected)
        return any(memory_item_metadata_value_matches(candidate, expected) for candidate in actual)
    if isinstance(expected, list):
        return any(memory_item_metadata_value_matches(actual, candidate) for candidate in expected)
    if isinstance(actual, dict) or isinstance(expected, dict):
        return actual == expected
    if isinstance(actual, bool) or isinstance(expected, bool):
        return isinstance(actual, bool) and isinstance(expected, bool) and actual is expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return float(actual) == float(expected)
    return str(actual or "").strip().lower() == str(expected or "").strip().lower()


def normalize_memory_item_actor(actor: str | None) -> str:
    normalized = str(actor or "runtime").strip().lower().replace(" ", "_").replace("-", "_")
    if not normalized:
        return "runtime"
    if "\x00" in normalized:
        raise ValueError("actor must be UTF-8 text")
    if len(normalized) > 80:
        raise ValueError("actor must be at most 80 characters")
    return normalized


def normalize_memory_item_history_event(event: str | None) -> MemoryItemHistoryEvent:
    normalized = str(event or "").strip().upper()
    if normalized not in {"ADD", "UPDATE", "DELETE"}:
        raise ValueError("memory history event must be ADD, UPDATE, or DELETE")
    return normalized  # type: ignore[return-value]


def normalize_confidence(confidence: float) -> float:
    normalized = float(confidence)
    if normalized < 0 or normalized > 1:
        raise ValueError("confidence must be between 0 and 1")
    return normalized


def normalize_optional_non_negative_int(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    normalized = int(value)
    if normalized < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return normalized


def normalize_optional_text(value: str | None, field_name: str, max_chars: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if "\x00" in normalized:
        raise ValueError(f"{field_name} must be UTF-8 text")
    if len(normalized) > max_chars:
        raise ValueError(f"{field_name} must be at most {max_chars} characters")
    return normalized


def normalize_default_workspace_path(value: Path | str | None) -> str | None:
    if value is None:
        return None
    return normalize_optional_text(str(value), "default_workspace_path", 1000)


def normalize_memory_review_safety_labels(value: list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("safety_labels must be a list of strings")

    labels: list[str] = []
    seen: set[str] = set()
    for raw_label in value:
        if not isinstance(raw_label, str):
            raise ValueError("safety_labels must be a list of strings")
        label = raw_label.strip().lower().replace(" ", "_")
        if not label:
            continue
        if "\x00" in label:
            raise ValueError("safety_labels must be UTF-8 text")
        if len(label) > MEMORY_REVIEW_LABEL_LIMIT:
            raise ValueError(f"safety_labels entries must be at most {MEMORY_REVIEW_LABEL_LIMIT} characters")
        if label not in seen:
            labels.append(label)
            seen.add(label)
        if len(labels) > MEMORY_REVIEW_MAX_SAFETY_LABELS:
            raise ValueError(f"safety_labels must include at most {MEMORY_REVIEW_MAX_SAFETY_LABELS} labels")
    return labels


def normalize_memory_review_retention_type(value: str | None) -> MemoryReviewRetentionType:
    if value is None:
        return "long_term"
    normalized = value.strip().lower()
    allowed: set[MemoryReviewRetentionType] = {
        "long_term",
        "stable_preference",
        "durable_project_fact",
        "agent_instruction",
    }
    if normalized not in allowed:
        raise ValueError("retention_type must be long_term, stable_preference, durable_project_fact, or agent_instruction")
    return normalized  # type: ignore[return-value]


def parse_memory_review_safety_labels(raw: object) -> list[str]:
    if raw is None:
        return []
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [label for label in parsed if isinstance(label, str)]


def memory_item_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, Any]:
    deleted_at = row[13]
    content = str(row[2])
    return {
        "memoryItemId": int(row[0]),
        "scope": str(row[1]),
        "content": content,
        "charCount": len(content),
        "memoryType": normalize_memory_item_type(str(row[3]) if row[3] is not None else None),
        "metadata": parse_memory_item_metadata(row[4]),
        "contentHash": str(row[5] or ""),
        "confidence": float(row[6]),
        "sourceSessionId": str(row[7]) if row[7] is not None else "",
        "sourceMessageId": int(row[8]) if row[8] is not None else 0,
        "lastAccessedAt": str(row[9]) if row[9] is not None else "",
        "accessCount": int(row[10] or 0),
        "createdAt": str(row[11]),
        "updatedAt": str(row[12]),
        "deleted": deleted_at is not None,
        "deletedAt": str(deleted_at) if deleted_at is not None else "",
    }


def memory_item_history_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, Any]:
    return {
        "historyId": int(row[0]),
        "memoryItemId": int(row[1]),
        "event": normalize_memory_item_history_event(str(row[2])),
        "oldContent": str(row[3]) if row[3] is not None else "",
        "newContent": str(row[4]) if row[4] is not None else "",
        "oldMetadata": parse_memory_item_metadata(row[5]),
        "newMetadata": parse_memory_item_metadata(row[6]),
        "actor": normalize_memory_item_actor(str(row[7]) if row[7] is not None else None),
        "sourceSessionId": str(row[8]) if row[8] is not None else "",
        "sourceMessageId": int(row[9]) if row[9] is not None else 0,
        "createdAt": str(row[10]),
    }


def memory_review_candidate_response(row: sqlite3.Row | tuple[object, ...]) -> MemoryReviewCandidatePayload:
    content = str(row[3])
    return {
        "candidateId": int(row[0]),
        "sessionId": str(row[1]),
        "scope": str(row[2]),
        "content": content,
        "charCount": len(content),
        "confidence": float(row[4]),
        "reason": str(row[5]) if row[5] is not None else "",
        "scopeReason": str(row[6]) if row[6] is not None else "",
        "safetyLabels": parse_memory_review_safety_labels(row[7]),
        "retentionType": str(row[8]) if row[8] is not None else "long_term",
        "sourceMessageStartId": int(row[9]) if row[9] is not None else 0,
        "sourceMessageEndId": int(row[10]) if row[10] is not None else 0,
        "status": str(row[11]),
        "memoryItemId": int(row[12]) if row[12] is not None else 0,
        "createdAt": str(row[13]),
        "updatedAt": str(row[14]),
    }


def memory_review_job_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, str | int]:
    return {
        "jobId": int(row[0]),
        "sessionId": str(row[1]),
        "trigger": str(row[2]),
        "status": str(row[3]),
        "reason": str(row[4]) if row[4] is not None else "",
        "error": str(row[5]) if row[5] is not None else "",
        "sourceMessageStartId": int(row[6]) if row[6] is not None else 0,
        "sourceMessageEndId": int(row[7]) if row[7] is not None else 0,
        "sourceMessageCount": int(row[8]),
        "proposedCandidateCount": int(row[9]),
        "savedCandidateCount": int(row[10]),
        "suppressedCandidateCount": int(row[11]),
        "startedAt": str(row[12]),
        "finishedAt": str(row[13]) if row[13] is not None else "",
        "durationMs": int(row[14]) if row[14] is not None else 0,
    }


def normalize_optional_message_metadata(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def normalize_tool_calls_json(tool_calls: list[dict[str, Any]] | None) -> str | None:
    if tool_calls is None:
        return None
    normalized = [tool_call for tool_call in tool_calls if isinstance(tool_call, dict)]
    if not normalized:
        return None
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def parse_tool_calls_json(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def message_search_text(content: object, tool_name: object = None, tool_calls: object = None) -> str:
    parts = [str(content or "")]
    if tool_name:
        parts.append(str(tool_name))
    if tool_calls:
        parts.append(str(tool_calls))
    return "\n".join(part for part in parts if part)


def message_row_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, Any]:
    message: dict[str, Any] = {
        "id": int(row[0]),
        "role": str(row[1]),
        "content": str(row[2]),
        "createdAt": str(row[3]),
    }
    tool_call_id = normalize_optional_message_metadata(str(row[4])) if row[4] is not None else None
    tool_name = normalize_optional_message_metadata(str(row[5])) if row[5] is not None else None
    tool_calls = parse_tool_calls_json(row[6])
    if tool_call_id:
        message["toolCallId"] = tool_call_id
        message["tool_call_id"] = tool_call_id
    if tool_name:
        message["toolName"] = tool_name
        message["tool_name"] = tool_name
    if tool_calls:
        message["toolCalls"] = tool_calls
        message["tool_calls"] = tool_calls
    return message


def ensure_stable_memory_file(path: Path, target: str) -> None:
    if path.exists():
        return

    title = STABLE_MEMORY_TITLES[target]
    path.write_text(
        f"# {title}\n\n"
        "Stable facts only. Do not store transient task progress, raw transcripts, secrets, or speculative guesses.\n\n"
        "## Entries\n\n",
        encoding="utf-8",
    )


def stable_memory_response(target: str, path: Path, content: str) -> dict[str, str | int]:
    return {
        "target": target,
        "path": path.as_posix(),
        "content": content,
        "charCount": len(content),
        "lineCount": len(content.splitlines()),
        "limit": STABLE_MEMORY_LIMITS[target],
    }


def build_stable_memory_update(
    before: str,
    target: str,
    action: str,
    content: str | None,
    old_text: str | None,
) -> str:
    if action == "add":
        entry = normalize_memory_entry(content)
        return append_stable_memory_entry(before, entry)

    needle = old_text.strip() if isinstance(old_text, str) else ""
    if not needle:
        raise ValueError("oldText is required for replace/remove")

    count = before.count(needle)
    if count == 0:
        raise ValueError("oldText was not found in stable memory")
    if count > 1:
        raise ValueError("oldText must match exactly one stable memory section")

    if action == "remove":
        return normalize_markdown_spacing(before.replace(needle, "", 1))

    replacement = normalize_memory_entry(content)
    return before.replace(needle, replacement, 1)


def normalize_memory_entry(content: str | None) -> str:
    entry = content.strip() if isinstance(content, str) else ""
    if not entry:
        raise ValueError("content is required")
    if "\x00" in entry:
        raise ValueError("content must be UTF-8 text")
    if len(entry) > 1000:
        raise ValueError("content must be at most 1000 characters")
    if not entry.startswith(("- ", "* ", "## ", "### ")):
        entry = f"- {entry}"
    return entry


def append_stable_memory_entry(before: str, entry: str) -> str:
    stripped = before.rstrip()
    return f"{stripped}\n\n{entry}\n"


def normalize_markdown_spacing(content: str) -> str:
    lines = content.splitlines()
    normalized: list[str] = []
    blank_count = 0
    for line in lines:
        if line.strip():
            blank_count = 0
            normalized.append(line.rstrip())
        else:
            blank_count += 1
            if blank_count <= 2:
                normalized.append("")
    return "\n".join(normalized).rstrip() + "\n"


def atomic_write_text(path: Path, content: str) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)
