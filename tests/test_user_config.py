from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from areal_rsi.config import ModelConfigurationError, init_config, load_user_config


def test_init_private_and_preserves_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    path = init_config()
    assert path == tmp_path / ".areal_rsi/config.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["RSI_AGENT_MODELS"] == {}
    path.write_text('{"RSI_TEACHER_MODEL": "existing"}')
    init_config()
    assert json.loads(path.read_text())["RSI_TEACHER_MODEL"] == "existing"


def test_load_overrides_and_nested_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RSI_AGENT_MODELS", raising=False)
    monkeypatch.delenv("RSI_RESEARCH_AGENT_COUNT", raising=False)
    monkeypatch.setenv("RSI_TEACHER_MODEL", "environment-model")
    monkeypatch.setenv("RSI_TEACHER_API_KEY", "")
    path = init_config()
    overrides = {"research-agent": {"model": "research"}}
    path.write_text(
        json.dumps(
            {
                "RSI_TEACHER_MODEL": "file-model",
                "RSI_TEACHER_API_KEY": "file-secret",
                "RSI_RESEARCH_AGENT_COUNT": 2,
                "RSI_AGENT_MODELS": overrides,
            }
        )
    )
    load_user_config()
    assert os.environ["RSI_TEACHER_MODEL"] == "environment-model"
    assert os.environ["RSI_TEACHER_API_KEY"] == ""
    assert os.environ["RSI_RESEARCH_AGENT_COUNT"] == "2"
    assert json.loads(os.environ["RSI_AGENT_MODELS"]) == overrides


@pytest.mark.parametrize(
    "content", ['{"secret": "sentinel"', "[]", '{"RSI_BENCHMARK": "new", "SECRET": ["sentinel"]}']
)
def test_invalid_config_is_atomic_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RSI_BENCHMARK", raising=False)
    init_config().write_text(content)
    with pytest.raises(ModelConfigurationError) as error:
        load_user_config()
    assert "sentinel" not in str(error.value)
    assert "RSI_BENCHMARK" not in os.environ


def test_cli_uses_config_data_root_from_another_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = home / ".areal_rsi/config.json"
    config.parent.mkdir(parents=True)
    data = tmp_path / "state"
    # An invalid record proves status visited the configured directory.
    records = data / "run-states"
    records.mkdir(parents=True)
    (records / "probe.json").write_text('{"unexpected": true}')
    config.write_text(json.dumps({"RSI_DATA_ROOT": str(data)}))
    env = {key: value for key, value in os.environ.items() if not key.startswith("RSI_")}
    env["HOME"] = str(home)
    result = subprocess.run(
        [sys.executable, "-m", "areal_rsi", "status"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "unexpected" in result.stderr
    (records / "probe.json").unlink()
    result = subprocess.run(
        [sys.executable, "-m", "areal_rsi", "status"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == []
