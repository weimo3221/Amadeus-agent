from __future__ import annotations

import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from amadeus.os_sandbox import (
    OsSandboxBackend,
    normalize_os_sandbox_mode,
    select_os_sandbox_backend,
)


class OsSandboxTests(unittest.TestCase):
    def tearDown(self) -> None:
        select_os_sandbox_backend.cache_clear()

    def test_mode_normalization_is_explicit(self) -> None:
        self.assertEqual(normalize_os_sandbox_mode("preferred"), "auto")
        self.assertEqual(normalize_os_sandbox_mode("strict"), "required")
        self.assertEqual(normalize_os_sandbox_mode("bwrap"), "bubblewrap")
        self.assertEqual(normalize_os_sandbox_mode("seatbelt"), "sandbox-exec")
        with self.assertRaisesRegex(ValueError, "OS sandbox mode"):
            normalize_os_sandbox_mode("unknown")

    def test_auto_mode_reports_probe_failure_without_claiming_enforcement(self) -> None:
        select_os_sandbox_backend.cache_clear()
        with (
            mock.patch(
                "amadeus.os_sandbox.shutil.which",
                return_value="/usr/bin/sandbox-exec",
            ),
            mock.patch(
                "amadeus.os_sandbox._probe_backend",
                return_value=(False, "Operation not permitted"),
            ),
        ):
            backend = select_os_sandbox_backend("auto", "Darwin")

        self.assertEqual(backend.name, "none")
        self.assertFalse(backend.enforced)
        self.assertIn("Operation not permitted", str(backend.reason))

    def test_required_mode_rejects_unavailable_backend(self) -> None:
        select_os_sandbox_backend.cache_clear()
        with mock.patch("amadeus.os_sandbox.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "required OS sandbox"):
                select_os_sandbox_backend("required", "Linux")

    def test_bubblewrap_command_mounts_host_read_only_and_state_writable(self) -> None:
        backend = OsSandboxBackend(
            requested="bubblewrap",
            name="bubblewrap",
            executable="/usr/bin/bwrap",
            enforced=True,
        )
        workspace = Path("/tmp/workspace").resolve()
        state = Path("/tmp/state").resolve()

        command = backend.wrap_command(
            ["/usr/bin/python3", "-m", "worker"],
            workspace_path=workspace,
            state_root=state,
            database_path=state / "amadeus.sqlite",
            protected_workspace_root=state / "worker_workspaces",
            profile_path=Path("/tmp/unused.sb"),
        )

        self.assertEqual(command[:4], ["/usr/bin/bwrap", "--ro-bind", "/", "/"])
        self.assertIn(
            ["--bind", str(workspace), str(workspace)],
            [command[index : index + 3] for index in range(len(command) - 2)],
        )
        self.assertIn(
            ["--bind", str(state), str(state)],
            [command[index : index + 3] for index in range(len(command) - 2)],
        )
        self.assertEqual(command[-3:], ["/usr/bin/python3", "-m", "worker"])

    def test_sandbox_exec_profile_allows_writes_only_to_workspace_and_state(self) -> None:
        backend = OsSandboxBackend(
            requested="sandbox-exec",
            name="sandbox-exec",
            executable="/usr/bin/sandbox-exec",
            enforced=True,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "worker.sb"
            workspace = Path("/tmp/workspace").resolve()
            state = Path("/tmp/state").resolve()
            database = state / "amadeus.sqlite"

            command = backend.wrap_command(
                ["/usr/bin/python3", "-m", "worker"],
                workspace_path=workspace,
                state_root=state,
                database_path=database,
                protected_workspace_root=state / "worker_workspaces",
                profile_path=profile_path,
            )
            profile = profile_path.read_text(encoding="utf-8")

        self.assertEqual(
            command,
            [
                "/usr/bin/sandbox-exec",
                "-f",
                str(profile_path.resolve()),
                "/usr/bin/python3",
                "-m",
                "worker",
            ],
        )
        self.assertIn("(deny default)", profile)
        self.assertIn(f'(subpath "{workspace}")', profile)
        self.assertIn(f'(literal "{database}")', profile)
        self.assertIn(f'(literal "{database}-wal")', profile)
        self.assertNotIn(f'(subpath "{state}")', profile)
        self.assertNotIn("(allow file-write*)", profile)

    def test_available_native_backend_blocks_write_outside_allowed_roots(self) -> None:
        backend = select_os_sandbox_backend("auto")
        if not backend.enforced:
            self.skipTest(str(backend.reason or "OS sandbox backend is unavailable"))

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            state = root / "state"
            outside = root / "outside.txt"
            workspace.mkdir()
            state.mkdir()
            script = (
                "from pathlib import Path\n"
                f"Path({str(workspace / 'inside.txt')!r}).write_text('inside')\n"
                "try:\n"
                f"    Path({str(outside)!r}).write_text('outside')\n"
                "except OSError:\n"
                "    raise SystemExit(0)\n"
                "raise SystemExit(3)\n"
            )
            command = backend.wrap_command(
                [sys.executable, "-c", script],
                workspace_path=workspace,
                state_root=state,
                database_path=state / "amadeus.sqlite",
                protected_workspace_root=None,
                profile_path=state / "worker.sb",
            )
            completed = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((workspace / "inside.txt").is_file())
            self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
