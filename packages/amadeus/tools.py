from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def normalize_positive_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback

    return max(minimum, min(maximum, number))


def get_current_time(args: dict[str, Any]) -> dict[str, Any]:
    requested_timezone = args.get("timeZone")
    time_zone = requested_timezone if isinstance(requested_timezone, str) and requested_timezone else "Asia/Shanghai"

    try:
        zone = ZoneInfo(time_zone)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
        time_zone = "UTC"

    now = datetime.now(zone)
    return {
        "iso": now.astimezone(timezone.utc).isoformat(),
        "timeZone": time_zone,
        "formatted": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


def roll_dice(args: dict[str, Any]) -> dict[str, Any]:
    sides = normalize_positive_int(args.get("sides"), 6, 2, 1000)
    count = normalize_positive_int(args.get("count"), 1, 1, 20)
    rolls = [random.randint(1, sides) for _ in range(count)]

    return {
        "sides": sides,
        "count": count,
        "rolls": rolls,
        "total": sum(rolls),
    }


TOOLS: dict[str, ToolHandler] = {
    "get_current_time": get_current_time,
    "roll_dice": roll_dice,
}


def list_tools() -> list[str]:
    return sorted(TOOLS)


def execute_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = TOOLS.get(tool_name)
    if not handler:
        raise KeyError(f"Unknown tool: {tool_name}")

    return handler(args)
