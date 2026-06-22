from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from amadeus.harness.registry import DEFAULT_HARNESSES_CONFIG_PATH, parse_harnesses_config


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

    @staticmethod
    def _is_inside(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False
