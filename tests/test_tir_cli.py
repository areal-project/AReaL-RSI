"""CLI orchestration preserves frozen runs and separates research from test."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from examples.tir import rsi
from examples.tir.pipeline import PROJECT


def arguments(root: Path, **changes: object) -> argparse.Namespace:
    values = dict(
        action="research",
        experiment=root,
        resume=False,
        source=None,
        env_file=None,
        config=root / "unused.json",
        candidate=None,
    )
    values.update(changes)
    return argparse.Namespace(**values)


def test_research_prepares_baseline_and_releases_stage_lock_for_leader(tmp_path: Path) -> None:
    root = tmp_path / "experiment"

    def prepare(config: object, target: Path) -> None:
        (target / "protocol.json").write_text("frozen")

    async def leader(target: Path, env: Path | None, *, resume: bool) -> None:
        assert not resume
        with (target / ".experiment.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    with (
        patch.object(rsi, "executable"),
        patch.object(rsi, "read_record"),
        patch.object(rsi, "prepare", side_effect=prepare) as freeze,
        patch.object(rsi, "Experiment") as experiment,
        patch.object(rsi, "run", side_effect=leader) as run,
    ):
        experiment.return_value.evaluate = AsyncMock()
        experiment.return_value.test = AsyncMock()
        asyncio.run(rsi.launch(arguments(root)))
        freeze.assert_called_once()
        experiment.return_value.evaluate.assert_awaited_once_with("base")
        experiment.return_value.test.assert_not_called()
        run.assert_awaited_once()


def test_baseline_failure_can_resume_without_refreezing(tmp_path: Path) -> None:
    (tmp_path / "protocol.json").write_text("original")
    with (
        patch.object(rsi, "executable"),
        patch.object(rsi, "prepare") as freeze,
        patch.object(rsi, "Experiment") as experiment,
        patch.object(rsi, "run", new_callable=AsyncMock) as run,
    ):
        experiment.return_value.evaluate = AsyncMock(side_effect=RuntimeError("baseline failed"))
        with pytest.raises(RuntimeError, match="baseline failed"):
            asyncio.run(rsi.launch(arguments(tmp_path)))
        run.assert_not_called()
        experiment.return_value.evaluate = AsyncMock()
        asyncio.run(rsi.launch(arguments(tmp_path, resume=True)))
        freeze.assert_not_called()
        run.assert_awaited_once_with(tmp_path, None, resume=False)
        assert (tmp_path / "protocol.json").read_text() == "original"


def test_existing_session_requires_explicit_resume_and_status_is_readable(tmp_path: Path) -> None:
    (tmp_path / "protocol.json").write_text("original")
    (tmp_path / "rsi").mkdir()
    (tmp_path / "rsi/driver.json").write_text("{}")
    with patch.object(rsi, "executable"), patch.object(rsi, "Experiment") as experiment:
        with pytest.raises(ValueError, match="use --resume"):
            asyncio.run(rsi.launch(arguments(tmp_path)))
        experiment.assert_not_called()
    (tmp_path / "rsi/driver.json").unlink()
    with (tmp_path / ".driver.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(rsi.launch(arguments(tmp_path, action="status")))


def test_test_requires_candidate_and_never_starts_research(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires --candidate"):
        asyncio.run(rsi.launch(arguments(tmp_path, action="test")))
    (tmp_path / "protocol.json").write_text("original")
    with patch.object(rsi, "Experiment") as experiment, patch.object(rsi, "run") as run:
        experiment.return_value.test = AsyncMock()
        asyncio.run(rsi.launch(arguments(tmp_path, action="test", candidate="r2")))
        experiment.return_value.test.assert_awaited_once_with("r2")
        run.assert_not_called()


def test_benchmark_submission_failure_keeps_receipt_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from areal_rsi.infra.artifacts import read_record
    from examples.tir.experiment import ExperimentConfig

    submit = tmp_path / "srun"
    submit.write_text("#!/bin/sh\necho 'submission rejected'\nexit 7\n")
    submit.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    source = tmp_path / "source"
    source.mkdir()
    for name in ("train.parquet", "test.parquet"):
        (source / name).write_bytes(b"unused: submission fails before data reading")
    config = replace(
        read_record(PROJECT / "examples/tir/experiment.json", ExperimentConfig),
        benchmark=str(tmp_path / "benchmark/benchmark.json"),
    )
    root = tmp_path / "experiment"
    with pytest.raises(RuntimeError, match="failed; Exit code 7"):
        asyncio.run(rsi.prepare_benchmark(config, source, root))
    assert not (tmp_path / "benchmark").exists()
    assert not (root / "protocol.json").exists()
    logs = list(root.rglob("stdout.log"))
    assert len(logs) == 1
    assert "submission rejected" in logs[0].read_text()
