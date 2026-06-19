from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.tool_runtime import ToolContext, ToolLoopGuardrail, ToolRegistry
from amadeus.tools import ToolSpec


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

    def test_execute_returns_structured_tool_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="echo",
                        display_name="Echo",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "echo"}},
                        handler=lambda args: {"echo": args["text"]},
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute("echo", {"text": "hello"}, ToolContext(session_id="session-1"))

        self.assertTrue(result.ok)
        self.assertEqual(result.tool_name, "echo")
        self.assertEqual(result.output, {"echo": "hello"})
        self.assertEqual(result.model_output, {"echo": "hello"})
        self.assertIsNone(result.failure_code)
        self.assertIsNone(result.output_preview)
        self.assertFalse(result.output_truncated)
        self.assertGreaterEqual(result.duration_ms, 0)

    def test_execute_converts_handler_errors_to_structured_failure(self) -> None:
        def fail(_args: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="fail",
                        display_name="Fail",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "fail"}},
                        handler=fail,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute("fail", {}, ToolContext(session_id="session-1"))

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "tool_exception")
        self.assertEqual(result.output, {"error": "boom"})
        self.assertEqual(result.model_output, {"error": "boom"})
        self.assertFalse(result.output_truncated)

    def test_execute_times_out_slow_tool(self) -> None:
        def slow(_args: dict[str, object]) -> dict[str, object]:
            time.sleep(0.05)
            return {"ok": True}

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="slow",
                        display_name="Slow",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "slow"}},
                        handler=slow,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute("slow", {}, ToolContext(session_id="session-1", timeout_seconds=0.001))

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "tool_timeout")
        self.assertEqual(result.output, {"error": "Tool timed out: slow"})
        self.assertEqual(result.model_output, {"error": "Tool timed out: slow"})
        self.assertFalse(result.output_truncated)

    def test_execute_compresses_large_success_output_for_model_context(self) -> None:
        large_text = "x" * 200

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="large",
                        display_name="Large",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "large"}},
                        handler=lambda _args: {"text": large_text},
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "large",
                {},
                ToolContext(session_id="session-1", max_model_output_chars=80, output_preview_chars=40),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, {"text": large_text})
        self.assertTrue(result.output_truncated)
        self.assertIsNotNone(result.output_preview)
        self.assertLessEqual(len(result.output_preview or ""), 40)
        self.assertEqual(result.model_output["_amadeus_result_truncated"], True)
        self.assertEqual(result.model_output["tool_name"], "large")
        self.assertGreater(result.model_output["original_char_count"], 80)
        self.assertEqual(result.model_output["preview"], result.output_preview)


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
