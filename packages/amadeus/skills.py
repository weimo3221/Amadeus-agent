from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILLS_ROOT = REPO_ROOT / "skills"
SKILL_FILE_NAME = "SKILL.md"
MAX_SKILL_DESCRIPTION_CHARS = 280
MAX_SKILL_BODY_CHARS = 12000
KNOWN_SKILL_RESOURCE_DIRS = ("scripts", "references", "assets", "agents", "evals")
_SLUG_INVALID_CHARS_RE = re.compile(r"[^a-z0-9-]")
_SLUG_MULTI_DASH_RE = re.compile(r"-{2,}")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    identifier: str
    path: Path
    category: str | None = None
    instructions: str = ""
    preferred_tools: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    compatibility: dict[str, Any] = field(default_factory=dict)
    platforms: tuple[str, ...] = ()
    resource_dirs: tuple[str, ...] = ()
    has_evals: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return slugify(self.name)

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "identifier": self.identifier,
            "description": self.description,
            "category": self.category,
            "preferredTools": list(self.preferred_tools),
            "allowedTools": list(self.allowed_tools),
            "compatibility": dict(self.compatibility),
            "platforms": list(self.platforms),
            "resourceDirs": list(self.resource_dirs),
            "hasEvals": self.has_evals,
            "path": str(self.path),
        }


@dataclass(frozen=True)
class ResolvedSkills:
    loaded: tuple[Skill, ...]
    missing: tuple[str, ...]
    ambiguous: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing and not self.ambiguous


@dataclass(frozen=True)
class SkillValidationIssue:
    level: str
    code: str
    message: str
    path: str


@dataclass(frozen=True)
class SkillValidationResult:
    skill_dir: Path
    identifier: str
    errors: tuple[SkillValidationIssue, ...]
    warnings: tuple[SkillValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def slugify(value: str) -> str:
    slug = value.strip().lower().replace("_", "-").replace(" ", "-")
    slug = _SLUG_INVALID_CHARS_RE.sub("", slug)
    slug = _SLUG_MULTI_DASH_RE.sub("-", slug).strip("-")
    return slug


def split_frontmatter(markdown: str) -> tuple[str, str] | None:
    if not markdown.startswith("---\n"):
        return None

    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    closing_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        return None

    frontmatter_text = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :]).lstrip()
    return frontmatter_text, body


def parse_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    split = split_frontmatter(markdown)
    if split is None:
        return {}, markdown

    frontmatter_text, body = split
    frontmatter_lines = frontmatter_text.splitlines()

    if yaml is not None:
        try:
            parsed_yaml = yaml.safe_load(frontmatter_text)
        except Exception:
            parsed_yaml = None
        if isinstance(parsed_yaml, dict):
            return parsed_yaml, body

    parsed: dict[str, Any] = {}
    current_list_key: str | None = None

    for raw_line in frontmatter_lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("- ") and current_list_key:
            existing = parsed.get(current_list_key)
            if not isinstance(existing, list):
                existing = []
                parsed[current_list_key] = existing
            existing.append(stripped[2:].strip())
            continue

        current_list_key = None
        if ":" not in stripped:
            continue

        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key:
            continue
        if not value:
            parsed[key] = []
            current_list_key = key
            continue
        if value.startswith("[") and value.endswith("]"):
            parts = [part.strip().strip("'\"") for part in value[1:-1].split(",")]
            parsed[key] = [part for part in parts if part]
            continue
        parsed[key] = value.strip("'\"")

    return parsed, body


def sanitize_skill_text(text: str, *, max_chars: int) -> str:
    sanitized = (
        text.replace("\x00", "")
        .replace("<memory-context", "[memory-context")
        .replace("</memory-context>", "[/memory-context]")
        .replace("<stable_memory", "[stable_memory")
        .replace("</stable_memory>", "[/stable_memory]")
        .replace("<system", "[system")
        .replace("</system>", "[/system]")
    ).strip()
    if len(sanitized) <= max_chars:
        return sanitized
    return sanitized[: max(0, max_chars - 1)].rstrip() + "…"


