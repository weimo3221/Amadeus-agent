from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.harness import HarnessContext, HarnessFeedbackPolicy, HarnessRegistry, Live2DHarness, parse_harnesses_config


class HarnessTests(unittest.TestCase):
    def test_parse_harnesses_config_reads_live2d_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "harnesses.yaml"
            config_path.write_text(
                "\n".join([
                    "harnesses:",
                    "  live2d:",
                    "    enabled: true",
                    "    adapter: desktop-live2d",
                    "    model:",
                    "      id: vivian",
                    "      path: models/live2d/vivian/default.model3.json",
                    "    audioPlaybackBehaviors:",
                    "      started:",
                    "        emotion: happy",
                    "        expression: grin",
                    "        motion: custom_talk",
                    "        intensity: 0.9",
                    "      audio.playback-ended:",
                    "        motion: custom_idle",
                    "    lipsync:",
                    "      cueIntervalMs: 120",
                    "      maxCues: 6",
                ]),
                encoding="utf-8",
            )

            config = parse_harnesses_config(config_path)

        self.assertEqual(config["live2d"]["enabled"], True)
        self.assertEqual(config["live2d"]["adapter"], "desktop-live2d")
        self.assertEqual(config["live2d"]["model"]["id"], "vivian")
        self.assertEqual(config["live2d"]["model"]["path"], "models/live2d/vivian/default.model3.json")
        self.assertEqual(config["live2d"]["audioPlaybackBehaviors"]["started"]["motion"], "custom_talk")
        self.assertEqual(config["live2d"]["audioPlaybackBehaviors"]["started"]["intensity"], 0.9)
        self.assertEqual(config["live2d"]["audioPlaybackBehaviors"]["audio.playback-ended"]["motion"], "custom_idle")
        self.assertEqual(config["live2d"]["lipsync"]["cueIntervalMs"], 120)
        self.assertEqual(config["live2d"]["lipsync"]["maxCues"], 6)

    def test_live2d_harness_maps_assistant_state_to_character_behavior(self) -> None:
        harness = Live2DHarness()

        events = harness.observe_event(
            HarnessContext(session_id="default", turn_id="turn-1"),
            {"type": "assistant.state", "payload": {"state": "thinking"}},
        )

        self.assertEqual(events, [{
            "type": "character.behavior",
            "payload": {
                "emotion": "focused",
                "expression": "serious",
                "motion": "think",
                "intensity": 0.6,
            },
        }])

    def test_live2d_harness_maps_audio_playback_feedback_to_character_behavior(self) -> None:
        harness = Live2DHarness()
        context = HarnessContext(
            session_id="default",
            client_capabilities={
                "live2d": {
                    "available": True,
                    "modelId": "hiyori-free",
                    "expressions": ["smile"],
                    "motions": ["Idle", "TapBody"],
                },
            },
        )

        started = harness.observe_event(context, {
            "type": "audio.playback-started",
            "payload": {"source": "runtime_audio", "audioUrl": "http://runtime/audio.wav", "durationMs": 400},
        })
        ended = harness.observe_event(context, {
            "type": "audio.playback-ended",
            "payload": {"source": "runtime_audio", "audioUrl": "http://runtime/audio.wav"},
        })
        failed = harness.observe_event(context, {
            "type": "audio.playback-error",
            "payload": {"source": "runtime_audio", "reason": "audio_element_error"},
        })

        self.assertEqual(started[0]["payload"]["motion"], "talk")
        self.assertEqual(started[0]["payload"]["expression"], "smile")
        self.assertEqual(started[1]["type"], "audio.lipsync-cues")
        self.assertEqual(started[1]["payload"]["source"], "runtime_audio")
        self.assertEqual(started[1]["payload"]["audioUrl"], "http://runtime/audio.wav")
        self.assertGreaterEqual(len(started[1]["payload"]["cues"]), 2)
        self.assertEqual(ended[0]["payload"]["motion"], "idle")
        self.assertEqual(failed[0]["payload"]["motion"], "shake_head")

    def test_live2d_harness_ignores_audio_feedback_when_live2d_unavailable(self) -> None:
        harness = Live2DHarness()

        events = harness.observe_event(
            HarnessContext(session_id="default", client_capabilities={"live2d": {"available": False}}),
            {"type": "audio.playback-started", "payload": {"source": "runtime_audio"}},
        )

        self.assertEqual(events, [])

    def test_live2d_harness_skips_lipsync_cues_without_runtime_audio_duration(self) -> None:
        harness = Live2DHarness()
        context = HarnessContext(
            session_id="default",
            client_capabilities={"live2d": {"available": True}},
        )

        events = harness.observe_event(
            context,
            {"type": "audio.playback-started", "payload": {"source": "runtime_audio", "audioUrl": "http://runtime/audio.wav"}},
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "character.behavior")

    def test_live2d_harness_skips_fallback_cues_when_runtime_cues_are_already_active(self) -> None:
        harness = Live2DHarness()
        context = HarnessContext(
            session_id="default",
            client_capabilities={"live2d": {"available": True}},
        )

        events = harness.observe_event(
            context,
            {
                "type": "audio.playback-started",
                "payload": {
                    "source": "runtime_audio",
                    "audioUrl": "http://runtime/audio.wav",
                    "durationMs": 400,
                    "runtimeCuesActive": True,
                },
            },
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "character.behavior")

    def test_harness_registry_reads_configured_audio_playback_behaviors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "harnesses.yaml"
            config_path.write_text(
                "\n".join([
                    "harnesses:",
                    "  live2d:",
                    "    enabled: true",
                    "    audioPlaybackBehaviors:",
                    "      started:",
                    "        expression: grin",
                    "        motion: custom_talk",
                    "        intensity: 0.9",
                    "      ended:",
                    "        motion: custom_idle",
                    "    lipsync:",
                    "      cueIntervalMs: 120",
                    "      maxCues: 6",
                ]),
                encoding="utf-8",
            )

            registry = HarnessRegistry.from_config(config_path)

        started = registry.observe_event(
            HarnessContext(session_id="default", client_capabilities={"live2d": {"available": True}}),
            {"type": "audio.playback-started", "payload": {"source": "runtime_audio"}},
        )
        ended = registry.observe_event(
            HarnessContext(session_id="default", client_capabilities={"live2d": {"available": True}}),
            {"type": "audio.playback-ended", "payload": {"source": "runtime_audio"}},
        )
        started_with_duration = registry.observe_event(
            HarnessContext(session_id="default", client_capabilities={"live2d": {"available": True}}),
            {"type": "audio.playback-started", "payload": {"source": "runtime_audio", "durationMs": 900}},
        )

        self.assertEqual(started[0]["payload"]["expression"], "grin")
        self.assertEqual(started[0]["payload"]["motion"], "custom_talk")
        self.assertEqual(started[0]["payload"]["intensity"], 0.9)
        self.assertEqual(ended[0]["payload"]["expression"], "neutral")
        self.assertEqual(ended[0]["payload"]["motion"], "custom_idle")
        self.assertEqual(started_with_duration[1]["type"], "audio.lipsync-cues")
        self.assertLessEqual(len(started_with_duration[1]["payload"]["cues"]), 7)

    def test_harness_registry_can_disable_live2d(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "harnesses.yaml"
            config_path.write_text(
                "\n".join([
                    "harnesses:",
                    "  live2d:",
                    "    enabled: false",
                ]),
                encoding="utf-8",
            )

            registry = HarnessRegistry.from_config(config_path)

        self.assertEqual(registry.capabilities(), [])
        self.assertEqual(
            registry.observe_event(
                HarnessContext(session_id="default"),
                {"type": "assistant.state", "payload": {"state": "thinking"}},
            ),
            [],
        )

    def test_harness_feedback_policy_tracks_capabilities_and_audio_state(self) -> None:
        policy = HarnessFeedbackPolicy(event_limit=2)

        capabilities = policy.record_feedback(
            "session-1",
            "desktop.capabilities",
            {
                "desktop": {"runtime": "electron", "protocolVersion": 1},
                "live2d": {
                    "available": True,
                    "modelId": "hiyori-free",
                    "expressions": ["smile", 1],
                    "motions": ["Idle"],
                },
                "audio": {
                    "runtimeAudio": True,
                    "speechSynthesis": True,
                    "voiceCount": 3,
                },
            },
            timestamp="2026-06-22T00:00:00.000Z",
        )

        self.assertEqual(capabilities["desktopCapabilities"]["live2d"]["modelId"], "hiyori-free")
        self.assertEqual(capabilities["desktopCapabilities"]["live2d"]["expressions"], ["smile"])
        self.assertEqual(policy.client_capabilities("session-1")["audio"]["voiceCount"], 3)

        playing = policy.record_feedback(
            "session-1",
            "audio.playback-started",
            {"source": "runtime_audio", "audioUrl": "http://runtime/audio.wav"},
            timestamp="2026-06-22T00:00:01.000Z",
        )
        self.assertEqual(playing["audioPlayback"]["status"], "playing")
        self.assertEqual(policy.runtime_state("session-1")["audioPlayback"]["status"], "playing")

        failed = policy.record_feedback(
            "session-1",
            "audio.playback-error",
            {"source": "runtime_audio", "audioUrl": "http://runtime/audio.wav", "reason": "audio_element_error"},
            timestamp="2026-06-22T00:00:02.000Z",
        )
        self.assertEqual(failed["audioPlayback"]["status"], "error")
        self.assertEqual(failed["audioPlayback"]["reason"], "audio_element_error")
        self.assertEqual(failed["recentEventCount"], 2)
        self.assertEqual([event["type"] for event in failed["recentEvents"]], [
            "audio.playback-started",
            "audio.playback-error",
        ])


if __name__ == "__main__":
    unittest.main()
