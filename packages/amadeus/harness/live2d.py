from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
            events_out=["character.behavior"],
        )

    def observe_event(self, context: HarnessContext, event: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.enabled:
            return []

        event_type = event.get("type")
        if event_type == "assistant.state":
            return self._behavior_for_assistant_state(event)
        if event_type in self.audio_playback_behaviors:
            return self._behavior_for_audio_playback(context, event_type)
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

    def _behavior_for_audio_playback(self, context: HarnessContext, event_type: str) -> list[dict[str, Any]]:
        if not self._live2d_available(context):
            return []

        behavior = self.audio_playback_behaviors.get(event_type)
        if not behavior:
            return []
        return [{"type": "character.behavior", "payload": dict(behavior)}]

    def _live2d_available(self, context: HarnessContext) -> bool:
        capabilities = context.client_capabilities
        live2d = capabilities.get("live2d") if isinstance(capabilities.get("live2d"), dict) else None
        if live2d is None:
            return True
        return bool(live2d.get("available"))
