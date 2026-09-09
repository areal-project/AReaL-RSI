"""Mixed-mode planning and workers in one workspace, without external services."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_experiment_workers import fixture_app

from areal_rsi.config import ModelConfigurationError
from areal_rsi.experiment_config import default_config, plan_environment
from areal_rsi.ui.cli import run_experiment
from areal_rsi.ui.web import RunReader
from areal_rsi.ui.workspace import ExperimentWorkspace


def workspace(root: Path) -> ExperimentWorkspace:
    return ExperimentWorkspace(
        root,
        app_factory=fixture_app,
        worker_command=(
            sys.executable,
            str(Path(__file__).with_name("test_experiment_workers.py")),
        ),
    )


async def wait_state(store: ExperimentWorkspace, run_id: str, state: str) -> None:
    async with asyncio.timeout(10):
        while (await store.status(run_id))["state"] != state:
            await asyncio.sleep(0.03)


def test_switch_modes_keeps_plans_and_workers_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RSI_MODE", "caller-mode")
    runtime = tmp_path / "runtime.json"
    runtime.write_text('{"default": {"kind": "fake"}}')
    monkeypatch.setenv("RSI_RUNTIME_CONFIG", str(runtime))
    store = workspace(tmp_path / "workspace")
    source = tmp_path / "local.yaml"
    source.write_text(default_config("local").read_text())
    before = dict(os.environ)

    async def check() -> None:
        first, second = await asyncio.gather(
            store.plan("local experiment", 1, config_path=str(source)),
            store.plan("remote experiment", 1, preset="lcb"),
        )
        assert dict(os.environ) == before
        assert first["plan"]["config"]["mode"] == "local"
        assert second["plan"]["config"]["mode"] == "remote"
        local = store.locate(first["plan_id"], plan=True)
        remote = store.locate(second["plan_id"], plan=True)
        assert local.root != remote.root
        assert await store.status() == {"runs": []}
        with pytest.raises(ValueError, match="确认"):
            await store.start(first["plan_id"], confirmed=False)
        assert await store.status() == {"runs": []}
        # Mutating source files and inherited mode cannot redirect already-reviewed plans.
        source.write_text("mode: remote\nbenchmark: changed\n")
        runtime.write_text('{"default": {"kind": "codex-cli"}}')
        run1 = await store.start(first["plan_id"], confirmed=True)
        run2 = await store.start(second["plan_id"], confirmed=True)
        try:
            await wait_state(store, run1["run_id"], "running")
            await wait_state(store, run2["run_id"], "running")
            for controller, run, mode in ((local, run1, "local"), (remote, run2, "remote")):
                async with asyncio.timeout(5):
                    entered = controller.root / f"{run['run_id']}-entered.json"
                    while not entered.exists():
                        await asyncio.sleep(0.02)
                record = json.loads(entered.read_text())
                assert record["mode"] == mode
                assert (
                    json.loads(Path(record["runtime_config"]).read_text())["default"]["kind"]
                    == "fake"
                )
            listed = await store.status()
            assert {r["mode"] for r in listed["runs"]} == {"local", "remote"}
            reader = RunReader(store.root)
            assert {r["mode"] for r in reader.list()} == {"local", "remote"}
            assert reader.detail(run1["run_id"])["mode"] == "local"
            await store.stop(run1["run_id"])
            await wait_state(store, run1["run_id"], "cancelled")
            assert (await store.status(run2["run_id"]))["state"] == "running"
            (remote.root / "fixture-release.json").touch()
            await wait_state(store, run2["run_id"], "succeeded")
            # A new TUI/controller can find and resume the same frozen local plan.
            async with asyncio.timeout(5):
                while (await store.status(run1["run_id"]))["worker"]["active"]:
                    await asyncio.sleep(0.03)
            (local.root / "fixture-release.json").touch()
            restarted = workspace(store.root)
            await restarted.resume(run1["run_id"], confirmed=True)
            await wait_state(restarted, run1["run_id"], "succeeded")
            for controller in restarted.controllers.values():
                for child in controller._children.values():
                    await asyncio.to_thread(child.wait, timeout=5)
        finally:
            for controller in store.controllers.values():
                for child in controller._children.values():
                    if child.poll() is None:
                        child.terminate()
                    await asyncio.to_thread(child.wait, timeout=5)
        assert dict(os.environ) == before

    asyncio.run(check())


def test_changed_snapshot_is_rejected_before_start(tmp_path: Path) -> None:
    store = workspace(tmp_path)

    async def check() -> None:
        record = await store.plan("test", 1, preset="arithmetic")
        controller = store.locate(record["plan_id"], plan=True)
        config = record["plan"]["config"]
        assert plan_environment(controller.root, config)["RSI_MODE"] == "local"
        (controller.root / "experiment.yaml").write_text("mode: remote\n")
        with pytest.raises(ModelConfigurationError, match="快照"):
            await store.start(record["plan_id"], confirmed=True)
        assert await store.status() == {"runs": []}

    asyncio.run(check())


def test_missing_mode_and_failed_planning_leave_environment_unchanged(tmp_path: Path) -> None:
    config = tmp_path / "missing-mode.yaml"
    config.write_text("benchmark: arithmetic-v1\n")
    store = workspace(tmp_path / "state")
    before = dict(os.environ)

    async def check() -> None:
        with pytest.raises(ModelConfigurationError, match="mode"):
            await store.plan("test", 1, config_path=str(config))
        failing = ExperimentWorkspace(
            tmp_path / "failed", app_factory=AsyncMock(side_effect=ValueError)
        )
        with pytest.raises(ValueError):
            await failing.plan("test", 1, preset="lcb")
        assert dict(os.environ) == before
        assert not list((tmp_path / "failed/experiments").glob("*/launch.json"))

    asyncio.run(check())


def test_cli_runs_a_saved_plan_using_its_own_mode_and_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RSI_MODE", "remote")
    monkeypatch.setenv("RSI_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr("areal_rsi.ui.cli.create_app", fixture_app)
    store = workspace(tmp_path)

    async def check() -> None:
        record = await store.plan("CLI fixture", 1, preset="arithmetic")
        controller = store.locate(record["plan_id"], plan=True)
        (controller.root / "fixture-release.json").touch()
        path = controller.root / "experiment-plans" / f"{record['plan_id']}.json"
        assert await run_experiment(str(path), None) == 0
        result = await store.status()
        run = result["runs"][0]
        entered = json.loads((controller.root / f"{run['run_id']}-entered.json").read_text())
        assert entered["mode"] == "local"
        assert os.environ["RSI_MODE"] == "remote"
        assert await run_experiment(None, run["run_id"]) == 0

    asyncio.run(check())
