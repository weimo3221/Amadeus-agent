from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


MessageRole = Literal["user", "assistant"]
StableMemoryTarget = Literal["agent", "user"]
CONVERSATION_SUMMARY_LIMIT = 12000
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


class MessageMemoryStore:
    def __init__(self, database_path: Path, stable_memory_dir: Path | None = None) -> None:
        self.database_path = database_path
        self.stable_memory_dir = stable_memory_dir or database_path.parent / "memory"
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.stable_memory_dir.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
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
                  CREATE INDEX IF NOT EXISTS idx_conversation_summaries_session_covered
                  ON conversation_summaries(session_id, covered_through_message_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                USING fts5(
                  content,
                  session_id UNINDEXED,
                  role UNINDEXED,
                  created_at UNINDEXED
                );
                """
            )
            self._migrate_conversation_summaries(connection)
            connection.execute("DELETE FROM messages_fts")
            connection.execute(
                """
                INSERT INTO messages_fts(rowid, content, session_id, role, created_at)
                SELECT id, content, session_id, role, created_at
                FROM messages
                """
            )

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

    def save(self, session_id: str, role: str, content: str) -> int:
        if role not in ("user", "assistant"):
            raise ValueError("role must be user or assistant")

        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (session_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, role, content, datetime.now(timezone.utc).isoformat()),
            )
            row_id = cursor.lastrowid
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

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def read_stable_memory(self, target: str) -> dict[str, str | int]:
        target = normalize_stable_memory_target(target)
        path = self._stable_memory_path(target)
        ensure_stable_memory_file(path, target)
        content = path.read_text(encoding="utf-8")
        return stable_memory_response(target, path, content)

    def stable_memory_snapshot(self) -> dict[str, dict[str, str | int]]:
        return {
            target: self.read_stable_memory(target)
            for target in STABLE_MEMORY_FILES
        }

    def update_stable_memory(
        self,
        target: str,
        action: str,
        content: str | None = None,
        old_text: str | None = None,
    ) -> dict[str, str | int | bool]:
        target = normalize_stable_memory_target(target)
        normalized_action = action.strip().lower()
        if normalized_action not in {"add", "replace", "remove"}:
            raise ValueError("action must be add, replace, or remove")

        path = self._stable_memory_path(target)
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

    def _stable_memory_path(self, target: str) -> Path:
        return self.stable_memory_dir / STABLE_MEMORY_FILES[target]


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


def normalize_session_id(session_id: str) -> str:
    normalized = session_id.strip() if isinstance(session_id, str) else ""
    if not normalized:
        raise ValueError("session_id is required")
    if "\x00" in normalized:
        raise ValueError("session_id must be UTF-8 text")
    if len(normalized) > 200:
        raise ValueError("session_id must be at most 200 characters")
    return normalized


def normalize_conversation_summary(content: str) -> str:
    normalized = content.strip() if isinstance(content, str) else ""
    if not normalized:
        raise ValueError("content is required")
    if "\x00" in normalized:
        raise ValueError("content must be UTF-8 text")
    if len(normalized) > CONVERSATION_SUMMARY_LIMIT:
        raise ValueError(f"content must be at most {CONVERSATION_SUMMARY_LIMIT} characters")
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
