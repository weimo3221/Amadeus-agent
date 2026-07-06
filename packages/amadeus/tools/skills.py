from __future__ import annotations

from typing import Any

from amadeus.role_scope import normalize_role_runtime_scope
from amadeus.skills import SkillCatalog
from amadeus.tools.base import ToolSpec


def _allowed_skills_from_context(context: Any | None) -> set[str] | None:
    memory_store = getattr(context, "memory_store", None)
    session_id = getattr(context, "session_id", None)
    if memory_store is None or not isinstance(session_id, str):
        return None
    try:
        scope = normalize_role_runtime_scope(memory_store.role_runtime_scope_for_session(session_id))
    except Exception:
        return None
    return set(scope.skills) if scope.skills else None


def skills_list(_args: dict[str, object], context: Any | None = None) -> dict[str, object]:
    catalog = SkillCatalog()
    skills = catalog.skill_summaries(allowed_skills=_allowed_skills_from_context(context))
    return {
        "skills": skills,
        "count": len(skills),
    }


def skill_view(args: dict[str, object], context: Any | None = None) -> dict[str, object]:
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        return {"error": "name must be a non-empty string"}

    catalog = SkillCatalog()
    skill = catalog.view_skill(name.strip(), allowed_skills=_allowed_skills_from_context(context))
    if skill is None:
        return {"error": f"Skill not found: {name.strip()}"}

    return skill


def skill_manage(args: dict[str, object]) -> dict[str, object]:
    action = args.get("action")
    if action != "save_experience":
        return {"error": "action must be save_experience"}
    name = args.get("name")
    description = args.get("description")
    instructions = args.get("instructions")
    category = args.get("category")
    overwrite = bool(args.get("overwrite")) if isinstance(args.get("overwrite"), bool) else False
    if not isinstance(name, str) or not name.strip():
        return {"error": "name must be a non-empty string"}
    if not isinstance(description, str) or not description.strip():
        return {"error": "description must be a non-empty string"}
    if not isinstance(instructions, str) or not instructions.strip():
        return {"error": "instructions must be a non-empty string"}

    catalog = SkillCatalog()
    try:
        return catalog.save_experience_skill(
            name=name,
            description=description,
            instructions=instructions,
            category=category if isinstance(category, str) and category.strip() else "experience",
            overwrite=overwrite,
        )
    except ValueError as error:
        return {"error": str(error)}


SKILLS_LIST_TOOL_SPEC = ToolSpec(
    name="skills_list",
    display_name="Skills List",
    permission="allow",
    enabled=True,
    schema={
        "type": "function",
        "function": {
            "name": "skills_list",
            "description": "List installed runtime skills with identifiers, descriptions, and declared tool preferences.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    handler=skills_list,
    prompt_hint="Use when the user asks what skills or workflows are available.",
)


SKILL_VIEW_TOOL_SPEC = ToolSpec(
    name="skill_view",
    display_name="Skill View",
    permission="allow",
    enabled=True,
    schema={
        "type": "function",
        "function": {
            "name": "skill_view",
            "description": "Load the full instructions for one installed runtime skill by identifier.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill identifier or unique skill name, such as development/runtime-debug or runtime-debug.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    handler=skill_view,
    prompt_hint="Use before relying on a relevant installed skill, and when the user asks to inspect, compare, debug, or use a specific skill.",
)


SKILL_MANAGE_TOOL_SPEC = ToolSpec(
    name="skill_manage",
    display_name="Skill Manage",
    permission="ask",
    enabled=True,
    schema={
        "type": "function",
        "function": {
            "name": "skill_manage",
            "description": "Save a reusable workflow or hard-won task experience as an installed runtime skill after user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["save_experience"],
                        "description": "Use save_experience to create or update a skill from a reusable workflow.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Stable kebab-case skill name or plain title; it will be slugified.",
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line summary shown in the skills catalog.",
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Full reusable workflow instructions, including pitfalls, commands, checks, and when to use it.",
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional skills category. Defaults to experience.",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Set true to replace an existing skill with the same category/name.",
                    },
                },
                "required": ["action", "name", "description", "instructions"],
                "additionalProperties": False,
            },
        },
    },
    handler=skill_manage,
    prompt_hint="After a difficult, repeated, or tool-heavy workflow, ask to save the reusable approach as a skill; use for procedural knowledge, not durable facts.",
)
