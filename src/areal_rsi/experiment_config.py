"""Shared experiment-file selection; credentials remain in model environment settings."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from areal_rsi.config import ModelConfigurationError


def default_config(mode: str) -> Path:
    name = {"local": "arithmetic.yaml", "remote": "livecodebench.yaml"}[mode]
    return Path(str(files("areal_rsi").joinpath("resources/presets", name)))


def configured_path(mode: str) -> Path | None:
    path = os.environ.get(f"RSI_{mode.upper()}_CONFIG")
    if (
        not path
        and mode == "remote"
        and os.environ.get("RSI_REMOTE_BACKEND", "envarena") == "envarena"
    ):
        path = os.environ.get("RSI_ARENA_CONFIG")
    return Path(path).expanduser().resolve() if path else None


def read_config(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        raise ModelConfigurationError("无法读取实验 YAML，请检查 --config 文件。") from None
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ModelConfigurationError("实验配置必须是 YAML 对象。")
    research_harness(value)
    return cast(dict[str, Any], value)


def research_harness(value: dict[str, Any]) -> str | None:
    kind = value.get("research_harness")
    if kind is not None and kind not in ("dsh", "codex-cli"):
        raise ModelConfigurationError("research_harness 支持 dsh 或 codex-cli。")
    return cast(str | None, kind)


def experiment_mode(value: dict[str, Any]) -> str:
    mode = value.get("mode")
    if mode not in ("local", "remote"):
        raise ModelConfigurationError("实验 YAML 必须声明 mode: local 或 mode: remote。")
    return cast(str, mode)


def select_config(preset: str | None = None, config_path: str | None = None) -> Path:
    if preset and config_path:
        raise ModelConfigurationError("preset 和 config_path 只能选择一个。")
    if config_path:
        return Path(config_path).expanduser().resolve()
    if preset not in (None, "arithmetic", "lcb", "tb21"):
        raise ModelConfigurationError("preset 支持 arithmetic、lcb 或 tb21。")
    name = {
        None: "arithmetic.yaml",
        "arithmetic": "arithmetic.yaml",
        "lcb": "livecodebench.yaml",
        "tb21": "tb21.yaml",
    }[preset]
    return Path(str(files("areal_rsi").joinpath("resources/presets", name)))


@contextmanager
def environment_scope(values: Mapping[str, str]) -> Iterator[None]:
    """Only the serialized planning section uses this; workers receive their own env."""
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def launch_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for name in ("launch.json", "experiment.yaml", "runtime.json"):
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def plan_environment(root: Path, plan_config: Mapping[str, Any]) -> dict[str, str]:
    expected = plan_config.get("launch_digest")
    if expected is None:
        # Legacy plans have no snapshot; retain their deployment settings but honor a saved mode.
        mode = plan_config.get("mode")
        return {"RSI_MODE": mode} if mode in ("local", "remote") else {}
    if expected != launch_digest(root):
        raise ModelConfigurationError("计划的配置快照已改变，请重新生成计划。")
    value = json.loads((root / "launch.json").read_text())
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
    ):
        raise ModelConfigurationError("计划启动配置无效。")
    if value.get("RSI_MODE") != plan_config.get("mode"):
        raise ModelConfigurationError("计划 mode 与配置快照不一致。")
    return cast(dict[str, str], value)


def experiment_roots(root: Path) -> list[Path]:
    """Workspace experiments plus legacy layouts, without recursively scanning artifacts."""
    children = [path.parent for path in sorted((root / "experiments").glob("*/launch.json"))]
    legacy = [root / mode for mode in ("local", "remote") if (root / mode / "run-states").is_dir()]
    return [root, *children, *legacy]


def configured_research_harness() -> str | None:
    mode = os.environ.get("RSI_MODE", "local")
    path = configured_path(mode)
    return research_harness(read_config(path)) if path else None
