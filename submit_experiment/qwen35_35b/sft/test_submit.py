"""Offline SFT submission, data-boundary, and credential handling checks."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


submit = load_module("sft_experiment_submit", ROOT / "submit.py")
train = load_module("sft_text_train", ROOT / "train.py")


def test_dry_run_uses_one_gpu_allocation_without_side_effects(tmp_path):
    credentials = tmp_path / "private.env"
    marker = tmp_path / "sourced"
    credentials.write_text(f"touch {shlex.quote(str(marker))}\n")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "submit.py"),
            "--dry-run",
            "--experiment-name",
            "sft-test",
            "--trial-name",
            "dry",
            "--fileroot",
            str(tmp_path / "experiments"),
        ],
        cwd=tmp_path,
        env={**os.environ, "CREDENTIALS_FILE": str(credentials), "SBATCH_RESERVATION": "stale"},
        text=True,
        capture_output=True,
        check=True,
    )
    command = shlex.split(result.stdout.split("Planned command: ", 1)[1].splitlines()[0])
    assert command.count("sbatch") == 1
    assert "--gres=gpu:8" in command
    assert "--reservation" not in command
    assert not marker.exists()
    assert not (tmp_path / "experiments").exists()
    config = yaml.safe_load(result.stdout.split("Composed config:\n", 1)[1])
    assert config["scheduler"]["type"] is None
    assert "launch" not in config
    assert "rollout" not in config
    assert "econfig" not in config
    assert "n_mbs" not in config["actor"]["mb_spec"]


def test_data_paths_resolve_from_data_file_and_validation_is_independent(tmp_path):
    data = tmp_path / "data.yaml"
    data.write_text("train_dataset:\n  path: train.jsonl\nvalid_dataset:\n  path: valid.jsonl\n")
    config = submit.compose(ROOT / "params.yaml", data)
    assert config["train_dataset"]["path"] == str(tmp_path / "train.jsonl")
    assert config["valid_dataset"]["path"] == str(tmp_path / "valid.jsonl")
    assert config["valid_dataset"]["shuffle"] is False
    assert config["valid_dataset"]["drop_last"] is False
    assert config["valid_dataset"]["scheduling_spec"] is None


@pytest.mark.parametrize("n_mbs", [None, 0, 17, True])
def test_explicit_invalid_microbatch_count_is_rejected(tmp_path, n_mbs):
    params = submit.load_yaml(ROOT / "params.yaml")
    params["actor"]["mb_spec"]["n_mbs"] = n_mbs
    path = tmp_path / "params.yaml"
    path.write_text(yaml.safe_dump(params))
    with pytest.raises(ValueError, match="Require 1 <= n_mbs"):
        submit.compose(path, ROOT / "data.yaml")


def test_params_cannot_switch_away_from_direct_slurm_spmd(tmp_path):
    params = tmp_path / "params.yaml"
    config = submit.load_yaml(ROOT / "params.yaml")
    config["scheduler"] = {"type": "local"}
    params.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="fixed in base"):
        submit.compose(params, ROOT / "data.yaml")


@pytest.mark.parametrize("failed", [False, True])
def test_preflight_waits_and_only_success_removes_temporary_files(monkeypatch, tmp_path, failed):
    monkeypatch.setattr(submit, "check_paths", lambda *_: None)
    directories = []

    def run(command, **_kwargs):
        root = Path(command[-2])
        directories.append(root)
        assert root.is_dir()
        assert "--wait" in command
        assert "--gres=gpu:0" in command
        assert (root / "train.py").is_file()
        return subprocess.CompletedProcess(command, int(failed), stdout="", stderr="")

    monkeypatch.setattr(submit.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["submit.py", "--fileroot", str(tmp_path), "--check-only"])
    if failed:
        with pytest.raises(ValueError, match="logs retained"):
            submit.main()
        assert directories[0].is_dir()
    else:
        submit.main()
        submit.main()
        assert directories[0] != directories[1]
        assert all(not p.exists() for p in directories)


@pytest.mark.parametrize("content", [[{"type": "image", "image": "image.png"}], None])
def test_text_profile_rejects_nontext_payloads(tmp_path, content):
    path = tmp_path / "data.jsonl"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": content},
                    {"role": "assistant", "content": "answer"},
                ]
            }
        )
        + "\n"
    )
    with pytest.raises(ValueError, match="text content"):
        train.inspect_jsonl(str(path))


def test_text_profile_counts_complete_multi_turn_conversations(tmp_path):
    path = tmp_path / "data.jsonl"
    messages = [
        {"role": role, "content": text}
        for role, text in [
            ("user", "First"),
            ("assistant", "Answer"),
            ("user", "Follow up"),
            ("assistant", "Second answer"),
        ]
    ]
    path.write_text(json.dumps({"messages": messages}) + "\n")
    assert train.inspect_jsonl(str(path)) == {"records": 1, "multi_turn_records": 1}


@pytest.mark.parametrize(
    "has_key,has_url", [(True, True), (True, False), (False, True), (False, False)]
)
def test_worker_loads_credentials_inside_runtime_and_uses_torchrun(tmp_path, has_key, has_url):
    credentials = tmp_path / "private.env"
    credentials.write_text(
        ("export WANDB_API_KEY=private-test-value\n" if has_key else "")
        + ("export WANDB_BASE_URL=https://wandb.example\n" if has_url else "")
    )
    binary = tmp_path / "python"
    binary.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "print(json.dumps({'args':sys.argv[1:],'mode':os.environ['WANDB_MODE'],"
        "'has_key':bool(os.getenv('WANDB_API_KEY'))}))\n"
    )
    binary.chmod(0o755)
    values = {
        "AREAL_DIR": str(tmp_path),
        "SFT_PYTHONPATH": "",
        "SFT_PYTHON": str(binary),
        "CREDENTIALS_FILE": str(credentials),
    }
    (tmp_path / "environment.sh").write_text(
        "".join(f"export {k}={shlex.quote(v)}\n" for k, v in values.items())
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("WANDB_")}
    result = subprocess.run(
        ["bash", str(ROOT / "worker.sh"), str(tmp_path)],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    record = json.loads(result.stdout.splitlines()[-1])
    assert record["args"][:2] == ["-m", "torch.distributed.run"]
    assert "--nproc-per-node=8" in record["args"]
    assert record["mode"] == ("online" if has_key and has_url else "disabled")
    assert "private-test-value" not in result.stdout
