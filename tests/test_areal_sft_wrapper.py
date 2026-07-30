import subprocess
from pathlib import Path

import pytest

from areal_rsi.services.areal import (
    AReaLSFTConfig,
    AReaLSFTConfigurationError,
    AReaLSFTSubmissionError,
    AReaLSFTTrainingError,
    AReaLSlurmConfig,
    AReaLSFTWrapper,
)


def _experiment_config(tmp_path: Path, *, scheduler_type: str = "null") -> Path:
    config_path = tmp_path / "sft.yaml"
    config_path.write_text(
        "\n".join(
            (
                "experiment_name: sft-exp",
                "trial_name: trial-1",
                "actor:",
                "  path: /models/qwen",
                "  backend: fsdp:d4p1t1",
                "cluster:",
                "  n_nodes: 2",
                "  n_gpus_per_node: 4",
                f"  fileroot: {tmp_path / 'artifacts'}",
                "scheduler:",
                f"  type: {scheduler_type}",
                "train_dataset:",
                "  path: /datasets/sft",
                "  type: sft",
                "saver:",
                "  freq_epochs: 1",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _wrapper(tmp_path: Path, runner) -> AReaLSFTWrapper:
    return AReaLSFTWrapper(
        AReaLSFTConfig(command=("areal",), working_directory=tmp_path, username="tester"),
        runner=runner,
    )


def _slurm_wrapper(tmp_path: Path, runner) -> AReaLSFTWrapper:
    return AReaLSFTWrapper(
        AReaLSFTConfig(
            command=("areal",),
            working_directory=tmp_path,
            username="tester",
            slurm=AReaLSlurmConfig(
                image="/images/areal.sif",
                cpus_per_task=32,
                memory="200G",
                time_limit="01:30:00",
                partition="gpu",
                account="research",
                reservation="nightly",
                offline_datasets=True,
                container_environment={"NCCL_DEBUG": "WARN"},
            ),
        ),
        runner=runner,
    )


def test_sft_wrapper_runs_self_contained_experiment_config_and_collects_artifacts(tmp_path):
    seen: list[tuple[list[str], dict]] = []
    config_path = _experiment_config(tmp_path)

    def runner(command, **kwargs):
        seen.append((command, kwargs))
        checkpoint = (
            tmp_path
            / "artifacts/checkpoints/tester/sft-exp/trial-1/default/epoch0epochstep3globalstep3"
        )
        checkpoint.mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0)

    wrapper = _wrapper(tmp_path, runner)
    train_run = wrapper.run_experiment(config_path)
    artifacts = wrapper.get_experiment_artifacts(config_path)

    assert seen[0][0] == [
        "areal",
        "train",
        "run",
        "--config",
        str(config_path.resolve()),
        "--driver",
        "examples.math.gsm8k_sft:main",
    ]
    assert seen[0][1]["cwd"] == str(tmp_path)
    assert train_run["base_ckpt"] == "/models/qwen"
    assert train_run["ckpt"].endswith("globalstep3")
    assert train_run["metadata"]["config_path"] == str(config_path.resolve())
    assert artifacts["latest_checkpoint"] == train_run["ckpt"]


def test_sft_wrapper_reports_failed_process_and_rejects_incomplete_config(tmp_path):
    config_path = _experiment_config(tmp_path)
    wrapper = _wrapper(tmp_path, lambda command, **kwargs: subprocess.CompletedProcess(command, 23))

    with pytest.raises(AReaLSFTTrainingError, match="exit code 23") as error:
        wrapper.run_experiment(config_path)

    assert error.value.log_path.is_file()
    incomplete = tmp_path / "incomplete.yaml"
    incomplete.write_text("experiment_name: only-name\n", encoding="utf-8")
    with pytest.raises(AReaLSFTConfigurationError, match="trial_name"):
        wrapper.run_experiment(incomplete)


def test_sft_wrapper_rejects_interpolated_fileroot(tmp_path):
    config_path = _experiment_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            str(tmp_path / "artifacts"), "${oc.env:AREAL_ROOT}"
        ),
        encoding="utf-8",
    )

    with pytest.raises(AReaLSFTConfigurationError, match="literal path"):
        _wrapper(tmp_path, lambda *args, **kwargs: None).run_experiment(config_path)


