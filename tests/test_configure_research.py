from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest

from areal_rsi.adapters.cuhkx import prepare_dataset
from areal_rsi.coordination.config import load_config

REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def configure() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "configure_research", REPOSITORY / "scripts/configure_research.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def operator(root: Path, example: str, **updates: Any) -> Path:
    prepared = root / "prepared"
    (prepared / "train").mkdir(parents=True)
    (prepared / "train/manifest.json").write_text("{}")
    config: dict[str, Any] = {"prepared_data": str(prepared)}
    if example == "kernelbench":
        config.update(
            baseline_harness=str(REPOSITORY / "examples/kernelbench/seed"),
            edit_scope="skills",
            gpu={"kind": "ssh", "cuda_visible_devices": "4,7"},
            phases={"search": "search", "final": "final", "diagnostic": "development"},
        )
    else:
        config.update(
            runner={"kind": "docker", "device": "cuda:0", "gpu_ids": ["2"]},
            training={"timeout_seconds": 300},
            verification={"timeout_seconds": 60},
            phases={"search": "validate", "final": "final"},
        )
    config.update(updates)
    path = root / "operator.json"
    path.write_text(json.dumps(config))
    return path


def identity(example: str, phase: str) -> dict[str, Any]:
    split = phase
    if phase == "diagnostic":
        split = "development" if example == "kernelbench" else "validate"
    if example == "cuhkx" and phase == "search":
        split = "validate"
    result = {
        "adapter": example,
        "phase": phase,
        "split": split,
        "protocol_id": "a" * 64,
        "manifest_id": ("c" if phase == "final" else "b") * 64,
        "environment": {"private_audit_only": True},
    }
    if example == "kernelbench":
        result["target_model"] = "fixed-task-model"
    else:
        result.update(prepared_protocol_id="d" * 64, dataset_kind="competition")
    return result


