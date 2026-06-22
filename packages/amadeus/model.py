from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Iterable, Literal
from typing import Protocol

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROVIDERS_CONFIG_PATH = REPO_ROOT / "configs" / "providers.yaml"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
ModelErrorKind = Literal[
    "auth",
    "rate_limit",
    "server_error",
    "timeout",
    "context_overflow",
    "payload_too_large",
    "format_error",
    "model_not_found",
    "unknown",
]


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str


class ChatModel(Protocol):
    def complete(self, messages: list[ModelMessage]) -> str:
        ...


class ModelError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: ModelErrorKind = "unknown",
        status_code: int | None = None,
        body: str | None = None,
        retry_after: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.body = body
        self.retry_after = retry_after
        self.provider = provider
        self.model = model


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    api_mode: str = "openai_compatible"
    supports_streaming: bool = True
    default_headers: dict[str, str] = field(default_factory=dict)
    request_timeout_seconds: int = 60
    stream_timeout_seconds: int = 120


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    provider: str
    base_url: str
    api_key: str
    model: str
    streaming: bool = True
    default_headers: dict[str, str] = field(default_factory=dict)
    request_timeout_seconds: int = 60
    stream_timeout_seconds: int = 120

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleConfig":
        return cls(
            provider="openai_compatible",
            base_url=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        )

    @classmethod
    def from_sources(cls, config_path: Path = DEFAULT_PROVIDERS_CONFIG_PATH) -> "OpenAICompatibleConfig":
        llm_config = parse_providers_config(config_path).get("llm", {})
        providers = llm_config.get("providers") if isinstance(llm_config.get("providers"), dict) else {}
        default_provider = str(llm_config.get("default") or "openai_compatible")
        provider_entry = providers.get(default_provider) if isinstance(providers.get(default_provider), dict) else {}
        profile = ProviderProfile(
            name=default_provider,
            supports_streaming=parse_bool_value(provider_entry.get("streaming"), True),
        )
        return cls(
            provider=profile.name,
            base_url=str(provider_entry.get("baseUrl") or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            api_key=str(provider_entry.get("apiKey") or os.environ.get("OPENAI_API_KEY") or ""),
            model=str(provider_entry.get("model") or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL),
            streaming=profile.supports_streaming,
            default_headers=profile.default_headers,
            request_timeout_seconds=profile.request_timeout_seconds,
            stream_timeout_seconds=profile.stream_timeout_seconds,
        )


class OpenAICompatibleChatModel:
    def __init__(self, config: OpenAICompatibleConfig | None = None) -> None:
        self.config = config or OpenAICompatibleConfig.from_sources()

    @property
    def provider(self) -> str:
        return self.config.provider

    @property
    def base_url(self) -> str:
        return self.config.base_url

    @property
    def api_key(self) -> str:
        return self.config.api_key

    @api_key.setter
    def api_key(self, value: str) -> None:
        self.config = OpenAICompatibleConfig(
            provider=self.config.provider,
            base_url=self.config.base_url,
            api_key=value,
            model=self.config.model,
            streaming=self.config.streaming,
            default_headers=self.config.default_headers,
            request_timeout_seconds=self.config.request_timeout_seconds,
            stream_timeout_seconds=self.config.stream_timeout_seconds,
        )

    @property
    def model(self) -> str:
        return self.config.model

    def post_chat_completion(self, payload: dict[str, Any], *, timeout_seconds: int | None = None) -> dict[str, Any]:
        return self._post_json("/chat/completions", payload, timeout_seconds=timeout_seconds or self.config.request_timeout_seconds)

    def stream_chat_completion(self, payload: dict[str, Any], *, timeout_seconds: int | None = None) -> Iterable[str]:
        if not self.config.streaming:
            raise ModelError(
                f"Provider {self.provider} does not support streaming.",
                kind="unknown",
                provider=self.provider,
                model=self.model,
            )
        timeout_seconds = timeout_seconds or self.config.stream_timeout_seconds
        request_start = perf_counter()
        chunk_count = 0
        content_chars = 0
        logger.info(
            "Provider stream request starting path=%s model=%s messageCount=%s timeoutSeconds=%s",
            "/chat/completions",
            payload.get("model"),
            len(payload.get("messages", [])) if isinstance(payload.get("messages"), list) else None,
            timeout_seconds,
        )
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                logger.info(
                    "Provider stream response opened path=%s model=%s status=%s elapsedMs=%s",
                    "/chat/completions",
                    payload.get("model"),
                    getattr(response, "status", None),
                    round((perf_counter() - request_start) * 1000),
                )
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue

                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue

                    try:
                        payload_data = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choices = payload_data.get("choices") if isinstance(payload_data.get("choices"), list) else []
                    first = choices[0] if choices and isinstance(choices[0], dict) else {}
                    delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        chunk_count += 1
                        content_chars += len(content)
                        yield content
                logger.info(
                    "Provider stream request finished path=%s model=%s chunks=%s contentChars=%s elapsedMs=%s",
                    "/chat/completions",
                    payload.get("model"),
                    chunk_count,
                    content_chars,
                    round((perf_counter() - request_start) * 1000),
                )
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            retry_after = error.headers.get("Retry-After") if error.headers else None
            logger.info(
                "Provider stream request failed path=%s model=%s status=%s elapsedMs=%s bodyChars=%s",
                "/chat/completions",
                payload.get("model"),
                error.code,
                round((perf_counter() - request_start) * 1000),
                len(body),
            )
            raise model_error_from_http_error(
                error.code,
                body or str(error.reason),
                retry_after=retry_after,
                provider=self.provider,
                model=self.model,
            ) from error
        except OSError as error:
            logger.info(
                "Provider stream request failed path=%s model=%s error=%s elapsedMs=%s",
                "/chat/completions",
                payload.get("model"),
                error,
                round((perf_counter() - request_start) * 1000),
            )
            raise ModelError(
                str(error),
                kind="timeout" if "timed out" in str(error).lower() else "unknown",
                provider=self.provider,
                model=self.model,
            ) from error

    def _post_json(self, path: str, payload: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
        request_start = perf_counter()
        serialized_payload = json.dumps(payload).encode("utf-8")
        logger.info(
            "Provider JSON request starting path=%s model=%s stream=%s messageCount=%s toolCount=%s payloadBytes=%s timeoutSeconds=%s",
            path,
            payload.get("model"),
            payload.get("stream"),
            len(payload.get("messages", [])) if isinstance(payload.get("messages"), list) else None,
            len(payload.get("tools", [])) if isinstance(payload.get("tools"), list) else None,
            len(serialized_payload),
            timeout_seconds,
        )
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=serialized_payload,
            headers=self._headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
                logger.info(
                    "Provider JSON request finished path=%s model=%s status=%s responseChars=%s elapsedMs=%s",
                    path,
                    payload.get("model"),
                    getattr(response, "status", None),
                    len(raw_body),
                    round((perf_counter() - request_start) * 1000),
                )
                data = json.loads(raw_body)
                return data if isinstance(data, dict) else {}
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            retry_after = error.headers.get("Retry-After") if error.headers else None
            logger.info(
                "Provider JSON request failed path=%s model=%s status=%s elapsedMs=%s bodyChars=%s",
                path,
                payload.get("model"),
                error.code,
                round((perf_counter() - request_start) * 1000),
                len(body),
            )
            raise model_error_from_http_error(
                error.code,
                body or str(error.reason),
                retry_after=retry_after,
                provider=self.provider,
                model=self.model,
            ) from error
        except json.JSONDecodeError as error:
            logger.info(
                "Provider JSON request failed path=%s model=%s error=%s elapsedMs=%s",
                path,
                payload.get("model"),
                error,
                round((perf_counter() - request_start) * 1000),
            )
            raise ModelError(
                f"Provider returned invalid JSON: {error}",
                kind="format_error",
                provider=self.provider,
                model=self.model,
            ) from error
        except OSError as error:
            logger.info(
                "Provider JSON request failed path=%s model=%s error=%s elapsedMs=%s",
                path,
                payload.get("model"),
                error,
                round((perf_counter() - request_start) * 1000),
            )
            raise ModelError(
                str(error),
                kind="timeout" if "timed out" in str(error).lower() else "unknown",
                provider=self.provider,
                model=self.model,
            ) from error

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.config.default_headers)
        return headers


def first_choice_message(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices") if isinstance(data.get("choices"), list) else []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    return message


def model_error_from_http_error(
    status_code: int,
    body: str,
    *,
    retry_after: str | None,
    provider: str,
    model: str,
) -> ModelError:
    kind = classify_model_error(status_code, body)
    return ModelError(
        f"Provider returned {status_code}: {body}",
        kind=kind,
        status_code=status_code,
        body=body,
        retry_after=retry_after,
        provider=provider,
        model=model,
    )


def classify_model_error(status_code: int | None, body: str = "") -> ModelErrorKind:
    normalized = body.lower()
    if status_code in {401, 403}:
        return "auth"
    if status_code == 404:
        return "model_not_found"
    if status_code == 408:
        return "timeout"
    if status_code == 413:
        return "payload_too_large"
    if status_code == 429:
        return "rate_limit"
    if status_code is not None and status_code >= 500:
        return "server_error"
    if any(needle in normalized for needle in ("context length", "context window", "maximum context", "too many tokens")):
        return "context_overflow"
    if any(needle in normalized for needle in ("payload too large", "request too large")):
        return "payload_too_large"
    if any(needle in normalized for needle in ("invalid json", "malformed", "schema")):
        return "format_error"
    return "unknown"


def parse_providers_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    config: dict[str, Any] = {}
    current_top: str | None = None
    in_providers = False
    current_provider: str | None = None

    for raw_line in lines:
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        trimmed = line.strip()

        if indent == 0 and trimmed.endswith(":"):
            current_top = trimmed[:-1]
            config[current_top] = {}
            in_providers = False
            current_provider = None
            continue

        if current_top is None:
            continue

        if indent == 2 and ":" in trimmed:
            key, raw_value = trimmed.split(":", 1)
            key = key.strip()
            raw_value = raw_value.strip()
            if raw_value == "":
                config[current_top][key] = {}
                in_providers = key == "providers"
                current_provider = None
            else:
                config[current_top][key] = parse_scalar_config_value(raw_value)
                in_providers = False
                current_provider = None
            continue

        if indent == 4 and in_providers and trimmed.endswith(":"):
            current_provider = trimmed[:-1]
            config[current_top].setdefault("providers", {})[current_provider] = {}
            continue

        if indent == 6 and in_providers and current_provider and ":" in trimmed:
            key, raw_value = trimmed.split(":", 1)
            config[current_top]["providers"][current_provider][key.strip()] = parse_scalar_config_value(raw_value.strip())

    return config


def parse_scalar_config_value(value: str) -> Any:
    if value == "":
        return ""
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def parse_bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return default


def parse_json_object_from_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("provider returned invalid JSON")
        try:
            parsed = json.loads(stripped[start:end + 1])
        except json.JSONDecodeError as error:
            raise RuntimeError("provider returned invalid JSON") from error

    if not isinstance(parsed, dict):
        raise RuntimeError("provider returned JSON that is not an object")
    return parsed


def is_context_overflow_error(error: RuntimeError) -> bool:
    if isinstance(error, ModelError):
        return error.kind in {"context_overflow", "payload_too_large"}

    message = str(error).lower()
    needles = (
        "context length",
        "context window",
        "maximum context",
        "too many tokens",
        "payload too large",
        "request too large",
        "413",
    )
    return any(needle in message for needle in needles)
