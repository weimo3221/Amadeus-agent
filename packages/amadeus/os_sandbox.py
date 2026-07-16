from __future__ import annotations

import json
import platform
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class OsSandboxBackend:
    requested: str
    name: str
    executable: str | None
    enforced: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested,
            "backend": self.name,
            "executable": self.executable,
            "enforced": self.enforced,
            "reason": self.reason,
        }

    def wrap_command(
        self,
        command: list[str],
        *,
        workspace_path: Path,
        state_root: Path,
        database_path: Path,
        protected_workspace_root: Path | None,
        profile_path: Path,
    ) -> list[str]:
        if not self.enforced:
            return list(command)
        workspace_path = workspace_path.resolve()
        state_root = state_root.resolve()
        database_path = database_path.resolve()
        protected_workspace_root = (
            protected_workspace_root.resolve()
            if protected_workspace_root is not None
            else None
        )
        profile_path = profile_path.resolve()
        if self.name == "bubblewrap":
            assert self.executable is not None
            return [
                self.executable,
                "--ro-bind",
                "/",
                "/",
                "--bind",
                str(state_root),
                str(state_root),
                *(
                    [
                        "--ro-bind",
                        str(protected_workspace_root),
                        str(protected_workspace_root),
                    ]
                    if protected_workspace_root is not None
                    and protected_workspace_root.exists()
                    else []
                ),
                "--bind",
                str(workspace_path),
                str(workspace_path),
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--chdir",
                str(workspace_path),
                "--",
                *command,
            ]
        if self.name == "sandbox-exec":
            assert self.executable is not None
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile_path.write_text(
                _sandbox_exec_profile(
                    workspace_path=workspace_path,
                    database_path=database_path,
                ),
                encoding="utf-8",
            )
            return [self.executable, "-f", str(profile_path), *command]
        raise RuntimeError(f"unsupported OS sandbox backend: {self.name}")


def normalize_os_sandbox_mode(value: object) -> str:
    normalized = str(value or "auto").strip().lower().replace("_", "-")
    aliases = {
        "": "auto",
        "auto": "auto",
        "preferred": "auto",
        "none": "none",
        "off": "none",
        "disabled": "none",
        "false": "none",
        "0": "none",
        "required": "required",
        "strict": "required",
        "bubblewrap": "bubblewrap",
        "bwrap": "bubblewrap",
        "sandbox-exec": "sandbox-exec",
        "seatbelt": "sandbox-exec",
    }
    if normalized not in aliases:
        raise ValueError(
            "OS sandbox mode must be one of auto, required, none, bubblewrap, or sandbox-exec"
        )
    return aliases[normalized]


@lru_cache(maxsize=16)
def select_os_sandbox_backend(
    requested: str = "auto",
    system_name: str | None = None,
) -> OsSandboxBackend:
    mode = normalize_os_sandbox_mode(requested)
    system = system_name or platform.system()
    if mode == "none":
        return OsSandboxBackend(mode, "none", None, False, "disabled by configuration")

    candidates: list[tuple[str, str | None]]
    if mode == "bubblewrap":
        candidates = [("bubblewrap", shutil.which("bwrap"))]
    elif mode == "sandbox-exec":
        candidates = [("sandbox-exec", shutil.which("sandbox-exec"))]
    elif system == "Linux":
        candidates = [("bubblewrap", shutil.which("bwrap"))]
    elif system == "Darwin":
        candidates = [("sandbox-exec", shutil.which("sandbox-exec"))]
    else:
        candidates = []

    failures: list[str] = []
    for name, executable in candidates:
        if not executable:
            failures.append(f"{name} executable not found")
            continue
        ok, error = _probe_backend(name, executable)
        if ok:
            return OsSandboxBackend(mode, name, executable, True)
        failures.append(f"{name} probe failed: {error or 'unknown error'}")

    reason = "; ".join(failures) or f"no supported backend for {system}"
    if mode in {"required", "bubblewrap", "sandbox-exec"}:
        raise RuntimeError(f"required OS sandbox is unavailable: {reason}")
    return OsSandboxBackend(mode, "none", None, False, reason)


def _probe_backend(name: str, executable: str) -> tuple[bool, str | None]:
    if name == "bubblewrap":
        command = [
            executable,
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--",
            "/usr/bin/true",
        ]
    else:
        command = [
            executable,
            "-p",
            "(version 1) (allow default)",
            "/usr/bin/true",
        ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    if completed.returncode == 0:
        return True, None
    return False, (completed.stderr or "").strip() or f"exit code {completed.returncode}"


def _sandbox_exec_profile(*, workspace_path: Path, database_path: Path) -> str:
    workspace = json.dumps(str(workspace_path))
    database_paths = [
        json.dumps(str(database_path) + suffix)
        for suffix in ("", "-wal", "-shm", "-journal")
    ]
    database_rules = " ".join(
        f"(literal {path})"
        for path in database_paths
    )
    return "\n".join(
        [
            "(version 1)",
            "(deny default)",
            "(allow process*)",
            "(allow network*)",
            "(allow sysctl-read)",
            "(allow mach-lookup)",
            "(allow file-read*)",
            f"(allow file-write* (subpath {workspace}) {database_rules})",
            "",
        ]
    )
