from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from amadeus.audio import LocalAudioLibrary
from amadeus.harness.base import HarnessCapability, HarnessContext


DEFAULT_STATE_BEHAVIORS: dict[str, dict[str, Any]] = {
    "idle": {"emotion": "neutral", "expression": "neutral", "motion": "idle", "intensity": 0.4},
    "thinking": {"emotion": "focused", "expression": "serious", "motion": "think", "intensity": 0.6},
    "speaking": {"emotion": "neutral", "expression": "smile", "motion": "talk", "intensity": 0.5},
    "tool-running": {"emotion": "focused", "expression": "serious", "motion": "think", "intensity": 0.65},
    "error": {"emotion": "confused", "expression": "confused", "motion": "shake_head", "intensity": 0.75},
}

DEFAULT_AUDIO_PLAYBACK_BEHAVIORS: dict[str, dict[str, Any]] = {
    "audio.playback-started": {"emotion": "neutral", "expression": "smile", "motion": "talk", "intensity": 0.65},
    "audio.playback-ended": {"emotion": "neutral", "expression": "neutral", "motion": "idle", "intensity": 0.35},
    "audio.playback-error": {"emotion": "confused", "expression": "confused", "motion": "shake_head", "intensity": 0.55},
}


@dataclass
class Live2DHarness:
    enabled: bool = True
    adapter: str = "desktop-live2d"
    model_id: str = "default"
    model_path: str = ""
    state_behaviors: dict[str, dict[str, Any]] = field(default_factory=lambda: dict(DEFAULT_STATE_BEHAVIORS))
    audio_playback_behaviors: dict[str, dict[str, Any]] = field(default_factory=lambda: dict(DEFAULT_AUDIO_PLAYBACK_BEHAVIORS))
    lipsync_enabled: bool = True
    lipsync_cue_interval_ms: int = 90
    lipsync_max_cues: int = 48
    audio_library: LocalAudioLibrary | None = None

    name: str = "live2d"

    def capabilities(self) -> HarnessCapability:
        return HarnessCapability(
            name=self.name,
            version="0.1",
            events_in=[
                "assistant.state",
                "audio.playback-started",
                "audio.playback-ended",
                "audio.playback-error",
            ],
            events_out=["character.behavior", "audio.lipsync-cues"],
        )

    def observe_event(self, context: HarnessContext, event: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.enabled:
            return []

        event_type = event.get("type")
        if event_type == "assistant.state":
            return self._behavior_for_assistant_state(event)
        if event_type in self.audio_playback_behaviors:
            return self._events_for_audio_playback(context, event_type, event)
        return []

    def _behavior_for_assistant_state(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        state = payload.get("state")
        if not isinstance(state, str):
            return []

        behavior = self.state_behaviors.get(state)
        if not behavior:
            return []

        return [{"type": "character.behavior", "payload": dict(behavior)}]

    def _events_for_audio_playback(
        self,
        context: HarnessContext,
        event_type: str,
        event: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not self._live2d_available(context):
            return []

        emitted: list[dict[str, Any]] = []
        behavior = self.audio_playback_behaviors.get(event_type)
        if behavior:
            emitted.append({"type": "character.behavior", "payload": dict(behavior)})

        if event_type == "audio.playback-started":
            lipsync_event = self._lipsync_for_audio_playback(event)
            if lipsync_event:
                emitted.append(lipsync_event)

        return emitted

    def _lipsync_for_audio_playback(self, event: dict[str, Any]) -> dict[str, Any] | None:
        if not self.lipsync_enabled:
            return None

        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if payload.get("source") != "runtime_audio":
            return None

        duration_ms = payload.get("durationMs")
        cues: list[dict[str, float | int]] = []
        resolved_duration_ms: int | None = None
        audio_url = payload.get("audioUrl") if isinstance(payload.get("audioUrl"), str) else None
        if audio_url and self.audio_library is not None:
            resolved_duration_ms, cues = self.audio_library.lipsync_cues_for_audio_url(
                audio_url,
                cue_interval_ms=self.lipsync_cue_interval_ms,
                max_cues=self.lipsync_max_cues,
            )

        if not cues:
            if not isinstance(duration_ms, (int, float)) or duration_ms <= 0:
                return None
            resolved_duration_ms = int(duration_ms)
            cues = self._build_lipsync_cues(resolved_duration_ms)

        if not cues:
            return None

        return {
            "type": "audio.lipsync-cues",
            "payload": {
                "source": "runtime_audio",
                "audioUrl": audio_url,
                "durationMs": resolved_duration_ms,
                "cues": cues,
            },
        }

    def _build_lipsync_cues(self, duration_ms: int) -> list[dict[str, float | int]]:
        interval_ms = max(50, self.lipsync_cue_interval_ms)
        max_cues = max(1, self.lipsync_max_cues)
        cue_count = max(1, min(max_cues, duration_ms // interval_ms))
        pattern = (0.18, 0.78, 0.34, 0.64, 0.22, 0.56)
        cues: list[dict[str, float | int]] = []
        for index in range(cue_count):
            offset_ms = min(duration_ms, index * interval_ms)
            cues.append({
                "offsetMs": offset_ms,
                "mouthOpen": pattern[index % len(pattern)],
            })
        if int(cues[-1]["offsetMs"]) < duration_ms:
            cues.append({"offsetMs": duration_ms, "mouthOpen": 0.0})
        return cues

    def _live2d_available(self, context: HarnessContext) -> bool:
        capabilities = context.client_capabilities
        live2d = capabilities.get("live2d") if isinstance(capabilities.get("live2d"), dict) else None
        if live2d is None:
            return True
        return bool(live2d.get("available"))
