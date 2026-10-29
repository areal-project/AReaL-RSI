"""Offline behavior tests; no Slurm, Arena, containers, or GPU allocations."""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("experiment_submit", ROOT / "submit.py")
assert SPEC and SPEC.loader
submit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = submit
SPEC.loader.exec_module(submit)
PROFILE = ROOT


def configuration(params_path: Path | None = None):
    return submit.compose(PROFILE, params_path or PROFILE / "params.yaml", PROFILE / "arena.yaml")


def arguments(**overrides):
    values = dict(areal_dir=None, partition=None, reservation=None)
    values.update(overrides)
    return argparse.Namespace(**values)


def write_params(tmp_path, update):
    params = submit.merge(submit.load_yaml(PROFILE / "params.yaml"), update)
    path = tmp_path / "params.yaml"
    path.write_text(yaml.safe_dump(params))
    return path


def test_cpu_memory_override_preserves_role_containers_and_topology(tmp_path):
    path = write_params(
        tmp_path,
        {"actor": {"optimizer": {"lr": 1.0e-6}, "scheduling_spec": [{"cpu": 8, "mem": 96}]}},
    )
    config = configuration(path)
    spec = config["actor"]["scheduling_spec"][0]
    assert spec["cpu"] == 8 and spec["mem"] == 96
    assert spec["image"] == "${oc.env:AREAL_ACTOR_IMAGE}"
    assert spec["cmd"].endswith("areal.infra.rpc.rpc_server")
    assert config["actor"]["backend"] == "megatron:(attn:d2p1t1c4|ffn:d1p1t1e8)"
    assert config["actor"]["optimizer"]["lr"] == 1.0e-6
    assert config["ref"]["scheduling_spec"][0]["cpu"] == 8
    assert config["ref"]["scheduling_spec"][0]["mem"] == 96


@pytest.mark.parametrize(
    "update",
    [
        {"scheduler": {"type": "local"}},
        {"actor": {"backend": "megatron:d16"}},
        {"rollout": {"return_routed_experts": True}},
        {"econfig": {"arena_llm_route_mode": "session_gateway"}},
        {"actor": {"scheduling_spec": [{"image": "another.sif"}]}},
    ],
)
def test_fixed_runtime_cannot_be_overridden_from_params(tmp_path, update):
    with pytest.raises(ValueError, match=r"fixed in base\.yaml"):
        configuration(write_params(tmp_path, update))


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"gconfig": {"max_tokens": 262144}}, "context_length"),
        ({"actor": {"min_usable_group_size": 13}}, "n_samples"),
        ({"actor": {"optimizer": {"type": "sgd"}}}, "CPU staging"),
        ({"actor": {"megatron": {"entropy_requires_grad": True}}}, "Chunked loss"),
        ({"total_train_steps": 1}, "warmup"),
        ({"actor": {"reward_norm": {"mean_level": "group"}}}, "not both"),
        ({"rollout": {"queue_size": 64}}, "queue_size must exceed"),
        ({"rollout": {"queue_size": 63}}, "queue_size must exceed"),
    ],
)
def test_incompatible_tuning_fails_before_submission(tmp_path, update, message):
    with pytest.raises(ValueError, match=message):
        configuration(write_params(tmp_path, update))


def test_reservation_reaches_controller_workers_and_can_be_cleared(monkeypatch, tmp_path):
    monkeypatch.setenv("AREAL_RESERVATION", "old-pool")
    monkeypatch.setenv("SBATCH_RESERVATION", "stale-pool")
    monkeypatch.setenv("SBATCH_NODELIST", "unwanted-node")
    monkeypatch.setenv("AREAL_SPMD_MODE", "1")
    config = configuration()
    runtime = submit.runtime_from_config(config, PROFILE, arguments(reservation="new-pool"))
    env = submit.environment(runtime, tmp_path, "experiment", "trial", "/private/credentials.env")
    command = submit.sbatch_command(runtime, tmp_path, "experiment", "trial", False)
    assert command[command.index("--reservation") + 1] == "new-pool"
    assert env["EXPERIMENT_RESERVATION"] == env["SBATCH_RESERVATION"] == "new-pool"
    for role in ("actor", "rollout"):
        assert (
            config[role]["scheduling_spec"][0]["reservation"]
            == "${oc.env:EXPERIMENT_RESERVATION,null}"
        )
    stale_config = configuration()
    stale_config["launch"]["slurm"]["reservation"] = "profile-pool"
    cleared = submit.runtime_from_config(stale_config, PROFILE, arguments())
    env = submit.clean_submission_environment(
        submit.environment(cleared, tmp_path, "experiment", "trial", "")
    )
    assert "SBATCH_RESERVATION" not in env
    assert "SBATCH_NODELIST" not in env
    assert "AREAL_SPMD_MODE" not in env
    assert "EXPERIMENT_RESERVATION" not in env
    assert "--reservation" not in submit.sbatch_command(
        cleared, tmp_path, "experiment", "trial", False
    )


