from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.model import (
    ModelError,
    OpenAICompatibleConfig,
    OpenAICompatibleChatModel,
    classify_model_error,
    first_choice_message,
    is_context_overflow_error,
    model_error_from_http_error,
    parse_providers_config,
    parse_json_object_from_text,
)


class ModelBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_provider = os.environ.get("AMADEUS_LLM_PROVIDER")
        self.previous_base_url = os.environ.get("OPENAI_BASE_URL")
        self.previous_api_key = os.environ.get("OPENAI_API_KEY")
        self.previous_model = os.environ.get("OPENAI_MODEL")
        self.previous_deepseek_base_url = os.environ.get("DEEPSEEK_BASE_URL")
        self.previous_deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY")
        self.previous_deepseek_model = os.environ.get("DEEPSEEK_MODEL")

    def tearDown(self) -> None:
        self._restore_env("AMADEUS_LLM_PROVIDER", self.previous_provider)
        self._restore_env("OPENAI_BASE_URL", self.previous_base_url)
        self._restore_env("OPENAI_API_KEY", self.previous_api_key)
        self._restore_env("OPENAI_MODEL", self.previous_model)
        self._restore_env("DEEPSEEK_BASE_URL", self.previous_deepseek_base_url)
        self._restore_env("DEEPSEEK_API_KEY", self.previous_deepseek_api_key)
        self._restore_env("DEEPSEEK_MODEL", self.previous_deepseek_model)

    def test_openai_compatible_config_loads_environment_defaults(self) -> None:
        os.environ.pop("AMADEUS_LLM_PROVIDER", None)
        os.environ.pop("OPENAI_BASE_URL", None)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("OPENAI_MODEL", None)
        os.environ.pop("DEEPSEEK_BASE_URL", None)
        os.environ.pop("DEEPSEEK_API_KEY", None)
        os.environ.pop("DEEPSEEK_MODEL", None)

        config = OpenAICompatibleConfig.from_environment()

        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.model, "deepseek-v4-pro")

    def test_openai_compatible_config_loads_environment_overrides(self) -> None:
        os.environ["AMADEUS_LLM_PROVIDER"] = "openai"
        os.environ["OPENAI_BASE_URL"] = "https://example.test/"
        os.environ["OPENAI_API_KEY"] = "test-key"
        os.environ["OPENAI_MODEL"] = "test-model"

        config = OpenAICompatibleConfig.from_environment()

        self.assertEqual(config.base_url, "https://example.test")
        self.assertEqual(config.api_key, "test-key")
        self.assertEqual(config.model, "test-model")

    def test_openai_compatible_config_loads_providers_yaml_with_env_expansion(self) -> None:
        os.environ["DEEPSEEK_BASE_URL"] = "https://deepseek.test/"
        os.environ["DEEPSEEK_API_KEY"] = "deepseek-key"
        os.environ["DEEPSEEK_MODEL"] = "deepseek-test"
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "providers.yaml"
            config_path.write_text(
                "\n".join([
                    "llm:",
                    "  default: deepseek",
                    "  providers:",
                    "    deepseek:",
                    "      baseUrl: ${DEEPSEEK_BASE_URL}",
                    "      apiKey: ${DEEPSEEK_API_KEY}",
                    "      model: ${DEEPSEEK_MODEL}",
                    "      streaming: false",
                ]),
                encoding="utf-8",
            )

            config = OpenAICompatibleConfig.from_sources(config_path)

        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.base_url, "https://deepseek.test")
        self.assertEqual(config.api_key, "deepseek-key")
        self.assertEqual(config.model, "deepseek-test")
        self.assertFalse(config.streaming)

    def test_model_client_api_key_setter_preserves_provider_config(self) -> None:
        client = OpenAICompatibleChatModel(OpenAICompatibleConfig(
            provider="test-provider",
            base_url="https://example.test",
            api_key="old-key",
            model="test-model",
        ))

        client.api_key = "new-key"

        self.assertEqual(client.provider, "test-provider")
        self.assertEqual(client.base_url, "https://example.test")
        self.assertEqual(client.api_key, "new-key")
        self.assertEqual(client.model, "test-model")

    def test_first_choice_message_returns_empty_dict_for_malformed_response(self) -> None:
        self.assertEqual(first_choice_message({}), {})
        self.assertEqual(first_choice_message({"choices": [{"message": {"content": "ok"}}]}), {"content": "ok"})

    def test_parse_json_object_from_text_accepts_markdown_wrapped_json(self) -> None:
        parsed = parse_json_object_from_text("""```json
{"candidates":[{"content":"remember this"}]}
```""")

        self.assertEqual(parsed["candidates"][0]["content"], "remember this")

    def test_parse_json_object_from_text_extracts_embedded_json(self) -> None:
        parsed = parse_json_object_from_text('prefix {"ok": true} suffix')

        self.assertEqual(parsed, {"ok": True})

    def test_context_overflow_detection_matches_provider_errors(self) -> None:
        self.assertTrue(is_context_overflow_error(RuntimeError("maximum context length exceeded")))
        self.assertTrue(is_context_overflow_error(RuntimeError("Provider returned 413: request too large")))
        self.assertTrue(is_context_overflow_error(ModelError("too large", kind="payload_too_large")))
        self.assertFalse(is_context_overflow_error(RuntimeError("temporary upstream failure")))

    def test_model_error_classification_uses_status_and_body(self) -> None:
        self.assertEqual(classify_model_error(401, ""), "auth")
        self.assertEqual(classify_model_error(429, ""), "rate_limit")
        self.assertEqual(classify_model_error(503, ""), "server_error")
        self.assertEqual(classify_model_error(400, "maximum context length exceeded"), "context_overflow")

    def test_model_error_from_http_error_preserves_metadata(self) -> None:
        error = model_error_from_http_error(
            429,
            "slow down",
            retry_after="2",
            provider="deepseek",
            model="deepseek-test",
        )

        self.assertEqual(error.kind, "rate_limit")
        self.assertEqual(error.status_code, 429)
        self.assertEqual(error.body, "slow down")
        self.assertEqual(error.retry_after, "2")
        self.assertEqual(error.provider, "deepseek")
        self.assertEqual(error.model, "deepseek-test")

    def test_parse_providers_config_reads_current_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "providers.yaml"
            config_path.write_text(
                "\n".join([
                    "llm:",
                    "  default: openai_compatible",
                    "  providers:",
                    "    openai_compatible:",
                    "      baseUrl: https://example.test",
                    "      apiKey: test-key",
                    "      model: test-model",
                    "      streaming: true",
                ]),
                encoding="utf-8",
            )

            config = parse_providers_config(config_path)

        provider = config["llm"]["providers"]["openai_compatible"]
        self.assertEqual(config["llm"]["default"], "openai_compatible")
        self.assertEqual(provider["baseUrl"], "https://example.test")
        self.assertEqual(provider["apiKey"], "test-key")
        self.assertEqual(provider["model"], "test-model")
        self.assertTrue(provider["streaming"])

    def test_parse_providers_config_reads_embedding_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "providers.yaml"
            config_path.write_text(
                "\n".join([
                    "embedding:",
                    "  default: local_bge_m3",
                    "  providers:",
                    "    local_bge_m3:",
                    "      type: flag_embedding",
                    "      model: BAAI/bge-m3",
                    "      localPath: /tmp/bge-m3",
                    "      dimensions: 1024",
                    "      normalizeEmbeddings: true",
                ]),
                encoding="utf-8",
            )

            config = parse_providers_config(config_path)

        provider = config["embedding"]["providers"]["local_bge_m3"]
        self.assertEqual(config["embedding"]["default"], "local_bge_m3")
        self.assertEqual(provider["model"], "BAAI/bge-m3")
        self.assertEqual(provider["localPath"], "/tmp/bge-m3")
        self.assertEqual(provider["dimensions"], 1024)
        self.assertTrue(provider["normalizeEmbeddings"])

    @staticmethod
    def _restore_env(name: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
