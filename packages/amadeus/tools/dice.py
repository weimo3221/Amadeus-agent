from __future__ import annotations

import random
from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int


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


DICE_TOOL_SPEC = ToolSpec(
    name="roll_dice",
    display_name="Rolling dice",
    permission="ask",
    enabled=True,
    handler=roll_dice,
    schema={
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": "Roll dice and return the random results. Use this when the user asks to roll dice.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sides": {
                        "type": "number",
                        "description": "Number of sides per die. Defaults to 6.",
                    },
                    "count": {
                        "type": "number",
                        "description": "Number of dice to roll. Defaults to 1 and is capped at 20.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
)
