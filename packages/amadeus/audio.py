from __future__ import annotations

import hashlib
import json
import os
import platform
import re
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
    lipsync_cues: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class AudioFallbackResult:
    reason: str
    fallback: str = "speechSynthesis"


@dataclass(frozen=True)
class AudioTranscriptCommand:
    audio_bytes: bytes
    audio_format: str = "webm"
    language: str | None = None


@dataclass(frozen=True)
class AudioTranscriptResult:
    text: str
    provider: str = "unknown"
    language: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class AudioTranscriptFailure:
    reason: str
    provider: str = "unknown"


@dataclass(frozen=True)
class PhonemeUnit:
    viseme: str
    weight: float
    mouth_open: float
    symbol: str


PHONEME_WORD_PATTERN = re.compile(r"[A-Za-z']+|[0-9]+|[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]|[^\w\s]", re.UNICODE)
VISEME_MOUTH_OPEN = {
    "sil": 0.0,
    "A": 1.0,
    "E": 0.62,
    "I": 0.38,
    "O": 0.84,
    "U": 0.28,
    "MBP": 0.1,
    "FV": 0.24,
    "L": 0.44,
    "WQ": 0.26,
    "R": 0.33,
    "C": 0.18,
}

ENGLISH_CLUSTER_VISEMES: list[tuple[str, str]] = [
    ("tion", "C"),
    ("sion", "C"),
    ("ough", "O"),
    ("eigh", "E"),
    ("igh", "I"),
    ("air", "A"),
    ("ear", "E"),
    ("oor", "O"),
    ("ee", "E"),
    ("ea", "E"),
    ("oo", "O"),
    ("ou", "O"),
    ("ow", "O"),
    ("oa", "O"),
    ("ai", "A"),
    ("ay", "A"),
    ("au", "O"),
    ("oi", "O"),
    ("oy", "O"),
    ("er", "R"),
    ("ir", "R"),
    ("ur", "R"),
    ("ar", "A"),
    ("or", "O"),
    ("th", "C"),
    ("sh", "C"),
    ("ch", "C"),
    ("zh", "C"),
    ("ph", "FV"),
    ("wh", "WQ"),
    ("qu", "WQ"),
    ("ck", "C"),
    ("ng", "C"),
]
LETTER_VISEMES = {
    "a": "A",
    "e": "E",
    "i": "I",
    "o": "O",
    "u": "U",
    "y": "I",
    "b": "MBP",
    "m": "MBP",
    "p": "MBP",
    "f": "FV",
    "v": "FV",
    "l": "L",
    "w": "WQ",
    "q": "WQ",
    "r": "R",
}
VISEME_WEIGHT = {
    "sil": 0.85,
    "A": 1.35,
    "E": 1.2,
    "I": 1.0,
    "O": 1.28,
    "U": 1.0,
    "MBP": 0.6,
    "FV": 0.65,
    "L": 0.72,
    "WQ": 0.78,
    "R": 0.82,
    "C": 0.58,
}


