"""Route plans and runs in one TUI workspace, with an independent configuration per plan."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from areal_rsi.base.storage import new_id, safe_name, write_json
from areal_rsi.config import ModelConfigurationError
from areal_rsi.experiment_config import (
    environment_scope,
    experiment_mode,
    experiment_roots,
    read_config,
    select_config,
)
from areal_rsi.modules.runtime_package import RuntimeSettings
from areal_rsi.ui.experiments import AppFactory, ExperimentController, _configured_app


class ExperimentWorkspace:
    def __init__(
        self,
        root: Path,
        *,
        app_factory: AppFactory = _configured_app,
        worker_command: Sequence[str] | None = None,
    ):
        self.root = root.resolve()
        self.app_factory, self.worker_command = app_factory, worker_command
        self.controllers: dict[Path, ExperimentController] = {}
        # One workspace owns the MCP process. No app survives this planning section;
        # start/resume also take the lock so even legacy workers inherit a clean environment.
        self.planning = asyncio.Lock()

    def controller(self, root: Path) -> ExperimentController:
        if root not in self.controllers:
            self.controllers[root] = ExperimentController(
                root, app_factory=self.app_factory, worker_command=self.worker_command
            )
        return self.controllers[root]

    def locate(self, identifier: str, *, plan: bool = False) -> ExperimentController:
        identifier = safe_name(identifier)
        folder = "experiment-plans" if plan else "run-states"
        matches = [
            root
            for root in experiment_roots(self.root)
            if (root / folder / f"{identifier}.json").is_file()
        ]
        if len(matches) != 1:
            raise KeyError(identifier)
        return self.controller(matches[0])

    def _snapshot(self, root: Path, source: Path, value: dict[str, Any]) -> dict[str, str]:
        mode = experiment_mode(value)
        backend = value.get("backend", "envarena")
        if not isinstance(backend, str):
            raise ModelConfigurationError("backend 必须是后端名称。")
        if mode == "local":
            for key in ("harness", "dataset"):
                item = value.get(key)
                if isinstance(item, str) and not (key == "harness" and item == "default"):
                    value[key] = str((source.parent / Path(item).expanduser()).resolve())
        root.mkdir(parents=True)
        config_path = root / "experiment.yaml"
        config_path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
        config_path.chmod(0o600)
        runtime = os.environ.get("RSI_RUNTIME_CONFIG")
        runtime_value = json.loads(Path(runtime).expanduser().read_text()) if runtime else {}
        RuntimeSettings(runtime_value)  # Reject literal Runtime credentials before persisting.
        write_json(root / "runtime.json", runtime_value, immutable=True)
        values = {
            "RSI_MODE": mode,
            "RSI_LOCAL_CONFIG": str(config_path) if mode == "local" else "",
            "RSI_REMOTE_CONFIG": str(config_path) if mode == "remote" else "",
            "RSI_REMOTE_BACKEND": backend,
            "RSI_ARENA_CONFIG": "",
            "RSI_RUNTIME_CONFIG": str(root / "runtime.json"),
            "RSI_DATA_ROOT": str(root),
            "RSI_RESEARCH_AGENT_COUNT": os.environ.get("RSI_RESEARCH_AGENT_COUNT", "1"),
            "RSI_RESEARCH_MAX_TOKENS": os.environ.get("RSI_RESEARCH_MAX_TOKENS", "2048"),
            "RSI_TURN_TIMEOUT": os.environ.get("RSI_TURN_TIMEOUT", "1200"),
        }
        write_json(root / "launch.json", values, immutable=True)
        # Plans have separate data but share the installed CLI and its installation locks.
        for name in ("dsh-cli", "runtime-dsh"):
            (self.root / name).mkdir(exist_ok=True)
            (root / name).symlink_to(self.root / name, target_is_directory=True)
        (self.root / ".dsh-cli.lock").touch(exist_ok=True)
        (root / ".dsh-cli.lock").symlink_to(self.root / ".dsh-cli.lock")
        return values

    async def plan(
        self,
        goal: str,
        max_iterations: int,
        *,
        preset: str | None = None,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        source = select_config(preset, config_path)
        value = read_config(source)
        experiment_mode(value)
        root = self.root / "experiments" / new_id("experiment")
        async with self.planning:
            try:
                values = self._snapshot(root, source, value)
                with environment_scope(values):
                    return await self.controller(root).plan(goal, max_iterations)
            except BaseException:
                self.controllers.pop(root, None)
                if root.exists():
                    shutil.rmtree(root)
                raise

    async def start(self, plan_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        async with self.planning:
            return await self.locate(plan_id, plan=True).start(plan_id, confirmed=confirmed)

    async def resume(self, run_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        async with self.planning:
            return await self.locate(run_id).resume(run_id, confirmed=confirmed)

    async def stop(self, run_id: str) -> dict[str, Any]:
        return await self.locate(run_id).stop(run_id)

    async def status(self, run_id: str | None = None) -> dict[str, Any]:
        if run_id:
            return await self.locate(run_id).status(run_id)
        runs = []
        for root in experiment_roots(self.root):
            result = await self.controller(root).status()
            runs.extend(result["runs"])
        return {"runs": sorted(runs, key=lambda run: run["started_at"] or "", reverse=True)[:20]}
