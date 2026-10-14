from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from areal_rsi.infra.storage.paths import data_root


def test_root_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RSI_DATA_ROOT", raising=False)
    assert data_root() == tmp_path / ".rsi/research-lab"
    monkeypatch.setenv("RSI_DATA_ROOT", "custom")
    assert data_root() == tmp_path / "custom"
    assert data_root(Path("explicit")) == tmp_path / "explicit"


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
