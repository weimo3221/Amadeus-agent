from __future__ import annotations

import unittest
from pathlib import Path

from scripts.release_check import run_checks


class ReleaseCheckTests(unittest.TestCase):
    def test_release_check_passes_for_current_repo(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        result = run_checks(repo_root, strict=False)

        self.assertTrue(result["ok"], result)
        statuses = {check["name"]: check["status"] for check in result["checks"]}
        self.assertEqual(statuses["root_release_scripts"], "passed")
        self.assertEqual(statuses["desktop_release_scripts"], "passed")
        self.assertEqual(statuses["auto_update_integration"], "passed")
        self.assertEqual(statuses["e2e_real_runtime"], "passed")
        self.assertEqual(statuses["runtime_soak_script"], "passed")


if __name__ == "__main__":
    unittest.main()
