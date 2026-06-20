from __future__ import annotations

import threading
import tempfile
import time
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.tool_runtime import ToolAuditLog, ToolAuditStore, ToolContext, ToolLoopGuardrail, ToolRegistry
from amadeus.tools import ToolSpec, execute_tool, list_tools


class ToolRegistryTests(unittest.TestCase):
    def test_default_registry_includes_search_and_read_file_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(config_path=Path(tmpdir) / "missing-tools.yaml")

        tool_state = {entry["name"]: entry for entry in registry.permission_state()}
        schema_names = {entry["function"]["name"] for entry in registry.enabled_schemas()}

        self.assertIn("search_files", list_tools())
        self.assertIn("search_files", tool_state)
        self.assertEqual(tool_state["search_files"]["permission"], "ask")
        self.assertIn("search_files", schema_names)

        self.assertIn("local_file_search", list_tools())
        self.assertIn("local_file_search", tool_state)
        self.assertFalse(tool_state["local_file_search"]["enabled"])
        self.assertNotIn("local_file_search", schema_names)

        self.assertIn("read_file", list_tools())
        self.assertIn("read_file", tool_state)
        self.assertEqual(tool_state["read_file"]["permission"], "ask")
        self.assertIn("read_file", schema_names)

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

    def test_execute_marks_cancelled_context_as_structured_failure(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="cancelled",
                        display_name="Cancelled",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "cancelled"}},
                        handler=lambda _args: {"ok": True},
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "cancelled",
                {},
                ToolContext(session_id="session-1", cancel_event=cancel_event),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "tool_cancelled")
        self.assertEqual(result.output, {"error": "Tool cancelled: cancelled"})
        self.assertEqual(result.model_output, {"error": "Tool cancelled: cancelled"})

    def test_execute_sets_cancel_event_on_timeout(self) -> None:
        cancel_event = threading.Event()

        def slow(_args: dict[str, object], context: ToolContext) -> dict[str, object]:
            time.sleep(0.05)
            return {"cancelled": context.is_cancelled()}

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

            result = registry.execute(
                "slow",
                {},
                ToolContext(session_id="session-1", timeout_seconds=0.001, cancel_event=cancel_event),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "tool_timeout")
        self.assertTrue(cancel_event.is_set())

    def test_execute_passes_context_to_context_aware_handler(self) -> None:
        def read_context(_args: dict[str, object], context: ToolContext) -> dict[str, object]:
            return {"sessionId": context.session_id, "cwd": context.cwd.name}

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="read_context",
                        display_name="Read Context",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "read_context"}},
                        handler=read_context,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute("read_context", {}, ToolContext(session_id="session-1"))

        self.assertTrue(result.ok)
        self.assertEqual(result.output, {"sessionId": "session-1", "cwd": "Amadeus-agent"})

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

    def test_execute_applies_search_files_result_policy(self) -> None:
        long_preview = "match " + ("x" * 220)
        search_results = [
            {
                "path": f"packages/example_{index}.py",
                "line": index,
                "preview": long_preview,
                "match": "content",
            }
            for index in range(8)
        ]
        output = {
            "query": "example",
            "target": "all",
            "root": ".",
            "maxResults": 8,
            "results": search_results,
            "scannedFiles": 42,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="search_files",
                        display_name="Search",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "search_files"}},
                        handler=lambda _args: output,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "search_files",
                {"query": "example"},
                ToolContext(session_id="session-1", max_model_output_chars=4000, output_preview_chars=500),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, output)
        self.assertTrue(result.output_truncated)
        self.assertEqual(result.model_output["_amadeus_result_policy"], "search_files_v1")
        self.assertEqual(result.model_output["tool_name"], "search_files")
        self.assertEqual(result.model_output["target"], "all")
        self.assertEqual(result.model_output["resultCount"], 8)
        self.assertEqual(result.model_output["includedResults"], 5)
        self.assertEqual(result.model_output["omittedResults"], 3)
        self.assertEqual(len(result.model_output["results"]), 5)
        self.assertLessEqual(len(result.model_output["results"][0]["preview"]), 160)
        self.assertIsNotNone(result.output_preview)
        self.assertLessEqual(len(result.output_preview or ""), 160)

    def test_execute_keeps_small_search_files_result_unchanged(self) -> None:
        output = {
            "query": "example",
            "target": "content",
            "root": ".",
            "maxResults": 2,
            "results": [
                {
                    "path": "packages/example.py",
                    "line": 1,
                    "preview": "example",
                    "match": "content",
                },
            ],
            "scannedFiles": 3,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="search_files",
                        display_name="Search",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "search_files"}},
                        handler=lambda _args: output,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "search_files",
                {"query": "example"},
                ToolContext(session_id="session-1", max_model_output_chars=4000, output_preview_chars=500),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, output)
        self.assertEqual(result.model_output, output)
        self.assertFalse(result.output_truncated)
        self.assertIsNone(result.output_preview)

    def test_local_file_search_alias_still_executes(self) -> None:
        output = execute_tool("local_file_search", {"query": "README", "maxResults": 1})

        self.assertEqual(output["query"], "README")
        self.assertEqual(output["target"], "all")
        self.assertIn("results", output)

    def test_search_files_can_search_only_filenames(self) -> None:
        output = execute_tool("search_files", {"query": "README", "target": "files", "maxResults": 3})

        self.assertEqual(output["target"], "files")
        self.assertTrue(output["results"])
        self.assertTrue(all(result["match"] == "path" for result in output["results"]))

    def test_read_file_reads_workspace_text_file_with_line_numbers(self) -> None:
        output = execute_tool("read_file", {"path": "packages/amadeus/README.md", "lineLimit": 2, "maxChars": 200})

        self.assertEqual(output["path"], "packages/amadeus/README.md")
        self.assertIn("# Amadeus Runtime", output["content"])
        self.assertIn("     1 |", output["content"])
        self.assertEqual(output["startLine"], 1)
        self.assertEqual(output["lineCount"], 2)
        self.assertGreaterEqual(output["totalLines"], 2)
        self.assertTrue(output["hasMore"])
        self.assertLessEqual(len(output["content"]), 200)
        self.assertGreater(output["sizeBytes"], 0)

    def test_read_file_blocks_paths_outside_workspace(self) -> None:
        output = execute_tool("read_file", {"path": "../outside.txt"})

        self.assertEqual(output, {"error": "path must be inside the project workspace"})

    def test_read_file_supports_explicit_line_window(self) -> None:
        output = execute_tool(
            "read_file",
            {"path": "packages/amadeus/README.md", "startLine": 2, "lineLimit": 1, "maxChars": 200},
        )

        self.assertEqual(output["startLine"], 2)
        self.assertEqual(output["endLine"], 2)
        self.assertEqual(output["lineCount"], 1)
        self.assertTrue(output["content"].startswith("     2 |"))

    def test_execute_does_not_apply_read_file_result_policy(self) -> None:
        content = "x" * 5000
        output = {
            "path": "packages/example.py",
            "sizeBytes": len(content),
            "charCount": len(content),
            "totalLines": 1,
            "startLine": 1,
            "endLine": 1,
            "lineCount": 1,
            "lineLimit": 1,
            "maxChars": len(content),
            "hasMore": False,
            "truncated": False,
            "content": content,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = ToolRegistry(
                specs=[
                    ToolSpec(
                        name="read_file",
                        display_name="Read",
                        permission="allow",
                        enabled=True,
                        schema={"type": "function", "function": {"name": "read_file"}},
                        handler=lambda _args: output,
                    ),
                ],
                config_path=Path(tmpdir) / "missing-tools.yaml",
            )

            result = registry.execute(
                "read_file",
                {"path": "packages/example.py"},
                ToolContext(session_id="session-1", max_model_output_chars=4000, output_preview_chars=200),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.output, output)
        self.assertEqual(result.model_output, output)
        self.assertFalse(result.output_truncated)
        self.assertIsNone(result.output_preview)


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
        self.assertEqual(decision.failure_code, "guardrail_blocked")

    def test_blocks_repeated_completed_calls_as_no_progress(self) -> None:
        guardrail = ToolLoopGuardrail(max_completed_repeats=2)
        args = {"query": "same"}

        self.assertTrue(guardrail.before_call("local_file_search", args).allowed)
        guardrail.after_call("local_file_search", args, {"results": []}, ok=True)

        self.assertTrue(guardrail.before_call("local_file_search", args).allowed)
        guardrail.after_call("local_file_search", args, {"results": []}, ok=True)

        decision = guardrail.before_call("local_file_search", args)
        self.assertFalse(decision.allowed)
        self.assertIn("Blocked no-progress repeated tool call", decision.reason or "")
        self.assertEqual(decision.failure_code, "no_progress_loop")


class ToolAuditStoreTests(unittest.TestCase):
    def test_saves_and_loads_audit_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ToolAuditStore(Path(tmpdir) / "amadeus.sqlite")
            log = ToolAuditLog()
            record = log.append(
                session_id="session-1",
                tool_name="get_current_time",
                decision="finished",
                ok=True,
                duration_ms=12,
            )

            store.save(record)
            loaded = store.load(session_id="session-1")

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].record_id, record.record_id)
        self.assertEqual(loaded[0].session_id, "session-1")
        self.assertEqual(loaded[0].tool_name, "get_current_time")
        self.assertEqual(loaded[0].decision, "finished")
        self.assertTrue(loaded[0].ok)
        self.assertEqual(loaded[0].duration_ms, 12)

    def test_filters_audit_records_by_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ToolAuditStore(Path(tmpdir) / "amadeus.sqlite")
            log = ToolAuditLog()
            store.save(log.append(session_id="session-1", tool_name="a", decision="started"))
            store.save(log.append(session_id="session-2", tool_name="b", decision="started"))

            loaded = store.load(session_id="session-2")

        self.assertEqual([record.session_id for record in loaded], ["session-2"])
        self.assertEqual([record.tool_name for record in loaded], ["b"])


if __name__ == "__main__":
    unittest.main()
