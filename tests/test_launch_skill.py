import json
import subprocess
import sys
from pathlib import Path

import yaml


LAUNCH_SCRIPT = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "start-rsi-experiment"
    / "scripts"
    / "launch.py"
)


def test_launch_skill_resolves_isolated_configs_without_leaking_token(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (tmp_path / ".env").write_text("ENVARENA_API_TOKEN=private-token\n", encoding="utf-8")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "areal.yaml").write_text(
        """
experiment_name: old-name
trial_name: template
cluster:
  fileroot: /fixed/experiments
  name_resolve:
    type: nfs
    nfs_record_root: /old/name-resolve
actor:
  path: /old/checkpoint
""".lstrip(),
        encoding="utf-8",
    )
    (config_dir / "service.yaml").write_text(
        "areal:\n  sft:\n    scheduler_type: slurm\n", encoding="utf-8"
    )
    (config_dir / "rsi.yaml").write_text(
        """
run:
  initial_ckpt: /old/checkpoint
  method: sft
  max_iterations: 1
  run_root: /fixed/runs
env:
  benchmark: old-train
  holdout_benchmark: old-holdout
  harness_config:
    harness: old-harness
services:
  areal:
    config_path: service.yaml
  envarena:
    backend: mock
train:
  config:
    train_config_path: areal.yaml
stop_criteria:
  target_mean_reward: 1.0
""".lstrip(),
        encoding="utf-8",
    )
    output_dir = tmp_path / "prepared"

    result = subprocess.run(
        [
            sys.executable,
            str(LAUNCH_SCRIPT),
            "--repo-root",
            str(tmp_path),
            "--experiment-name",
            "skill-test",
            "--initial-ckpt",
            "/models/new",
            "--benchmark",
            "train@1",
            "--holdout-benchmark",
            "holdout@2",
            "--harness",
            "harness@3",
            "--max-iterations",
            "4",
            "--target-mean-reward",
            "0.75",
            "--output-dir",
            str(output_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    receipt = json.loads(result.stdout)
    assert receipt["launched"] is False
    assert receipt["envarena_api_token_configured"] is True
    assert "private-token" not in result.stdout
    resolved = yaml.safe_load((output_dir / "rsi.yaml").read_text(encoding="utf-8"))
    assert resolved["run"] == {
        "initial_ckpt": "/models/new",
        "method": "sft",
        "max_iterations": 4,
        "run_root": "/fixed/runs",
    }
    assert resolved["env"] == {
        "benchmark": "train@1",
        "holdout_benchmark": "holdout@2",
        "harness_config": {"harness": "harness@3"},
    }
    assert resolved["services"]["envarena"] == {"backend": "mock"}
    assert resolved["services"]["areal"]["config_path"] == str(
        (config_dir / "service.yaml").resolve()
    )
    assert resolved["stop_criteria"]["target_mean_reward"] == 0.75

    areal = yaml.safe_load(
        (output_dir / "areal-training.yaml").read_text(encoding="utf-8")
    )
    assert areal["experiment_name"] == "skill-test"
    assert areal["actor"]["path"] == "/models/new"
    assert areal["cluster"]["fileroot"] == "/fixed/experiments"
    assert areal["cluster"]["name_resolve"]["nfs_record_root"] == (
        "/fixed/experiments/name_resolve/${experiment_name}"
    )
    assert "private-token" not in (output_dir / "launch.json").read_text(
        encoding="utf-8"
    )


def test_launch_skill_opd_uses_opd_base_without_sft_template(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "service-opd.yaml").write_text(
        "areal:\n  opd:\n    scheduler_type: local\n", encoding="utf-8"
    )
    (config_dir / "rsi.opd.yaml").write_text(
        """
run:
  initial_ckpt: /old/checkpoint
  method: opd
  max_iterations: 1
  run_root: /fixed/opd-runs
env:
  benchmark: old-train
  holdout_benchmark: old-holdout
  harness_config:
    harness: old-harness
services:
  areal:
    config_path: service-opd.yaml
  envarena:
    backend: mock
    task_set_path: /data/train.jsonl
    task_set_limit: 32
research:
  backend: mock
train:
  config:
    train_data_path: /data/train.jsonl
    valid_data_path: /data/train.jsonl
stop_criteria:
  target_mean_reward: 1.0
""".lstrip(),
        encoding="utf-8",
    )
    output_dir = tmp_path / "prepared-opd"

    result = subprocess.run(
        [
            sys.executable,
            str(LAUNCH_SCRIPT),
            "--repo-root",
            str(tmp_path),
            "--method",
            "opd",
            "--experiment-name",
            "opd-test",
            "--initial-ckpt",
            "/models/new",
            "--benchmark",
            "gsm8k@mock",
            "--holdout-benchmark",
            "gsm8k@mock",
            "--harness",
            "gsm8k-mock@mock",
            "--max-iterations",
            "2",
            "--target-mean-reward",
            "1.0",
            "--output-dir",
            str(output_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    receipt = json.loads(result.stdout)
    assert receipt["method"] == "opd"
    assert receipt["base_config_path"] == str((config_dir / "rsi.opd.yaml").resolve())
    resolved = yaml.safe_load((output_dir / "rsi.yaml").read_text(encoding="utf-8"))
    assert resolved["run"] == {
        "initial_ckpt": "/models/new",
        "method": "opd",
        "max_iterations": 2,
        "run_root": "/fixed/opd-runs",
    }
    assert resolved["env"]["benchmark"] == "gsm8k@mock"
    assert resolved["services"]["areal"]["config_path"] == str(
        (config_dir / "service-opd.yaml").resolve()
    )
    # Researcher experiences drive experience_path at train time; the base carries
    # only train/valid data, and the run is isolated by trial name.
    assert "experience_path" not in resolved["train"]["config"]
    assert resolved["train"]["config"]["trial_name"] == "opd-test"
    # OPD does not materialize a per-run SFT training YAML.
    assert not (output_dir / "areal-training.yaml").exists()

