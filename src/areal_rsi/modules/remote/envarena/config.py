"""Non-secret experiment inputs. Runtime capability declarations are explicit."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from areal_rsi.modules.remote.envarena.client import ArenaClient, reference

EDITABLE = frozenset({"system_prompt", "skills", "tools", "mcp"})


@dataclass(frozen=True)
class ArenaConfig:
    benchmark: dict[str, str]
    harness: dict[str, str]
    model: dict[str, str]
    train_cases: tuple[str, ...]
    validate_cases: tuple[str, ...]
    allowed_changes: tuple[str, ...]
    namespace: str = "remote"
    max_parallel: int = 2
    max_retries: int = 1
    poll_seconds: float = 5
    timeout_seconds: float = 3600
    target_score: float = 1
    source_job: str | None = None

    @classmethod
    async def load(cls, path: Path, client: ArenaClient) -> ArenaConfig:
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError("remote 配置必须是 YAML 对象")
        supported = {
            "benchmark",
            "harness",
            "model",
            "source_job",
            "train_cases",
            "validate_cases",
            "allowed_changes",
            "namespace",
            "max_parallel",
            "max_retries",
            "poll_seconds",
            "timeout_seconds",
            "target_score",
            "sample_numbers",
        }
        if set(raw) - supported:
            raise ValueError("remote 配置包含未知字段；凭证请通过环境变量设置")
        if type(raw.get("sample_numbers", 1)) is not int or raw.get("sample_numbers", 1) != 1:
            raise ValueError("remote 首期只支持 sample_numbers=1")
        source = raw.get("source_job")
        baseline: dict[str, Any] = {}
        if source:
            if not isinstance(source, str) or not re.fullmatch(r"[a-z0-9-]+", source):
                raise ValueError("source_job 必须是 Arena Job ID")
            baseline = await client.job_summary(source)
        refs = {
            name: reference(raw.get(name, baseline.get(f"{name}_ref")))
            for name in ("benchmark", "harness", "model")
        }
        splits = {}
        for name in ("train_cases", "validate_cases"):
            values = raw.get(name)
            if (
                not isinstance(values, list)
                or not values
                or any(
                    not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", v)
                    for v in values
                )
                or len(set(values)) != len(values)
            ):
                raise ValueError(f"{name} 必须是非空且无重复的 Case key 列表")
            splits[name] = tuple(values)
        if set(splits["train_cases"]) & set(splits["validate_cases"]):
            raise ValueError("TRAIN 与 VALIDATE 不得重叠")
        allowed = raw.get("allowed_changes", [])
        if not isinstance(allowed, list) or any(
            not isinstance(v, str) or v not in EDITABLE for v in allowed
        ):
            raise ValueError("allowed_changes 只允许 system_prompt/skills/tools/mcp")
        namespace = raw.get("namespace", "remote")
        if not isinstance(namespace, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{0,31}", namespace
        ):
            raise ValueError("namespace 必须是 1 到 32 位小写字母、数字或连字符")
        for name, default, minimum in (("max_parallel", 2, 1), ("max_retries", 1, 0)):
            value = raw.get(name, default)
            if type(value) is not int or not minimum <= value <= 100:
                raise ValueError(f"{name} 超出允许范围")
        for name, default in (("poll_seconds", 5), ("timeout_seconds", 3600), ("target_score", 1)):
            value = raw.get(name, default)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须是有限正数")
        if raw.get("target_score", 1) > 1:
            raise ValueError("target_score 必须在 (0,1] 内")
        return cls(
            benchmark=refs["benchmark"],
            harness=refs["harness"],
            model=refs["model"],
            train_cases=splits["train_cases"],
            validate_cases=splits["validate_cases"],
            allowed_changes=tuple(allowed),
            namespace=namespace,
            max_parallel=raw.get("max_parallel", 2),
            max_retries=raw.get("max_retries", 1),
            poll_seconds=raw.get("poll_seconds", 5),
            timeout_seconds=raw.get("timeout_seconds", 3600),
            target_score=raw.get("target_score", 1),
            source_job=source,
        )
