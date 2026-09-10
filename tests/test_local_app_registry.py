"""External local applications use the same confirmed plan and worker lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from areal_rsi.config import ModelConfigurationError
from areal_rsi.modules.local.registry import local_apps, local_factory, register_local_app
from areal_rsi.ui.workspace import ExperimentWorkspace


@pytest.mark.parametrize(
    "value", ["{}", "[]", "null", "bad json", '{"demo": 3}', '{"demo": "missing.module:create"}']
)
def test_unregistered_or_invalid_factory_fails_safely(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("RSI_LOCAL_APPS", value)
    with pytest.raises(ModelConfigurationError, match="RSI_LOCAL_APPS"):
        local_factory("demo")


def test_non_callable_factory_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSI_LOCAL_APPS", '{"demo":"module:create"}')
    with (
        patch("importlib.import_module", return_value=Mock(create=42)),
        pytest.raises(ModelConfigurationError, match="异步工厂"),
    ):
        local_factory("demo")


def test_project_registration_is_persistent_and_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RSI_LOCAL_APPS", raising=False)
    factory = Mock()
    with patch("importlib.import_module", return_value=Mock(create=factory, other=factory)):
        path = register_local_app("demo", "module:create")
        assert path == tmp_path / ".rsi/local-apps.json"
        assert local_factory("demo") is factory
        factory.assert_not_called()
        register_local_app("demo", "module:create")
        with pytest.raises(ModelConfigurationError, match="--replace"):
            register_local_app("demo", "module:other")
        register_local_app("demo", "module:other", replace=True)
        monkeypatch.setenv("RSI_DATA_ROOT", str(tmp_path / "different-output"))
        assert local_apps() == {"demo": "module:other"}
        monkeypatch.setenv("RSI_LOCAL_APPS", "{}")
        assert local_apps() == {}
        assert local_apps(project_only=True) == {"demo": "module:other"}


def test_invalid_project_registration_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RSI_LOCAL_APPS", raising=False)
    path = tmp_path / ".rsi/local-apps.json"
    path.parent.mkdir()
    path.write_text("invalid json")
    with (
        patch("importlib.import_module", return_value=Mock(create=Mock())),
        pytest.raises(ModelConfigurationError, match=r"local-apps\.json"),
    ):
        register_local_app("demo", "module:create")
    assert path.read_text() == "invalid json"


def test_console_registration_loads_project_code(tmp_path: Path) -> None:
    (tmp_path / "demo_app.py").write_text("async def create(root, emit):\n    return None\n")
    # Like the installed console script, this entrypoint lives outside the project.
    launcher = tmp_path / "bin" / "entry.py"
    launcher.parent.mkdir()
    launcher.write_text("from areal_rsi.ui.cli import main\nmain()\n")
    result = subprocess.run(
        [sys.executable, str(launcher), "local-app", "register", "demo", "demo_app:create"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / ".rsi/local-apps.json").read_text()) == {
        "demo": "demo_app:create"
    }


def test_external_factory_is_frozen_and_loaded_by_default_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests = Path(__file__).parent.resolve()
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(tests), str(tests.parent / "src"))))
    mapping = json.dumps({"demo": "test_experiment_workers:fixture_app"})
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RSI_LOCAL_APPS", raising=False)
    registry = tmp_path / ".rsi/local-apps.json"
    registry.parent.mkdir()
    registry.write_text(mapping)
    monkeypatch.delenv("RSI_RUNTIME_CONFIG", raising=False)
    config = tmp_path / "external.yaml"
    config.write_text("mode: local\nbackend: demo\n")
    store = ExperimentWorkspace(tmp_path / "workspace")

    async def check() -> None:
        record = await store.plan("external application", 1, config_path=str(config))
        controller = store.locate(record["plan_id"], plan=True)
        assert not list(controller.root.glob("*-entered.json"))
        with pytest.raises(ValueError, match="确认"):
            await store.start(record["plan_id"], confirmed=False)
        # Neither project registration nor shell changes can redirect an approved plan.
        registry.write_text("{}")
        monkeypatch.setenv("RSI_LOCAL_APPS", "{}")
        run = await store.start(record["plan_id"], confirmed=True)
        try:
            async with asyncio.timeout(15):
                while not (controller.root / f"{run['run_id']}-entered.json").exists():
                    await asyncio.sleep(0.05)
            assert (await store.status(run["run_id"]))["state"] == "running"
            (controller.root / "fixture-release.json").touch()
            async with asyncio.timeout(10):
                while (await store.status(run["run_id"]))["state"] != "succeeded":
                    await asyncio.sleep(0.05)
            restored = ExperimentWorkspace(store.root)
            assert (await restored.status(run["run_id"]))["state"] == "succeeded"
            assert (await restored.resume(run["run_id"], confirmed=True))["state"] == "succeeded"
        finally:
            for child in controller._children.values():
                if child.poll() is None:
                    child.terminate()
                await asyncio.to_thread(child.wait, timeout=10)

    asyncio.run(check())
