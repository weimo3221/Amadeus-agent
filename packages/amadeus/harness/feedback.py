from __future__ import annotations

import logging
import threading
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)

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
        client_id: str | None = None,
        surface: str | None = None,
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
                "clientId": client_id,
                "surface": surface,
            }
            events = self._events_by_session.setdefault(session_id, deque(maxlen=self.event_limit))
            events.append(event_record)

            state["lastFeedbackAt"] = observed_at
            state["lastFeedbackType"] = event_type
            if event_type == "desktop.capabilities":
                client_key = self._client_key(client_id, surface)
                previous_client_count = len(state["desktopCapabilitiesByClient"])
                capabilities = self._normalize_desktop_capabilities(payload)
                capabilities["clientId"] = client_id
                capabilities["surface"] = surface
                state["desktopCapabilitiesByClient"][client_key] = capabilities
                state["desktopCapabilities"] = self._aggregate_desktop_capabilities(state["desktopCapabilitiesByClient"])
                aggregate = state["desktopCapabilities"]
                logger.info(
                    "Recorded client capabilities sessionId=%s clientId=%s surface=%s clientKey=%s replaced=%s previousClientCount=%s nextClientCount=%s clientSummary=%s aggregateSummary=%s",
                    session_id,
                    client_id,
                    surface,
                    client_key,
                    client_key in state["desktopCapabilitiesByClient"] and len(state["desktopCapabilitiesByClient"]) == previous_client_count,
                    previous_client_count,
                    len(state["desktopCapabilitiesByClient"]),
                    self._capabilities_summary(capabilities),
                    self._capabilities_summary(aggregate),
                )
            elif event_type == "audio.playback-started":
                state["audioPlayback"] = self._audio_playback_state("playing", payload, observed_at, client_id, surface)
                logger.info(
                    "Recorded audio playback feedback sessionId=%s clientId=%s surface=%s status=playing source=%s audioUrlPresent=%s",
                    session_id,
                    client_id,
                    surface,
                    state["audioPlayback"].get("source"),
                    bool(state["audioPlayback"].get("audioUrl")),
                )
            elif event_type == "audio.playback-ended":
                state["audioPlayback"] = self._audio_playback_state("idle", payload, observed_at, client_id, surface)
                logger.info(
                    "Recorded audio playback feedback sessionId=%s clientId=%s surface=%s status=idle source=%s audioUrlPresent=%s",
                    session_id,
                    client_id,
                    surface,
                    state["audioPlayback"].get("source"),
                    bool(state["audioPlayback"].get("audioUrl")),
                )
            elif event_type == "audio.playback-error":
                state["audioPlayback"] = self._audio_playback_state("error", payload, observed_at, client_id, surface)
                logger.info(
                    "Recorded audio playback feedback sessionId=%s clientId=%s surface=%s status=error source=%s reason=%s audioUrlPresent=%s",
                    session_id,
                    client_id,
                    surface,
                    state["audioPlayback"].get("source"),
                    state["audioPlayback"].get("reason"),
                    bool(state["audioPlayback"].get("audioUrl")),
                )

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
            "desktopCapabilitiesByClient": {},
            "audioPlayback": {
                "status": "unknown",
                "source": None,
                "audioUrl": None,
                "reason": None,
                "updatedAt": None,
                "clientId": None,
                "surface": None,
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

    def _aggregate_desktop_capabilities(self, capabilities_by_client: dict[str, Any]) -> dict[str, Any]:
        clients = [capabilities for capabilities in capabilities_by_client.values() if isinstance(capabilities, dict)]
        live2d_clients = [
            capabilities
            for capabilities in clients
            if isinstance(capabilities.get("live2d"), dict) and bool(capabilities["live2d"].get("available"))
        ]
        audio_clients = [capabilities for capabilities in clients if isinstance(capabilities.get("audio"), dict)]
        desktop_clients = [capabilities for capabilities in clients if isinstance(capabilities.get("desktop"), dict)]
        selected_live2d = live2d_clients[-1] if live2d_clients else None

        return {
            "desktop": {
                "runtime": "mixed" if len({capabilities["desktop"].get("runtime") for capabilities in desktop_clients}) > 1 else (
                    str(desktop_clients[-1]["desktop"].get("runtime") or "unknown") if desktop_clients else "unknown"
                ),
                "protocolVersion": max(
                    [
                        capabilities["desktop"].get("protocolVersion")
                        for capabilities in desktop_clients
                        if isinstance(capabilities["desktop"].get("protocolVersion"), int)
                    ],
                    default=None,
                ),
                "clientCount": len(clients),
            },
            "live2d": {
                "available": bool(live2d_clients),
                "modelId": (
                    selected_live2d["live2d"].get("modelId")
                    if selected_live2d and isinstance(selected_live2d["live2d"].get("modelId"), str)
                    else None
                ),
                "expressions": self._unique_strings(
                    item
                    for capabilities in live2d_clients
                    for item in capabilities["live2d"].get("expressions", [])
                ),
                "motions": self._unique_strings(
                    item
                    for capabilities in live2d_clients
                    for item in capabilities["live2d"].get("motions", [])
                ),
            },
            "audio": {
                "runtimeAudio": any(bool(capabilities["audio"].get("runtimeAudio")) for capabilities in audio_clients),
                "speechSynthesis": any(bool(capabilities["audio"].get("speechSynthesis")) for capabilities in audio_clients),
                "voiceCount": sum(
                    capabilities["audio"].get("voiceCount", 0)
                    for capabilities in audio_clients
                    if isinstance(capabilities["audio"].get("voiceCount"), int)
                ),
            },
        }

    def _capabilities_summary(self, capabilities: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(capabilities, dict):
            return {}
        desktop = capabilities.get("desktop") if isinstance(capabilities.get("desktop"), dict) else {}
        live2d = capabilities.get("live2d") if isinstance(capabilities.get("live2d"), dict) else {}
        audio = capabilities.get("audio") if isinstance(capabilities.get("audio"), dict) else {}
        return {
            "clientId": capabilities.get("clientId"),
            "surface": capabilities.get("surface"),
            "clientCount": desktop.get("clientCount"),
            "runtime": desktop.get("runtime"),
            "protocolVersion": desktop.get("protocolVersion"),
            "live2dAvailable": live2d.get("available"),
            "live2dModelId": live2d.get("modelId"),
            "live2dExpressionCount": len(live2d.get("expressions")) if isinstance(live2d.get("expressions"), list) else 0,
            "live2dMotionCount": len(live2d.get("motions")) if isinstance(live2d.get("motions"), list) else 0,
            "runtimeAudio": audio.get("runtimeAudio"),
            "speechSynthesis": audio.get("speechSynthesis"),
            "voiceCount": audio.get("voiceCount"),
        }

    def _audio_playback_state(self, status: str, payload: dict[str, Any], timestamp: str, client_id: str | None, surface: str | None) -> dict[str, Any]:
        return {
            "status": status,
            "source": payload.get("source") if isinstance(payload.get("source"), str) else None,
            "audioUrl": payload.get("audioUrl") if isinstance(payload.get("audioUrl"), str) else None,
            "reason": payload.get("reason") if isinstance(payload.get("reason"), str) else None,
            "updatedAt": timestamp,
            "clientId": client_id,
            "surface": surface,
        }

    def _client_key(self, client_id: str | None, surface: str | None) -> str:
        if client_id:
            return client_id
        if surface:
            return f"surface:{surface}"
        return "default"

    def _unique_strings(self, values: Any) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str) or value in seen:
                continue
            seen.add(value)
            unique.append(value)
        return unique

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]
