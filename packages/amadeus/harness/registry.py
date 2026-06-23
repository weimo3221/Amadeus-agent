from __future__ import annotations

from pathlib import Path
from typing import Any

from amadeus.harness.base import Harness, HarnessCapability, HarnessContext
from amadeus.harness.live2d import DEFAULT_AUDIO_PLAYBACK_BEHAVIORS, DEFAULT_STATE_BEHAVIORS, Live2DHarness

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HARNESSES_CONFIG_PATH = REPO_ROOT / "configs" / "harnesses.yaml"

AUDIO_PLAYBACK_BEHAVIOR_ALIASES = {
    "started": "audio.playback-started",
    "ended": "audio.playback-ended",
    "error": "audio.playback-error",
}


class HarnessRegistry:
    def __init__(self, harnesses: list[Harness] | None = None) -> None:
        self.harnesses = harnesses or []

    @classmethod
    def from_config(cls, config_path: Path = DEFAULT_HARNESSES_CONFIG_PATH) -> "HarnessRegistry":
        config = parse_harnesses_config(config_path)
        harnesses: list[Harness] = []
        live2d_config = config.get("live2d", {})
        if live2d_config.get("enabled", True):
            model_config = live2d_config.get("model") if isinstance(live2d_config.get("model"), dict) else {}
            harnesses.append(Live2DHarness(
                enabled=True,
                adapter=str(live2d_config.get("adapter") or "desktop-live2d"),
                model_id=str(model_config.get("id") or "default"),
                model_path=str(model_config.get("path") or ""),
                state_behaviors=merge_behavior_config(
                    DEFAULT_STATE_BEHAVIORS,
                    live2d_config.get("stateBehaviors"),
                ),
                audio_playback_behaviors=merge_behavior_config(
                    DEFAULT_AUDIO_PLAYBACK_BEHAVIORS,
                    live2d_config.get("audioPlaybackBehaviors"),
                    aliases=AUDIO_PLAYBACK_BEHAVIOR_ALIASES,
                ),
            ))
        return cls(harnesses)

    def capabilities(self) -> list[HarnessCapability]:
        return [harness.capabilities() for harness in self.harnesses]

    def observe_event(self, context: HarnessContext, event: dict[str, Any]) -> list[dict[str, Any]]:
        emitted: list[dict[str, Any]] = []
        for harness in self.harnesses:
            emitted.extend(harness.observe_event(context, event))
        return emitted


def parse_harnesses_config(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for raw_line in lines:
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        trimmed = line.strip()
        if ":" not in trimmed:
            continue

        key, raw_value = trimmed.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root

        if raw_value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = parse_scalar_config_value(raw_value)

    harnesses = root.get("harnesses")
    if isinstance(harnesses, dict):
        return harnesses

    return {}


def merge_behavior_config(
    defaults: dict[str, dict[str, Any]],
    configured: Any,
    *,
    aliases: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    behaviors = {key: dict(value) for key, value in defaults.items()}
    if not isinstance(configured, dict):
        return behaviors

    for raw_key, raw_behavior in configured.items():
        if not isinstance(raw_key, str) or not isinstance(raw_behavior, dict):
            continue
        behavior = {
            key: value
            for key, value in raw_behavior.items()
            if key in {"emotion", "expression", "motion", "intensity"}
        }
        if not behavior:
            continue
        behavior_key = aliases.get(raw_key, raw_key) if aliases else raw_key
        previous = behaviors.get(behavior_key, {})
        behaviors[behavior_key] = {**previous, **behavior}
    return behaviors


def parse_scalar_config_value(value: str) -> Any:
    if value == "":
        return ""
    if value in {"true", "false"}:
        return value == "true"
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
