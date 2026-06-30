from __future__ import annotations

from amadeus.skills import SkillCatalog
from amadeus.tools.base import ToolSpec


def skills_list(_args: dict[str, object]) -> dict[str, object]:
    catalog = SkillCatalog()
    skills = catalog.skill_summaries()
    return {
        "skills": skills,
        "count": len(skills),
    }


def skill_view(args: dict[str, object]) -> dict[str, object]:
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        return {"error": "name must be a non-empty string"}

    catalog = SkillCatalog()
    skill = catalog.view_skill(name.strip())
    if skill is None:
        return {"error": f"Skill not found: {name.strip()}"}

    return skill


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
