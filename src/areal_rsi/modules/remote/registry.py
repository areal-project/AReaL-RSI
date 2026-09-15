"""Select a provider without exposing provider details to App or Web."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from areal_rsi.modules.remote.base import RemoteJob, RemoteProvider
from areal_rsi.modules.remote.envarena.backend import EnvArenaProvider

PROVIDERS: dict[str, RemoteProvider] = {"envarena": EnvArenaProvider()}


def get_provider(name: str) -> RemoteProvider:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError(f"未知 remote backend：{name}") from None


@dataclass(frozen=True)
class RemoteSettings:
    backend: str
    config_path: Path

    @classmethod
    def from_environment(cls) -> RemoteSettings:
        backend = os.environ.get("RSI_REMOTE_BACKEND", "envarena")
        get_provider(backend)
        path = os.environ.get("RSI_REMOTE_CONFIG")
        if not path and backend == "envarena":
            path = os.environ.get("RSI_ARENA_CONFIG")
        if not path:
            raise ValueError("远端执行需要设置 RSI_REMOTE_CONFIG")
        return cls(backend, Path(path).expanduser().resolve())


def stored_backend(root: Path) -> str | None:
    binding = root / "remote" / "backend.json"
    if binding.exists():
        value = json.loads(binding.read_text()).get("backend")
        if not isinstance(value, str):
            raise ValueError("remote backend 记录无效")
        return value
    # Compatibility with runs created before provider selection was introduced.
    if (root / "remote" / "config.json").exists():
        return "envarena"
    return None


def read_remote_jobs(root: Path, run_id: str) -> list[RemoteJob]:
    return [job for provider in PROVIDERS.values() for job in provider.read_jobs(root, run_id)]


def remote_environment_names() -> tuple[str, ...]:
    return get_provider(os.environ.get("RSI_REMOTE_BACKEND", "envarena")).environment_names


def all_remote_environment_names() -> tuple[str, ...]:
    """The unbound TUI may select any registered backend when planning."""
    return tuple(
        sorted({name for provider in PROVIDERS.values() for name in provider.environment_names})
    )
