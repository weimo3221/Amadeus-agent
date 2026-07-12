from __future__ import annotations

from typing import Any

from amadeus.tools.base import ToolSpec


MAX_CLARIFY_OPTIONS = 5


def clarify(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    _ = context
    question = args.get("question").strip() if isinstance(args.get("question"), str) else ""
    if not question:
        return {"error": "question is required"}
    raw_options = args.get("options")
    options: list[dict[str, str]] = []
    if isinstance(raw_options, list):
        for item in raw_options[:MAX_CLARIFY_OPTIONS]:
            if isinstance(item, str) and item.strip():
                options.append({"label": item.strip(), "description": ""})
            elif isinstance(item, dict):
                label = item.get("label").strip() if isinstance(item.get("label"), str) else ""
                description = item.get("description").strip() if isinstance(item.get("description"), str) else ""
                if label:
                    options.append({"label": label, "description": description})
    allow_free_text = args.get("allowFreeText")
    if not isinstance(allow_free_text, bool):
        allow_free_text = True
    return {
        "clarificationRequired": True,
        "question": question,
        "options": options,
        "allowFreeText": allow_free_text,
        "instruction": "Ask the user this question and wait for their answer before taking irreversible action.",
    }


CLARIFY_TOOL_SPEC = ToolSpec(
    name="clarify",
    display_name="Asking clarification",
    permission="allow",
    enabled=True,
    handler=clarify,
    prompt_hint="Use when a short clarifying question is needed before irreversible or ambiguous work. Do not use for routine internal planning.",
    schema={
        "type": "function",
        "function": {
            "name": "clarify",
            "description": "Prepare a user-facing clarifying question with optional choices. The tool returns a structured clarification request for the assistant to present.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Single concise question to ask the user."},
                    "options": {
                        "type": "array",
                        "description": "Optional choices. At most 5 are used.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["label"],
                            "additionalProperties": False,
                        },
                    },
                    "allowFreeText": {"type": "boolean", "description": "Whether the user may answer outside the choices. Defaults to true."},
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        },
    },
)
