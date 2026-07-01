from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from amadeus.identity import (
    ensure_role_soul,
    normalize_soul_text,
    read_soul,
    role_home_path,
    role_soul_path,
)
from amadeus.planning import empty_plan_response, merge_plan_items, plan_response
from amadeus.tasks import (
    MAX_TASK_ERROR_CHARS,
    MAX_TASK_EVENT_MESSAGE_CHARS,
    MAX_TASK_RESULT_CHARS,
    normalize_optional_text,
    normalize_task_body,
    normalize_task_max_attempts,
    normalize_task_event_type,
    normalize_task_priority,
    normalize_task_status,
    normalize_task_title,
    task_summary,
)


MessageRole = Literal["user", "assistant"]
StableMemoryTarget = Literal["agent", "user"]
MemoryItemScope = Literal["user", "agent", "project"]
MemoryReviewCandidateStatus = Literal["pending", "accepted", "rejected", "superseded"]
MemoryReviewRetentionType = Literal["long_term", "stable_preference", "durable_project_fact", "agent_instruction"]
MemoryReviewJobStatus = Literal["running", "completed", "skipped", "failed"]
MemoryReviewJobTrigger = Literal["manual", "auto", "compaction"]
MemoryReviewCandidatePayload = dict[str, str | int | float | bool | list[str]]
CONVERSATION_SUMMARY_LIMIT = 12000
MEMORY_ITEM_LIMIT = 2000
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
    def __init__(
        self,
        database_path: Path,
        stable_memory_dir: Path | None = None,
        default_workspace_path: Path | str | None = None,
    ) -> None:
        self.database_path = database_path
        self.stable_memory_dir = stable_memory_dir or database_path.parent / "memory"
        self.roles_root = self.database_path.parent / "roles"
        self.default_workspace_path = normalize_default_workspace_path(default_workspace_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.stable_memory_dir.mkdir(parents=True, exist_ok=True)
        self.roles_root.mkdir(parents=True, exist_ok=True)
        self.initialize()

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
                  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                  content TEXT NOT NULL,
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
                    confidence REAL NOT NULL DEFAULT 1.0,
                    source_session_id TEXT,
                    source_message_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                  );
                  CREATE INDEX IF NOT EXISTS idx_memory_items_scope_updated
                  ON memory_items(scope, deleted_at, updated_at);
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
                  CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'blocked', 'succeeded', 'failed', 'cancelled')),
                    priority INTEGER NOT NULL DEFAULT 0,
                    due_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    next_run_at TEXT,
                    claim_lock TEXT,
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
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                USING fts5(
                  content,
                  session_id UNINDEXED,
                  role UNINDEXED,
                  created_at UNINDEXED
                );
                """
            )
            self._migrate_roles_and_sessions(connection)
            self._migrate_conversation_summaries(connection)
            self._migrate_memory_review_candidates(connection)
            self._migrate_task_statuses(connection)
            self._migrate_task_reliability_columns(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_summaries_session_covered
                ON conversation_summaries(session_id, covered_through_message_id)
                """
            )
            connection.execute("DELETE FROM messages_fts")
            connection.execute(
                """
                INSERT INTO messages_fts(rowid, content, session_id, role, created_at)
                SELECT id, content, session_id, role, created_at
                FROM messages
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
        default_workspace_path = self.default_workspace_path
        connection.execute(
            """
            INSERT OR IGNORE INTO roles (
              id, name, description, persona, style, provider, model, live2d_model, tts_voice, workspace_path, archived, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, 0, ?, ?)
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
              status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'blocked', 'succeeded', 'failed', 'cancelled')),
              priority INTEGER NOT NULL DEFAULT 0,
              due_at TEXT,
              attempt_count INTEGER NOT NULL DEFAULT 0,
              max_attempts INTEGER NOT NULL DEFAULT 3,
              next_run_at TEXT,
              claim_lock TEXT,
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
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_status_next_run
            ON tasks(status, next_run_at, due_at, priority)
            """
        )

    def list_roles(self, include_archived: bool = False) -> list[dict[str, str | int | bool]]:
        where = "1 = 1" if include_archived else "archived = 0"
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, name, description, persona, style, provider, model, live2d_model, tts_voice, workspace_path, archived, created_at, updated_at
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
    ) -> dict[str, str | int | bool]:
        normalized_name = normalize_role_name(name)
        normalized_description = normalize_optional_text(description, "description", 500) or ""
        normalized_persona = normalize_optional_text(persona, "persona", 4000) or ""
        normalized_style = normalize_optional_text(style, "style", 1000) or ""
        normalized_provider = normalize_optional_text(provider, "provider", 120)
        normalized_model = normalize_optional_text(model, "model", 160)
        normalized_live2d_model = normalize_optional_text(live2d_model, "live2d_model", 160)
        normalized_tts_voice = normalize_optional_text(tts_voice, "tts_voice", 160)
        normalized_workspace_path = normalize_optional_text(workspace_path, "workspace_path", 1000) or self.default_workspace_path
        role_id = f"role-{uuid4().hex[:12]}"
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO roles (
                  id, name, description, persona, style, provider, model, live2d_model, tts_voice, workspace_path, archived, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
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
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT id, name, description, persona, style, provider, model, live2d_model, tts_voice, workspace_path, archived, created_at, updated_at
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

    def get_role(self, role_id: str) -> dict[str, str | int | bool] | None:
        normalized_role_id = normalize_role_id(role_id)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, name, description, persona, style, provider, model, live2d_model, tts_voice, workspace_path, archived, created_at, updated_at
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
    ) -> dict[str, str | int | bool]:
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
                SELECT id, name, description, persona, style, provider, model, live2d_model, tts_voice, workspace_path, archived, created_at, updated_at
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

    def save(self, session_id: str, role: str, content: str) -> int:
        if role not in ("user", "assistant"):
            raise ValueError("role must be user or assistant")

        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        now = datetime.now(timezone.utc).isoformat()
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
                INSERT INTO messages (session_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (normalized_session_id, role, content, now),
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
                SELECT content, session_id, role, created_at
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
                    (row_id, row[0], row[1], row[2], row[3]),
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
            where += " AND session_id = ?"
            params.append(session_id)
        params.append(bounded_limit)

        with self.connect() as connection:
            try:
                rows = connection.execute(
                    f"""
                    SELECT
                      rowid,
                      session_id,
                      role,
                      content,
                      created_at,
                      snippet(messages_fts, 0, '[', ']', ' … ', 12) AS snippet
                    FROM messages_fts
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

    def load(self, session_id: str, limit: int = 40, after_message_id: int | None = None) -> list[dict[str, str]]:
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
                SELECT role, content
                FROM messages
                WHERE {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [{"role": row[0], "content": row[1]} for row in reversed(rows)]

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
                SELECT id, role, content, created_at
                FROM messages
                WHERE {where}
                ORDER BY id ASC
                {limit_clause}
                """,
                params,
            ).fetchall()

        return [
            {
                "id": int(row[0]),
                "role": str(row[1]),
                "content": str(row[2]),
                "createdAt": str(row[3]),
            }
            for row in rows
        ]

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
        return plan_response(normalized_session_id, normalized_items, updated_at=now)

    def create_task(
        self,
        *,
        session_id: str,
        title: str,
        body: str | None = None,
        priority: int | None = None,
        due_at: str | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, object]:
        normalized_session_id = normalize_session_id(session_id)
        self.ensure_session(normalized_session_id)
        normalized_title = normalize_task_title(title)
        normalized_body = normalize_task_body(body)
        normalized_priority = normalize_task_priority(priority)
        normalized_due_at = normalize_optional_text(due_at, max_chars=80, field_name="due_at")
        normalized_max_attempts = normalize_task_max_attempts(max_attempts)
        task_id = uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                  id, session_id, title, body, status, priority, due_at, max_attempts, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    normalized_session_id,
                    normalized_title,
                    normalized_body,
                    normalized_priority,
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
                metadata=None,
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
                       attempt_count, max_attempts, next_run_at
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
                       attempt_count, max_attempts, next_run_at
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
                       attempt_count, max_attempts, next_run_at
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

    def start_task(self, task_id: str, *, claim_lock: str) -> dict[str, object] | None:
        normalized_task_id = normalize_task_id(task_id)
        normalized_claim_lock = normalize_optional_text(claim_lock, max_chars=120, field_name="claim_lock")
        if not normalized_claim_lock:
            raise ValueError("claim_lock is required")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, session_id, status, due_at, next_run_at
                FROM tasks
                WHERE id = ?
                """,
                (normalized_task_id,),
            ).fetchone()
            if not row:
                raise ValueError("task not found")
            if str(row[2]) != "queued":
                return self.get_task(normalized_task_id)
            if not task_time_is_due(str(row[3] or ""), now_dt) or not task_time_is_due(str(row[4] or ""), now_dt):
                return self.get_task(normalized_task_id)
            connection.execute(
                """
                UPDATE tasks
                SET status = 'running',
                    attempt_count = attempt_count + 1,
                    next_run_at = NULL,
                    claim_lock = ?,
                    last_heartbeat = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (normalized_claim_lock, now, now, normalized_task_id),
            )
            self._insert_task_event(
                connection,
                task_id=normalized_task_id,
                session_id=str(row[1]),
                event_type="running",
                status="running",
                message="Task worker started",
                metadata={"claimLock": normalized_claim_lock},
                created_at=now,
            )
        return self.get_task(normalized_task_id)

    def heartbeat_task(self, task_id: str, *, claim_lock: str) -> dict[str, object] | None:
        normalized_task_id = normalize_task_id(task_id)
        normalized_claim_lock = normalize_optional_text(claim_lock, max_chars=120, field_name="claim_lock")
        if not normalized_claim_lock:
            raise ValueError("claim_lock is required")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET last_heartbeat = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'running' AND claim_lock = ?
                """,
                (now, now, normalized_task_id, normalized_claim_lock),
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

    def retry_task(
        self,
        task_id: str,
        *,
        claim_lock: str,
        error: str | None = None,
        next_run_at: str | None = None,
    ) -> dict[str, object]:
        normalized_task_id = normalize_task_id(task_id)
        normalized_claim_lock = normalize_optional_text(claim_lock, max_chars=120, field_name="claim_lock")
        if not normalized_claim_lock:
            raise ValueError("claim_lock is required")
        normalized_error = normalize_optional_text(error, max_chars=MAX_TASK_ERROR_CHARS, field_name="error")
        normalized_next_run_at = normalize_optional_text(next_run_at, max_chars=80, field_name="next_run_at")
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
                    last_heartbeat = NULL,
                    next_run_at = ?,
                    error = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'running' AND claim_lock = ?
                """,
                (
                    normalized_next_run_at,
                    normalized_error,
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
                       attempt_count, max_attempts, next_run_at
                FROM tasks
                WHERE status = 'queued'
                ORDER BY priority DESC, updated_at ASC
                LIMIT 200
                """,
            ).fetchall()
        runnable = [
            task_response(row)
            for row in rows
            if task_time_is_due(str(row[6] or ""), now_dt) and task_time_is_due(str(row[16] or ""), now_dt)
        ]
        return runnable[:normalized_limit]

    def recover_stale_running_tasks(self, *, stale_after_seconds: float = 300.0, limit: int = 50) -> list[dict[str, object]]:
        normalized_limit = max(1, min(200, int(limit)))
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        stale_after = max(1.0, float(stale_after_seconds))
        stale: list[tuple[str, str, str | None, str | None]] = []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, session_id, claim_lock, last_heartbeat
                FROM tasks
                WHERE status = 'running'
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (normalized_limit,),
            ).fetchall()
            for row in rows:
                heartbeat = parse_iso_datetime(str(row[3])) if row[3] else None
                if heartbeat is not None and (now_dt - heartbeat).total_seconds() < stale_after:
                    continue
                stale.append((str(row[0]), str(row[1]), str(row[2]) if row[2] else None, str(row[3]) if row[3] else None))
            for task_id, session_id, claim_lock, heartbeat in stale:
                connection.execute(
                    """
                    UPDATE tasks
                    SET status = 'queued',
                        claim_lock = NULL,
                        last_heartbeat = NULL,
                        next_run_at = ?,
                        error = ?,
                        updated_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (
                        now,
                        "Task worker recovered stale running task",
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
                        "lastHeartbeat": heartbeat,
                    },
                    created_at=now,
                )
        return [task for task_id, _, _, _ in stale if (task := self.get_task(task_id)) is not None]

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
    ) -> dict[str, str | int | float | bool]:
        normalized_scope = normalize_memory_item_scope(scope)
        normalized_content = normalize_memory_item_content(content)
        normalized_confidence = normalize_confidence(confidence)
        normalized_source_session_id = normalize_optional_text(source_session_id, "source_session_id", max_chars=200)
        normalized_source_message_id = normalize_optional_non_negative_int(source_message_id, "source_message_id")
        now = datetime.now(timezone.utc).isoformat()

        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memory_items (
                  scope,
                  content,
                  confidence,
                  source_session_id,
                  source_message_id,
                  created_at,
                  updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_scope,
                    normalized_content,
                    normalized_confidence,
                    normalized_source_session_id,
                    normalized_source_message_id,
                    now,
                    now,
                ),
            )
            item_id = cursor.lastrowid

        return {
            "memoryItemId": int(item_id),
            "scope": normalized_scope,
            "content": normalized_content,
            "charCount": len(normalized_content),
            "confidence": normalized_confidence,
            "sourceSessionId": normalized_source_session_id or "",
            "sourceMessageId": normalized_source_message_id or 0,
            "createdAt": now,
            "updatedAt": now,
            "deleted": False,
        }

    def list_memory_items(
        self,
        *,
        scope: str | None = None,
        query: str | None = None,
        include_deleted: bool = False,
        limit: int = 20,
    ) -> list[dict[str, str | int | float | bool]]:
        normalized_scope = normalize_memory_item_scope(scope) if scope else None
        normalized_query = query.strip() if isinstance(query, str) else ""
        bounded_limit = max(1, min(100, int(limit)))
        where = "1 = 1"
        params: list[object] = []
        if normalized_scope:
            where += " AND scope = ?"
            params.append(normalized_scope)
        if normalized_query:
            where += " AND content LIKE ?"
            params.append(f"%{normalized_query}%")
        if not include_deleted:
            where += " AND deleted_at IS NULL"
        params.append(bounded_limit)

        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                  id,
                  scope,
                  content,
                  confidence,
                  source_session_id,
                  source_message_id,
                  created_at,
                  updated_at,
                  deleted_at
                FROM memory_items
                WHERE {where}
                ORDER BY confidence DESC, updated_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [memory_item_response(row) for row in rows]

    def delete_memory_item(self, memory_item_id: int) -> bool:
        normalized_id = int(memory_item_id)
        if normalized_id <= 0:
            raise ValueError("memory_item_id must be positive")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_items
                SET deleted_at = ?, updated_at = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (now, now, normalized_id),
            )
        return cursor.rowcount > 0

    def replace_memory_item(
        self,
        memory_item_id: int,
        content: str,
        *,
        scope: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, str | int | float | bool] | None:
        normalized_id = int(memory_item_id)
        if normalized_id <= 0:
            raise ValueError("memory_item_id must be positive")
        normalized_content = normalize_memory_item_content(content)
        normalized_scope = normalize_memory_item_scope(scope) if scope else None
        normalized_confidence = normalize_confidence(confidence) if confidence is not None else None
        now = datetime.now(timezone.utc).isoformat()

        assignments = ["content = ?", "updated_at = ?"]
        params: list[object] = [normalized_content, now]
        if normalized_scope:
            assignments.append("scope = ?")
            params.append(normalized_scope)
        if normalized_confidence is not None:
            assignments.append("confidence = ?")
            params.append(normalized_confidence)
        params.append(normalized_id)

        with self.connect() as connection:
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
            row = connection.execute(
                """
                SELECT
                  id,
                  scope,
                  content,
                  confidence,
                  source_session_id,
                  source_message_id,
                  created_at,
                  updated_at,
                  deleted_at
                FROM memory_items
                WHERE id = ?
                """,
                (normalized_id,),
            ).fetchone()

        return memory_item_response(row)

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


def role_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, str | int | bool]:
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
        "archived": bool(row[10]),
        "createdAt": str(row[11]),
        "updatedAt": str(row[12]),
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


def make_fts_query(query: str) -> str:
    terms = [term.replace('"', " ").strip() for term in query.split()]
    terms = [term for term in terms if term]
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"' for term in terms)


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


def task_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, object]:
    attempt_count = int(row[14] or 0) if len(row) > 14 else 0
    max_attempts = int(row[15] or 3) if len(row) > 15 else 3
    next_run_at = str(row[16]) if len(row) > 16 and row[16] else None
    return {
        "id": str(row[0]),
        "sessionId": str(row[1]),
        "title": str(row[2]),
        "body": str(row[3] or ""),
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
    }


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


def memory_item_response(row: sqlite3.Row | tuple[object, ...]) -> dict[str, str | int | float | bool]:
    deleted_at = row[8]
    content = str(row[2])
    return {
        "memoryItemId": int(row[0]),
        "scope": str(row[1]),
        "content": content,
        "charCount": len(content),
        "confidence": float(row[3]),
        "sourceSessionId": str(row[4]) if row[4] is not None else "",
        "sourceMessageId": int(row[5]) if row[5] is not None else 0,
        "createdAt": str(row[6]),
        "updatedAt": str(row[7]),
        "deleted": deleted_at is not None,
        "deletedAt": str(deleted_at) if deleted_at is not None else "",
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
