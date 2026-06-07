from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Live2DCommand:
    state: str | None = None
    expression: str | None = None
    motion: str | None = None
    intensity: float | None = None


@dataclass(frozen=True)
class LipsyncCue:
    offset_ms: int
    mouth_open: float
