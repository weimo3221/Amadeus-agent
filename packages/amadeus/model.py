from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str


class ChatModel(Protocol):
    def complete(self, messages: list[ModelMessage]) -> str:
        ...
