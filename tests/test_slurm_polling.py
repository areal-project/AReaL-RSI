"""Slurm completion polling must not depend on sacct (absent on some clusters)."""

from areal_rsi.controller import local_controller as lc


def test_slurm_job_state_falls_back_to_squeue_and_reports_gone(monkeypatch):
    seen: list[str] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd[0])
        if cmd[0] == "sacct":
            raise FileNotFoundError("sacct not installed")

        class _R:
            returncode = 0
            stdout = ""  # squeue: job not in queue

        return _R()

    monkeypatch.setattr(lc.subprocess, "run", fake_run)
    assert lc._slurm_job_state("123") == "GONE"
    assert seen == ["sacct", "squeue"]


def test_slurm_job_state_reports_running_from_squeue(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[0] == "sacct":
            raise FileNotFoundError("sacct not installed")

        class _R:
            returncode = 0
            stdout = "RUNNING\n"

        return _R()

    monkeypatch.setattr(lc.subprocess, "run", fake_run)
    assert lc._slurm_job_state("123") == "RUNNING"


def test_wait_for_slurm_job_completes_when_job_leaves_queue_without_sacct(monkeypatch):
    states = iter(["RUNNING", "RUNNING", "GONE"])
    monkeypatch.setattr(lc, "_slurm_job_state", lambda job_id: next(states))
    monkeypatch.setattr(lc.time, "sleep", lambda _seconds: None)
    # Returns (no exception) once the job disappears after having been active.
    lc._wait_for_slurm_job("123", timeout_seconds=100.0, poll_interval_seconds=0.01)
