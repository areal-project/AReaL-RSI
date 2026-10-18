"""Exercise moved entry points outside the checkout without GPU dependencies."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "examples.search.goal",
        "examples.search.experiment.pipeline",
        "examples.search.task.dataset",
        "examples.search.task.retrieval",
        "examples.search.training.worker",
        "examples.search.training.pilot",
        "examples.search.evaluation.official",
        "examples.search.evaluation.publish",
        "examples.search.evaluation.analysis",
    ],
)
def test_entrypoint_help_outside_checkout(tmp_path: Path, module: str) -> None:
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(project), str(project / "src")))},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
