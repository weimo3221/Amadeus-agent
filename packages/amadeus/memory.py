from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


MessageRole = Literal["user", "assistant"]
StableMemoryTarget = Literal["agent", "user"]
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
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                USING fts5(
                  content,
                  session_id UNINDEXED,
                  role UNINDEXED,
                  created_at UNINDEXED
                );
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

    def save(self, session_id: str, role: str, content: str) -> None:
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

    def load(self, session_id: str, limit: int = 40) -> list[dict[str, str]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
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
