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


@dataclass
class Live2DHarness:
    enabled: bool = True
    adapter: str = "desktop-live2d"
    model_id: str = "default"
    model_path: str = ""
    state_behaviors: dict[str, dict[str, Any]] = field(default_factory=lambda: dict(DEFAULT_STATE_BEHAVIORS))

    name: str = "live2d"

    def capabilities(self) -> HarnessCapability:
        return HarnessCapability(
            name=self.name,
            version="0.1",
            events_in=["assistant.state"],
            events_out=["character.behavior"],
        )

    def observe_event(self, context: HarnessContext, event: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.enabled or event.get("type") != "assistant.state":
            return []

        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        state = payload.get("state")
        if not isinstance(state, str):
            return []

        behavior = self.state_behaviors.get(state)
        if not behavior:
            return []

        return [{"type": "character.behavior", "payload": dict(behavior)}]
