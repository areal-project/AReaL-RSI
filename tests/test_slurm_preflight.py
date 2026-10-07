from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from areal_rsi.api.control import json_bytes, read_record
from areal_rsi.api.research import GenerationSpec, HarnessSelectionRule, RsiPromotionRule
from areal_rsi.scripts.slurm import check_slurm


@pytest.fixture
def submission_environment(tmp_path: Path) -> tuple[Path, dict[str, str], Path, Path]:
    root = Path(__file__).resolve().parents[1]
    binaries = tmp_path / "bin"
    binaries.mkdir()
    sbatch = binaries / "sbatch"
    sbatch.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$RSI_TEST_SUBMISSION"\nprintf "4242\\n"\n')
    sbatch.chmod(0o755)
    container = binaries / "container"
    container.write_text(
        '#!/bin/sh\nwhile [ "$1" != "$TRAIN_PYTHON" ]; do shift; done\n'
        'shift\nexec "$RSI_TEST_PYTHON" "$@"\n'
    )
    container.chmod(0o755)
    (tmp_path / "image.sif").touch()
    (tmp_path / "config.yaml").write_text("scheduler: {type: slurm}\n")
    (tmp_path / "test_recipe.py").write_text(
        "from pathlib import Path\nimport os\n"
        "from areal_rsi.api.control import json_bytes\n"
        "def prepare(argv):\n"
        "    Path(os.environ['RSI_TEST_RESULT']).write_bytes(json_bytes(argv))\n"
        "    return None\n"
    )
    output = tmp_path / "observed-argv.json"
    environment = os.environ.copy()
    environment.update(
        RSI_ROOT=str(root),
        AREAL_ROOT=str(tmp_path),
        TRAIN_IMAGE=str(tmp_path / "image.sif"),
        RUN_ROOT=str(tmp_path / "run"),
        CONFIG_PATH=str(tmp_path / "config.yaml"),
        RECIPE="test_recipe.prepare",
        CONTAINER_BIN=str(container),
        SUBMIT_PYTHON=sys.executable,
        CONTROLLER_MOUNTS="/shared:/shared",
        WORKER_MOUNTS="/shared:/shared",
        AREAL_ADMIN_API_KEY="test-admin",
        RSI_TEST_PYTHON=sys.executable,
        RSI_TEST_RESULT=str(output),
        RSI_TEST_SUBMISSION=str(tmp_path / "submission.txt"),
        BASH_ENV=os.devnull,
        PATH=str(binaries) + os.pathsep + os.environ.get("PATH", ""),
    )
    return root / "src/areal_rsi/scripts/submit_train.sh", environment, tmp_path / "run", output


def test_submit_script_preserves_literal_argv_and_refuses_a_second_submission(
    submission_environment: tuple[Path, dict[str, str], Path, Path],
) -> None:
    script, environment, run, output = submission_environment
    arguments = [
        "--data",
        "/shared/中文 data.jsonl",
        "actor.path=/models/Qwen 3",
        "`literal`",
        "$literal",
        '"quoted"',
        "line1\nline2",
        "",
    ]
    submitted = subprocess.run(
        ["bash", str(script), *arguments], env=environment, capture_output=True, text=True
    )
    assert submitted.returncode == 0, submitted.stderr
    assert submitted.stdout.strip() == "4242"
    assert read_record(run / "recipe-args.json", list[str]) == arguments
    scheduler_args = Path(environment["RSI_TEST_SUBMISSION"]).read_text()
    assert "--gres=gpu:0\n" in scheduler_args and "--no-requeue\n" in scheduler_args
    controlled = subprocess.run(
        ["bash", str(script), "--controller"], env=environment, capture_output=True, text=True
    )
    assert controlled.returncode == 0, controlled.stderr
    assert read_record(output, list[str]) == ["--config", environment["CONFIG_PATH"], *arguments]
    repeated = subprocess.run(
        ["bash", str(script), *arguments], env=environment, capture_output=True, text=True
    )
    assert repeated.returncode != 0 and "FileExistsError" in repeated.stderr
    assert Path(environment["RSI_TEST_SUBMISSION"]).read_text() == scheduler_args


def test_controller_rejects_non_argv_json_before_running_the_recipe(
    submission_environment: tuple[Path, dict[str, str], Path, Path],
) -> None:
    script, environment, run, output = submission_environment
    run.mkdir()
    (run / "recipe-args.json").write_bytes(json_bytes({"unexpected": "argument"}))
    controlled = subprocess.run(
        ["bash", str(script), "--controller"], env=environment, capture_output=True, text=True
    )
    assert controlled.returncode != 0
    assert "Expected JSON list" in controlled.stderr
    assert not output.exists()
    assert not Path(environment["RSI_TEST_SUBMISSION"]).exists()


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
