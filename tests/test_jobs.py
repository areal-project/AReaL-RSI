from __future__ import annotations

import sys
from pathlib import Path

import pytest

from areal_rsi.api.control import read_record
from areal_rsi.api.execution import JobReceipt
from areal_rsi.scripts.jobs import AReaLJobRunner, UncertainAReaLJob


def test_completed_recipe_is_reused_and_tampering_fails(tmp_path: Path) -> None:
    runner = AReaLJobRunner(tmp_path / "jobs", "run1")
    output = tmp_path / "report.json"
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('{}')",
        str(output),
    )
    assert runner.run("baseline", command, output) == output
    assert runner.run("baseline", command, output) == output
    output.write_text("changed")
    with pytest.raises(ValueError, match="output changed"):
        runner.run("baseline", command, output)


def test_failed_external_job_cannot_be_resubmitted_blindly(tmp_path: Path) -> None:
    runner = AReaLJobRunner(tmp_path / "jobs", "run1")
    output = tmp_path / "report.json"
    command = (sys.executable, "-c", "raise SystemExit(3)")
    with pytest.raises(UncertainAReaLJob, match="failed"):
        runner.run("train", command, output)
    with pytest.raises(UncertainAReaLJob, match="Reconcile"):
        runner.run("train", command, output)


def test_researcher_credential_is_not_inherited_by_gpu_recipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RSI_TEST_SECRET", "private")
    runner = AReaLJobRunner(tmp_path / "jobs", "run1", exclude_env=("RSI_TEST_SECRET",))
    output = tmp_path / "environment.txt"
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; import os,sys; "
        "Path(sys.argv[1]).write_text(os.environ.get('RSI_TEST_SECRET','missing'))",
        str(output),
    )
    runner.run("env", command, output)
    assert output.read_text() == "missing"


def test_slurm_job_id_is_required_for_real_recipe(tmp_path: Path) -> None:
    runner = AReaLJobRunner(tmp_path / "jobs", "run1", require_slurm_jobs=True)
    output = tmp_path / "report.json"
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; "
        "print('Submitted batch job 12345'); Path(sys.argv[1]).write_text('{}')",
        str(output),
    )
    runner.run("eval", command, output)
    receipt_path = tmp_path / "jobs/eval.receipt.json"
    receipt = read_record(receipt_path, JobReceipt)
    assert receipt.slurm_job_ids == (12345,)
    assert receipt.completion_source == "process"
    receipt_path.unlink()  # Recipe finished; controller died before recording completion.
    assert runner.run("eval", command, output) == output
    recovered = read_record(receipt_path, JobReceipt)
    assert recovered.slurm_job_ids == (12345,)
    assert recovered.completion_source == "recovered"
    assert recovered.elapsed_seconds is None
