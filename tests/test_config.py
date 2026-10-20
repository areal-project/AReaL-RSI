"""Configuration precedence, path ownership, diagnostics and saved-session isolation."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from areal_rsi.api.research import SessionConfig
from areal_rsi.infra.config import load_config
from areal_rsi.infra.session import SessionSystem

PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("RSI_")}
    with patch.dict(os.environ, environment, clear=True):
        yield


def test_defaults_work_without_files_and_match_the_public_template(tmp_path: Path) -> None:
    config = load_config()
    assert config.session.model == "gpt-6.1-sol"
    assert config.session.effort == "xhigh"
    assert config.session.base_url == "https://htnn-gateway.alipay.com/arenaonlinegateway/api"
    assert config.session.workspace == str(tmp_path.resolve())
    assert config.data_root == str(Path("~/.local/share/areal-rsi").expanduser().resolve())
    assert load_config(config_file=PROJECT / "rsi.example.toml") == config
    assert not list(tmp_path.iterdir())


def test_file_environment_and_cli_precedence_preserve_path_origins(tmp_path: Path) -> None:
    directory = tmp_path / "settings"
    directory.mkdir()
    (directory / "project").mkdir()
    path = directory / "custom.toml"
    path.write_text(
        'data_root = "data"\n[session]\nworkspace = "project"\n'
        'model = "file-model"\neffort = "medium"\nauto_learn = false\n'
    )
    config = load_config(config_file=path)
    assert config.data_root == str(directory / "data")
    assert config.session.workspace == str(directory / "project")
    assert config.session.model == "file-model"
    env_file = directory / "keys.env"
    env_file.write_text(
        "RSI_API_KEY=private-config-fixture\nRSI_MODEL=dotenv-model\n"
        "RSI_REASONING_EFFORT=high\nRSI_DATA_ROOT=env-data\nRSI_AUTO_LEARN=TrUe\n"
    )
    os.environ["RSI_MODEL"] = "exported-model"
    os.environ["RSI_KEEP_AWAKE"] = "0"
    config = load_config(config_file=path, env_file=env_file)
    assert config.session.model == "exported-model"
    assert config.session.effort == "high"
    assert config.session.auto_learn
    assert not config.session.keep_awake
    assert config.data_root == str(tmp_path / "env-data")
    config = load_config(Path("."), config_file=path, env_file=env_file, root=Path("cli-data"))
    assert config.session.workspace == str(tmp_path)
    assert config.data_root == str(tmp_path / "cli-data")


@pytest.mark.parametrize(
    "content",
    [
        'data_rooot = "misplaced"',
        '[session]\neffrot = "high"',
        '[session]\nauto_learn = "false"',
        '[session]\napi_key = "must-stay-in-env"',
    ],
)
def test_misspelled_options_wrong_types_and_embedded_keys_are_rejected(
    tmp_path: Path, content: str
) -> None:
    (tmp_path / "rsi.toml").write_text(content)
    with pytest.raises(ValueError, match="Invalid AppConfig"):
        load_config()


def test_bad_boolean_and_explicit_missing_files_fail_early(tmp_path: Path) -> None:
    os.environ["RSI_AUTO_LEARN"] = "enabled"
    with pytest.raises(ValueError, match="RSI_AUTO_LEARN must be"):
        load_config()
    del os.environ["RSI_AUTO_LEARN"]
    with pytest.raises(FileNotFoundError):
        load_config(config_file=tmp_path / "missing.toml")
    with pytest.raises(ValueError, match="Environment file not found"):
        load_config(env_file=tmp_path / "missing.env")


def test_config_command_is_offline_redacts_keys_and_respects_saved_sessions(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("RSI_API_KEY=private-config-fixture\n")
    (tmp_path / "rsi.toml").write_text(
        'data_root = "data"\n[session]\nmodel = "new-default"\n'
        'codex_bin = "codex-must-not-be-started"\n'
    )
    system = SessionSystem(tmp_path / "data")
    run_id = asyncio.run(
        system.create(SessionConfig(str(tmp_path), model="saved-model", effort="low"))
    )

    def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "areal_rsi", "config", *arguments],
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        assert "private-config-fixture" not in result.stdout + result.stderr
        return result

    assert json.loads(invoke().stdout)["session"]["model"] == "new-default"
    restored = json.loads(invoke("--resume", run_id).stdout)
    assert restored["session"]["model"] == "saved-model"
    assert restored["session"]["effort"] == "low"
    assert not (tmp_path / "data/codex").exists()
    assert len(system.store.list_sessions()) == 1


def test_project_credentials_are_isolated_from_user_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from areal_rsi.infra.config import runtime_environment

    user = tmp_path / "config/areal-rsi"
    user.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user.parent))
    (user / ".env").write_text("RSI_API_KEY=user-key\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("RSI_API_KEY=project-key\nRSI_MODEL=project-model\n")
    first, second = load_config(project).session, load_config(tmp_path).session
    assert runtime_environment(first)["RSI_API_KEY"] == "project-key"
    assert runtime_environment(second)["RSI_API_KEY"] == "user-key"
    assert first.model != second.model
    assert "RSI_API_KEY" not in os.environ
    (project / ".env").write_text("RSI_API_KEY=rotated\n")
    assert runtime_environment(first)["RSI_API_KEY"] == "rotated"
    assert runtime_environment(SessionConfig(str(project)))["RSI_API_KEY"] == "rotated"
