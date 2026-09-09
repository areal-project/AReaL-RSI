from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from areal_rsi.app import LocalApp
from areal_rsi.config import ModelConfigurationError
from areal_rsi.experiment_config import read_config, select_config
from areal_rsi.modules.local.config import LocalConfig
from areal_rsi.modules.remote.envarena.config import ArenaConfig
from areal_rsi.modules.remote.registry import RemoteSettings
from areal_rsi.modules.runtime_package import RuntimeSettings
from areal_rsi.ui.cli import data_root
from areal_rsi.ui.mcp import _summary


@pytest.fixture(autouse=True)
def isolated_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("RSI_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("mode,name", [("local", "arithmetic"), ("remote", "livecodebench")])
def test_symmetric_demo_selection_with_dsh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    name: str,
) -> None:
    stale = tmp_path / "old-runtime.json"
    stale.write_text(json.dumps({"default": {"kind": "codex-cli"}}))
    monkeypatch.setenv("RSI_RUNTIME_CONFIG", str(stale))
    monkeypatch.setenv("RSI_DATA_ROOT", "")
    path = select_config("lcb" if mode == "remote" else "arithmetic")
    monkeypatch.setenv("RSI_MODE", mode)
    monkeypatch.setenv(f"RSI_{mode.upper()}_CONFIG", str(path))
    assert os.environ[f"RSI_{mode.upper()}_CONFIG"] == str(path)
    assert data_root() == tmp_path / ".rsi"
    root = Path(__file__).resolve().parents[1]
    assert path.read_bytes() == (root / "examples" / mode / f"{name}.yaml").read_bytes()
    selection = RuntimeSettings.from_environment()
    assert selection.select("research-agent")["kind"] == "dsh"
    assert selection.select("rsi-agent")["kind"] == "dsh"
    assert selection.select("task-agent")["kind"] == "codex-cli"


def test_remote_demo_keeps_server_harness_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSI_MODE", "remote")
    monkeypatch.setenv("RSI_REMOTE_CONFIG", str(select_config("lcb")))
    path = RemoteSettings.from_environment().config_path
    config = asyncio.run(ArenaConfig.load(path, AsyncMock()))
    assert config.harness == {"key": "rsi-lcb-claude-code", "version": "1.0.5"}
    assert config.train_cases == ("rsi-lcb-claude-code-lcb-2727",)
    assert config.validate_cases == ("rsi-lcb-claude-code-lcb-2777",)
    assert RuntimeSettings.from_environment().select("research-agent")["kind"] == "dsh"


def test_local_yaml_is_used_for_model_and_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = LocalConfig.from_environment().harness
    harness = tmp_path / "task-harness"
    shutil.copytree(builtin, harness)
    (harness / "prompt.md").write_text("custom initial prompt")
    config = tmp_path / "local.yaml"
    config.write_text("benchmark: arithmetic-v1\nharness: task-harness\nmodel: yaml-target\n")
    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"default": {"kind": "fake"}}')
    monkeypatch.setenv("RSI_RUNTIME_CONFIG", str(runtime))
    monkeypatch.setenv("RSI_TEACHER_MODEL", "teacher")
    monkeypatch.setenv("RSI_TEACHER_BASE_URL", "http://localhost:1/v1")
    monkeypatch.setenv("RSI_TARGET_MODEL", "old-target")
    monkeypatch.setenv("RSI_TARGET_BASE_URL", "http://localhost:2/v1")
    monkeypatch.setenv("RSI_BENCHMARK", "gsm8k")
    monkeypatch.setenv("RSI_LOCAL_CONFIG", str(config))

    async def check() -> None:
        app = await LocalApp.create(tmp_path / "state", lambda _: None)
        try:
            assert app.target_model.name == "yaml-target"
            assert (
                _summary({"plan": {"config": app.model_plan_config()}})["plan"]["config"][
                    "research_harness"
                ]
                == "fake"
            )
            assert app.service.context.benchmark.name == "arithmetic-v1"
            assert app.baseline.harness.content == await app.content.snapshot(harness)
            assert not app.service.tasks
        finally:
            await app.close()

    asyncio.run(check())


def test_legacy_environment_and_runtime_remain_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RSI_BENCHMARK", "gsm8k")
    monkeypatch.setenv("RSI_GSM8K_DATA", "legacy-dataset")
    monkeypatch.setenv("RSI_TRAIN_LIMIT", "4")
    monkeypatch.setenv("RSI_VALIDATE_LIMIT", "3")
    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"default": {"kind": "fake"}}')
    monkeypatch.setenv("RSI_RUNTIME_CONFIG", str(runtime))
    config = LocalConfig.from_environment()
    assert config.benchmark == "gsm8k"
    assert config.dataset == tmp_path / "legacy-dataset"
    assert (config.train_limit, config.validate_limit) == (4, 3)
    assert RuntimeSettings.from_environment().select("research-agent")["kind"] == "fake"


def test_custom_config_without_harness_override_keeps_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "custom.yaml"
    config.write_text("benchmark: arithmetic-v1\nharness: default\nmodel: target\n")
    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"default": {"kind": "codex-cli"}}')
    monkeypatch.setenv("RSI_RUNTIME_CONFIG", str(runtime))
    monkeypatch.setenv("RSI_LOCAL_CONFIG", str(config))
    assert RuntimeSettings.from_environment().select("research-agent")["kind"] == "codex-cli"


@pytest.mark.parametrize("extra", ["api_key: SECRET", "research_harness: unknown", "model: []"])
def test_invalid_local_configuration_fails_without_echoing_secrets(
    tmp_path: Path,
    extra: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "invalid.yaml"
    config.write_text("benchmark: arithmetic-v1\nharness: default\nmodel: target\n" + extra)
    monkeypatch.setenv("RSI_LOCAL_CONFIG", str(config))
    with pytest.raises(ModelConfigurationError) as error:
        LocalConfig.from_environment()
    assert "SECRET" not in str(error.value)


def test_local_dataset_paths_are_relative_to_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "local.yaml"
    config.write_text(
        "benchmark: gsm8k\nharness: default\nmodel: target\ndataset: data\n"
        "train_limit: 4\nvalidate_limit: 3\n"
    )
    monkeypatch.setenv("RSI_LOCAL_CONFIG", str(config))
    local = LocalConfig.from_environment()
    assert local.dataset == tmp_path / "data"
    assert local.train_limit == 4
    assert read_config(config)["benchmark"] == "gsm8k"
