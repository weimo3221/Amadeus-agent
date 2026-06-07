from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentEvent:
    type: str
    payload: dict


class AgentRuntime:
    """Placeholder boundary for the Python-owned agent loop."""

    def plan_response(self, user_text: str) -> list[AgentEvent]:
        return [
            AgentEvent("assistant.state", {"state": "thinking"}),
            AgentEvent("agent.input", {"text": user_text}),
        ]
