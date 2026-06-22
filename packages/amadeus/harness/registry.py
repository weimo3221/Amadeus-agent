from __future__ import annotations

from pathlib import Path
from typing import Any

from amadeus.harness.base import Harness, HarnessCapability, HarnessContext
from amadeus.harness.live2d import Live2DHarness

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_HARNESSES_CONFIG_PATH = REPO_ROOT / "configs" / "harnesses.yaml"


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

    config: dict[str, dict[str, Any]] = {}
    in_harnesses = False
    current_harness: str | None = None
    current_nested: str | None = None

    for raw_line in lines:
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        trimmed = line.strip()
        if indent == 0:
            in_harnesses = trimmed == "harnesses:"
            current_harness = None
            current_nested = None
            continue

        if not in_harnesses:
            continue

        if indent == 2 and trimmed.endswith(":"):
            current_harness = trimmed[:-1]
            current_nested = None
            config[current_harness] = {}
            continue

        if indent == 4 and current_harness and trimmed.endswith(":"):
            current_nested = trimmed[:-1]
            config[current_harness][current_nested] = {}
            continue

        if ":" not in trimmed or not current_harness:
            continue

        key, raw_value = trimmed.split(":", 1)
        target: dict[str, Any]
        if indent == 6 and current_nested and isinstance(config[current_harness].get(current_nested), dict):
            target = config[current_harness][current_nested]
        elif indent == 4:
            current_nested = None
            target = config[current_harness]
        else:
            continue

        target[key.strip()] = parse_scalar_config_value(raw_value.strip())

    return config


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
