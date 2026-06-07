from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote


SUPPORTED_AUDIO_SUFFIXES = {".wav", ".mp3", ".ogg", ".m4a", ".aac", ".flac"}


@dataclass(frozen=True)
class AudioOutputCommand:
    text: str
    voice: str | None = None
    format: str = "wav"


@dataclass(frozen=True)
class AudioOutputResult:
    audio_url: str
    duration_ms: int | None = None
    provider: str = "unknown"


@dataclass(frozen=True)
class AudioFallbackResult:
    reason: str
    fallback: str = "speechSynthesis"


class TtsProvider(Protocol):
    name: str

    def synthesize(self, command: AudioOutputCommand) -> AudioOutputResult | None:
        ...


class NoopTtsProvider:
    name = "none"

    def synthesize(self, command: AudioOutputCommand) -> AudioOutputResult | None:
        return None


class LocalAudioLibrary:
    def __init__(self, root_dir: Path, public_base_url: str) -> None:
        self.root_dir = root_dir.resolve()
        self.public_base_url = public_base_url.rstrip("/")
        self.voices_dir = self.root_dir / "voices"
        self.sfx_dir = self.root_dir / "sfx"
        self.cache_dir = self.root_dir / "cache"

        for path in (self.voices_dir, self.sfx_dir, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)

    def resolve_public_path(self, relative_path: str) -> Path | None:
        candidate = (self.root_dir / relative_path).resolve()
        if not candidate.is_file() or not self._is_inside(candidate, self.root_dir):
            return None

        if candidate.suffix.lower() not in SUPPORTED_AUDIO_SUFFIXES:
            return None

        return candidate

    def public_url(self, file_path: Path) -> str:
        relative = file_path.resolve().relative_to(self.root_dir).as_posix()
        return f"{self.public_base_url}/audio/files/{quote(relative)}"

    @staticmethod
    def _is_inside(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False


class AudioRuntime:
    def __init__(self, library: LocalAudioLibrary, tts_provider: TtsProvider | None = None) -> None:
        self.library = library
        self.tts_provider = tts_provider or NoopTtsProvider()

    def speak(self, command: AudioOutputCommand) -> AudioOutputResult | AudioFallbackResult:
        normalized_text = command.text.strip()
        if not normalized_text:
            return AudioFallbackResult(reason="empty_text")

        result = self.tts_provider.synthesize(command)
        if result:
            return result

        return AudioFallbackResult(reason=f"tts_provider_unavailable:{self.tts_provider.name}")