class PhonemeLipsyncPlanner:
    def build_cues(
        self,
        text: str,
        duration_ms: int,
        *,
        audio_envelope: list[float] | None = None,
        max_cues: int = 96,
    ) -> list[dict[str, Any]]:
        normalized_text = text.strip()
        if not normalized_text or duration_ms <= 0:
            return []

        units = self._phoneme_units(normalized_text)
        if not units:
            return []

        if len(units) > max_cues:
            units = self._compress_units(units, max_cues=max_cues)

        total_weight = sum(max(0.1, unit.weight) for unit in units)
        if total_weight <= 0:
            return []

        cues: list[dict[str, Any]] = []
        accumulated_ms = 0.0
        envelope = audio_envelope or []
        for index, unit in enumerate(units):
            cue_duration_ms = duration_ms * (max(0.1, unit.weight) / total_weight)
            envelope_scale = envelope[index] if index < len(envelope) else 1.0
            mouth_open = self._scaled_mouth_open(unit.mouth_open, envelope_scale)
            cues.append({
                "offsetMs": min(duration_ms, round(accumulated_ms)),
                "mouthOpen": mouth_open,
                "viseme": unit.viseme,
                "phoneme": unit.symbol,
            })
            accumulated_ms += cue_duration_ms

        cues.append({
            "offsetMs": duration_ms,
            "mouthOpen": 0.0,
            "viseme": "sil",
            "phoneme": "",
        })
        return self._dedupe_cues(cues)

    def _phoneme_units(self, text: str) -> list[PhonemeUnit]:
        units: list[PhonemeUnit] = []
        for token in PHONEME_WORD_PATTERN.findall(text):
            if token.isascii() and any(character.isalpha() for character in token):
                units.extend(self._english_units(token.lower()))
                continue
            if token.isdigit():
                units.extend(self._digit_units(token))
                continue
            if re.fullmatch(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", token):
                units.extend(self._cjk_units(token))
                continue
            units.append(self._unit("sil", 0.75, token))
        return units

    def _english_units(self, word: str) -> list[PhonemeUnit]:
        units: list[PhonemeUnit] = []
        index = 0
        while index < len(word):
            matched = False
            for cluster, viseme in ENGLISH_CLUSTER_VISEMES:
                if word.startswith(cluster, index):
                    units.append(self._unit(viseme, VISEME_WEIGHT[viseme], cluster))
                    index += len(cluster)
                    matched = True
                    break
            if matched:
                continue

            character = word[index]
            viseme = LETTER_VISEMES.get(character, "C")
            weight = VISEME_WEIGHT[viseme]
            if character in {"'", "-"}:
                viseme = "sil"
                weight = 0.4
            units.append(self._unit(viseme, weight, character))
            index += 1
        return units

    def _digit_units(self, digits: str) -> list[PhonemeUnit]:
        units: list[PhonemeUnit] = []
        for digit in digits:
            viseme = {
                "0": "O",
                "1": "WQ",
                "2": "U",
                "3": "E",
                "4": "O",
                "5": "I",
                "6": "I",
                "7": "E",
                "8": "A",
                "9": "I",
            }.get(digit, "C")
            units.append(self._unit(viseme, VISEME_WEIGHT[viseme], digit))
        return units

    def _cjk_units(self, character: str) -> list[PhonemeUnit]:
        viseme_cycle = ("A", "E", "I", "O", "U")
        nucleus = viseme_cycle[ord(character) % len(viseme_cycle)]
        return [
            self._unit("C", 0.45, f"{character}:onset"),
            self._unit(nucleus, 1.05, character),
        ]

    def _compress_units(self, units: list[PhonemeUnit], *, max_cues: int) -> list[PhonemeUnit]:
        if len(units) <= max_cues:
            return units

        bucket_size = max(1, round(len(units) / max_cues))
        merged: list[PhonemeUnit] = []
        for index in range(0, len(units), bucket_size):
            chunk = units[index:index + bucket_size]
            dominant = max(chunk, key=lambda unit: unit.mouth_open)
            merged.append(PhonemeUnit(
                viseme=dominant.viseme,
                weight=sum(unit.weight for unit in chunk),
                mouth_open=max(unit.mouth_open for unit in chunk),
                symbol="+".join(unit.symbol for unit in chunk[:3]),
            ))
        return merged[:max_cues]

    def _scaled_mouth_open(self, base_mouth_open: float, envelope_scale: float) -> float:
        scale = min(1.35, max(0.55, envelope_scale))
        return round(min(1.0, max(0.0, base_mouth_open * scale)), 4)

    def _dedupe_cues(self, cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        last_key: tuple[int, float, str] | None = None
        for cue in cues:
            key = (
                int(cue["offsetMs"]),
                float(cue["mouthOpen"]),
                str(cue.get("viseme") or ""),
            )
            if key == last_key:
                continue
            deduped.append(cue)
            last_key = key
        return deduped

    def _unit(self, viseme: str, weight: float, symbol: str) -> PhonemeUnit:
        return PhonemeUnit(
            viseme=viseme,
            weight=weight,
            mouth_open=VISEME_MOUTH_OPEN[viseme],
            symbol=symbol,
        )


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
        normalized_duration_ms = self._normalize_duration_ms(duration_ms)
        lipsync_cues = self._extract_provider_lipsync_cues(parsed, normalized_duration_ms)
        if normalized_duration_ms is None and lipsync_cues:
            normalized_duration_ms = max(int(cue["offsetMs"]) for cue in lipsync_cues)
        return AudioOutputResult(
            audio_url=audio_url,
            duration_ms=normalized_duration_ms,
            provider=self.name,
            lipsync_cues=lipsync_cues,
        )

    def _extract_provider_lipsync_cues(
        self,
        payload: dict[str, Any],
        duration_ms: int | None,
    ) -> list[dict[str, Any]] | None:
        cue_candidates: list[Any] = []
        for key in ("lipsyncCues", "lipsync_cues", "cues", "visemes", "phonemes"):
            value = payload.get(key)
            if isinstance(value, list):
                cue_candidates = value
                break

        lipsync = payload.get("lipsync")
        if not cue_candidates and isinstance(lipsync, dict):
            for key in ("cues", "lipsyncCues", "lipsync_cues", "visemes", "phonemes"):
                value = lipsync.get(key)
                if isinstance(value, list):
                    cue_candidates = value
                    break

        if not cue_candidates:
            return None

        cues: list[dict[str, Any]] = []
        for raw_cue in cue_candidates:
            normalized = self._normalize_provider_cue(raw_cue)
            if normalized:
                cues.append(normalized)

        if not cues:
            return None

        cues.sort(key=lambda cue: int(cue["offsetMs"]))
        resolved_duration_ms = duration_ms
        if resolved_duration_ms is None:
            resolved_duration_ms = max(int(cue["offsetMs"]) for cue in cues)
        if resolved_duration_ms is not None and int(cues[-1]["offsetMs"]) < resolved_duration_ms:
            cues.append({
                "offsetMs": resolved_duration_ms,
                "mouthOpen": 0.0,
                "viseme": "sil",
                "phoneme": "",
            })
        return cues

    def _normalize_provider_cue(self, raw_cue: Any) -> dict[str, Any] | None:
        if not isinstance(raw_cue, dict):
            return None

        offset_value = self._first_present(
            raw_cue,
            "offsetMs",
            "offset_ms",
            "timeMs",
            "time_ms",
            "startMs",
            "start_ms",
            "start",
        )
        if not isinstance(offset_value, (int, float)) or offset_value < 0:
            return None

        viseme = raw_cue.get("viseme")
        if not isinstance(viseme, str) or not viseme.strip():
            viseme = "sil"
        viseme = viseme.strip()

        phoneme = raw_cue.get("phoneme")
        if not isinstance(phoneme, str):
            phoneme = ""

        mouth_open_value = self._first_present(
            raw_cue,
            "mouthOpen",
            "mouth_open",
            "value",
            "strength",
            "weight",
        )
        mouth_open = self._normalize_provider_mouth_open(mouth_open_value, viseme)

        return {
            "offsetMs": int(round(offset_value)),
            "mouthOpen": mouth_open,
            "viseme": viseme,
            "phoneme": phoneme,
        }

    def _normalize_provider_mouth_open(self, raw_value: Any, viseme: str) -> float:
        if isinstance(raw_value, (int, float)):
            value = float(raw_value)
            if value > 1.0:
                value = value / 100.0
            return round(min(1.0, max(0.0, value)), 4)

        viseme_key = viseme.upper()
        if viseme_key in VISEME_MOUTH_OPEN:
            return round(VISEME_MOUTH_OPEN[viseme_key], 4)
        return 0.0

    def _normalize_duration_ms(self, raw_duration: Any) -> int | None:
        if isinstance(raw_duration, (int, float)) and raw_duration >= 0:
            return int(round(raw_duration))
        return None

    def _first_present(self, payload: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in payload:
                return payload[key]
        return None

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

    def list_voices(self) -> list[dict[str, str]]:
        if not self.is_available():
            return []

        try:
            completed = subprocess.run(
                ["say", "-v", "?"],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            return []

        voices: list[dict[str, str]] = []
        for line in completed.stdout.splitlines():
            match = re.match(r"^(.+?)\s+([A-Za-z0-9_\-]+)\s*#\s*(.*)$", line)
            if not match:
                continue
            name = match.group(1).strip()
            if not name:
                continue
            voices.append({
                "id": name,
                "label": name,
                "locale": match.group(2).strip(),
                "sample": match.group(3).strip(),
            })
        return voices

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
        self.lipsync_planner = PhonemeLipsyncPlanner()

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

    def waveform_envelope_for_audio_path(
        self,
        file_path: Path,
        *,
        cue_count: int,
    ) -> list[float]:
        if file_path.suffix.lower() != ".wav" or cue_count <= 0:
            return []

        try:
            with wave.open(str(file_path), "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                sample_width = wav_file.getsampwidth()
                channel_count = wav_file.getnchannels()
                frame_count = wav_file.getnframes()
                if frame_rate <= 0 or sample_width <= 0 or channel_count <= 0 or frame_count <= 0:
                    return []

                window_frames = max(1, round(frame_count / cue_count))
                envelope: list[float] = []
                max_amplitude = {
                    1: 128.0,
                    2: 32768.0,
                    3: 8388608.0,
                    4: 2147483648.0,
                }.get(sample_width)
                if max_amplitude is None:
                    return []

                while len(envelope) < cue_count:
                    frames = wav_file.readframes(window_frames)
                    if not frames:
                        break

                    if channel_count == 2:
                        frames = tomono(frames, sample_width, 0.5, 0.5)
                    elif channel_count > 2:
                        return []

                    signal_rms = rms(frames, sample_width)
                    normalized = min(1.0, max(0.0, signal_rms / max_amplitude))
                    envelope.append(round(0.55 + normalized * 0.8, 4))
                return envelope
        except (OSError, EOFError, wave.Error):
            return []

    def phoneme_lipsync_cues_for_audio(
        self,
        text: str,
        duration_ms: int,
        *,
        audio_url: str | None = None,
        max_cues: int,
    ) -> list[dict[str, Any]]:
        if duration_ms <= 0:
            return []

        file_path = self.public_file_path(audio_url) if audio_url else None
        envelope = self.waveform_envelope_for_audio_path(file_path, cue_count=max_cues) if file_path else []
        return self.lipsync_planner.build_cues(
            text,
            duration_ms,
            audio_envelope=envelope,
            max_cues=max_cues,
        )

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
        self.lipsync_max_cues = 96

    def speak(self, command: AudioOutputCommand) -> AudioOutputResult | AudioFallbackResult:
        normalized_text = command.text.strip()
        if not normalized_text:
            return AudioFallbackResult(reason="empty_text")

        result = self.tts_provider.synthesize(command)
        if result:
            if result.duration_ms is not None and result.duration_ms > 0:
                lipsync_cues = result.lipsync_cues or self.library.phoneme_lipsync_cues_for_audio(
                    normalized_text,
                    result.duration_ms,
                    audio_url=result.audio_url,
                    max_cues=self.lipsync_max_cues,
                )
                return AudioOutputResult(
                    audio_url=result.audio_url,
                    duration_ms=result.duration_ms,
                    provider=result.provider,
                    lipsync_cues=lipsync_cues,
                )
            return result

        return AudioFallbackResult(reason=f"tts_provider_unavailable:{self.tts_provider.name}")

    def list_voices(self) -> dict[str, Any]:
        provider = self.tts_provider
        lister = getattr(provider, "list_voices", None)
        if callable(lister):
            voices = lister()
            return {
                "provider": provider.name,
                "supportsEnumeration": True,
                "voices": voices,
            }
        return {
            "provider": provider.name,
            "supportsEnumeration": False,
            "voices": [],
        }


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


class AsrProvider(Protocol):
    name: str

    def transcribe(self, command: AudioTranscriptCommand) -> AudioTranscriptResult | None:
        ...


class NoopAsrProvider:
    name = "none"

    def transcribe(self, command: AudioTranscriptCommand) -> AudioTranscriptResult | None:
        return None


@dataclass(frozen=True)
class FasterWhisperConfig:
    model_size: str = "base"
    device: str = "auto"
    compute_type: str = "default"
    language: str = ""
    beam_size: int = 5
    download_root: str = ""


class FasterWhisperAsrProvider:
    name = "faster_whisper"

    def __init__(self, config: FasterWhisperConfig, library: "LocalAudioLibrary") -> None:
        self.config = config
        self.library = library
        self._model: Any = None

    @classmethod
    def is_available(cls) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        from faster_whisper import WhisperModel

        kwargs: dict[str, Any] = {
            "device": self.config.device or "auto",
            "compute_type": self.config.compute_type or "default",
        }
        if self.config.download_root:
            kwargs["download_root"] = self.config.download_root
        self._model = WhisperModel(self.config.model_size or "base", **kwargs)
        return self._model

    def transcribe(self, command: AudioTranscriptCommand) -> AudioTranscriptResult | None:
        if not command.audio_bytes:
            return None

        model = self._load_model()
        suffix = command.audio_format.lower().lstrip(".") or "webm"
        request_start = perf_counter()
        with tempfile.NamedTemporaryFile(
            prefix="amadeus-asr-",
            suffix=f".{suffix}",
            dir=self.library.cache_dir,
            delete=False,
        ) as handle:
            handle.write(command.audio_bytes)
            temp_path = Path(handle.name)

        try:
            language = command.language or self.config.language or None
            segments, info = model.transcribe(
                str(temp_path),
                language=language,
                beam_size=self.config.beam_size,
            )
            text = "".join(segment.text for segment in segments).strip()
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass

        if not text:
            return None

        detected_language = getattr(info, "language", None)
        return AudioTranscriptResult(
            text=text,
            provider=self.name,
            language=detected_language or language,
            duration_ms=round((perf_counter() - request_start) * 1000),
        )


class AsrRuntime:
    def __init__(self, library: LocalAudioLibrary, asr_provider: AsrProvider | None = None) -> None:
        self.library = library
        self.asr_provider = asr_provider or NoopAsrProvider()

    def transcribe(
        self, command: AudioTranscriptCommand
    ) -> AudioTranscriptResult | AudioTranscriptFailure:
        if not command.audio_bytes:
            return AudioTranscriptFailure(reason="empty_audio", provider=self.asr_provider.name)

        try:
            result = self.asr_provider.transcribe(command)
        except Exception as error:  # noqa: BLE001
            return AudioTranscriptFailure(
                reason=f"asr_provider_error:{error}",
                provider=self.asr_provider.name,
            )

        if result and result.text.strip():
            return result

        return AudioTranscriptFailure(
            reason=f"asr_provider_no_text:{self.asr_provider.name}",
            provider=self.asr_provider.name,
        )


def create_asr_provider_from_config(
    library: LocalAudioLibrary,
    config_path: Path = DEFAULT_PROVIDERS_CONFIG_PATH,
) -> AsrProvider:
    config = parse_providers_config(config_path).get("asr", {})
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    default_provider = str(config.get("default") or "disabled")
    provider_config = providers.get(default_provider) if isinstance(providers.get(default_provider), dict) else {}
    provider_type = str(provider_config.get("type") or default_provider)

    if provider_type == "auto":
        whisper_config = providers.get("faster_whisper") if isinstance(providers.get("faster_whisper"), dict) else {}
        if FasterWhisperAsrProvider.is_available():
            return _create_faster_whisper_provider(whisper_config, library)
        return NoopAsrProvider()

    if provider_type in {"none", "disabled"}:
        return NoopAsrProvider()

    if provider_type in {"faster_whisper", "faster-whisper", "fasterwhisper", "whisper"}:
        return _create_faster_whisper_provider(provider_config, library)

    return NoopAsrProvider()


def _create_faster_whisper_provider(
    provider_config: dict[str, Any], library: LocalAudioLibrary
) -> FasterWhisperAsrProvider:
    return FasterWhisperAsrProvider(
        FasterWhisperConfig(
            model_size=str(provider_config.get("modelSize") or os.environ.get("FASTER_WHISPER_MODEL_SIZE") or "base"),
            device=str(provider_config.get("device") or os.environ.get("FASTER_WHISPER_DEVICE") or "auto"),
            compute_type=str(provider_config.get("computeType") or os.environ.get("FASTER_WHISPER_COMPUTE_TYPE") or "default"),
            language=str(provider_config.get("language") or os.environ.get("FASTER_WHISPER_LANGUAGE") or ""),
            beam_size=int(provider_config.get("beamSize") or os.environ.get("FASTER_WHISPER_BEAM_SIZE") or 5),
            download_root=str(provider_config.get("downloadRoot") or os.environ.get("FASTER_WHISPER_DOWNLOAD_ROOT") or ""),
        ),
        library,
    )
