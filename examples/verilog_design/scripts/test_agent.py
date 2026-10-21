"""Exercise public exports and submission attacks without making model requests."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent import freeze, safe_name, task_list, write_public
from evaluate import execute


class AgentBoundaryTests(unittest.TestCase):
    def test_public_exports_exclude_official_tests_and_solutions(self) -> None:
        selections = (
            ("rtllm", "adder_8bit"),
            ("verilogeval", "Prob009_popcount3"),
            ("cvdp", "cvdp_copilot_bcd_adder_0001"),
        )
        for bench, identifier in selections:
            with self.subTest(benchmark=bench), tempfile.TemporaryDirectory() as temporary:
                task = task_list(bench, [identifier], "spec-to-rtl")[0]
                workspace = Path(temporary)
                write_public(workspace, {"problem.txt": task.prompt, **task.context})
                files = sorted(str(file.relative_to(workspace)) for file in workspace.rglob("*"))
                self.assertEqual(files, ["problem.txt"])
                self.assertTrue((workspace / "problem.txt").read_text())

    def test_submission_is_frozen_and_hashed_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "candidate.sv").write_text("module dut; endmodule\n")
            records = freeze(workspace, root / "frozen", ("candidate.sv",))
            (workspace / "candidate.sv").write_text("changed after freeze")
            self.assertEqual((root / "frozen/candidate.sv").read_text(), "module dut; endmodule\n")
            self.assertEqual(len(records[0].sha256), 64)
            self.assertEqual((root / "frozen/candidate.sv").stat().st_mode & 0o222, 0)

    def test_leaf_and_parent_symlinks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "secret.sv").write_text("must not be collected")
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "leaf.sv").symlink_to(root / "secret.sv")
            (workspace / "escape").symlink_to(root)
            for index, name in enumerate(("leaf.sv", "escape/secret.sv")):
                with self.subTest(path=name), self.assertRaisesRegex(ValueError, "Symlink"):
                    freeze(workspace, root / f"frozen{index}", (name,))

    def test_fifo_is_rejected_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            os.mkfifo(workspace / "candidate.sv")
            with self.assertRaisesRegex(ValueError, "Invalid RTL"):
                freeze(workspace, root / "frozen", ("candidate.sv",))

    def test_timeout_kills_simulator_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "escaped-child"
            child = "import time; from pathlib import Path; time.sleep(2); " + (
                f"Path({str(marker)!r}).write_text('still running')"
            )
            parent = (
                "import subprocess, sys, time; "
                f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)"
            )
            result = execute([sys.executable, "-c", parent], root, root / "process.log", 1)
            self.assertTrue(result.timed_out)
            time.sleep(2)
            self.assertFalse(marker.exists())

    def test_controller_interruption_terminates_running_container_process(self) -> None:
        processes = []
        original_wait = subprocess.Popen.wait

        def interrupted(process: subprocess.Popen, timeout: float | None = None) -> int:
            if not processes:
                processes.append(process)
                raise KeyboardInterrupt()
            return original_wait(process, timeout)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                mock.patch.object(subprocess.Popen, "wait", interrupted),
                self.assertRaises(KeyboardInterrupt),
            ):
                execute(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    root,
                    root / "process.log",
                    60,
                )
            self.assertIsNotNone(processes[0].poll())

    def test_public_paths_cannot_escape_workspace(self) -> None:
        for name in ("../hidden", "/tmp/hidden", "rtl/../../hidden"):
            with self.subTest(path=name), self.assertRaises(ValueError):
                safe_name(name)


if __name__ == "__main__":
    unittest.main()
