from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from amadeus.harness.live2d import DEFAULT_AUDIO_PLAYBACK_BEHAVIORS, DEFAULT_STATE_BEHAVIORS
from amadeus.harness.registry import (
    DEFAULT_HARNESSES_CONFIG_PATH,
    merge_behavior_config,
    parse_harnesses_config,
)


AUDIO_PLAYBACK_SHORT_DEFAULTS: dict[str, dict[str, Any]] = {
    "started": dict(DEFAULT_AUDIO_PLAYBACK_BEHAVIORS["audio.playback-started"]),
    "ended": dict(DEFAULT_AUDIO_PLAYBACK_BEHAVIORS["audio.playback-ended"]),
    "error": dict(DEFAULT_AUDIO_PLAYBACK_BEHAVIORS["audio.playback-error"]),
}


SUPPORTED_LIVE2D_SUFFIXES = {
    ".json",
    ".moc3",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".wav",
    ".mp3",
}


@dataclass(frozen=True)
class Live2DCommand:
    state: str | None = None
    expression: str | None = None
    motion: str | None = None
    intensity: float | None = None


@dataclass(frozen=True)
class LipsyncCue:
    offset_ms: int
    mouth_open: float


@dataclass(frozen=True)
class Live2DModelSelection:
    model_id: str
    relative_path: str


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


