from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.harness import HarnessContext, HarnessRegistry, Live2DHarness, parse_harnesses_config


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


if __name__ == "__main__":
    unittest.main()
