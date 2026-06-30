from __future__ import annotations

from pathlib import Path


DEFAULT_SOUL_MD = (
    "You are Amadeus, a calm, precise, and practical desktop Live2D companion agent. "
    "Help the user think, plan, search, remember, and execute tasks. "
    "Communicate clearly, admit uncertainty when appropriate, and prioritize useful, "
    "grounded action over verbose narration."
)
MAX_SOUL_MD_CHARS = 12000


def role_home_path(roles_root: Path, role_id: str) -> Path:
    return roles_root / role_id


def role_soul_path(roles_root: Path, role_id: str) -> Path:
    return role_home_path(roles_root, role_id) / "SOUL.md"


def ensure_role_soul(
    roles_root: Path,
    role_id: str,
    *,
    role_name: str | None = None,
    persona: str | None = None,
    style: str | None = None,
) -> Path:
    path = role_soul_path(roles_root, role_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    path.write_text(default_soul_for_role(role_name=role_name, persona=persona, style=style), encoding="utf-8")
    return path


def default_soul_for_role(
    *,
    role_name: str | None = None,
    persona: str | None = None,
    style: str | None = None,
) -> str:
    name = (role_name or "").strip()
    lines: list[str] = []
    if name:
        lines.append(f"You are {name}.")
    else:
        lines.append(DEFAULT_SOUL_MD)
    if persona and persona.strip():
        lines.append(f"Persona: {persona.strip()}")
    if style and style.strip():
        lines.append(f"Style: {style.strip()}")
    if len(lines) == 1 and name:
        lines.append(
            "You are a calm, precise, and practical desktop Live2D companion. "
            "Help the user think, plan, search, remember, and execute tasks."
        )
    return "\n".join(lines).strip()


def read_soul(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(content) > MAX_SOUL_MD_CHARS:
        return content[:MAX_SOUL_MD_CHARS].rstrip() + "\n\n[truncated]"
    return content


def normalize_soul_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.replace("\x00", "").strip()
    if not text:
        return None
    if len(text) > MAX_SOUL_MD_CHARS:
        raise ValueError(f"SOUL.md content must be at most {MAX_SOUL_MD_CHARS} characters")
    return text