class LocalLive2DModelLibrary:
    def __init__(
        self,
        root_dir: Path,
        public_base_url: str,
        config_path: Path = DEFAULT_HARNESSES_CONFIG_PATH,
    ) -> None:
        self.root_dir = root_dir.resolve()
        self.public_base_url = public_base_url.rstrip("/")
        self.config_path = config_path
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def configured_model(self, config_path: Path | None = None) -> Live2DModelSelection | None:
        config = parse_harnesses_config(config_path or self.config_path)
        live2d_config = config.get("live2d", {})
        model_config = live2d_config.get("model") if isinstance(live2d_config.get("model"), dict) else {}
        model_id = str(model_config.get("id") or "default")
        model_path = str(model_config.get("path") or "")
        if not model_path:
            return self.find_model(model_id)

        normalized = self.normalize_model_path(model_path)
        if self.resolve_public_path(normalized):
            return Live2DModelSelection(model_id=model_id, relative_path=normalized)

        return None

    def find_model(self, model_id: str) -> Live2DModelSelection | None:
        model_dir = self.root_dir / model_id
        if not model_dir.is_dir():
            return None

        candidates = sorted(model_dir.glob("*.model3.json"))
        if not candidates:
            candidates = sorted(model_dir.rglob("*.model3.json"))
        if not candidates:
            return None

        relative = candidates[0].resolve().relative_to(self.root_dir).as_posix()
        return Live2DModelSelection(model_id=model_id, relative_path=relative)

    def list_models(self) -> list[dict[str, Any]]:
        active = self.configured_model()
        if not self.root_dir.is_dir():
            return []

        models: list[dict[str, Any]] = []
        for entry in sorted(self.root_dir.iterdir(), key=lambda candidate: candidate.name):
            if not entry.is_dir():
                continue
            selection = self.find_model(entry.name)
            if not selection:
                continue
            models.append({
                "id": selection.model_id,
                "path": selection.relative_path,
                "url": self.model_url(selection),
                "active": bool(
                    active
                    and active.model_id == selection.model_id
                    and active.relative_path == selection.relative_path
                ),
                "manifest": self.read_manifest(selection.relative_path),
            })
        return models

    def select_model(self, model_id: str) -> Live2DModelSelection | None:
        normalized_model_id = model_id.strip()
        if not normalized_model_id or not all(character.isalnum() or character in "._-" for character in normalized_model_id):
            return None

        selection = self.find_model(normalized_model_id)
        if not selection:
            return None

        self.persist_configured_model(selection)
        return selection

    def import_model(self, source_dir: str, *, model_id: str | None = None) -> Live2DModelSelection:
        source = Path(source_dir).expanduser()
        if not source.exists() or not source.is_dir():
            raise ValueError(f"source directory not found: {source_dir}")

        model_files = sorted(source.rglob("*.model3.json"))
        if not model_files:
            raise ValueError("no *.model3.json found in source directory")

        resolved_id = (model_id or source.name).strip()
        if not resolved_id or not all(character.isalnum() or character in "._-" for character in resolved_id):
            raise ValueError("invalid model id derived from source directory")

        destination = (self.root_dir / resolved_id).resolve()
        if not self._is_inside(destination, self.root_dir):
            raise ValueError("resolved destination escapes the models directory")
        if destination.exists():
            raise ValueError(f"model '{resolved_id}' already exists")

        copied = 0
        for entry in sorted(source.rglob("*")):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in SUPPORTED_LIVE2D_SUFFIXES:
                continue
            relative = entry.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, target)
            copied += 1

        if copied == 0:
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            raise ValueError("no supported Live2D asset files were imported")

        selection = self.find_model(resolved_id)
        if not selection:
            shutil.rmtree(destination, ignore_errors=True)
            raise ValueError("imported model is missing a usable *.model3.json entry")

        return selection

    def audio_playback_behaviors(self, config_path: Path | None = None) -> dict[str, dict[str, Any]]:
        config = parse_harnesses_config(config_path or self.config_path)
        live2d_config = config.get("live2d", {}) if isinstance(config.get("live2d"), dict) else {}
        merged = merge_behavior_config(
            AUDIO_PLAYBACK_SHORT_DEFAULTS,
            live2d_config.get("audioPlaybackBehaviors"),
        )
        return merged

    def default_state_behaviors(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in DEFAULT_STATE_BEHAVIORS.items()}

    def persist_audio_playback_behaviors(self, behaviors: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        allowed_states = set(AUDIO_PLAYBACK_SHORT_DEFAULTS)
        normalized: dict[str, dict[str, Any]] = {}
        for state, behavior in behaviors.items():
            if state not in allowed_states or not isinstance(behavior, dict):
                continue
            entry: dict[str, Any] = {}
            for key in ("emotion", "expression", "motion"):
                value = behavior.get(key)
                if isinstance(value, str) and value.strip():
                    entry[key] = value.strip()
            intensity = behavior.get("intensity")
            if isinstance(intensity, (int, float)) and not isinstance(intensity, bool):
                entry["intensity"] = round(float(max(0.0, min(1.0, intensity))), 3)
            if entry:
                normalized[state] = entry

        merged = merge_behavior_config(AUDIO_PLAYBACK_SHORT_DEFAULTS, normalized)

        try:
            current = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else ""
        except OSError:
            current = ""
        next_content = self._update_harness_audio_behaviors_config(current, merged)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(next_content, encoding="utf-8")
        return merged

    def _render_audio_behaviors_lines(self, behaviors: dict[str, dict[str, Any]]) -> list[str]:
        lines = ["    audioPlaybackBehaviors:"]
        for state in ("started", "ended", "error"):
            behavior = behaviors.get(state)
            if not behavior:
                continue
            lines.append(f"      {state}:")
            for key in ("emotion", "expression", "motion", "intensity"):
                if key not in behavior:
                    continue
                lines.append(f"        {key}: {behavior[key]}")
        return lines

    def _update_harness_audio_behaviors_config(self, content: str, behaviors: dict[str, dict[str, Any]]) -> str:
        behavior_lines = self._render_audio_behaviors_lines(behaviors)
        if not content.strip():
            selection = self.configured_model()
            model_id = selection.model_id if selection else "default"
            model_path = selection.relative_path if selection else ""
            return "\n".join([
                "harnesses:",
                "  live2d:",
                "    enabled: true",
                "    adapter: desktop-live2d",
                "    model:",
                f"      id: {model_id}",
                f"      path: {model_path}",
                *behavior_lines,
                "",
            ])

        lines = content.splitlines()
        in_harnesses = False
        in_live2d = False
        block_start: int | None = None
        block_end: int | None = None

        for index, raw_line in enumerate(lines):
            stripped_full = raw_line.split("#", 1)[0].rstrip()
            trimmed = stripped_full.strip()
            if not trimmed:
                continue
            indent = len(stripped_full) - len(stripped_full.lstrip(" "))

            if indent == 0:
                in_harnesses = trimmed == "harnesses:"
                in_live2d = False
                continue
            if not in_harnesses:
                continue
            if indent == 2:
                in_live2d = trimmed == "live2d:"
                continue
            if not in_live2d:
                continue

            if indent == 4 and trimmed.startswith("audioPlaybackBehaviors:"):
                block_start = index
                block_end = len(lines)
                for follow in range(index + 1, len(lines)):
                    follow_line = lines[follow].split("#", 1)[0].rstrip()
                    if not follow_line.strip():
                        continue
                    follow_indent = len(follow_line) - len(follow_line.lstrip(" "))
                    if follow_indent <= 4:
                        block_end = follow
                        break
                break

        if block_start is not None and block_end is not None:
            next_lines = lines[:block_start] + behavior_lines + lines[block_end:]
            return "\n".join(next_lines).rstrip("\n") + "\n"

        # No existing block: insert after the live2d model block (or end of live2d section).
        insert_at = len(lines)
        in_harnesses = False
        in_live2d = False
        for index, raw_line in enumerate(lines):
            stripped_full = raw_line.split("#", 1)[0].rstrip()
            trimmed = stripped_full.strip()
            if not trimmed:
                continue
            indent = len(stripped_full) - len(stripped_full.lstrip(" "))
            if indent == 0:
                if in_live2d:
                    insert_at = index
                    break
                in_harnesses = trimmed == "harnesses:"
                in_live2d = False
                continue
            if not in_harnesses:
                continue
            if indent == 2:
                if in_live2d:
                    insert_at = index
                    break
                in_live2d = trimmed == "live2d:"

        next_lines = lines[:insert_at] + behavior_lines + lines[insert_at:]
        return "\n".join(next_lines).rstrip("\n") + "\n"

    def model_url(self, selection: Live2DModelSelection) -> str:
        return f"{self.public_base_url}/live2d/models/{quote(selection.relative_path)}"

    def resolve_public_path(self, relative_path: str) -> Path | None:
        normalized = self.normalize_model_path(relative_path)
        candidate = (self.root_dir / normalized).resolve()
        if not candidate.is_file() or not self._is_inside(candidate, self.root_dir):
            return None

        if candidate.suffix.lower() not in SUPPORTED_LIVE2D_SUFFIXES:
            return None

        return candidate

    def normalize_model_path(self, path: str) -> str:
        normalized = path.replace("\\", "/").lstrip("/")
        prefix = "models/live2d/"
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
        return normalized

    def read_manifest(self, relative_model_path: str) -> dict[str, Any] | None:
        model_dir = (self.root_dir / self.normalize_model_path(relative_model_path)).resolve().parent
        candidates = [
            model_dir / "manifest.json",
            model_dir / "manifest.yaml",
            model_dir / "manifest.yml",
        ]
        manifest_path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if manifest_path is None:
            return None

        try:
            content = manifest_path.read_text(encoding="utf-8")
        except OSError:
            return None

        try:
            parsed = json.loads(content) if manifest_path.suffix.lower() == ".json" else self._parse_manifest_yaml(content)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return self._normalize_manifest(parsed)

    def persist_configured_model(self, selection: Live2DModelSelection) -> None:
        try:
            current = self.config_path.read_text(encoding="utf-8") if self.config_path.exists() else ""
        except OSError:
            current = ""
        next_content = self._update_harness_live2d_model_config(
            current,
            selection.model_id,
            selection.relative_path,
        )
        self.config_path.write_text(next_content, encoding="utf-8")

    def _update_harness_live2d_model_config(self, content: str, model_id: str, model_path: str) -> str:
        if not content.strip():
            return "\n".join([
                "harnesses:",
                "  live2d:",
                "    enabled: true",
                "    adapter: desktop-live2d",
                "    model:",
                f"      id: {model_id}",
                f"      path: {model_path}",
                "",
            ])

        lines = content.splitlines()
        in_harnesses = False
        in_live2d = False
        in_model = False
        saw_id = False
        saw_path = False

        for index, raw_line in enumerate(lines):
            line = raw_line.split("#", 1)[0].rstrip()
            trimmed = line.strip()
            if not trimmed:
                continue

            indent = len(line) - len(line.lstrip(" "))
            if indent == 0:
                in_harnesses = trimmed == "harnesses:"
                in_live2d = False
                in_model = False
                continue
            if not in_harnesses:
                continue

            if indent == 2:
                in_live2d = trimmed == "live2d:"
                in_model = False
                continue
            if not in_live2d:
                continue

            if indent == 4:
                in_model = trimmed == "model:"
                continue

            if indent == 6 and in_model and ":" in trimmed:
                key = trimmed.split(":", 1)[0]
                if key == "id":
                    lines[index] = f"      id: {model_id}"
                    saw_id = True
                elif key == "path":
                    lines[index] = f"      path: {model_path}"
                    saw_path = True

        if saw_id and saw_path:
            return "\n".join(lines).rstrip("\n") + "\n"

        return "\n".join([
            "harnesses:",
            "  live2d:",
            "    enabled: true",
            "    adapter: desktop-live2d",
            "    model:",
            f"      id: {model_id}",
            f"      path: {model_path}",
            "",
        ])

    def _normalize_manifest(self, value: Any) -> dict[str, Any] | None:
        if not _is_record(value):
            return None

        manifest: dict[str, Any] = {}
        display_name = value.get("displayName")
        if isinstance(display_name, str) and display_name.strip():
            manifest["displayName"] = display_name

        defaults = value.get("defaults")
        if _is_record(defaults):
            normalized_defaults: dict[str, str] = {}
            expression = defaults.get("expression")
            motion = defaults.get("motion")
            if isinstance(expression, str) and expression.strip():
                normalized_defaults["expression"] = expression
            if isinstance(motion, str) and motion.strip():
                normalized_defaults["motion"] = motion
            if normalized_defaults:
                manifest["defaults"] = normalized_defaults

        aliases = value.get("aliases")
        if _is_record(aliases):
            normalized_aliases: dict[str, dict[str, list[str]]] = {}
            expressions = self._normalize_alias_map(aliases.get("expressions"))
            motions = self._normalize_alias_map(aliases.get("motions"))
            if expressions:
                normalized_aliases["expressions"] = expressions
            if motions:
                normalized_aliases["motions"] = motions
            if normalized_aliases:
                manifest["aliases"] = normalized_aliases

        return manifest or None

    def _normalize_alias_map(self, value: Any) -> dict[str, list[str]] | None:
        if not _is_record(value):
            return None

        aliases: dict[str, list[str]] = {}
        for key, raw_aliases in value.items():
            if not isinstance(key, str) or not key.strip():
                continue
            values: list[str] = []
            if isinstance(raw_aliases, list):
                values = [entry for entry in raw_aliases if isinstance(entry, str) and entry.strip()]
            elif isinstance(raw_aliases, str):
                values = [entry.strip() for entry in raw_aliases.split(",") if entry.strip()]
            if values:
                aliases[key] = values
        return aliases or None

    def _parse_manifest_yaml(self, content: str) -> dict[str, Any]:
        root: dict[str, Any] = {}
        section: str | None = None
        subsection: str | None = None

        for raw_line in content.splitlines():
            line = raw_line.split("#", 1)[0].rstrip()
            if not line.strip() or ":" not in line.strip():
                continue

            indent = len(line) - len(line.lstrip(" "))
            trimmed = line.strip()
            key, raw_value = trimmed.split(":", 1)
            key = key.strip()
            value = raw_value.strip()

            if indent == 0:
                section = key
                subsection = None
                root[section] = self._parse_manifest_scalar(value) if value else {}
                continue

            section_value = root.get(section) if section else None
            if not section or not _is_record(section_value):
                continue

            if indent == 2:
                subsection = key
                section_value[subsection] = self._parse_manifest_scalar(value) if value else {}
                continue

            subsection_value = section_value.get(subsection) if subsection else None
            if indent == 4 and subsection and _is_record(subsection_value):
                subsection_value[key] = self._parse_manifest_scalar(value)

        return root

    def _parse_manifest_scalar(self, value: str) -> str | list[str]:
        parsed = value
        if (parsed.startswith('"') and parsed.endswith('"')) or (parsed.startswith("'") and parsed.endswith("'")):
            parsed = parsed[1:-1]
        if parsed.startswith("[") and parsed.endswith("]"):
            return [
                item
                for item in (entry.strip().strip('"').strip("'") for entry in parsed[1:-1].split(","))
                if item
            ]
        return parsed

    @staticmethod
    def _is_inside(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False
