from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.audio import (
    AudioOutputCommand,
    AudioRuntime,
    GptSovitsConfig,
    GptSovitsTtsProvider,
    LocalAudioLibrary,
    MacOsSayConfig,
    MacOsSayTtsProvider,
    NoopTtsProvider,
    create_tts_provider_from_config,
)


class FakeHttpResponse:
    def __init__(self, body: bytes, content_type: str) -> None:
        self.body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def write_test_wav(path: Path, amplitudes: list[int], frame_rate: int = 1000) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(frame_rate)
        frames = bytearray()
        for amplitude in amplitudes:
            sample = max(-32768, min(32767, amplitude))
            frames.extend(int(sample).to_bytes(2, byteorder="little", signed=True))
        wav_file.writeframes(bytes(frames))


class AudioProviderTests(unittest.TestCase):
    def test_create_tts_provider_defaults_to_noop_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "providers.yaml"
            config_path.write_text(
                "\n".join([
                    "tts:",
                    "  default: disabled",
                    "  providers:",
                    "    disabled:",
                    "      type: none",
                ]),
                encoding="utf-8",
            )
            library = LocalAudioLibrary(root / "audio", "http://localhost:8790")

            provider = create_tts_provider_from_config(library, config_path)

        self.assertIsInstance(provider, NoopTtsProvider)

    def test_create_tts_provider_builds_gpt_sovits_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "providers.yaml"
            config_path.write_text(
                "\n".join([
                    "tts:",
                    "  default: gpt_sovits",
                    "  providers:",
                    "    gpt_sovits:",
                    "      type: gpt_sovits",
                    "      baseUrl: http://127.0.0.1:9880",
                    "      endpoint: /tts",
                    "      textLang: zh",
                    "      promptLang: zh",
                    "      promptText: 你好",
                    "      refAudioPath: /tmp/ref.wav",
                    "      timeoutSeconds: 12",
                    "      streamingMode: false",
                ]),
                encoding="utf-8",
            )
            library = LocalAudioLibrary(root / "audio", "http://localhost:8790")

            provider = create_tts_provider_from_config(library, config_path)

        self.assertIsInstance(provider, GptSovitsTtsProvider)
        self.assertEqual(provider.config.base_url, "http://127.0.0.1:9880")
        self.assertEqual(provider.config.timeout_seconds, 12)
        self.assertEqual(provider.config.text_lang, "zh")

    def test_create_tts_provider_auto_uses_macos_say_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "providers.yaml"
            config_path.write_text(
                "\n".join([
                    "tts:",
                    "  default: auto",
                    "  providers:",
                    "    auto:",
                    "      type: auto",
                    "    macos_say:",
                    "      type: macos_say",
                    "      voice: Samantha",
                    "      rate: 180",
                    "      timeoutSeconds: 7",
                ]),
                encoding="utf-8",
            )
            library = LocalAudioLibrary(root / "audio", "http://localhost:8790")

            with patch.object(MacOsSayTtsProvider, "is_available", return_value=True):
                provider = create_tts_provider_from_config(library, config_path)

        self.assertIsInstance(provider, MacOsSayTtsProvider)
        self.assertEqual(provider.config.voice, "Samantha")
        self.assertEqual(provider.config.rate, 180)
        self.assertEqual(provider.config.timeout_seconds, 7)

    def test_create_tts_provider_auto_prefers_gpt_sovits_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "providers.yaml"
            config_path.write_text(
                "\n".join([
                    "tts:",
                    "  default: auto",
                    "  providers:",
                    "    auto:",
                    "      type: auto",
                    "    gpt_sovits:",
                    "      type: gpt_sovits",
                    "      baseUrl: http://127.0.0.1:9880",
                ]),
                encoding="utf-8",
            )
            library = LocalAudioLibrary(root / "audio", "http://localhost:8790")

            with patch.object(MacOsSayTtsProvider, "is_available", return_value=True):
                provider = create_tts_provider_from_config(library, config_path)

        self.assertIsInstance(provider, GptSovitsTtsProvider)
        self.assertEqual(provider.config.base_url, "http://127.0.0.1:9880")

    def test_macos_say_provider_generates_wav_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            library = LocalAudioLibrary(Path(tmpdir) / "audio", "http://localhost:8790")
            provider = MacOsSayTtsProvider(MacOsSayConfig(voice="Samantha", rate=180), library)

            def fake_run(command: list[str], **_: object) -> None:
                if command[0] == "say":
                    Path(command[command.index("-o") + 1]).write_bytes(b"AIFF")
                    return
                if command[0] == "afconvert":
                    Path(command[-1]).write_bytes(b"RIFFfake-wav")
                    return
                raise AssertionError(f"unexpected command: {command}")

            with patch.object(MacOsSayTtsProvider, "is_available", return_value=True):
                with patch("subprocess.run", side_effect=fake_run) as run:
                    result = provider.synthesize(AudioOutputCommand(text="hello", format="wav"))

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.provider, "macos_say")
            self.assertTrue(result.audio_url.startswith("http://localhost:8790/audio/files/cache/tts-"))
            cached_files = list((Path(tmpdir) / "audio" / "cache").glob("*.wav"))
            self.assertEqual(len(cached_files), 1)
            self.assertEqual(cached_files[0].read_bytes(), b"RIFFfake-wav")
            self.assertEqual(run.call_count, 2)

    def test_gpt_sovits_provider_writes_binary_audio_to_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            library = LocalAudioLibrary(Path(tmpdir) / "audio", "http://localhost:8790")
            provider = GptSovitsTtsProvider(
                GptSovitsConfig(base_url="http://127.0.0.1:9880", text_lang="zh", prompt_lang="zh"),
                library,
            )
            response = FakeHttpResponse(b"RIFFfake-wav", "audio/wav")

            with patch("urllib.request.urlopen", return_value=response) as urlopen:
                result = provider.synthesize(AudioOutputCommand(text="你好", voice="vivian", format="wav"))

            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result.audio_url.startswith("http://localhost:8790/audio/files/cache/tts-"))
            self.assertEqual(result.provider, "gpt_sovits")
            cached_files = list((Path(tmpdir) / "audio" / "cache").glob("*.wav"))
            self.assertEqual(len(cached_files), 1)
            self.assertEqual(cached_files[0].read_bytes(), b"RIFFfake-wav")
            request = urlopen.call_args.args[0]
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual(payload["text"], "你好")
            self.assertEqual(payload["voice"], "vivian")
            self.assertEqual(payload["media_type"], "wav")
            self.assertFalse(payload["streaming_mode"])

    def test_gpt_sovits_provider_accepts_json_audio_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            library = LocalAudioLibrary(Path(tmpdir) / "audio", "http://localhost:8790")
            provider = GptSovitsTtsProvider(GptSovitsConfig(base_url="http://127.0.0.1:9880"), library)
            response = FakeHttpResponse(
                json.dumps({"audioUrl": "http://127.0.0.1/audio.wav", "durationMs": 123}).encode("utf-8"),
                "application/json",
            )

            with patch("urllib.request.urlopen", return_value=response):
                result = provider.synthesize(AudioOutputCommand(text="hello", format="wav"))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.audio_url, "http://127.0.0.1/audio.wav")
        self.assertEqual(result.duration_ms, 123)

    def test_audio_runtime_falls_back_when_provider_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = AudioRuntime(LocalAudioLibrary(Path(tmpdir) / "audio", "http://localhost:8790"))

            result = runtime.speak(AudioOutputCommand(text="hello"))

        self.assertEqual(result.reason, "tts_provider_unavailable:none")

    def test_local_audio_library_reports_wav_duration_and_lipsync_cues(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            library = LocalAudioLibrary(Path(tmpdir) / "audio", "http://localhost:8790")
            wav_path = library.cache_dir / "tts-test.wav"
            write_test_wav(wav_path, [0] * 100 + [22000] * 100 + [4000] * 100, frame_rate=1000)

            duration_ms = library.duration_ms(wav_path)
            resolved_duration_ms, cues = library.lipsync_cues_for_audio_url(
                library.public_url(wav_path),
                cue_interval_ms=100,
                max_cues=10,
            )

        self.assertEqual(duration_ms, 300)
        self.assertEqual(resolved_duration_ms, 300)
        self.assertGreaterEqual(len(cues), 3)
        self.assertLess(float(cues[0]["mouthOpen"]), float(cues[1]["mouthOpen"]))
        self.assertGreater(float(cues[1]["mouthOpen"]), float(cues[2]["mouthOpen"]))

    def test_macos_say_provider_reports_cached_wav_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            library = LocalAudioLibrary(Path(tmpdir) / "audio", "http://localhost:8790")
            provider = MacOsSayTtsProvider(MacOsSayConfig(voice="Samantha", rate=180), library)

            def fake_run(command: list[str], **_: object) -> None:
                if command[0] == "say":
                    Path(command[command.index("-o") + 1]).write_bytes(b"AIFF")
                    return
                if command[0] == "afconvert":
                    write_test_wav(Path(command[-1]), [12000] * 200, frame_rate=1000)
                    return
                raise AssertionError(f"unexpected command: {command}")

            with patch.object(MacOsSayTtsProvider, "is_available", return_value=True):
                with patch("subprocess.run", side_effect=fake_run):
                    result = provider.synthesize(AudioOutputCommand(text="hello", format="wav"))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.duration_ms, 200)


if __name__ == "__main__":
    unittest.main()
