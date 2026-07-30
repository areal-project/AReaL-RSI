"""RSI run configuration loading and override helpers."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RSIRunConfig:
    """Normalized configuration for one RSI loop run."""

    data: dict[str, Any]
    source_path: Path | None = None
    source_hash: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> "RSIRunConfig":
        config_path = Path(path).expanduser()
        raw = config_path.read_bytes()
        data = _load_mapping(config_path, raw)
        return cls(
            data=normalize_rsi_config(data),
            source_path=config_path.resolve(),
            source_hash=_sha256(raw),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RSIRunConfig":
        return cls(data=normalize_rsi_config(data))

    def with_overrides(self, overrides: dict[str, Any] | None) -> "RSIRunConfig":
        merged = copy.deepcopy(self.data)
        for key, value in (overrides or {}).items():
            _set_dot_path(merged, key, value)
        return RSIRunConfig(
            data=normalize_rsi_config(merged),
            source_path=self.source_path,
            source_hash=self.source_hash,
        )

    def to_run_loop_kwargs(self) -> dict[str, Any]:
        run = self.data["run"]
        env = self.data["env"]
        return {
            "initial_ckpt": run["initial_ckpt"],
            "task_set": env["task_set"],
            "harness_config": env["harness_config"],
            "method": run.get("method", "sft"),
            "max_iterations": int(run.get("max_iterations", 3)),
            "stop_criteria": self.data.get("stop_criteria", {}),
            "train_config": self.data.get("train", {}).get("config"),
            "traj_filter": self.data.get("research", {}).get("traj_filter"),
            "eval_gate": {"holdout_task_set": env["holdout_task_set"]},
        }

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)


def normalize_rsi_config(data: dict[str, Any]) -> dict[str, Any]:
    """Apply defaults and validate the minimal RSI config contract."""

    if not isinstance(data, dict):
        raise ValueError("RSI config must be a mapping.")
    normalized = copy.deepcopy(data)
    run = _section(normalized, "run")
    env = _section(normalized, "env")
    services = normalized.setdefault("services", {})
    train = normalized.setdefault("train", {})
    research = normalized.setdefault("research", {})

    run.setdefault("method", "sft")
    run.setdefault("max_iterations", 3)
    if "initial_ckpt" not in run:
        raise ValueError("RSI config requires run.initial_ckpt.")
    if "task_set" not in env:
        raise ValueError("RSI config requires env.task_set.")
    env.setdefault("holdout_task_set", f"{env['task_set']}_holdout")
    env.setdefault("harness_config", {"harness": "mock_harness"})
    train.setdefault("config", {"epochs": 1})
    research.setdefault("traj_filter", None)
    normalized.setdefault("stop_criteria", {})
    services.setdefault("areal", {"backend": "mock"})
    services.setdefault("envarena", {"backend": "mock"})
    services.setdefault("research", {"backend": "mock"})

    if run["method"] not in {"sft", "opd"}:
        raise ValueError("run.method must be 'sft' or 'opd'.")
    if int(run["max_iterations"]) < 1:
        raise ValueError("run.max_iterations must be >= 1.")
    if not isinstance(env["harness_config"], dict):
        raise ValueError("env.harness_config must be a mapping.")
    return normalized


def _load_mapping(path: Path, raw: bytes) -> dict[str, Any]:
    suffix = path.suffix.lower()
    text = raw.decode("utf-8")
    if suffix == ".json":
        payload = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("YAML config support requires PyYAML to be installed.") from exc
        payload = yaml.safe_load(text)
    else:
        raise ValueError(f"Unsupported RSI config extension: {path.suffix}")
    if not isinstance(payload, dict):
        raise ValueError("RSI config file must contain a mapping.")
    return payload


def _set_dot_path(data: dict[str, Any], path: str, value: Any) -> None:
    if not path or ".." in path:
        raise ValueError(f"Invalid override path: {path!r}")
    parts = path.split(".")
    cursor = data
    for part in parts[:-1]:
        next_value = cursor.setdefault(part, {})
        if not isinstance(next_value, dict):
            raise ValueError(f"Cannot set nested override under non-mapping path: {path}")
        cursor = next_value
    cursor[parts[-1]] = value


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    section = data.setdefault(key, {})
    if not isinstance(section, dict):
        raise ValueError(f"RSI config section {key!r} must be a mapping.")
    return section


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()
