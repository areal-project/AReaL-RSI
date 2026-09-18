from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from areal_rsi.base.paths import data_root
from areal_rsi.ui import cli


@pytest.mark.parametrize(
    "command", ["tui", "run", "resume", "status", "local-app", "_mcp", "_experiment-worker"]
)
def test_removed_workflow_entry_is_rejected(command: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "areal_rsi", command],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("command", [[], ["web"], ["workbench"]])
def test_new_entry_aliases_share_workbench_and_default_root(
    command: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RSI_DATA_ROOT", raising=False)
    called = []

    def serve(**kwargs: object) -> int:
        called.append(kwargs)
        assert kwargs["root"] is None
        assert data_root() == tmp_path / ".rsi/research-lab"
        return 0

    monkeypatch.setattr(cli, "serve_workbench", serve)
    monkeypatch.setattr(sys, "argv", ["rsi", *command])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    assert called == [{"port": 8197, "config_file": None, "simulate_gpu": False, "root": None}]


def test_root_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RSI_DATA_ROOT", raising=False)
    assert data_root() == tmp_path / ".rsi/research-lab"
    monkeypatch.setenv("RSI_DATA_ROOT", "custom")
    assert data_root() == tmp_path / "custom"
    assert data_root(Path("explicit")) == tmp_path / "explicit"


@pytest.mark.parametrize("command", ["web", "workbench"])
def test_workbench_explicit_root_reaches_application(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def serve(**kwargs: object) -> int:
        assert kwargs["root"] == tmp_path
        return 0

    monkeypatch.setattr(cli, "serve_workbench", serve)
    monkeypatch.setattr(sys, "argv", ["rsi", command, "--data-root", str(tmp_path)])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0


def test_preflight_without_credentials_fails_before_creating_records(tmp_path: Path) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("RSI_")}
    root = tmp_path / "research"
    result = subprocess.run(
        [sys.executable, "-m", "areal_rsi", "preflight", "--data-root", str(root)],
        env=env,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "RSI_API_KEY" in result.stderr and "Traceback" not in result.stderr
    assert not root.exists()


def test_old_preflight_script_forwards_native_options() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/preflight_responses.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "--backend {standard,codex}" in result.stdout
    assert "--config" in result.stdout
