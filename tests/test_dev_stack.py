from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

from scripts import dev_stack


class DevStackTests(unittest.TestCase):
    def test_external_subprocess_mode_starts_task_supervisor_first(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AMADEUS_TASK_RUNNER": "subprocess",
                    "AMADEUS_TASK_SUPERVISOR_MODE": "external",
                },
            ),
            mock.patch.object(sys, "argv", ["dev_stack.py", "--no-desktop"]),
            mock.patch.object(dev_stack, "StackSupervisor") as supervisor_class,
        ):
            supervisor_class.return_value.run.return_value = 0

            return_code = dev_stack.main()

        processes = supervisor_class.call_args.args[0]
        self.assertEqual(return_code, 0)
        self.assertEqual(
            [process.name for process in processes],
            ["task-supervisor", "python-runtime", "bridge"],
        )

    def test_in_process_mode_does_not_start_external_task_supervisor(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {
                    "AMADEUS_TASK_RUNNER": "in_process",
                    "AMADEUS_TASK_SUPERVISOR_MODE": "external",
                },
            ),
            mock.patch.object(sys, "argv", ["dev_stack.py", "--no-desktop"]),
            mock.patch.object(dev_stack, "StackSupervisor") as supervisor_class,
        ):
            supervisor_class.return_value.run.return_value = 0

            return_code = dev_stack.main()

        processes = supervisor_class.call_args.args[0]
        self.assertEqual(return_code, 0)
        self.assertEqual(
            [process.name for process in processes],
            ["python-runtime", "bridge"],
        )


if __name__ == "__main__":
    unittest.main()
