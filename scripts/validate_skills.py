#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_ROOT = REPO_ROOT / "packages"
if str(PACKAGES_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGES_ROOT))

from amadeus.skills import DEFAULT_SKILLS_ROOT, validate_skill_dir, validate_skills_root


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate one skill directory or every SKILL.md under the skills root.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_SKILLS_ROOT),
        help="Skill directory or skills root to validate. Defaults to the repository skills/ directory.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of plain text.",
    )
    return parser.parse_args()


def _result_to_json(result) -> dict[str, object]:
    return {
        "identifier": result.identifier,
        "skillDir": str(result.skill_dir),
        "ok": result.ok,
        "errors": [issue.__dict__ for issue in result.errors],
        "warnings": [issue.__dict__ for issue in result.warnings],
    }


def _infer_root_for_skill_dir(skill_dir: Path) -> Path:
    for ancestor in skill_dir.parents:
        if ancestor.name == "skills":
            return ancestor
    return skill_dir.parent


def _print_text(results) -> None:
    for result in results:
        status = "OK" if result.ok else "ERROR"
        print(f"[{status}] {result.identifier} ({result.skill_dir})")
        for issue in result.errors:
            print(f"  error   {issue.code}: {issue.message} [{issue.path}]")
        for issue in result.warnings:
            print(f"  warning {issue.code}: {issue.message} [{issue.path}]")


def main() -> int:
    args = _parse_args()
    target = Path(args.path).resolve()

    if target.is_file():
        target = target.parent

    if (target / "SKILL.md").exists():
        root = _infer_root_for_skill_dir(target)
        results = [validate_skill_dir(target, root=root)]
    else:
        results = validate_skills_root(target)

    if args.json:
        payload = {
            "ok": all(result.ok for result in results),
            "count": len(results),
            "results": [_result_to_json(result) for result in results],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_text(results)
        passed = sum(1 for result in results if result.ok)
        print(f"\nValidated {len(results)} skill(s): {passed} passed, {len(results) - passed} failed.")

    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
