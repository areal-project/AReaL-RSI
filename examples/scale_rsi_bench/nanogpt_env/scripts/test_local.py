"""Native launcher checks that need no GPU, Slurm or container runtime."""

import contextlib
import fcntl
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bench
import local_job


class LocalTests(unittest.TestCase):
    def test_research_deadline_prevents_new_gpu_work(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / "run"
            run.mkdir()
            bench.write_json(
                run / "experiment.json", {"gpu_uuid": "GPU-test", "timeout_seconds": 7200}
            )
            with (
                patch.dict(
                    os.environ,
                    {
                        "RSI_ROOT": str(root),
                        "RSI_SHARED_ROOT": str(root),
                        "RSI_RESEARCH_DEADLINE_EPOCH": "1",
                    },
                ),
                patch("subprocess.run", side_effect=AssertionError("unexpected GPU check")),
                patch("subprocess.Popen", side_effect=AssertionError("unexpected training")),
                self.assertRaisesRegex(RuntimeError, "deadline reached"),
            ):
                local_job.run_local(run)
            self.assertEqual(json.loads((run / "state.json").read_text())["state"], "FAILED")

    def test_single_gpu_required(self):
        for value in ("", "0,1", "all", "-1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                local_job.resolve_gpu(value)

    def test_pid_reuse_is_not_a_running_job(self):
        ticks = local_job.process_start_ticks(os.getpid())
        self.assertTrue(local_job.process_alive(os.getpid(), ticks))
        self.assertFalse(local_job.process_alive(os.getpid(), "different-start-time"))

    def test_local_status_does_not_call_slurm(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bench.write_json(root / "experiment.json", {"mode": "smoke"})
            bench.write_json(
                root / "job.json", {"backend": "local", "pid": os.getpid(), "start_ticks": "stale"}
            )
            output = io.StringIO()
            with (
                patch("subprocess.run", side_effect=AssertionError("unexpected scheduler call")),
                contextlib.redirect_stdout(output),
            ):
                bench.status(root)
            self.assertEqual(json.loads(output.getvalue())["state"], "LOST")
            bench.write_json(root / "state.json", {"state": "FAILED", "error": "timeout"})
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                bench.status(root)
            self.assertEqual(json.loads(output.getvalue())["state"], "FAILED")

    def test_busy_gpu_does_not_start_training(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root / "run"
            run.mkdir()
            gpu = "GPU-test"
            bench.write_json(run / "experiment.json", {"gpu_uuid": gpu, "timeout_seconds": 1})
            locks = root / "gpu-locks"
            locks.mkdir()
            name = hashlib.sha256(gpu.encode()).hexdigest() + ".lock"
            with (locks / name).open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with (
                    patch.dict(os.environ, {"RSI_ROOT": str(root), "RSI_SHARED_ROOT": str(root)}),
                    patch("subprocess.Popen", side_effect=AssertionError("unexpected child")),
                    self.assertRaisesRegex(RuntimeError, "already has"),
                ):
                    local_job.run_local(run)
            self.assertEqual(json.loads((run / "state.json").read_text())["state"], "FAILED")

    def test_stop_reaps_its_own_process_group(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
        )
        try:
            local_job.stop_child(child)
            self.assertIsNotNone(child.poll())
            # Repeated cleanup is harmless.
            local_job.stop_child(child)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


if __name__ == "__main__":
    unittest.main()