def test_sft_wrapper_renders_containerized_spmd_sbatch_script_from_config(tmp_path):
    config_path = _experiment_config(tmp_path)
    script = _slurm_wrapper(
        tmp_path, lambda *args, **kwargs: subprocess.CompletedProcess(args, 0)
    ).render_sbatch_script(config_path, driver="examples.custom_sft:main")

    assert "#SBATCH --job-name=sft-exp-trial-1" in script
    assert "#SBATCH --nodes=2" in script
    assert "#SBATCH --ntasks=2" in script
    assert "#SBATCH --gres=gpu:4" in script
    assert "#SBATCH --partition=gpu" in script
    assert "#SBATCH --account=research" in script
    assert "#SBATCH --reservation=nightly" in script
    assert "srun --mpi=none --nodes=2 --ntasks=2 --ntasks-per-node=1" in script
    assert "--node_rank=\"${SLURM_NODEID}\"" in script
    assert "--master_addr=\"${MASTER_ADDR}\"" in script
    assert "examples/custom_sft.py --config" in script
    assert "scheduler.type=" not in script
    assert "AREAL_CACHE_DIR=" in script
    assert "HF_DATASETS_OFFLINE=1" in script
    assert "NCCL_DEBUG=WARN" in script
    assert "/storage:/storage" in script


def test_sft_wrapper_requires_spmd_scheduler_and_cluster_resources_in_yaml(tmp_path):
    config_path = _experiment_config(tmp_path, scheduler_type="local")
    wrapper = _slurm_wrapper(tmp_path, lambda *args, **kwargs: subprocess.CompletedProcess(args, 0))

    with pytest.raises(AReaLSFTConfigurationError, match="scheduler.type: null"):
        wrapper.render_sbatch_script(config_path)

    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace("  n_nodes: 2\n", "")
        .replace("  type: local", "  type: null"),
        encoding="utf-8",
    )
    with pytest.raises(AReaLSFTConfigurationError, match="cluster.n_nodes"):
        wrapper.render_sbatch_script(config_path)


def test_sft_wrapper_writes_and_submits_sbatch_script(tmp_path):
    seen: list[tuple[list[str], dict]] = []
    config_path = _experiment_config(tmp_path)

    def runner(command, **kwargs):
        seen.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="987654;cluster\n", stderr="")

    wrapper = _slurm_wrapper(tmp_path, runner)
    submission = wrapper.submit_experiment(config_path)

    command, kwargs = seen[0]
    script_path = Path(submission["script_path"])
    assert command == ["sbatch", "--parsable", str(script_path)]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["stdout"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.PIPE
    assert script_path.is_file()
    assert subprocess.run(["bash", "-n", str(script_path)], check=False).returncode == 0
    assert submission["job_id"] == "987654"
    assert submission["status"] == "submitted"
    assert submission["slurm_log_path"].endswith("slurm-987654.out")
    assert submission["artifacts"]["latest_checkpoint"] is None


def test_sft_wrapper_reports_failed_sbatch_submission_and_rejects_secret_environment(tmp_path):
    config_path = _experiment_config(tmp_path)
    wrapper = _slurm_wrapper(
        tmp_path,
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 17, stdout="", stderr="invalid account"
        ),
    )

    with pytest.raises(AReaLSFTSubmissionError, match="invalid account") as error:
        wrapper.submit_experiment(config_path)

    assert error.value.script_path.is_file()
    with pytest.raises(AReaLSFTConfigurationError, match="credentials"):
        AReaLSlurmConfig(image="/images/areal.sif", container_environment={"API_KEY": "secret"})
