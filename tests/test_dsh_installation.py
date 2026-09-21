from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from unittest.mock import Mock

import pytest

import areal_rsi.modules.dsh as dsh


def test_complete_npm_cli_is_reused_without_installing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "dsh-cli/node_modules/@deepseek-ai/dsh/package.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"version": dsh.DSH_VERSION}))
    binary = tmp_path / "dsh-cli/node_modules/.bin/dsh"
    binary.parent.mkdir()
    binary.touch()
    installer = Mock(side_effect=AssertionError("already installed"))
    monkeypatch.setattr(dsh, "_require_compatible_node", lambda: Path("/fixture/node"))
    monkeypatch.setattr("areal_rsi.modules.dsh.subprocess.run", installer)
    assert dsh.ensure_cli(tmp_path) == binary
    installer.assert_not_called()


def test_missing_node_and_pnpm_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("areal_rsi.modules.dsh.shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match=r"Node\.js.*pnpm"):
        dsh.ensure_cli(tmp_path)
    assert not (tmp_path / "dsh-cli").exists()


def test_incompatible_node_reports_the_detected_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dsh.shutil, "which", lambda name: f"/fixture/{name}")
    probe = Mock(returncode=1)
    version = Mock(stdout="v18.20.8\n")
    runner = Mock(side_effect=(probe, version))
    monkeypatch.setattr(dsh.subprocess, "run", runner)
    with pytest.raises(RuntimeError, match=r"22\.19.*v18\.20\.8"):
        dsh.ensure_cli(tmp_path)


def test_concurrent_callers_share_one_completed_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    binary = tmp_path / "dsh-cli/node_modules/.bin/dsh"
    manifest = tmp_path / "dsh-cli/node_modules/@deepseek-ai/dsh/package.json"

    def install(*args: object, **kwargs: object) -> None:
        entered.set()
        assert release.wait(timeout=3)
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"version": dsh.DSH_VERSION}))
        binary.parent.mkdir(parents=True)
        binary.touch()

    installer = Mock(side_effect=install)
    monkeypatch.setattr(dsh, "_require_compatible_node", lambda: Path("/fixture/node"))
    monkeypatch.setattr("areal_rsi.modules.dsh.subprocess.run", installer)
    monkeypatch.setattr("areal_rsi.modules.dsh.shutil.which", lambda _: "/fixture/bin")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(dsh.ensure_cli, tmp_path)
        try:
            assert entered.wait(timeout=2)
            second = pool.submit(dsh.ensure_cli, tmp_path)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.1)
        finally:
            release.set()
        assert first.result(timeout=3) == second.result(timeout=3) == binary
    installer.assert_called_once()
