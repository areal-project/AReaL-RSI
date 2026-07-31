import getpass
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


def _experiment_config(tmp_path: Path, *, include_resources: bool = True) -> Path:
    resources = "  n_nodes: 2\n  n_gpus_per_node: 4\n" if include_resources else ""
    config_path = tmp_path / "sft.yaml"
    config_path.write_text(
        "\n".join(
            (
                "experiment_name: sft-exp",
                "trial_name: trial-1",
                "actor:",
                "  path: /models/qwen",
                "cluster:",
                resources.rstrip(),
                f"  fileroot: {tmp_path / 'artifacts'}",
                "saver:",
                "  freq_epochs: 1",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _wrapper(runner) -> AReaLSFTWrapper:
    return AReaLSFTWrapper(
        AReaLSFTConfig(
            commands=("areal", "train", "run"),
            environment_variables={"NCCL_DEBUG": "WARN"},
        ),
        runner=runner,
    )


def _slurm_wrapper(tmp_path: Path, runner) -> AReaLSFTWrapper:
    return AReaLSFTWrapper(
        AReaLSFTConfig(
            scheduler_type="slurm",
            commands=("python", "examples/test_sft.py"),
            environment_variables={"NCCL_DEBUG": "WARN"},
            slurm=AReaLSlurmConfig(
                image="/images/areal.sif",
                working_directory=tmp_path,
                cpus_per_task=32,
                memory="200G",
                time_limit="01:30:00",
                partition="gpu",
                account="research",
                reservation="nightly",
                offline_datasets=True,
                container_environment={"NCCL_DEBUG": "INFO"},
            ),
        ),
        runner=runner,
    )


def test_sft_wrapper_starts_local_training_yaml_and_collects_artifacts(tmp_path):
    seen: list[tuple[list[str], dict]] = []
    config_path = _experiment_config(tmp_path)

    def runner(command, **kwargs):
        seen.append((command, kwargs))
        checkpoint = (
            tmp_path
            / "artifacts/checkpoints"
            / getpass.getuser()
            / "sft-exp/trial-1/default/epoch0epochstep3globalstep3"
        )
        checkpoint.mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0)

    wrapper = _wrapper(runner)
    train_run = wrapper.start_experiment(config_path)
    artifacts = wrapper.get_experiment_artifacts(config_path)

    assert seen[0][0] == ["areal", "train", "run", "--config", str(config_path.resolve())]
    assert seen[0][1]["env"]["NCCL_DEBUG"] == "WARN"
    assert train_run["base_ckpt"] == "/models/qwen"
    assert train_run["ckpt"].endswith("globalstep3")
    assert train_run["metadata"]["scheduler_type"] == "local"
    assert artifacts["latest_checkpoint"] == train_run["ckpt"]


def test_sft_wrapper_allows_per_launch_command_and_environment_overrides(tmp_path):
    seen: list[tuple[list[str], dict]] = []
    config_path = _experiment_config(tmp_path)

    def runner(command, **kwargs):
        seen.append((command, kwargs))
        checkpoint = (
            tmp_path
            / "artifacts/checkpoints"
            / getpass.getuser()
            / "sft-exp/trial-1/default/epoch0epochstep1globalstep1"
        )
        checkpoint.mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0)

    _wrapper(runner).start_experiment(
        config_path,
        command=("python", "examples/custom_sft.py"),
        environment_variables={"NCCL_DEBUG": "TRACE", "WANDB_MODE": "offline"},
    )

    assert seen[0][0] == ["python", "examples/custom_sft.py", "--config", str(config_path.resolve())]
    assert seen[0][1]["env"]["NCCL_DEBUG"] == "TRACE"
    assert seen[0][1]["env"]["WANDB_MODE"] == "offline"


def test_sft_wrapper_reports_failed_process_and_rejects_incomplete_config(tmp_path):
    config_path = _experiment_config(tmp_path)
    wrapper = _wrapper(lambda command, **kwargs: subprocess.CompletedProcess(command, 23))

    with pytest.raises(AReaLSFTTrainingError, match="exit code 23") as error:
        wrapper.start_experiment(config_path)

    assert error.value.log_path.is_file()
    incomplete = tmp_path / "incomplete.yaml"
    incomplete.write_text("experiment_name: only-name\n", encoding="utf-8")
    with pytest.raises(AReaLSFTConfigurationError, match="trial_name"):
        wrapper.start_experiment(incomplete)


def test_sft_wrapper_rejects_interpolated_fileroot(tmp_path):
    config_path = _experiment_config(tmp_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            str(tmp_path / "artifacts"), "${oc.env:AREAL_ROOT}"
        ),
        encoding="utf-8",
    )

    with pytest.raises(AReaLSFTConfigurationError, match="literal path"):
        _wrapper(lambda *args, **kwargs: None).start_experiment(config_path)