def _normalize_str_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
        return tuple(item for item in items if item)
    if isinstance(value, list):
        normalized = [str(item).strip() for item in value]
        return tuple(item for item in normalized if item)
    return ()


def _normalize_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): nested for key, nested in value.items()}
    return {}


def _bool_from_metadata(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _validate_skill_name_value(name: str) -> str | None:
    if not name:
        return "name must not be empty"
    if slugify(name) != name:
        return "name should be kebab-case and stable for identifier lookup"
    if len(name) > 64:
        return "name should be 64 characters or fewer"
    return None


def _validate_description_value(description: str) -> str | None:
    if not description:
        return "description must not be empty"
    if len(description) > 1024:
        return "description should be 1024 characters or fewer"
    return None


def _infer_description(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        return sanitize_skill_text(stripped, max_chars=MAX_SKILL_DESCRIPTION_CHARS)
    return ""


def _skill_lookup_path_error(name: str) -> str | None:
    candidate = name.strip()
    if not candidate:
        return "Skill name must be a non-empty string."
    if PurePosixPath(candidate).is_absolute() or PureWindowsPath(candidate).is_absolute() or PureWindowsPath(candidate).drive:
        return "Skill name must be relative to the skills directory."
    if ".." in PurePosixPath(candidate).parts or ".." in PureWindowsPath(candidate).parts:
        return "Skill name cannot contain path traversal."
    return None


def validate_skill_dir(skill_dir: Path, *, root: Path | None = None) -> SkillValidationResult:
    import json

    root_dir = root or DEFAULT_SKILLS_ROOT
    skill_md = skill_dir / SKILL_FILE_NAME
    errors: list[SkillValidationIssue] = []
    warnings: list[SkillValidationIssue] = []

    try:
        identifier = str(skill_dir.relative_to(root_dir)).replace("\\", "/")
    except ValueError:
        identifier = skill_dir.name

    def add(level: str, code: str, message: str, path: Path) -> None:
        issue = SkillValidationIssue(level=level, code=code, message=message, path=str(path))
        if level == "error":
            errors.append(issue)
        else:
            warnings.append(issue)

    if not skill_md.exists():
        add("error", "missing_skill_md", "SKILL.md not found", skill_md)
        return SkillValidationResult(skill_dir, identifier, tuple(errors), tuple(warnings))

    try:
        content = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        add("error", "read_failed", f"Failed to read SKILL.md: {exc}", skill_md)
        return SkillValidationResult(skill_dir, identifier, tuple(errors), tuple(warnings))

    split = split_frontmatter(content)
    if split is None:
        add("error", "missing_frontmatter", "SKILL.md must start with YAML frontmatter bounded by ---", skill_md)
        return SkillValidationResult(skill_dir, identifier, tuple(errors), tuple(warnings))

    frontmatter_text, body = split
    parsed_frontmatter: dict[str, Any] | None = None
    if yaml is not None:
        try:
            parsed = yaml.safe_load(frontmatter_text)
        except Exception as exc:
            add("error", "invalid_yaml", f"YAML frontmatter parse error: {exc}", skill_md)
            parsed = None
        if parsed is not None and not isinstance(parsed, dict):
            add("error", "frontmatter_not_mapping", "Frontmatter must be a YAML mapping", skill_md)
        elif isinstance(parsed, dict):
            parsed_frontmatter = parsed
    else:
        parsed_frontmatter, _ = parse_frontmatter(content)

    if parsed_frontmatter is None:
        return SkillValidationResult(skill_dir, identifier, tuple(errors), tuple(warnings))

    name = parsed_frontmatter.get("name")
    if not isinstance(name, str):
        add("error", "missing_name", "Frontmatter must include string field 'name'", skill_md)
        normalized_name = ""
    else:
        normalized_name = name.strip()
        name_error = _validate_skill_name_value(normalized_name)
        if name_error:
            add("warning", "name_style", name_error, skill_md)

    description = parsed_frontmatter.get("description")
    if not isinstance(description, str):
        add("error", "missing_description", "Frontmatter must include string field 'description'", skill_md)
    else:
        description_error = _validate_description_value(description.strip())
        if description_error:
            add("error", "description_invalid", description_error, skill_md)

    if not body.strip():
        add("error", "empty_body", "SKILL.md must include instructions after the frontmatter", skill_md)

    for resource_dir in KNOWN_SKILL_RESOURCE_DIRS:
        resource_path = skill_dir / resource_dir
        if resource_path.exists() and not resource_path.is_dir():
            add("error", "resource_not_dir", f"{resource_dir} must be a directory when present", resource_path)

    evals_path = skill_dir / "evals" / "evals.json"
    if evals_path.exists():
        try:
            payload = json.loads(evals_path.read_text(encoding="utf-8"))
        except Exception as exc:
            add("error", "invalid_evals_json", f"Failed to parse evals/evals.json: {exc}", evals_path)
        else:
            if not isinstance(payload, dict):
                add("error", "invalid_evals_json", "evals/evals.json must contain a JSON object", evals_path)
            else:
                if payload.get("skill_name") not in (None, normalized_name):
                    add("warning", "eval_skill_name_mismatch", "evals/evals.json skill_name does not match frontmatter name", evals_path)
                if not isinstance(payload.get("evals"), list):
                    add("error", "invalid_evals_shape", "evals/evals.json must contain an 'evals' array", evals_path)

    return SkillValidationResult(skill_dir, identifier, tuple(errors), tuple(warnings))


def validate_skills_root(root: Path = DEFAULT_SKILLS_ROOT) -> list[SkillValidationResult]:
    if not root.exists():
        return []

    results: list[SkillValidationResult] = []
    for skill_md in sorted(root.rglob(SKILL_FILE_NAME)):
        try:
            relative_dir = skill_md.parent.relative_to(root)
        except ValueError:
            continue
        if any(part.startswith(".") for part in relative_dir.parts):
            continue
        results.append(validate_skill_dir(skill_md.parent, root=root))
    return results


class SkillCatalog:
    def __init__(self, root: Path = DEFAULT_SKILLS_ROOT) -> None:
        self.root = root

    def list_skills(self) -> list[Skill]:
        if not self.root.exists():
            return []

        skills: list[Skill] = []
        for skill_md in sorted(self.root.rglob(SKILL_FILE_NAME)):
            try:
                relative_dir = skill_md.parent.relative_to(self.root)
            except ValueError:
                continue
            if any(part.startswith(".") for part in relative_dir.parts):
                continue
            skill = self._load_skill(skill_md)
            if skill is not None:
                skills.append(skill)
        return skills

    def skill_summaries(self) -> list[dict[str, Any]]:
        return [skill.summary() for skill in self.list_skills()]

    def resolve(self, requested_names: list[str] | tuple[str, ...]) -> ResolvedSkills:
        if not requested_names:
            return ResolvedSkills(loaded=(), missing=())

        skills = self.list_skills()
        indexes = self._build_indexes(skills)
        loaded: list[Skill] = []
        missing: list[str] = []
        ambiguous: list[str] = []
        seen_identifiers: set[str] = set()

        for raw_name in requested_names:
            if not isinstance(raw_name, str) or not raw_name.strip():
                missing.append(str(raw_name))
                continue
            path_error = _skill_lookup_path_error(raw_name)
            if path_error:
                missing.append(raw_name)
                continue
            key = raw_name.strip().lower().replace("\\", "/")
            matches = indexes.get(key, ())
            if len(matches) == 1:
                skill = matches[0]
                if skill.identifier not in seen_identifiers:
                    loaded.append(skill)
                    seen_identifiers.add(skill.identifier)
                continue
            if len(matches) > 1:
                ambiguous.append(raw_name)
                continue
            missing.append(raw_name)

        return ResolvedSkills(loaded=tuple(loaded), missing=tuple(missing), ambiguous=tuple(ambiguous))

    def view_skill(self, name: str) -> dict[str, Any] | None:
        resolved = self.resolve([name])
        if len(resolved.loaded) != 1 or not resolved.ok:
            return None
        skill = resolved.loaded[0]
        return {
            **skill.summary(),
            "instructions": skill.instructions,
            "metadata": dict(skill.metadata),
        }

    def build_prompt_block(self, requested_names: list[str] | tuple[str, ...]) -> tuple[str, ResolvedSkills]:
        resolved = self.resolve(requested_names)
        if not resolved.loaded:
            return "", resolved

        lines = [
            "<active-skills>",
            "The following explicitly selected skills are additional operating instructions for this turn. Treat them as higher-priority workflow guidance than the user's phrasing details, but never override tool permissions, stable safety rules, or explicit user constraints.",
        ]
        for index, skill in enumerate(resolved.loaded, start=1):
            lines.append(
                f"<skill index=\"{index}\" identifier=\"{skill.identifier}\" name=\"{skill.name}\""
                + (f" category=\"{skill.category}\"" if skill.category else "")
                + ">"
            )
            if skill.description:
                lines.append(f"description: {skill.description}")
            if skill.allowed_tools:
                lines.append(f"allowed_tools: {', '.join(skill.allowed_tools)}")
            if skill.preferred_tools:
                lines.append(f"preferred_tools: {', '.join(skill.preferred_tools)}")
            if skill.platforms:
                lines.append(f"platforms: {', '.join(skill.platforms)}")
            if skill.resource_dirs:
                lines.append(f"resources: {', '.join(skill.resource_dirs)}")
            lines.append(skill.instructions)
            lines.append("</skill>")
        lines.append("</active-skills>")
        return "\n".join(lines), resolved

    def _build_indexes(self, skills: list[Skill]) -> dict[str, tuple[Skill, ...]]:
        aliases: dict[str, list[Skill]] = {}
        for skill in skills:
            keys = {
                skill.identifier.lower(),
                skill.slug.lower(),
                skill.name.lower(),
                str(skill.path.parent.relative_to(self.root)).replace("\\", "/").lower(),
            }
            if skill.category:
                keys.add(f"{skill.category}/{skill.slug}".lower())
                keys.add(f"{skill.category}:{skill.slug}".lower())
            for key in keys:
                aliases.setdefault(key, []).append(skill)
        return {key: tuple(values) for key, values in aliases.items()}

    def _load_skill(self, path: Path) -> Skill | None:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return None

        metadata, body = parse_frontmatter(content)
        try:
            relative_dir = path.parent.relative_to(self.root)
        except ValueError:
            return None

        category = "/".join(relative_dir.parts[:-1]) or None
        name = str(metadata.get("name") or relative_dir.parts[-1]).strip()
        if not name:
            return None
        description = str(metadata.get("description") or "").strip()
        if not description:
            description = _infer_description(body)
        identifier = "/".join(relative_dir.parts)
        instructions = sanitize_skill_text(body, max_chars=MAX_SKILL_BODY_CHARS)
        compatibility = _normalize_mapping(metadata.get("compatibility"))
        resource_dirs = tuple(
            directory_name
            for directory_name in KNOWN_SKILL_RESOURCE_DIRS
            if (path.parent / directory_name).exists()
        )
        has_evals = _bool_from_metadata(metadata.get("has_evals")) or (path.parent / "evals").exists()
        preferred_tools = _normalize_str_list(
            metadata.get("preferred_tools")
            or metadata.get("preferredTools")
            or compatibility.get("preferred_tools")
            or compatibility.get("preferredTools")
        )
        allowed_tools = _normalize_str_list(
            metadata.get("allowed_tools")
            or metadata.get("allowedTools")
            or compatibility.get("allowed_tools")
            or compatibility.get("allowedTools")
            or compatibility.get("required_tools")
            or compatibility.get("requiredTools")
            or compatibility.get("tools")
        )
        return Skill(
            name=name,
            description=sanitize_skill_text(description, max_chars=MAX_SKILL_DESCRIPTION_CHARS),
            identifier=identifier,
            path=path,
            category=category,
            instructions=instructions,
            preferred_tools=preferred_tools,
            allowed_tools=allowed_tools,
            compatibility=compatibility,
            platforms=_normalize_str_list(metadata.get("platforms")),
            resource_dirs=resource_dirs,
            has_evals=has_evals,
            metadata=metadata,
        )
