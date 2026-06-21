from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.memory_safety import (
    detect_local_path_reason,
    detect_scope_mismatch_reason,
    detect_uncertain_claim_reason,
    evaluate_memory_candidate,
)


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

    def test_blocks_uncertain_english_claims(self) -> None:
        decision = evaluate_memory_candidate(
            "user",
            "The user might prefer shorter answers.",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "uncertain_claim:speculative_modal")

    def test_blocks_uncertain_chinese_claims(self) -> None:
        decision = evaluate_memory_candidate(
            "project",
            "项目可能需要切换到另一个 provider。",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "uncertain_claim:chinese_speculation")

    def test_blocks_temporary_local_paths(self) -> None:
        decision = evaluate_memory_candidate(
            "project",
            "The failing cache file is /var/folders/zz/example/cache.json.",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "local_path:tmp_path")

    def test_blocks_generated_or_cache_paths(self) -> None:
        self.assertEqual(
            detect_local_path_reason("The artifact lives in node_modules/.cache/vite/index.js."),
            "local_path:project_cache_path",
        )
        self.assertEqual(
            detect_local_path_reason("The local checkout is /Users/bytedance/Desktop/learning/Amadeus-agent."),
            "local_path:home_path",
        )
        self.assertEqual(
            detect_local_path_reason("The generated file is tmp/run-123/output.json."),
            "local_path:generated_artifact",
        )

    def test_uncertain_claim_detector_ignores_committed_facts(self) -> None:
        self.assertIsNone(detect_uncertain_claim_reason("The project uses Python-first AgentRuntime."))

    def test_blocks_project_fact_in_user_scope(self) -> None:
        decision = evaluate_memory_candidate(
            "user",
            "The project exposes POST /runtime/config/reload for dynamic config reload.",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "scope:user_contains_project:project_fact")

    def test_blocks_user_preference_in_project_scope(self) -> None:
        decision = evaluate_memory_candidate(
            "project",
            "The user prefers concise Chinese progress updates.",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "scope:project_contains_user:user_preference")

    def test_blocks_agent_behavior_in_project_scope(self) -> None:
        decision = evaluate_memory_candidate(
            "project",
            "The agent should ask before deleting files.",
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "scope:project_contains_agent:agent_behavior")

    def test_blocks_project_fact_in_agent_scope(self) -> None:
        self.assertEqual(
            detect_scope_mismatch_reason("agent", "The runtime stores memory review jobs in SQLite."),
            "scope:agent_contains_project:project_fact",
        )

    def test_allows_matching_scope_candidates(self) -> None:
        self.assertIsNone(detect_scope_mismatch_reason("user", "The user prefers direct answers."))
        self.assertIsNone(detect_scope_mismatch_reason("project", "The project uses Python-first AgentRuntime."))
        self.assertIsNone(detect_scope_mismatch_reason("agent", "The agent should provide concise updates."))

    def test_invalid_scope_is_rejected(self) -> None:
        decision = evaluate_memory_candidate("workspace", "The project uses Python-first runtime.")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "scope:invalid")

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