def test_sft_wrapper_submits_slurm_from_the_same_start_method(tmp_path):
    seen: list[tuple[list[str], dict]] = []
    config_path = _experiment_config(tmp_path)

    def runner(command, **kwargs):
        seen.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="987654;cluster\n", stderr="")

    submission = _slurm_wrapper(tmp_path, runner).start_experiment(config_path)

    command, kwargs = seen[0]
    script_path = Path(submission["script_path"])
    script = script_path.read_text(encoding="utf-8")
    assert command == ["sbatch", "--parsable", str(script_path)]
    assert kwargs["env"]["NCCL_DEBUG"] == "WARN"
    assert script_path.is_file()
    assert subprocess.run(["bash", "-n", str(script_path)], check=False).returncode == 0
    assert submission["job_id"] == "987654"
    assert submission["status"] == "submitted"
    assert submission["scheduler_type"] == "slurm"
    assert submission["slurm_log_path"].endswith("slurm-987654.out")
    assert "python examples/test_sft.py --config" in script
    assert "--driver" not in script
    assert "NCCL_DEBUG=WARN" in script
    assert "HF_DATASETS_OFFLINE=1" in script


def test_sft_wrapper_requires_slurm_resources_and_reports_submission_errors(tmp_path):
    config_path = _experiment_config(tmp_path, include_resources=False)
    wrapper = _slurm_wrapper(
        tmp_path,
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 17, stdout="", stderr="invalid account"
        ),
    )
    with pytest.raises(AReaLSFTConfigurationError, match="cluster.n_nodes"):
        wrapper.start_experiment(config_path)

    config_path = _experiment_config(tmp_path)
    with pytest.raises(AReaLSFTSubmissionError, match="invalid account") as error:
        wrapper.start_experiment(config_path)
    assert error.value.script_path.is_file()


def test_sft_service_config_is_limited_to_launch_settings(tmp_path: Path):
    config_path = tmp_path / "service.yaml"
    config_path.write_text(
        f"""
areal:
  sft:
    scheduler_type: slurm
    commands: [python, examples.custom_sft.py]
    environment_variables:
      NCCL_DEBUG: WARN
    slurm:
      image: /images/areal.sif
      working_directory: {tmp_path}
      bind_paths: [/storage, /tmp]
      container_environment:
        NCCL_DEBUG: INFO
""".lstrip(),
        encoding="utf-8",
    )

    config = AReaLSFTConfig.from_yaml(config_path)

    assert config.scheduler_type == "slurm"
    assert config.commands == ("python", "examples.custom_sft.py")
    assert config.environment_variables == {"NCCL_DEBUG": "WARN"}
    assert config.slurm is not None
    assert config.slurm.working_directory == tmp_path
    assert config.slurm.bind_paths == ("/storage", "/tmp")


def test_sft_config_allows_only_local_or_slurm_and_needs_commands(tmp_path: Path):
    with pytest.raises(AReaLSFTConfigurationError, match="scheduler_type"):
        AReaLSFTConfig(scheduler_type="ray")
    with pytest.raises(AReaLSFTConfigurationError, match="commands"):
        AReaLSFTConfig(commands=())

    config_path = tmp_path / "invalid.yaml"
    config_path.write_text("areal:\n  sft:\n    scheduler_type: ray\n", encoding="utf-8")
    with pytest.raises(AReaLSFTConfigurationError, match="scheduler_type"):
        AReaLSFTConfig.from_yaml(config_path)

    config_path.write_text("areal:\n  sft:\n    driver: examples.sft:main\n", encoding="utf-8")
    with pytest.raises(AReaLSFTConfigurationError, match="Unsupported areal.sft fields: driver"):
        AReaLSFTConfig.from_yaml(config_path)