@pytest.mark.parametrize("credentials_from_env", [False, True])
def test_dry_run_prints_config_without_files_sourcing_credentials_or_submitting(
    tmp_path, credentials_from_env
):
    marker = tmp_path / "credentials-were-sourced"
    credentials = tmp_path / "private.env"
    credentials.write_text(f"touch '{marker}'\nexport ARENA_OPENAPI_TOKEN=private-test-value\n")
    env = dict(
        os.environ,
        ARENA_OPENAPI_TOKEN="private-test-value",
        SBATCH_NODELIST="stale-node",
        SUBMIT_PYTHON=sys.executable,
    )
    if credentials_from_env:
        env["CREDENTIALS_FILE"] = str(credentials)
    else:
        env["CREDENTIALS_FILE"] = str(tmp_path / "ignored-environment-value.env")
    result = subprocess.run(
        [
            str(ROOT / "submit.sh"),
            "--dry-run",
            "--experiment-name",
            "test-experiment",
            "--trial-name",
            "offline",
            "--fileroot",
            str(tmp_path / "experiment_storage"),
            "--reservation",
            "test-pool",
            *([] if credentials_from_env else ["--credentials-file", str(credentials)]),
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert not marker.exists()
    run = (
        tmp_path / "experiment_storage" / "logs" / getpass.getuser() / "test-experiment" / "offline"
    )
    assert not (tmp_path / "experiment_storage").exists()
    config = yaml.safe_load(result.stdout.split("Composed config:\n", 1)[1])
    assert "launch" not in config
    assert config["experiment_name"] == "test-experiment"
    assert config["trial_name"] == "offline"
    assert config["cluster"]["fileroot"] == str(tmp_path / "experiment_storage")
    assert config["sglang"]["context_length"] == 262144
    assert config["gconfig"]["max_tokens"] == 262143
    assert config["actor"]["megatron"]["cpu_staged_offload"]["enabled"] is True
    assert config["actor"]["megatron"]["enable_chunked_logits"] is True
    assert f"Credentials file: {credentials}" in result.stdout
    command = shlex.split(result.stdout.split("Planned controller command: ", 1)[1].splitlines()[0])
    assert Path(command[command.index("--chdir") + 1]) == ROOT.parents[2] / "AReaL"
    assert command[command.index("--job-name") + 1] == "test-experiment-offline-controller"
    assert command[command.index("--reservation") + 1] == "test-pool"
    assert "private-test-value" not in result.stdout
    # Dry-run can inspect an existing training trial without touching it.
    run.mkdir(parents=True)
    sentinel = run / "keep.txt"
    sentinel.write_text("existing training data")
    repeated = subprocess.run(
        [
            sys.executable,
            str(ROOT / "submit.py"),
            "--dry-run",
            "--experiment-name",
            "test-experiment",
            "--trial-name",
            "offline",
            "--fileroot",
            str(tmp_path / "experiment_storage"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert repeated.returncode == 0
    assert list(run.iterdir()) == [sentinel]
    assert sentinel.read_text() == "existing training data"


@pytest.mark.parametrize("empty_environment", [False, True])
def test_credentials_default_is_discovered_beside_script_from_another_cwd(
    tmp_path, empty_environment
):
    model = tmp_path / "model"
    model.mkdir()
    for name in (
        "submit.sh",
        "submit.py",
        "base.yaml",
        "params.yaml",
        "arena.yaml",
        "controller.py",
        "controller.sbatch",
    ):
        shutil.copyfile(ROOT / name, model / name)
    credentials = model / "credentials.env"
    marker = tmp_path / "credentials-were-sourced"
    credentials.write_text(f"touch '{marker}'\n")
    files_before = set(model.iterdir())
    env = dict(os.environ, SUBMIT_PYTHON=sys.executable)
    env.pop("CREDENTIALS_FILE", None)
    if empty_environment:
        env["CREDENTIALS_FILE"] = ""
    result = subprocess.run(
        ["bash", str(model / "submit.sh"), "--dry-run", "--trial-name", "default-credentials"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert f"Credentials file: {credentials}" in result.stdout
    assert not marker.exists()
    assert set(model.iterdir()) == files_before


@pytest.mark.parametrize("check_only", [False, True])
def test_only_training_reserves_trial_and_checks_can_repeat(monkeypatch, tmp_path, check_only):
    submissions = []
    configs = []

    def run(command, **kwargs):
        if command[0] == "sbatch":
            submissions.append(command)
            directory = Path(kwargs["env"]["EXPERIMENT_RUN_DIR"])
            assert (directory / "train.yaml").exists()
            configs.append(yaml.safe_load((directory / "train.yaml").read_text()))
            return subprocess.CompletedProcess(command, 0, stdout="12345\n")
        return subprocess.CompletedProcess(command, 0, stdout="" if kwargs.get("text") else b"")

    monkeypatch.setattr(submit.subprocess, "run", run)
    monkeypatch.setattr(submit, "check_paths", lambda *args: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "submit.py",
            "--fileroot",
            str(tmp_path),
            "--experiment-name",
            "experiment",
            "--trial-name",
            "trial",
            *(["--check-only"] if check_only else []),
        ],
    )
    formal = tmp_path / "logs" / getpass.getuser() / "experiment" / "trial"
    if check_only:
        formal.mkdir(parents=True)
        (formal / "keep.txt").write_text("existing training data")
    submit.main()
    if check_only:
        submit.main()
        directories = [Path(command[-2]) for command in submissions]
        assert directories[0] != directories[1]
        assert all(
            path.parent == tmp_path / "logs" / getpass.getuser() / ".checks" for path in directories
        )
        assert configs[0]["cluster"]["fileroot"] == str(directories[0])
        assert all(not directory.exists() for directory in directories)
        assert (formal / "keep.txt").read_text() == "existing training data"
    else:
        assert (formal / "controller.jobid").read_text() == "12345\n"
        config = yaml.safe_load((formal / "train.yaml").read_text())
        assert config["cluster"]["fileroot"] == str(tmp_path)
        with pytest.raises(FileExistsError):
            submit.main()
        assert len(submissions) == 1


@pytest.mark.parametrize("exit_code", [0, 9])
def test_parent_waits_for_preflight_then_cleans_only_on_success(monkeypatch, tmp_path, exit_code):
    directories = []

    def run(command, **kwargs):
        if command[0] == "sbatch":
            assert "--wait" in command
            directory = Path(kwargs["env"]["EXPERIMENT_RUN_DIR"])
            directories.append(directory)
            (directory / "controller-12345.log").write_text("preflight log")
            return subprocess.CompletedProcess(command, exit_code)
        return subprocess.CompletedProcess(command, 0, stdout="" if kwargs.get("text") else b"")

    monkeypatch.setattr(submit.subprocess, "run", run)
    monkeypatch.setattr(submit, "check_paths", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["submit.py", "--fileroot", str(tmp_path), "--check-only"])
    if exit_code:
        with pytest.raises(ValueError, match="CPU preflight failed; logs retained"):
            submit.main()
        assert (directories[0] / "controller-12345.log").read_text() == "preflight log"
    else:
        submit.main()
        assert not directories[0].exists()


@pytest.mark.parametrize(
    ("has_key", "has_url", "expected_mode"),
    [
        (True, True, "online"),
        (True, False, "disabled"),
        (False, True, "disabled"),
        (False, False, "disabled"),
    ],
)
def test_controller_loads_credentials_and_auto_selects_wandb(
    tmp_path, has_key, has_url, expected_mode
):
    pytest.importorskip("omegaconf")
    credentials = tmp_path / "credentials.env"
    credentials.write_text(
        "export ARENA_OPENAPI_BASE=https://arena.example\n"
        "export ARENA_OPENAPI_TOKEN=arena-test-secret\n"
        "export SWE_RL_ADMIN_API_KEY=admin-test-secret\n"
        + ("export WANDB_API_KEY=wandb-test-secret\n" if has_key else "")
        + ("export WANDB_BASE_URL=https://wandb.example\n" if has_url else "")
    )
    config = configuration()
    runtime = submit.runtime_from_config(config, PROFILE, arguments())
    (tmp_path / "train.yaml").write_text(yaml.safe_dump(config))
    values = submit.environment(runtime, tmp_path, "experiment", "trial", str(credentials))
    (tmp_path / "environment.sh").write_text(
        "".join(f"export {key}={submit.shlex.quote(value)}\n" for key, value in values.items())
    )
    # Run the real controller shell with container execution replaced by a
    # recorder. No Slurm, containers, Arena requests or W&B login are performed.
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    stub = binary_dir / "singularity"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "from omegaconf import OmegaConf\n"
        "root = Path(os.environ['EXPERIMENT_RUN_DIR'])\n"
        "config = OmegaConf.load(root / 'train.yaml')\n"
        "with (root / 'calls.jsonl').open('a') as stream:\n"
        "    stream.write(json.dumps({'mode': config.stats_logger.wandb.mode,\n"
        "        'has_key': bool(os.environ.get('WANDB_API_KEY')),\n"
        "        'has_url': bool(os.environ.get('WANDB_BASE_URL'))}) + '\\n')\n"
    )
    stub.chmod(0o700)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("WANDB_", "BASH_FUNC_")) and key not in ("BASH_ENV", "ENV")
    }
    env["PATH"] = str(binary_dir) + os.pathsep + env["PATH"]
    # The decision must replace a stale mode inherited from the submit host.
    env["WANDB_MODE"] = "offline"
    result = subprocess.run(
        ["bash", str(ROOT / "controller.sbatch"), str(tmp_path), "--check-only"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert calls == [dict(mode=expected_mode, has_key=has_key, has_url=has_url)] * 2
    assert "wandb-test-secret" not in result.stdout + result.stderr
    assert "arena-test-secret" not in result.stdout + result.stderr


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    path = tmp_path / "duplicate.yaml"
    path.write_text("actor: {}\nactor: {}\n")
    with pytest.raises(ValueError, match="duplicate"):
        submit.load_yaml(path)


def test_native_swe_structured_config_loads(monkeypatch, tmp_path):
    # This check runs in the AReaL actor runtime; a lightweight submission host
    # may lack AReaL's dependencies. The Slurm preflight also uses this loader.
    pytest.importorskip("httpx")
    pytest.importorskip("mcp")
    monkeypatch.syspath_prepend(str(ROOT.parents[2] / "AReaL"))
    from areal.api.cli_args import to_structured_cfg
    from examples.swe.utils import SWEPPOConfig
    from omegaconf import OmegaConf

    config = configuration()
    runtime = submit.runtime_from_config(config, PROFILE, arguments())
    config["experiment_name"], config["trial_name"] = "schema-test", "trial"
    config["cluster"]["fileroot"] = str(tmp_path)
    for key, value in submit.environment(runtime, tmp_path, "schema-test", "trial", "").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ARENA_OPENAPI_BASE", "https://arena.example")
    monkeypatch.setenv("SWE_RL_ADMIN_API_KEY", "schema-test-key")
    typed = OmegaConf.to_object(to_structured_cfg(OmegaConf.create(config), SWEPPOConfig))
    assert typed.ref.megatron.enable_chunked_logits is True
    assert typed.ref.mb_spec.max_tokens_per_mb == 262144


def test_omitting_reservation_uses_public_pool_and_names_partition_trials(tmp_path):
    env = dict(
        os.environ,
        AREAL_RESERVATION="old-pool",
        SBATCH_RESERVATION="stale-pool",
        EXPERIMENT_RESERVATION="inherited-pool",
        SBATCH_NODELIST="unwanted-node",
    )
    for experiment, trial in (
        ("experiment-a", "trial-1"),
        ("experiment-a", "trial-2"),
        ("experiment-b", "trial-1"),
    ):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "submit.py"),
                "--dry-run",
                "--experiment-name",
                experiment,
                "--trial-name",
                trial,
                "--fileroot",
                str(tmp_path),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        command = shlex.split(
            result.stdout.split("Planned controller command: ", 1)[1].splitlines()[0]
        )
        assert "--reservation" not in command
        assert (
            str(tmp_path / "logs" / getpass.getuser() / experiment / trial / "controller.sbatch")
            in command
        )
        config = yaml.safe_load(result.stdout.split("Composed config:\n", 1)[1])
        assert config["experiment_name"] == experiment
        assert config["trial_name"] == trial
        assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize("reservation", [None, "gpu-pool"])
def test_native_omegaconf_links_reach_ref_and_both_worker_roles(monkeypatch, tmp_path, reservation):
    omega = pytest.importorskip("omegaconf").OmegaConf
    config = configuration()
    runtime = submit.runtime_from_config(config, PROFILE, arguments(reservation=reservation))
    for key, value in submit.environment(
        runtime, tmp_path, "experiment", "trial", "/private/env"
    ).items():
        monkeypatch.setenv(key, value)
    if reservation is None:
        monkeypatch.delenv("EXPERIMENT_RESERVATION", raising=False)
    monkeypatch.setenv("ARENA_OPENAPI_BASE", "https://arena.example")
    monkeypatch.setenv("SWE_RL_ADMIN_API_KEY", "test-admin-key")
    resolved = omega.to_container(omega.create(config), resolve=True)
    for field in ("megatron", "mb_spec", "scheduling_spec"):
        assert isinstance(config["ref"][field], (dict, list))
        assert config["ref"][field] == config["actor"][field]
    assert resolved["actor"]["temperature"] == resolved["gconfig"]["temperature"]
    assert resolved["rollout"]["agent"]["engine_max_tokens"] == 262143
    assert resolved["ref"]["backend"] == resolved["actor"]["backend"]
    assert resolved["ref"]["megatron"]["enable_chunked_logits"] is True
    for role in ("actor", "rollout", "ref"):
        spec = resolved[role]["scheduling_spec"][0]
        assert spec["reservation"] == reservation
        assert runtime.areal_dir in spec["srun_additional_args"]
