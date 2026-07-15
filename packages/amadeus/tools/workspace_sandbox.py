from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any

from amadeus.tools.search_files import is_inside


SANDBOX_DIR_NAME = ".amadeus-sandbox"
PYTHON_GUARD_DIR_NAME = "python"
PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_:])/(?!/)[^\s'\";|&<>`$(),]+")


def workspace_sandbox_enabled(context: Any = None) -> bool:
    return bool(
        getattr(context, "worker_workspace_path", None)
        or getattr(context, "worker_workspace_isolation", None)
        or getattr(context, "worker_sandbox_mode", None) == "workspace_execute"
    )


def ensure_workspace_sandbox_dir(workspace_root: Path) -> Path:
    sandbox_dir = workspace_root / SANDBOX_DIR_NAME
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    return sandbox_dir


def workspace_sandbox_environment(workspace_root: Path, env: dict[str, str] | None = None) -> dict[str, str]:
    effective_env = dict(env or os.environ)
    sandbox_dir = ensure_workspace_sandbox_dir(workspace_root)
    tmp_dir = sandbox_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    python_guard_dir = sandbox_dir / PYTHON_GUARD_DIR_NAME
    python_guard_dir.mkdir(parents=True, exist_ok=True)
    (python_guard_dir / "sitecustomize.py").write_text(python_guard_source(), encoding="utf-8")

    effective_env["HOME"] = str(workspace_root)
    effective_env["TMPDIR"] = str(tmp_dir)
    effective_env["TMP"] = str(tmp_dir)
    effective_env["TEMP"] = str(tmp_dir)
    effective_env["AMADEUS_WORKSPACE_SANDBOX_ROOT"] = str(workspace_root)
    effective_env["PYTHONDONTWRITEBYTECODE"] = "1"
    existing_pythonpath = effective_env.get("PYTHONPATH")
    effective_env["PYTHONPATH"] = (
        str(python_guard_dir)
        if not existing_pythonpath
        else os.pathsep.join([str(python_guard_dir), existing_pythonpath])
    )
    return effective_env


def validate_command_workspace_references(command: str, workspace_root: Path) -> str | None:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as error:
        return f"command uses unsupported shell syntax for workspace sandbox: {error}"

    for token in tokens:
        for candidate in _path_candidates(token):
            if _candidate_escapes_workspace(candidate, workspace_root):
                return f"command references a path outside the workspace sandbox: {candidate}"
    return None


def _path_candidates(token: str) -> list[str]:
    if not token or token.startswith(("http://", "https://")):
        return []
    candidates: list[str] = []
    for match in PATH_PATTERN.finditer(token):
        path = match.group(0).rstrip(":.")
        if path:
            candidates.append(path)
    for fragment in re.split(r"[=:]", token):
        fragment = fragment.strip()
        if fragment in {"..", "../"} or fragment.startswith("../") or "/../" in fragment:
            candidates.append(fragment)
    return candidates


def _candidate_escapes_workspace(candidate: str, workspace_root: Path) -> bool:
    path = Path(candidate).expanduser()
    try:
        resolved = path.resolve(strict=False) if path.is_absolute() else (workspace_root / path).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return True
    return not is_inside(resolved, workspace_root)


def python_guard_source() -> str:
    return r'''
from __future__ import annotations

import builtins
import os
import pathlib
import shutil

_ROOT_TEXT = os.environ.get("AMADEUS_WORKSPACE_SANDBOX_ROOT", "")
_ROOT = pathlib.Path(_ROOT_TEXT).resolve() if _ROOT_TEXT else None


def _mode_writes(mode):
    text = str(mode or "r")
    return any(flag in text for flag in ("w", "a", "x", "+"))


def _resolve_path(path):
    if _ROOT is None:
        return None
    try:
        text = os.fsdecode(path)
    except TypeError:
        return None
    candidate = pathlib.Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = pathlib.Path.cwd() / candidate
    return candidate.resolve(strict=False)


def _guard_path(path, operation):
    if _ROOT is None:
        return
    resolved = _resolve_path(path)
    if resolved is None:
        return
    try:
        resolved.relative_to(_ROOT)
    except ValueError as exc:
        raise PermissionError(f"{operation} outside workspace sandbox: {resolved}") from exc


_original_open = builtins.open


def _guarded_open(file, mode="r", *args, **kwargs):
    if _mode_writes(mode):
        _guard_path(file, "open")
    return _original_open(file, mode, *args, **kwargs)


builtins.open = _guarded_open

_original_os_open = os.open


def _guarded_os_open(path, flags, mode=0o777, *, dir_fd=None):
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    if flags & write_flags:
        _guard_path(path, "os.open")
    return _original_os_open(path, flags, mode, dir_fd=dir_fd)


os.open = _guarded_os_open


def _wrap_one_path(module, name):
    original = getattr(module, name)

    def wrapped(path, *args, **kwargs):
        _guard_path(path, name)
        return original(path, *args, **kwargs)

    setattr(module, name, wrapped)


for _name in ("remove", "unlink", "rmdir", "mkdir", "makedirs"):
    if hasattr(os, _name):
        _wrap_one_path(os, _name)


def _wrap_two_path(module, name):
    original = getattr(module, name)

    def wrapped(src, dst, *args, **kwargs):
        _guard_path(dst, name)
        return original(src, dst, *args, **kwargs)

    setattr(module, name, wrapped)


for _name in ("rename", "replace"):
    if hasattr(os, _name):
        _wrap_two_path(os, _name)


for _name in ("rmtree",):
    if hasattr(shutil, _name):
        _wrap_one_path(shutil, _name)

for _name in ("copy", "copy2", "copyfile", "copytree", "move"):
    if hasattr(shutil, _name):
        _wrap_two_path(shutil, _name)

_original_path_open = pathlib.Path.open


def _guarded_path_open(self, mode="r", *args, **kwargs):
    if _mode_writes(mode):
        _guard_path(self, "path.open")
    return _original_path_open(self, mode, *args, **kwargs)


pathlib.Path.open = _guarded_path_open


def _wrap_path_method(name):
    original = getattr(pathlib.Path, name)

    def wrapped(self, *args, **kwargs):
        _guard_path(self, f"path.{name}")
        return original(self, *args, **kwargs)

    setattr(pathlib.Path, name, wrapped)


for _name in ("mkdir", "unlink", "rmdir"):
    _wrap_path_method(_name)


def _wrap_path_two_method(name):
    original = getattr(pathlib.Path, name)

    def wrapped(self, target, *args, **kwargs):
        _guard_path(target, f"path.{name}")
        return original(self, target, *args, **kwargs)

    setattr(pathlib.Path, name, wrapped)


for _name in ("rename", "replace"):
    _wrap_path_two_method(_name)
'''.lstrip()
