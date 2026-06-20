from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


MessageRole = Literal["user", "assistant"]


class MessageMemoryStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
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


def make_fts_query(query: str) -> str:
    terms = [term.replace('"', " ").strip() for term in query.split()]
    terms = [term for term in terms if term]
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"' for term in terms)
