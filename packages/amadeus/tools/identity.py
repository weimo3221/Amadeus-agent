from __future__ import annotations

from typing import Any

from amadeus.tools.base import ToolSpec


def update_current_role_identity(args: dict[str, Any], context: Any) -> dict[str, Any]:
    memory_store = getattr(context, "memory_store", None)
    session_id = getattr(context, "session_id", None)
    if memory_store is None:
        return {"error": "memory store is not available"}
    if not isinstance(session_id, str) or not session_id:
        return {"error": "session id is not available"}

    name = args.get("name") if isinstance(args.get("name"), str) else None
    soul_text = args.get("soulText") if isinstance(args.get("soulText"), str) else None
    if name is None and soul_text is None:
        return {"error": "name or soulText is required"}

    try:
        identity = memory_store.update_role_identity_for_session(
            session_id,
            name=name,
            soul_text=soul_text,
        )
    except ValueError as error:
        return {"error": str(error)}

    return {
        "updated": True,
        "identity": identity,
    }


UPDATE_CURRENT_ROLE_IDENTITY_TOOL_SPEC = ToolSpec(
    name="update_current_role_identity",
    display_name="Updating current role identity",
    permission="ask",
    enabled=True,
    handler=update_current_role_identity,
    prompt_hint=(
        "Use only when the user explicitly asks to change this agent's name, identity, persona, "
        "or default speaking style; update the current session role only, not AGENT.md."
    ),
    schema={
        "type": "function",
        "function": {
            "name": "update_current_role_identity",
            "description": (
                "Update the current session role identity. This writes the role name and/or role SOUL.md; "
                "use only for explicit user requests to rename or restyle the agent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Optional new role display name, such as 小艾.",
                    },
                    "soulText": {
                        "type": "string",
                        "description": (
                            "Optional complete SOUL.md identity text for the role. Include the new name, "
                            "persona, and durable default style when changing identity."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
    },
)
