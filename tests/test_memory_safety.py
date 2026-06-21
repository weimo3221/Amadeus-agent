from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.memory_safety import evaluate_memory_candidate


class MemorySafetyTests(unittest.TestCase):
    def test_blocks_secret_like_memory_candidates(self) -> None:
        decision = evaluate_memory_candidate(
            "project",
            "The project API key is OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz.",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.reason.startswith("secret:"))

    def test_blocks_temporary_debug_state_candidates(self) -> None:
        decision = evaluate_memory_candidate(
            "project",
            "The current run has a pytest failure and should rerun npm test.",
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.reason.startswith("temporary_debug:"))

    def test_allows_durable_preference_candidate(self) -> None:
        decision = evaluate_memory_candidate(
            "user",
            "The user prefers concise Chinese progress updates.",
            "The user explicitly requested this style.",
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "")


if __name__ == "__main__":
    unittest.main()
