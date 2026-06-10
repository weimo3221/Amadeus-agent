from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.tool_runtime import ToolLoopGuardrail, ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def test_config_alias_updates_effective_tool_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "tools.yaml"
            config_path.write_text(
                "\n".join([
                    "tools:",
                    "  time:",
                    "    enabled: false",
                    "    permission: deny",
                ]),
                encoding="utf-8",
            )

            registry = ToolRegistry(config_path=config_path)

        tool_state = {entry["name"]: entry for entry in registry.permission_state()}
        schema_names = {entry["function"]["name"] for entry in registry.enabled_schemas()}

        self.assertFalse(tool_state["get_current_time"]["enabled"])
        self.assertEqual(tool_state["get_current_time"]["permission"], "deny")
        self.assertNotIn("get_current_time", schema_names)


class ToolLoopGuardrailTests(unittest.TestCase):
    def test_blocks_repeated_exact_failures_after_threshold(self) -> None:
        guardrail = ToolLoopGuardrail(max_failed_repeats=2)
        args = {"query": "missing"}

        self.assertTrue(guardrail.before_call("local_file_search", args).allowed)
        guardrail.after_call("local_file_search", args, {"error": "not found"}, ok=False)

        self.assertTrue(guardrail.before_call("local_file_search", args).allowed)
        guardrail.after_call("local_file_search", args, {"error": "not found"}, ok=False)

        decision = guardrail.before_call("local_file_search", args)
        self.assertFalse(decision.allowed)
        self.assertIn("Blocked repeated failing tool call", decision.reason or "")


if __name__ == "__main__":
    unittest.main()
