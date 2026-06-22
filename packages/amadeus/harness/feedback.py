from __future__ import annotations

import threading
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


FEEDBACK_EVENT_TYPES = {
    "desktop.capabilities",
    "audio.playback-started",
    "audio.playback-ended",
    "audio.playback-error",
}


class HarnessFeedbackPolicy:
    def __init__(self, event_limit: int = 20) -> None:
        self.event_limit = max(1, event_limit)
        self._sessions: dict[str, dict[str, Any]] = {}
        self._events_by_session: dict[str, deque[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def record_feedback(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        if event_type not in FEEDBACK_EVENT_TYPES:
            raise ValueError(f"unsupported feedback event type: {event_type}")

        observed_at = timestamp or datetime.now(timezone.utc).isoformat()
        with self._lock:
            state = self._sessions.setdefault(session_id, self._empty_state(session_id))
            event_record = {
                "type": event_type,
                "timestamp": observed_at,
                "payload": deepcopy(payload),
            }
            events = self._events_by_session.setdefault(session_id, deque(maxlen=self.event_limit))
            events.append(event_record)

            state["lastFeedbackAt"] = observed_at
            state["lastFeedbackType"] = event_type
            if event_type == "desktop.capabilities":
                state["desktopCapabilities"] = self._normalize_desktop_capabilities(payload)
            elif event_type == "audio.playback-started":
                state["audioPlayback"] = self._audio_playback_state("playing", payload, observed_at)
            elif event_type == "audio.playback-ended":
                state["audioPlayback"] = self._audio_playback_state("idle", payload, observed_at)
            elif event_type == "audio.playback-error":
                state["audioPlayback"] = self._audio_playback_state("error", payload, observed_at)

            return self.snapshot(session_id, _locked=True)

    def snapshot(self, session_id: str, *, _locked: bool = False) -> dict[str, Any]:
        if _locked:
            return self._snapshot_locked(session_id)

        with self._lock:
            return self._snapshot_locked(session_id)

    def client_capabilities(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            capabilities = self._sessions.get(session_id, {}).get("desktopCapabilities")
            return deepcopy(capabilities) if isinstance(capabilities, dict) else {}

    def runtime_state(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._sessions.get(session_id)
            if not state:
                return {}
            return {
                "audioPlayback": deepcopy(state.get("audioPlayback")),
                "lastFeedbackAt": state.get("lastFeedbackAt"),
                "lastFeedbackType": state.get("lastFeedbackType"),
            }

    def _snapshot_locked(self, session_id: str) -> dict[str, Any]:
        state = deepcopy(self._sessions.get(session_id) or self._empty_state(session_id))
        events = list(self._events_by_session.get(session_id, []))
        state["recentEvents"] = deepcopy(events)
        state["recentEventCount"] = len(events)
        return state

    def _empty_state(self, session_id: str) -> dict[str, Any]:
        return {
            "sessionId": session_id,
            "desktopCapabilities": None,
            "audioPlayback": {
                "status": "unknown",
                "source": None,
                "audioUrl": None,
                "reason": None,
                "updatedAt": None,
            },
            "lastFeedbackAt": None,
            "lastFeedbackType": None,
        }

    def _normalize_desktop_capabilities(self, payload: dict[str, Any]) -> dict[str, Any]:
        desktop = payload.get("desktop") if isinstance(payload.get("desktop"), dict) else {}
        live2d = payload.get("live2d") if isinstance(payload.get("live2d"), dict) else {}
        audio = payload.get("audio") if isinstance(payload.get("audio"), dict) else {}
        return {
            "desktop": {
                "runtime": str(desktop.get("runtime") or "unknown"),
                "protocolVersion": desktop.get("protocolVersion") if isinstance(desktop.get("protocolVersion"), int) else None,
            },
            "live2d": {
                "available": bool(live2d.get("available")),
                "modelId": live2d.get("modelId") if isinstance(live2d.get("modelId"), str) else None,
                "expressions": self._string_list(live2d.get("expressions")),
                "motions": self._string_list(live2d.get("motions")),
            },
            "audio": {
                "runtimeAudio": bool(audio.get("runtimeAudio")),
                "speechSynthesis": bool(audio.get("speechSynthesis")),
                "voiceCount": audio.get("voiceCount") if isinstance(audio.get("voiceCount"), int) else 0,
            },
        }

    def _audio_playback_state(self, status: str, payload: dict[str, Any], timestamp: str) -> dict[str, Any]:
        return {
            "status": status,
            "source": payload.get("source") if isinstance(payload.get("source"), str) else None,
            "audioUrl": payload.get("audioUrl") if isinstance(payload.get("audioUrl"), str) else None,
            "reason": payload.get("reason") if isinstance(payload.get("reason"), str) else None,
            "updatedAt": timestamp,
        }

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]
