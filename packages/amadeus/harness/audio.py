from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from amadeus.audio import AudioFallbackResult, AudioOutputCommand, AudioOutputResult
from amadeus.harness.base import HarnessCapability, HarnessContext

logger = logging.getLogger(__name__)


class AudioRuntimeLike(Protocol):
    def speak(self, command: AudioOutputCommand) -> AudioOutputResult | AudioFallbackResult:
        ...


@dataclass
class AudioHarness:
    enabled: bool = True
    audio_runtime: AudioRuntimeLike | None = None
    output_format: str = "wav"

    name: str = "audio"

    def capabilities(self) -> HarnessCapability:
        return HarnessCapability(
            name=self.name,
            version="0.1",
            events_in=["assistant.message"],
            events_out=["audio.lipsync-cues", "audio.tts-ready"],
        )

    def observe_event(self, context: HarnessContext, event: dict[str, Any]) -> list[dict[str, Any]]:
        if not self.enabled or self.audio_runtime is None:
            return []
        if event.get("type") != "assistant.message":
            return []
        if context.runtime_state.get("workerTurn") is True:
            return []

        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return []

        result = self.audio_runtime.speak(AudioOutputCommand(text=text, format=self.output_format))
        if isinstance(result, AudioFallbackResult):
            logger.info(
                "Runtime audio fallback sessionId=%s turnId=%s fallback=%s reason=%s",
                context.session_id,
                context.turn_id,
                result.fallback,
                result.reason,
            )
            return []

        logger.info(
            "Runtime audio ready sessionId=%s turnId=%s durationMs=%s provider=%s",
            context.session_id,
            context.turn_id,
            result.duration_ms,
            result.provider,
        )
        emitted: list[dict[str, Any]] = []
        if result.lipsync_cues:
            emitted.append(
                {
                    "type": "audio.lipsync-cues",
                    "payload": {
                        "source": "runtime_audio",
                        "audioUrl": result.audio_url,
                        "durationMs": result.duration_ms,
                        "cues": result.lipsync_cues,
                    },
                }
            )
        emitted.append(
            {
                "type": "audio.tts-ready",
                "payload": {
                    "audioUrl": result.audio_url,
                    "durationMs": result.duration_ms,
                },
            }
        )
        return emitted
