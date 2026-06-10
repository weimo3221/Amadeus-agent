from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolGuardrailDecision:
    allowed: bool
    reason: str | None = None


class ToolLoopGuardrail:
    def __init__(self, max_failed_repeats: int = 2) -> None:
        self.max_failed_repeats = max(1, max_failed_repeats)
        self._failed_signatures: dict[str, int] = {}

    def before_call(self, tool_name: str, args: dict[str, Any]) -> ToolGuardrailDecision:
        signature = self._signature(tool_name, args)
        failed_count = self._failed_signatures.get(signature, 0)
        if failed_count >= self.max_failed_repeats:
            return ToolGuardrailDecision(
                allowed=False,
                reason=f"Blocked repeated failing tool call: {tool_name}",
            )

        return ToolGuardrailDecision(allowed=True)

    def after_call(self, tool_name: str, args: dict[str, Any], result: dict[str, Any], ok: bool) -> None:
        if ok and "error" not in result:
            return

        signature = self._signature(tool_name, args)
        self._failed_signatures[signature] = self._failed_signatures.get(signature, 0) + 1

    @staticmethod
    def _signature(tool_name: str, args: dict[str, Any]) -> str:
        try:
            normalized_args = json.dumps(args, ensure_ascii=False, sort_keys=True)
        except TypeError:
            normalized_args = json.dumps(str(args), ensure_ascii=False)

        return f"{tool_name}:{normalized_args}"
