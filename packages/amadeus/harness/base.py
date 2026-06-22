from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class HarnessCapability:
    name: str
    version: str
    events_in: list[str] = field(default_factory=list)
    events_out: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HarnessContext:
    session_id: str
    turn_id: str | None = None
    runtime_state: dict[str, Any] = field(default_factory=dict)
    client_capabilities: dict[str, Any] = field(default_factory=dict)


class Harness(Protocol):
    name: str

    def capabilities(self) -> HarnessCapability:
        ...

    def observe_event(self, context: HarnessContext, event: dict[str, Any]) -> list[dict[str, Any]]:
        ...
