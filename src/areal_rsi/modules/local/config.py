"""Local counterpart of the remote evaluation YAML."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from areal_rsi.config import ModelConfig, ModelConfigurationError
from areal_rsi.experiment_config import configured_path, default_config, read_config


@dataclass(frozen=True)
class LocalConfig:
    benchmark: str
    harness: Path
    model: str
    dataset: Path
    train_limit: int
    validate_limit: int

    @classmethod
    def from_environment(cls) -> LocalConfig:
        path = configured_path("local")
        raw = read_config(path or default_config("local"))
        if path is None:
            # Legacy env-only callers retain their current benchmark selection.
            raw.update(
                {
                    key: os.environ[env]
                    for key, env in (
                        ("benchmark", "RSI_BENCHMARK"),
                        ("dataset", "RSI_GSM8K_DATA"),
                        ("train_limit", "RSI_TRAIN_LIMIT"),
                        ("validate_limit", "RSI_VALIDATE_LIMIT"),
                    )
                    if os.environ.get(env)
                }
            )
        if set(raw) - {
            "benchmark",
            "harness",
            "model",
            "dataset",
            "train_limit",
            "validate_limit",
            "research_harness",
            "mode",
        }:
            raise ModelConfigurationError("local 配置包含未知字段；连接和认证请配置 RSI_TARGET_*。")
        if raw.get("mode", "local") != "local":
            raise ModelConfigurationError("local 实验配置的 mode 必须是 local。")
        benchmark, harness, model = (raw.get(k) for k in ("benchmark", "harness", "model"))
        if benchmark not in ("arithmetic-v1", "gsm8k"):
            raise ModelConfigurationError("local benchmark 支持 arithmetic-v1 或 gsm8k。")
        if not isinstance(harness, str) or not harness.strip():
            raise ModelConfigurationError("local harness 必须为 default 或本地目录。")
        if not isinstance(model, str) or not model.strip():
            raise ModelConfigurationError("local model 必须为 target 或模型名。")
        directory = path.parent if path else Path.cwd()
        harness_path = (
            Path(__file__).parents[2] / "default_harness"
            if harness == "default"
            else (directory / Path(harness).expanduser()).resolve()
        )
        if not harness_path.is_dir():
            raise ModelConfigurationError("local harness 目录不存在。")
        limits = []
        for key in ("train_limit", "validate_limit"):
            value = raw.get(key, 16)
            if path is None and isinstance(value, str) and value.isdecimal():
                value = int(value)
            if type(value) is not int or value < 1:
                raise ModelConfigurationError(f"{key} 必须是正整数。")
            limits.append(value)
        dataset = raw.get("dataset", str(Path.cwd() / ".rsi/datasets/gsm8k"))
        if not isinstance(dataset, str):
            raise ModelConfigurationError("dataset 必须是目录路径。")
        return cls(
            benchmark,
            harness_path,
            model.strip(),
            (directory / Path(dataset).expanduser()).resolve(),
            *limits,
        )

    def target_connection(self) -> ModelConfig:
        if self.model == "target":
            return ModelConfig.from_environment("target")
        return ModelConfig.validated(
            self.model,
            os.environ.get("RSI_TARGET_BASE_URL", ""),
            os.environ.get("RSI_TARGET_API_KEY", ""),
            "RSI_TARGET_",
        )
