from __future__ import annotations

import unittest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.provider_reasoning import (
    ReasoningConfig,
    assistant_history_message,
    build_reasoning_request_extras,
    prepare_messages_for_provider,
)


class ProviderReasoningTests(unittest.TestCase):
    def test_deepseek_v4_builds_thinking_request_extras(self) -> None:
        extras = build_reasoning_request_extras(
            provider="deepseek",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com/v1",
            reasoning=ReasoningConfig(enabled=True, effort="high"),
        )

        self.assertEqual(extras.top_level, {"reasoning_effort": "high"})
        self.assertEqual(extras.body, {"thinking": {"type": "enabled"}})

    def test_non_deepseek_provider_gets_no_deepseek_extras(self) -> None:
        extras = build_reasoning_request_extras(
            provider="openai",
            model="gpt-4.1-mini",
            base_url="https://api.openai.com/v1",
            reasoning=ReasoningConfig(enabled=True, effort="high"),
        )

        self.assertEqual(extras.top_level, {})
        self.assertEqual(extras.body, {})

    def test_assistant_tool_history_preserves_deepseek_reasoning_content(self) -> None:
        message = assistant_history_message(
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "Need current date before weather lookup.",
                "tool_calls": [{"id": "call_date", "type": "function", "function": {"name": "get_date", "arguments": "{}"}}],
            },
            provider="deepseek",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com/v1",
            reasoning=ReasoningConfig(enabled=True, effort="high"),
        )

        self.assertEqual(message["reasoning_content"], "Need current date before weather lookup.")

    def test_assistant_tool_history_pads_missing_deepseek_reasoning_content(self) -> None:
        message = assistant_history_message(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_date", "type": "function", "function": {"name": "get_date", "arguments": "{}"}}],
            },
            provider="deepseek",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com/v1",
            reasoning=ReasoningConfig(enabled=True, effort="high"),
        )

        self.assertEqual(message["reasoning_content"], " ")

    def test_prepare_messages_strips_reasoning_content_for_other_providers(self) -> None:
        prepared = prepare_messages_for_provider(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning": "internal display reasoning",
                    "reasoning_content": "deepseek-only replay field",
                    "tool_calls": [{"id": "call_date", "type": "function", "function": {"name": "get_date", "arguments": "{}"}}],
                }
            ],
            provider="openai",
            model="gpt-4.1-mini",
            base_url="https://api.openai.com/v1",
            reasoning=ReasoningConfig(enabled=True, effort="high"),
        )

        self.assertNotIn("reasoning", prepared[0])
        self.assertNotIn("reasoning_content", prepared[0])

    def test_prepare_messages_replays_deepseek_reasoning_content(self) -> None:
        prepared = prepare_messages_for_provider(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "reasoned tool call",
                    "tool_calls": [{"id": "call_date", "type": "function", "function": {"name": "get_date", "arguments": "{}"}}],
                }
            ],
            provider="deepseek",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com/v1",
            reasoning=ReasoningConfig(enabled=True, effort="high"),
        )

        self.assertEqual(prepared[0]["reasoning_content"], "reasoned tool call")


if __name__ == "__main__":
    unittest.main()
