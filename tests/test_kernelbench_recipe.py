"""The historical recipe remains strict; current-code smoke uses a separate lock."""

import hashlib
import json
import os
import runpy
from pathlib import Path
from typing import Any

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "examples/kernelbench_artifacts/v0.2.1/run.py"


def test_recipe_scope_restores_model_environment_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = runpy.run_path(str(LAUNCHER))
    monkeypatch.setenv("RSI_MODEL", "original")
    monkeypatch.delenv("RSI_BACKEND", raising=False)
    with pytest.raises(RuntimeError), module["environment_scope"](
        {"RSI_MODEL": "pinned", "RSI_BACKEND": "codex"}
    ):
        assert os.environ["RSI_MODEL"] == "pinned"
        assert os.environ["RSI_BACKEND"] == "codex"
        raise RuntimeError("preparation failed")
    assert os.environ["RSI_MODEL"] == "original"
    assert "RSI_BACKEND" not in os.environ


def test_separate_source_lock_still_rejects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = runpy.run_path(str(LAUNCHER))
    check: Any = module["check_source"]
    monkeypatch.setitem(check.__globals__, "REPOSITORY", tmp_path)
    source = tmp_path / "source.py"
    source.write_text("original")
    lock = tmp_path / "smoke-lock.json"
    lock.write_text(json.dumps({
        "files": {source.name: hashlib.sha256(source.read_bytes()).hexdigest()}, "packages": {},
    }))
    check(lock)
    source.write_text("changed")
    with pytest.raises(ValueError, match=r"source\.py"):
        check(lock)
    source.unlink()
    with pytest.raises(ValueError, match=r"source\.py"):
        check(lock)
