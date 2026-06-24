from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import wave
import urllib.error
import urllib.request
from audioop import rms, tomono
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol
from urllib.parse import quote, unquote, urlparse

from amadeus.model import DEFAULT_PROVIDERS_CONFIG_PATH, parse_bool_value, parse_providers_config


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


@dataclass(frozen=True)
class GptSovitsConfig:
    base_url: str
    endpoint: str = "/tts"
    text_lang: str = "auto"
    prompt_lang: str = "auto"
    prompt_text: str = ""
    ref_audio_path: str = ""
    timeout_seconds: int = 60
    streaming_mode: bool = False


@dataclass(frozen=True)
class MacOsSayConfig:
    voice: str = ""
    rate: int | None = None
    timeout_seconds: int = 30


class GptSovitsTtsProvider:
    name = "gpt_sovits"

    def __init__(self, config: GptSovitsConfig, library: "LocalAudioLibrary") -> None:
        self.config = config
        self.library = library

    def synthesize(self, command: AudioOutputCommand) -> AudioOutputResult | None:
        if not self.config.base_url:
            return None

        normalized_text = command.text.strip()
        if not normalized_text:
            return None

        audio_format = command.format.lower().lstrip(".")
        if f".{audio_format}" not in SUPPORTED_AUDIO_SUFFIXES:
            audio_format = "wav"

        payload = self._build_payload(command, normalized_text, audio_format)
        request_start = perf_counter()
        request = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/{self.config.endpoint.lstrip('/')}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                response_body = response.read()
                content_type = response.headers.get("Content-Type", "") if response.headers else ""
        except (OSError, urllib.error.HTTPError) as error:
            raise RuntimeError(f"TTS provider {self.name} failed: {error}") from error

        if "application/json" in content_type:
            result = self._result_from_json(response_body)
            if result:
                return result
            return None

        audio_path = self._write_audio_cache(normalized_text, command.voice, audio_format, response_body)
        duration_ms = self.library.duration_ms(audio_path)
        if duration_ms is None:
            duration_ms = round((perf_counter() - request_start) * 1000)
        return AudioOutputResult(
            audio_url=self.library.public_url(audio_path),
            duration_ms=duration_ms,
            provider=self.name,
        )

    def _build_payload(self, command: AudioOutputCommand, text: str, audio_format: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "text": text,
            "text_lang": self.config.text_lang,
            "prompt_lang": self.config.prompt_lang,
            "media_type": audio_format,
            "streaming_mode": self.config.streaming_mode,
        }
        if self.config.prompt_text:
            payload["prompt_text"] = self.config.prompt_text
        if self.config.ref_audio_path:
            payload["ref_audio_path"] = self.config.ref_audio_path
        if command.voice:
            payload["voice"] = command.voice
        return payload

    def _result_from_json(self, response_body: bytes) -> AudioOutputResult | None:
        try:
            parsed = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

        if not isinstance(parsed, dict):
            return None

        audio_url = parsed.get("audioUrl") or parsed.get("audio_url") or parsed.get("url")
        if not isinstance(audio_url, str) or not audio_url:
            return None

        duration_ms = parsed.get("durationMs") or parsed.get("duration_ms")
        return AudioOutputResult(
            audio_url=audio_url,
            duration_ms=duration_ms if isinstance(duration_ms, int) else None,
            provider=self.name,
        )

    def _write_audio_cache(self, text: str, voice: str | None, audio_format: str, response_body: bytes) -> Path:
        cache_key = hashlib.sha256(
            json.dumps({
                "provider": self.name,
                "text": text,
                "voice": voice,
                "format": audio_format,
                "body": hashlib.sha256(response_body).hexdigest(),
            }, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        output_path = self.library.cache_dir / f"tts-{cache_key}.{audio_format}"
        output_path.write_bytes(response_body)
        return output_path


class MacOsSayTtsProvider:
    name = "macos_say"

    def __init__(self, config: MacOsSayConfig, library: "LocalAudioLibrary") -> None:
        self.config = config
        self.library = library

    @classmethod
    def is_available(cls) -> bool:
        return (
            platform.system() == "Darwin"
            and shutil.which("say") is not None
            and shutil.which("afconvert") is not None
        )

    def synthesize(self, command: AudioOutputCommand) -> AudioOutputResult | None:
        if not self.is_available():
            return None

        normalized_text = command.text.strip()
        if not normalized_text:
            return None

        voice = (command.voice or self.config.voice).strip()
        rate = self.config.rate
        cache_path = self._cache_path(normalized_text, voice, rate)
        if not cache_path.exists():
            self._generate_wav(normalized_text, voice, rate, cache_path)

        return AudioOutputResult(
            audio_url=self.library.public_url(cache_path),
            duration_ms=self.library.duration_ms(cache_path),
            provider=self.name,
        )

    def _cache_path(self, text: str, voice: str, rate: int | None) -> Path:
        cache_key = hashlib.sha256(
            json.dumps({
                "provider": self.name,
                "text": text,
                "voice": voice,
                "rate": rate,
                "format": "wav",
            }, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        return self.library.cache_dir / f"tts-{cache_key}.wav"

    def _generate_wav(self, text: str, voice: str, rate: int | None, output_path: Path) -> None:
        with tempfile.TemporaryDirectory(prefix="amadeus-say-") as tmpdir:
            aiff_path = Path(tmpdir) / "speech.aiff"
            say_command = ["say", "-o", str(aiff_path)]
            if voice:
                say_command.extend(["-v", voice])
            if rate is not None:
                say_command.extend(["-r", str(rate)])
            say_command.append(text)
            subprocess.run(say_command, check=True, timeout=self.config.timeout_seconds)
            subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16", str(aiff_path), str(output_path)],
                check=True,
                timeout=self.config.timeout_seconds,
            )


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

    def public_file_path(self, audio_url: str) -> Path | None:
        parsed = urlparse(audio_url)
        base = urlparse(self.public_base_url)
        if parsed.scheme != base.scheme or parsed.netloc != base.netloc:
            return None

        base_path = base.path.rstrip("/")
        prefix = f"{base_path}/audio/files/"
        if not parsed.path.startswith(prefix):
            return None

        relative_path = unquote(parsed.path[len(prefix):]).lstrip("/")
        return self.resolve_public_path(relative_path)

    def duration_ms(self, file_path: Path) -> int | None:
        if file_path.suffix.lower() != ".wav":
            return None

        try:
            with wave.open(str(file_path), "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                frame_count = wav_file.getnframes()
        except (OSError, EOFError, wave.Error):
            return None

        if frame_rate <= 0:
            return None
        return max(0, round((frame_count / frame_rate) * 1000))

    def lipsync_cues_for_audio_url(
        self,
        audio_url: str,
        *,
        cue_interval_ms: int,
        max_cues: int,
    ) -> tuple[int | None, list[dict[str, float | int]]]:
        file_path = self.public_file_path(audio_url)
        if file_path is None or file_path.suffix.lower() != ".wav":
            return (None, [])

        try:
            with wave.open(str(file_path), "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                sample_width = wav_file.getsampwidth()
                channel_count = wav_file.getnchannels()
                frame_count = wav_file.getnframes()
                if frame_rate <= 0 or sample_width <= 0 or channel_count <= 0 or frame_count <= 0:
                    return (None, [])

                duration_ms = max(0, round((frame_count / frame_rate) * 1000))
                window_ms = max(50, cue_interval_ms)
                window_frames = max(1, round(frame_rate * (window_ms / 1000)))
                cues: list[dict[str, float | int]] = []
                offset_ms = 0

                while len(cues) < max(1, max_cues):
                    frames = wav_file.readframes(window_frames)
                    if not frames:
                        break

                    if channel_count == 2:
                        frames = tomono(frames, sample_width, 1 / channel_count, 1 / channel_count)
                    elif channel_count > 2:
                        return (duration_ms, [])

                    signal_rms = rms(frames, sample_width)
                    max_amplitude = {
                        1: 128.0,
                        2: 32768.0,
                        3: 8388608.0,
                        4: 2147483648.0,
                    }.get(sample_width)
                    if max_amplitude is None:
                        return (duration_ms, [])

                    mouth_open = min(1.0, max(0.0, 0.05 + (signal_rms / max_amplitude) * 4.0))
                    cues.append({
                        "offsetMs": min(duration_ms, offset_ms),
                        "mouthOpen": round(mouth_open, 4),
                    })
                    offset_ms += window_ms

                if not cues:
                    return (duration_ms, [])
                if int(cues[-1]["offsetMs"]) < duration_ms:
                    cues.append({"offsetMs": duration_ms, "mouthOpen": 0.0})
                return (duration_ms, cues)
        except (OSError, EOFError, wave.Error):
            return (None, [])

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


def create_tts_provider_from_config(
    library: LocalAudioLibrary,
    config_path: Path = DEFAULT_PROVIDERS_CONFIG_PATH,
) -> TtsProvider:
    config = parse_providers_config(config_path).get("tts", {})
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    default_provider = str(config.get("default") or "disabled")
    provider_config = providers.get(default_provider) if isinstance(providers.get(default_provider), dict) else {}
    provider_type = str(provider_config.get("type") or default_provider)

    if provider_type == "auto":
        gpt_config = providers.get("gpt_sovits") if isinstance(providers.get("gpt_sovits"), dict) else {}
        if str(gpt_config.get("baseUrl") or os.environ.get("GPT_SOVITS_BASE_URL") or "").strip():
            return _create_gpt_sovits_provider(gpt_config, library)

        macos_config = providers.get("macos_say") if isinstance(providers.get("macos_say"), dict) else {}
        if MacOsSayTtsProvider.is_available():
            return _create_macos_say_provider(macos_config, library)

        return NoopTtsProvider()

    if provider_type in {"none", "disabled"}:
        return NoopTtsProvider()

    if provider_type in {"gpt_sovits", "gpt-sovits", "gptsovits"}:
        return _create_gpt_sovits_provider(provider_config, library)

    if provider_type in {"macos_say", "macos-say", "say"}:
        return _create_macos_say_provider(provider_config, library)

    return NoopTtsProvider()


def _create_gpt_sovits_provider(provider_config: dict[str, Any], library: LocalAudioLibrary) -> GptSovitsTtsProvider:
    return GptSovitsTtsProvider(
        GptSovitsConfig(
            base_url=str(provider_config.get("baseUrl") or os.environ.get("GPT_SOVITS_BASE_URL") or "").rstrip("/"),
            endpoint=str(provider_config.get("endpoint") or "/tts"),
            text_lang=str(provider_config.get("textLang") or os.environ.get("GPT_SOVITS_TEXT_LANG") or "auto"),
            prompt_lang=str(provider_config.get("promptLang") or os.environ.get("GPT_SOVITS_PROMPT_LANG") or "auto"),
            prompt_text=str(provider_config.get("promptText") or os.environ.get("GPT_SOVITS_PROMPT_TEXT") or ""),
            ref_audio_path=str(provider_config.get("refAudioPath") or os.environ.get("GPT_SOVITS_REF_AUDIO_PATH") or ""),
            timeout_seconds=int(provider_config.get("timeoutSeconds") or os.environ.get("GPT_SOVITS_TIMEOUT_SECONDS") or 60),
            streaming_mode=parse_bool_value(provider_config.get("streamingMode"), False),
        ),
        library,
    )


def _create_macos_say_provider(provider_config: dict[str, Any], library: LocalAudioLibrary) -> MacOsSayTtsProvider:
    raw_rate = provider_config.get("rate") or os.environ.get("MACOS_SAY_RATE") or ""
    return MacOsSayTtsProvider(
        MacOsSayConfig(
            voice=str(provider_config.get("voice") or os.environ.get("MACOS_SAY_VOICE") or ""),
            rate=int(raw_rate) if str(raw_rate).strip() else None,
            timeout_seconds=int(provider_config.get("timeoutSeconds") or os.environ.get("MACOS_SAY_TIMEOUT_SECONDS") or 30),
        ),
        library,
    )