def mock_describe(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return identity(args[3], args[4])


def arguments(configure: ModuleType, path: Path, example: str, *extra: str) -> Any:
    return configure.parser().parse_args(
        [
            "--example",
            example,
            "--operator",
            str(path),
            "--output",
            str(path.parent / "research.json"),
            *(["--target-model", "fixed-task-model"] if example == "kernelbench" else []),
            *extra,
        ]
    )


def test_kernel_config_roundtrip_pins_model_and_development_scope(
    configure: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = operator(tmp_path, "kernelbench")
    notes = tmp_path / "notes.txt"
    notes.write_text("先研究通用的内存访问策略，不要增加并行方向。")
    monkeypatch.setenv("RSI_API_KEY", "secret-canary-never-copy")
    args = arguments(
        configure, path, "kernelbench", "--instructions-file", str(notes), "--target", "0.7"
    )
    with patch.object(configure, "describe", side_effect=mock_describe) as probe:
        config = configure.build_config(args)
    assert [call.args[4] for call in probe.call_args_list] == ["search", "final", "diagnostic"]
    configure.write_config(config, args.output)
    loaded = load_config(args.output)
    assert loaded.brief.limits.allowed_paths == ("skills/*",)
    assert loaded.brief.limits.gpu_ids == ("4", "7")
    assert loaded.brief.limits.max_agents == 1
    assert loaded.brief.verification is not None and loaded.brief.verification.target == 0.7
    assert loaded.brief.verification.identity["require_final_confirmation"] is True
    assert notes.read_text() in loaded.brief.instructions
    assert loaded.metadata["evaluate_seed_as_baseline"] is True
    assert loaded.commands[0].name == "develop"
    assert loaded.commands[0].output_subdir == "public/development"
    assert loaded.verifier is not None
    for command in (*loaded.commands, loaded.verifier):
        assert Path(command.command[0]).is_absolute()
        assert Path(command.command[1]).is_absolute()
        assert command.command[command.command.index("--target-model") + 1] == "fixed-task-model"
        assert command.gpu_count == 2
        values = {"workspace": str(tmp_path / "workspace"), "output": str(tmp_path / "job")}
        if command.name == "verify":
            values["phase"] = "final"
        assert "{" not in " ".join(command.bind(values, cwd=str(tmp_path)).command)
    assert "environment" not in loaded.metadata["verification_identity"]["phases"]["search"]
    assert loaded.metadata["preflight"]["search"]["environment"]
    assert "secret-canary-never-copy" not in args.output.read_text()
    assert loaded.api_key_env == "RSI_API_KEY" and loaded.reasoning_effort == "xhigh"


def test_cuhkx_config_collects_only_trained_and_has_no_scored_baseline(
    configure: ModuleType, tmp_path: Path
) -> None:
    path = operator(tmp_path, "cuhkx")
    args = arguments(configure, path, "cuhkx", "--max-agents", "3", "--max-jobs", "2")
    with patch.object(configure, "describe", side_effect=mock_describe) as probe:
        config = configure.build_config(args)
    assert probe.call_count == 2
    assert config.metadata["evaluate_seed_as_baseline"] is False
    assert config.brief.verification.target is None
    assert config.brief.limits.max_agents == 3
    assert config.brief.limits.max_jobs == 2
    assert config.commands[0].name == "train"
    assert config.commands[0].output_subdir == "trained"
    assert config.commands[0].timeout_seconds == 360
    assert config.verifier.timeout_seconds == 120
    configure.write_config(config, args.output)
    assert load_config(args.output).metadata["verification_identity"]["adapter"] == "cuhkx"


@pytest.mark.parametrize(
    "bad", ["missing", "same_manifest", "wrong_split", "wrong_model", "invalid_hash"]
)
def test_preflight_rejects_missing_or_untrusted_identity(
    configure: ModuleType, tmp_path: Path, bad: str
) -> None:
    path = operator(tmp_path, "kernelbench")
    args = arguments(configure, path, "kernelbench")

    def probe(*values: Any) -> dict[str, Any]:
        value = mock_describe(*values)
        if values[4] == "final":
            if bad == "missing":
                value.pop("protocol_id")
            elif bad == "same_manifest":
                value["manifest_id"] = "b" * 64
            elif bad == "wrong_split":
                value["split"] = "search"
            elif bad == "wrong_model":
                value["target_model"] = "other-model"
            else:
                value["protocol_id"] = "placeholder"
        return value

    with patch.object(configure, "describe", side_effect=probe), pytest.raises(ValueError):
        configure.build_config(args)
    assert not args.output.exists()


def test_operator_changes_during_preflight_are_rejected(
    configure: ModuleType, tmp_path: Path
) -> None:
    path = operator(tmp_path, "cuhkx")
    args = arguments(configure, path, "cuhkx")

    def probe(*values: Any) -> dict[str, Any]:
        path.write_text(path.read_text() + " ")
        return mock_describe(*values)

    with (
        patch.object(configure, "describe", side_effect=probe),
        pytest.raises(ValueError, match="changed"),
    ):
        configure.build_config(args)


@pytest.mark.parametrize(
    "changes",
    [
        {"gpu": {"kind": "mock", "cuda_visible_devices": "0"}},
        {"gpu": {"kind": "ssh", "cuda_visible_devices": ""}},
        {"gpu": {"kind": "ssh", "cuda_visible_devices": "0,0"}},
        {"phases": {"search": "search", "final": "search"}},
        {"phases": {"search": "search"}},
        {"edit_scope": "skills", "baseline_harness": None},
    ],
)
def test_bad_operator_rejected_before_any_probe(
    configure: ModuleType, tmp_path: Path, changes: dict[str, Any]
) -> None:
    path = operator(tmp_path, "kernelbench", **changes)
    with patch.object(configure, "describe") as probe, pytest.raises(ValueError):
        configure.build_config(arguments(configure, path, "kernelbench"))
    probe.assert_not_called()


def test_describe_command_pins_devices_preserves_venv_and_redacts_child_error(
    configure: ModuleType, tmp_path: Path
) -> None:
    path = operator(tmp_path, "kernelbench")
    executable = Path(sys.executable).absolute()
    wrapper = REPOSITORY / "examples/kernelbench/run.py"
    answer = identity("kernelbench", "search")
    with patch.object(
        configure.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 0, json.dumps(answer)),
    ) as run:
        assert (
            configure.describe(
                executable,
                wrapper,
                path,
                "kernelbench",
                "search",
                "fixed-task-model",
                ("4", "7"),
                30,
            )
            == answer
        )
    assert run.call_args.args[0][:3] == [str(executable), str(wrapper), "--describe"]
    assert run.call_args.kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "4,7"
    error = subprocess.CalledProcessError(1, ["probe"], stderr="secret-canary")
    with (
        patch.object(configure.subprocess, "run", side_effect=error),
        pytest.raises(ValueError) as failure,
    ):
        configure.describe(
            executable, wrapper, path, "kernelbench", "final", "fixed-task-model", ("4",), 30
        )
    assert "secret-canary" not in str(failure.value)
    assert "final" in str(failure.value) and "No research configuration" in str(failure.value)


def test_writer_never_replaces_existing_file(configure: ModuleType, tmp_path: Path) -> None:
    path = operator(tmp_path, "cuhkx")
    args = arguments(configure, path, "cuhkx")
    with patch.object(configure, "describe", side_effect=mock_describe):
        config = configure.build_config(args)
    args.output.write_text("keep this file")
    with pytest.raises(FileExistsError):
        configure.write_config(config, args.output)
    assert args.output.read_text() == "keep this file"
    assert {item.name for item in tmp_path.iterdir()} == {
        "research.json",
        "operator.json",
        "prepared",
    }


@pytest.mark.parametrize(
    "options",
    [
        ["--target", "nan"],
        ["--target", "1.1"],
        ["--max-nodes", "0"],
        ["--base-url", "https://secret:password@example.com"],
        ["--base-url", "https://example.com?api_key=secret"],
        ["--allow-local"],
    ],
)
def test_invalid_user_settings_fail_before_preflight(
    configure: ModuleType, tmp_path: Path, options: list[str]
) -> None:
    path = operator(tmp_path, "kernelbench")
    with patch.object(configure, "describe") as probe, pytest.raises(ValueError):
        configure.build_config(arguments(configure, path, "kernelbench", *options))
    probe.assert_not_called()


@pytest.mark.parametrize(
    "runner",
    [
        {"kind": "mock", "device": "cpu"},
        {"kind": "docker", "device": "cuda:2", "gpu_ids": ["7"]},
        {"kind": "docker", "device": "cuda:0"},
        {"kind": "docker", "device": "gpu"},
    ],
)
def test_cuhkx_resource_mismatch_is_not_silently_configured(
    configure: ModuleType, tmp_path: Path, runner: dict[str, Any]
) -> None:
    path = operator(tmp_path, "cuhkx", runner=runner)
    with patch.object(configure, "describe") as probe, pytest.raises(ValueError):
        configure.build_config(arguments(configure, path, "cuhkx"))
    probe.assert_not_called()


def test_real_cli_preflights_synthetic_cuhkx_without_candidate_execution(tmp_path: Path) -> None:
    for subject in (1, 2, 3):
        directory = tmp_path / "raw/Depth_Color/0_action" / f"user{subject}/trial1"
        directory.mkdir(parents=True)
        (directory / "frame.png").write_bytes(f"synthetic-preflight-{subject}".encode())
    prepared = tmp_path / "prepared"
    prepare_dataset(
        tmp_path / "raw",
        prepared,
        validate_subjects=[2],
        final_subjects=[3],
        dataset_kind="synthetic",
    )
    path = tmp_path / "operator.json"
    path.write_text(
        json.dumps(
            {
                "prepared_data": str(prepared),
                "runner": {"kind": "local", "device": "cpu", "python": sys.executable},
                "phases": {"search": "validate", "final": "final"},
            }
        )
    )
    output = tmp_path / "research.json"
    command = [
        sys.executable,
        str(REPOSITORY / "scripts/configure_research.py"),
        "--example",
        "cuhkx",
        "--operator",
        str(path),
        "--output",
        str(output),
    ]
    failed = subprocess.run(command, cwd=REPOSITORY, capture_output=True, text=True, timeout=30)
    assert failed.returncode == 2 and "--allow-local" in failed.stderr
    assert not output.exists()
    result = subprocess.run(
        [*command, "--allow-local"],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "RSI_API_KEY": "never-persist-this-secret"},
    )
    assert result.returncode == 0, result.stderr
    config = load_config(output)
    assert config.brief.limits.gpu_ids == ()
    assert config.commands[0].gpu_count == 0
    assert "--allow-local" in config.commands[0].command
    assert config.metadata["preflight"]["final"]["dataset_kind"] == "synthetic"
    assert "never-persist-this-secret" not in output.read_text() + result.stdout + result.stderr
    assert not any(tmp_path.rglob("checkpoint.pt"))


def test_real_cli_missing_final_does_not_create_config(tmp_path: Path) -> None:
    for subject in (1, 2):
        directory = tmp_path / "raw/Depth_Color/0_action" / f"user{subject}/trial1"
        directory.mkdir(parents=True)
        (directory / "frame.png").write_bytes(b"synthetic")
    prepared = tmp_path / "prepared"
    prepare_dataset(tmp_path / "raw", prepared, validate_subjects=[2], dataset_kind="synthetic")
    path = tmp_path / "operator.json"
    path.write_text(
        json.dumps({"prepared_data": str(prepared), "runner": {"kind": "local", "device": "cpu"}})
    )
    output = tmp_path / "research.json"
    result = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY / "scripts/configure_research.py"),
            "--example",
            "cuhkx",
            "--operator",
            str(path),
            "--output",
            str(output),
            "--allow-local",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPOSITORY,
    )
    assert result.returncode == 2 and "final describe preflight failed" in result.stderr
    assert not output.exists()
