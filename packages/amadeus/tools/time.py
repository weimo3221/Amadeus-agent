from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from amadeus.tools.base import ToolSpec


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


TIME_TOOL_SPEC = ToolSpec(
    name="get_current_time",
    display_name="Reading current time",
    permission="allow",
    enabled=True,
    handler=get_current_time,
    prompt_hint="Call this before answering questions about current time, current date, today, now, or scheduling context.",
    schema={
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current local date and time. Use this when the user asks about current time, date, today, now, or scheduling context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timeZone": {
                        "type": "string",
                        "description": "IANA timezone. Defaults to Asia/Shanghai.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
)
