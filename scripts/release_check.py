#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str

    def to_payload(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Amadeus release-readiness wiring.")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="Repository root to check.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    args = parser.parse_args()

    result = run_checks(Path(args.repo_root), strict=args.strict)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


def run_checks(repo_root: Path, *, strict: bool = False) -> dict[str, Any]:
    checks = [
        check_root_release_scripts(repo_root),
        check_desktop_release_scripts(repo_root),
        check_auto_update_integration(repo_root),
        check_e2e_real_runtime(repo_root),
        check_runtime_soak_script(repo_root),
        check_release_documentation(repo_root),
    ]
    failed = [check for check in checks if check.status == "failed"]
    warnings = [check for check in checks if check.status == "warning"]
    ok = not failed and (not strict or not warnings)
    return {
        "ok": ok,
        "strict": strict,
        "checkCount": len(checks),
        "failedCount": len(failed),
        "warningCount": len(warnings),
        "checks": [check.to_payload() for check in checks],
    }


def check_root_release_scripts(repo_root: Path) -> CheckResult:
    package_json = read_json(repo_root / "package.json")
    scripts = package_json.get("scripts") if isinstance(package_json.get("scripts"), dict) else {}
    required = {
        "release:check",
        "release:desktop",
        "release:preflight",
        "soak:runtime",
        "test:e2e",
        "test",
        "typecheck",
    }
    missing = sorted(required.difference(scripts))
    if missing:
        return CheckResult("root_release_scripts", "failed", f"missing scripts: {', '.join(missing)}")
    return CheckResult("root_release_scripts", "passed", "root release, soak, E2E, test, and typecheck scripts are present")


def check_desktop_release_scripts(repo_root: Path) -> CheckResult:
    package_json = read_json(repo_root / "apps" / "desktop" / "package.json")
    scripts = package_json.get("scripts") if isinstance(package_json.get("scripts"), dict) else {}
    dependencies = package_json.get("dependencies") if isinstance(package_json.get("dependencies"), dict) else {}
    required_scripts = {"release:build", "test:e2e", "build"}
    missing_scripts = sorted(required_scripts.difference(scripts))
    if missing_scripts:
        return CheckResult("desktop_release_scripts", "failed", f"missing desktop scripts: {', '.join(missing_scripts)}")
    if "electron-updater" not in dependencies:
        return CheckResult("desktop_release_scripts", "failed", "electron-updater dependency is missing")
    return CheckResult("desktop_release_scripts", "passed", "desktop release build, E2E, and updater dependency are present")


def check_auto_update_integration(repo_root: Path) -> CheckResult:
    module_path = repo_root / "apps" / "desktop" / "src" / "main" / "auto-update.ts"
    main_path = repo_root / "apps" / "desktop" / "src" / "main" / "index.ts"
    if not module_path.exists():
        return CheckResult("auto_update_integration", "failed", "auto-update module is missing")
    main_source = main_path.read_text(encoding="utf-8")
    module_source = module_path.read_text(encoding="utf-8")
    if "configureAutoUpdates" not in main_source:
        return CheckResult("auto_update_integration", "failed", "main process does not call configureAutoUpdates")
    if "import { autoUpdater } from 'electron-updater'" in module_source or 'import { autoUpdater } from "electron-updater"' in module_source:
        return CheckResult("auto_update_integration", "failed", "electron-updater must be default-imported for ESM/CJS runtime compatibility")
    if "AMADEUS_UPDATE_FEED_URL" not in module_source or "AMADEUS_AUTO_UPDATE" not in module_source:
        return CheckResult("auto_update_integration", "failed", "auto-update module lacks explicit env-gated feed configuration")
    return CheckResult("auto_update_integration", "passed", "auto-update is packaged-only and env-gated")


def check_e2e_real_runtime(repo_root: Path) -> CheckResult:
    smoke_path = repo_root / "apps" / "desktop" / "e2e" / "electron-smoke.test.ts"
    stack_path = repo_root / "apps" / "desktop" / "e2e" / "real-runtime-stack.ts"
    if not smoke_path.exists() or not stack_path.exists():
        return CheckResult("e2e_real_runtime", "failed", "real runtime Electron E2E files are missing")
    smoke_source = smoke_path.read_text(encoding="utf-8")
    stack_source = stack_path.read_text(encoding="utf-8")
    required_tokens = [
        "AMADEUS_E2E_REAL_RUNTIME",
        "startRealRuntimeStack",
        "Real runtime E2E reply",
    ]
    missing = [token for token in required_tokens if token not in smoke_source + stack_source]
    if missing:
        return CheckResult("e2e_real_runtime", "failed", f"missing real-runtime E2E markers: {', '.join(missing)}")
    return CheckResult("e2e_real_runtime", "passed", "packaged Electron E2E covers the real Python runtime and Node bridge")


def check_runtime_soak_script(repo_root: Path) -> CheckResult:
    path = repo_root / "scripts" / "runtime_soak.py"
    if not path.exists():
        return CheckResult("runtime_soak_script", "failed", "scripts/runtime_soak.py is missing")
    source = path.read_text(encoding="utf-8")
    required = ["/runtime/health", "/runtime/observability", "/health"]
    missing = [token for token in required if token not in source]
    if missing:
        return CheckResult("runtime_soak_script", "failed", f"runtime soak script is missing probes: {', '.join(missing)}")
    return CheckResult("runtime_soak_script", "passed", "runtime soak probes runtime health, observability, and bridge health")


def check_release_documentation(repo_root: Path) -> CheckResult:
    status = (repo_root / "docs" / "project-status.md").read_text(encoding="utf-8")
    notes = (repo_root / "docs" / "implementation-notes.md").read_text(encoding="utf-8")
    if "P3 Release" not in status or "release preflight" not in notes:
        return CheckResult("release_documentation", "warning", "P3 release docs are not fully updated")
    return CheckResult("release_documentation", "passed", "P3 release status and implementation notes are documented")


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"missing required JSON file: {path}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
