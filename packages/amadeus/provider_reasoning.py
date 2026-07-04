from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


REASONING_EFFORTS = {"low", "medium", "high"}


@dataclass(frozen=True)
class ReasoningConfig:
    enabled: bool = False
    effort: str = "medium"


@dataclass(frozen=True)
class ProviderReasoningExtras:
    top_level: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)


def normalize_reasoning_effort(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in REASONING_EFFORTS:
        return value.strip().lower()
    return "medium"


def _host_matches(base_url: str | None, needle: str) -> bool:
    host = urlparse(str(base_url or "")).netloc.lower()
    return host == needle or host.endswith(f".{needle}")


def supports_deepseek_thinking(model: str | None) -> bool:
    normalized = str(model or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith("deepseek-v") and not normalized.startswith("deepseek-v3"):
        return True
    return normalized == "deepseek-reasoner"


def is_deepseek_endpoint(provider: str | None, model: str | None, base_url: str | None) -> bool:
    normalized_provider = str(provider or "").strip().lower()
    normalized_model = str(model or "").strip().lower()
    return (
        normalized_provider == "deepseek"
        or "deepseek" in normalized_model
        or _host_matches(base_url, "api.deepseek.com")
    )


def needs_reasoning_content_echo(
    *,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    reasoning: ReasoningConfig,
) -> bool:
    return (
        reasoning.enabled
        and is_deepseek_endpoint(provider, model, base_url)
        and supports_deepseek_thinking(model)
    )


def build_reasoning_request_extras(
    *,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    reasoning: ReasoningConfig,
) -> ProviderReasoningExtras:
    if not is_deepseek_endpoint(provider, model, base_url) or not supports_deepseek_thinking(model):
        return ProviderReasoningExtras()

    body = {"thinking": {"type": "enabled" if reasoning.enabled else "disabled"}}
    top_level: dict[str, Any] = {}
    if reasoning.enabled:
        top_level["reasoning_effort"] = normalize_reasoning_effort(reasoning.effort)
    return ProviderReasoningExtras(top_level=top_level, body=body)


def assistant_history_message(
    message: dict[str, Any],
    *,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    reasoning: ReasoningConfig,
) -> dict[str, Any]:
    tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    history_message: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content") or "",
        "tool_calls": tool_calls,
    }
    raw_reasoning_content = message.get("reasoning_content")
    if isinstance(raw_reasoning_content, str):
        history_message["reasoning_content"] = raw_reasoning_content or " "
    elif needs_reasoning_content_echo(
        provider=provider,
        model=model,
        base_url=base_url,
        reasoning=reasoning,
    ):
        history_message["reasoning_content"] = " "
    return history_message


def prepare_messages_for_provider(
    messages: list[dict[str, Any]],
    *,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    reasoning: ReasoningConfig,
) -> list[dict[str, Any]]:
    needs_echo = needs_reasoning_content_echo(
        provider=provider,
        model=model,
        base_url=base_url,
        reasoning=reasoning,
    )
    prepared = copy.deepcopy(messages)
    for message in prepared:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue

        existing = message.get("reasoning_content")
        message.pop("reasoning", None)

        if needs_echo:
            if isinstance(existing, str) and existing:
                message["reasoning_content"] = existing
            else:
                message["reasoning_content"] = " "
        else:
            message.pop("reasoning_content", None)
    return prepared
