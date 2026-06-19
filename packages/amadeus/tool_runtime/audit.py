from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
