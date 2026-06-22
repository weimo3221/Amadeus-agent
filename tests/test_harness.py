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
                ]),
                encoding="utf-8",
            )

            config = parse_harnesses_config(config_path)

        self.assertEqual(config["live2d"]["enabled"], True)
        self.assertEqual(config["live2d"]["adapter"], "desktop-live2d")
        self.assertEqual(config["live2d"]["model"]["id"], "vivian")
        self.assertEqual(config["live2d"]["model"]["path"], "models/live2d/vivian/default.model3.json")

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
