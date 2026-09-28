from __future__ import annotations

import os
from pathlib import Path

import pytest

from areal_rsi.api.research import GenerationSpec, HarnessSelectionRule, RsiPromotionRule
from areal_rsi.scripts.slurm import check_slurm


def _spec(tmp_path: Path) -> GenerationSpec:
    return GenerationSpec(
        "run1",
        str(tmp_path / "tasks"),
        str(tmp_path / "h0"),
        str(tmp_path / "candidate.txt"),
        str(tmp_path / "runner"),
        str(tmp_path / "model"),
        str(tmp_path / "python"),
        str(tmp_path / "config.yaml"),
        str(tmp_path / "experiments"),
        str(tmp_path / "name_resolve"),
        "demo",
        1,
        2,
        1,
        HarnessSelectionRule(0, 1),
        RsiPromotionRule("joint_performance", 0, 1, 1),
    )


def test_slurm_preflight_checks_commands_and_shared_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec(tmp_path)
    Path(spec.cluster_fileroot).mkdir()
    Path(spec.name_resolve_root).mkdir()
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("sbatch", "squeue", "scancel", "srun", "scontrol"):
        executable = commands / name
        executable.write_text("#!/bin/sh\necho 'slurm 24.11'\n")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ.get("PATH", ""))
    receipt = check_slurm(spec)
    assert receipt.sbatch_version == "slurm 24.11"
    assert receipt.scancel_path == str(commands / "scancel")
    assert receipt.srun_path == str(commands / "srun")
    assert list(Path(spec.cluster_fileroot).iterdir()) == []
    Path(spec.name_resolve_root).rename(tmp_path / "moved")
    with pytest.raises(ValueError, match="shared path"):
        check_slurm(spec)
