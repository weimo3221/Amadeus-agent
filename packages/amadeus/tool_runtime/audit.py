from __future__ import annotations

import sqlite3
import logging
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


logger = logging.getLogger(__name__)


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
    metadata: dict[str, Any] | None = None

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
        if self.metadata is not None:
            payload["metadata"] = self.metadata
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
        metadata: dict[str, Any] | None = None,
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
            metadata=json.loads(json.dumps(metadata)) if metadata is not None else None,
        )
        self._records.append(record)
        logger.info(
            "Appended in-memory tool audit sessionId=%s toolName=%s decision=%s ok=%s failureCode=%s recordId=%s",
            session_id,
            tool_name,
            decision,
            ok,
            failure_code,
            record.record_id,
        )
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
                  detail TEXT,
                  metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tool_audit_session_timestamp
                ON tool_audit_records(session_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_tool_audit_tool_timestamp
                ON tool_audit_records(tool_name, timestamp);
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(tool_audit_records)").fetchall()
            }
            if "metadata_json" not in columns:
                connection.execute("ALTER TABLE tool_audit_records ADD COLUMN metadata_json TEXT")
        logger.info("Initialized tool audit store database=%s", self.database_path)

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
                  detail,
                  metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    json.dumps(record.metadata, ensure_ascii=False, sort_keys=True) if record.metadata is not None else None,
                ),
            )
        logger.info(
            "Persisted tool audit record sessionId=%s toolName=%s decision=%s ok=%s failureCode=%s recordId=%s",
            record.session_id,
            record.tool_name,
            record.decision,
            record.ok,
            record.failure_code,
            record.record_id,
        )

    def load(self, session_id: str | None = None, limit: int = 100) -> list[ToolAuditRecord]:
        return self.query(session_id=session_id, limit=limit)

    def query(
        self,
        *,
        session_id: str | None = None,
        tool_name: str | None = None,
        decision: str | None = None,
        ok: bool | None = None,
        failure_code: str | None = None,
        limit: int = 100,
    ) -> list[ToolAuditRecord]:
        limit = max(1, limit)
        clauses: list[str] = []
        params: list[Any] = []

        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if tool_name:
            clauses.append("tool_name = ?")
            params.append(tool_name)
        if decision:
            clauses.append("decision = ?")
            params.append(decision)
        if ok is not None:
            clauses.append("ok = ?")
            params.append(int(ok))
        if failure_code:
            clauses.append("failure_code = ?")
            params.append(failure_code)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT record_id, timestamp, session_id, tool_name, decision, ok, duration_ms, failure_code, detail, metadata_json
            FROM tool_audit_records
            {where_sql}
            ORDER BY rowid DESC
            LIMIT ?
        """
        params.append(limit)

        with self.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()

        records = [self._row_to_record(row) for row in reversed(rows)]
        logger.info(
            "Queried tool audit records sessionId=%s toolName=%s decision=%s ok=%s failureCode=%s limit=%s resultCount=%s",
            session_id,
            tool_name,
            decision,
            ok,
            failure_code,
            limit,
            len(records),
        )
        return records

    def count(
        self,
        session_id: str | None = None,
        tool_name: str | None = None,
        decision: str | None = None,
        ok: bool | None = None,
        failure_code: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []

        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if tool_name:
            clauses.append("tool_name = ?")
            params.append(tool_name)
        if decision:
            clauses.append("decision = ?")
            params.append(decision)
        if ok is not None:
            clauses.append("ok = ?")
            params.append(int(ok))
        if failure_code:
            clauses.append("failure_code = ?")
            params.append(failure_code)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT COUNT(*) FROM tool_audit_records {where_sql}"

        with self.connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()

        count = int(row[0]) if row else 0
        logger.info(
            "Counted tool audit records sessionId=%s toolName=%s decision=%s ok=%s failureCode=%s count=%s",
            session_id,
            tool_name,
            decision,
            ok,
            failure_code,
            count,
        )
        return count

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    @staticmethod
    def _row_to_record(row: tuple[Any, ...]) -> ToolAuditRecord:
        ok_value = row[5]
        metadata = None
        if len(row) > 9 and row[9] is not None:
            try:
                parsed_metadata = json.loads(str(row[9]))
                if isinstance(parsed_metadata, dict):
                    metadata = parsed_metadata
            except json.JSONDecodeError:
                metadata = None
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
            metadata=metadata,
        )
