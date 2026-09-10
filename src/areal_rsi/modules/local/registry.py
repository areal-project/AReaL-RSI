"""Load local application factories explicitly authorized by the host."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from areal_rsi.base.storage import write_json
from areal_rsi.config import ModelConfigurationError

if TYPE_CHECKING:
    from areal_rsi.ui.experiments import AppFactory


def local_apps(*, project_only: bool = False) -> dict[str, str]:
    """Read project registration, or the complete mapping frozen by a plan."""
    path = Path.cwd() / ".rsi" / "local-apps.json"
    try:
        # An explicit environment mapping replaces the file, even when empty.
        raw = None if project_only else os.environ.get("RSI_LOCAL_APPS")
        entries = json.loads(
            raw if raw is not None else path.read_text() if path.exists() else "{}"
        )
        if not isinstance(entries, dict) or not all(
            isinstance(name, str) and isinstance(entry, str) for name, entry in entries.items()
        ):
            raise ValueError("invalid mapping")
        return entries
    except (OSError, ValueError) as exc:
        raise ModelConfigurationError(
            "无法读取本地应用注册，请检查 .rsi/local-apps.json 或 RSI_LOCAL_APPS。"
        ) from exc


def register_local_app(name: str, entry: str, *, replace: bool = False) -> Path:
    """Authorize an importable factory for this project without running it."""
    if not name.strip():
        raise ModelConfigurationError("本地应用名称不能为空。")
    _load_factory(entry)
    path = Path.cwd() / ".rsi" / "local-apps.json"
    # Registration always edits the project file, never an environment override.
    entries = local_apps(project_only=True)
    if name in entries and entries[name] != entry and not replace:
        raise ModelConfigurationError("名称已注册；确认替换后使用 --replace。")
    entries[name] = entry
    write_json(path, entries)
    return path


def local_factory(name: str) -> AppFactory:
    # YAML selects a name; only trusted deployment configuration can name Python code.
    try:
        entry = local_apps()[name]
    except KeyError:
        raise ModelConfigurationError(
            f"本地应用 {name!r} 未注册；请先运行 rsi local-app register，或配置 RSI_LOCAL_APPS。"
        ) from None
    return _load_factory(entry)


def _load_factory(entry: str) -> AppFactory:
    # Console scripts omit the project root from sys.path. Explicit registration
    # authorizes importing project code as well as installed Python packages.
    project = str(Path.cwd())
    added = project not in sys.path
    if added:
        sys.path.insert(0, project)
    try:
        module, attribute = entry.split(":")
        factory = getattr(importlib.import_module(module), attribute)
    except (ValueError, TypeError, KeyError, ImportError, AttributeError):
        raise ModelConfigurationError(
            f"无法加载本地应用入口 {entry!r}；请检查模块安装、项目目录和 RSI_LOCAL_APPS。"
        ) from None
    finally:
        if added:
            sys.path.remove(project)
    if not callable(factory):
        raise ModelConfigurationError("本地应用入口必须是可调用的异步工厂。")
    return cast("AppFactory", factory)
