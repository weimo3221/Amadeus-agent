from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class ToolAuditRecord:
    record_id: str
    timestamp: str
    session_id: str
    tool_name: str
    decision: str
    ok: bool | None = None
    duration_ms: int | None = None
    failure_code: str | None = None
    detail: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "recordId": self.record_id,
            "timestamp": self.timestamp,
            "sessionId": self.session_id,
            "toolName": self.tool_name,
            "decision": self.decision,
        }
        if self.ok is not None:
            payload["ok"] = self.ok
        if self.duration_ms is not None:
            payload["durationMs"] = self.duration_ms
        if self.failure_code is not None:
            payload["failureCode"] = self.failure_code
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


class ToolAuditLog:
    def __init__(self) -> None:
        self._records: list[ToolAuditRecord] = []

    def append(
        self,
        *,
        session_id: str,
        tool_name: str,
        decision: str,
        ok: bool | None = None,
        duration_ms: int | None = None,
        failure_code: str | None = None,
        detail: str | None = None,
    ) -> ToolAuditRecord:
        record = ToolAuditRecord(
            record_id=str(uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            session_id=session_id,
            tool_name=tool_name,
            decision=decision,
            ok=ok,
            duration_ms=duration_ms,
            failure_code=failure_code,
            detail=detail,
        )
        self._records.append(record)
        return record

    def records(self) -> list[ToolAuditRecord]:
        return list(self._records)


class ToolAuditStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tool_audit_records (
                  record_id TEXT PRIMARY KEY,
                  timestamp TEXT NOT NULL,
                  session_id TEXT NOT NULL,
                  tool_name TEXT NOT NULL,
                  decision TEXT NOT NULL,
                  ok INTEGER,
                  duration_ms INTEGER,
                  failure_code TEXT,
                  detail TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tool_audit_session_timestamp
                ON tool_audit_records(session_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_tool_audit_tool_timestamp
                ON tool_audit_records(tool_name, timestamp);
                """
            )

    def save(self, record: ToolAuditRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO tool_audit_records (
                  record_id,
                  timestamp,
                  session_id,
                  tool_name,
                  decision,
                  ok,
                  duration_ms,
                  failure_code,
                  detail
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.record_id,
                    record.timestamp,
                    record.session_id,
                    record.tool_name,
                    record.decision,
                    None if record.ok is None else int(record.ok),
                    record.duration_ms,
                    record.failure_code,
                    record.detail,
                ),
            )

    def load(self, session_id: str | None = None, limit: int = 100) -> list[ToolAuditRecord]:
        limit = max(1, limit)
        if session_id:
            query = """
                SELECT record_id, timestamp, session_id, tool_name, decision, ok, duration_ms, failure_code, detail
                FROM tool_audit_records
                WHERE session_id = ?
                ORDER BY rowid DESC
                LIMIT ?
            """
            params: tuple[Any, ...] = (session_id, limit)
        else:
            query = """
                SELECT record_id, timestamp, session_id, tool_name, decision, ok, duration_ms, failure_code, detail
                FROM tool_audit_records
                ORDER BY rowid DESC
                LIMIT ?
            """
            params = (limit,)

        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()

        return [self._row_to_record(row) for row in reversed(rows)]

    def count(self, session_id: str | None = None) -> int:
        if session_id:
            query = "SELECT COUNT(*) FROM tool_audit_records WHERE session_id = ?"
            params: tuple[Any, ...] = (session_id,)
        else:
            query = "SELECT COUNT(*) FROM tool_audit_records"
            params = ()

        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()

        return int(row[0]) if row else 0

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    @staticmethod
    def _row_to_record(row: tuple[Any, ...]) -> ToolAuditRecord:
        ok_value = row[5]
        return ToolAuditRecord(
            record_id=str(row[0]),
            timestamp=str(row[1]),
            session_id=str(row[2]),
            tool_name=str(row[3]),
            decision=str(row[4]),
            ok=None if ok_value is None else bool(ok_value),
            duration_ms=None if row[6] is None else int(row[6]),
            failure_code=None if row[7] is None else str(row[7]),
            detail=None if row[8] is None else str(row[8]),
        )
