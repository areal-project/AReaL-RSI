"""Public Case mapping and immutable split Benchmarks; oracle never leaves Arena."""

from __future__ import annotations

import base64
import copy
import json
import math
from collections.abc import Sequence
from typing import Any

from areal_rsi.api.base import BenchmarkSplit, Task
from areal_rsi.base.storage import encode
from areal_rsi.modules.remote.envarena.client import ArenaClient, fingerprint, reference
from areal_rsi.modules.remote.envarena.config import ArenaConfig


class ArenaBenchmark:
    def __init__(self, client: ArenaClient, config: ArenaConfig):
        self.client, self.config = client, config
        self.spec: dict[str, Any] = {}
        self.tasks: dict[BenchmarkSplit, list[Task]] = {}
        self.entries: dict[str, dict[str, Any]] = {}
        self.name = (
            "envarena:"
            + fingerprint(
                {
                    "base": client.connection.base_url,
                    "benchmark": config.benchmark,
                    "train": config.train_cases,
                    "validate": config.validate_cases,
                }
            )[:24]
        )

    async def load(self) -> None:
        version = await self.client.version("benchmarks", self.config.benchmark)
        self.spec = copy.deepcopy(version["spec"])
        if not self.spec.get("case_refs") or self.spec.get("variants"):
            raise ValueError("remote 首期要求 Case Benchmark，且不包含 variants")
        # Persist the same defaults that validation accepts for subset publication.
        aggregation = self.spec.setdefault("aggregation", {})
        if not isinstance(aggregation, dict):
            raise ValueError("aggregation 必须是对象")
        aggregation.setdefault("method", "mean")
        aggregation.setdefault("failed_task_policy", "failed_as_zero")
        if (
            aggregation.get("method", "mean") != "mean"
            or aggregation.get("failed_task_policy", "failed_as_zero") != "failed_as_zero"
        ):
            raise ValueError("remote 首期支持 mean + failed_as_zero，不能静默改变评分语义")
        await self.client.version("rewards", self.spec["reward_ref"])
        for entry in self.spec["case_refs"]:
            key = reference(entry["case_ref"])["key"]
            if key in self.entries:
                raise ValueError("Benchmark 包含重复 Case")
            self.entries[key] = entry
        for split, keys in (
            (BenchmarkSplit.TRAIN, self.config.train_cases),
            (BenchmarkSplit.VALIDATE, self.config.validate_cases),
        ):
            tasks = []
            for key in keys:
                if key not in self.entries:
                    raise ValueError(f"Case {key} 不在固定 Benchmark 中")
                entry = self.entries[key]
                if entry.get("harness_ref"):
                    raise ValueError(
                        "Case 自带 Harness override，会覆盖候选；请使用统一 Harness 的 Benchmark"
                    )
                case = await self.client.version("cases", entry["case_ref"])
                spec = case["spec"]
                query = spec.get("query", {})
                uri = str(query.get("uri", ""))
                # Some Cases expose a query containing sandbox-relative input paths.
                # Preserve it as public data; Arena materializes its actual files.
                public: Any = {"query": query}
                if uri.startswith("data:"):
                    header, encoded = uri.split(",", 1)
                    if ";base64" in header:
                        text = base64.b64decode(encoded, validate=True).decode()
                        public = json.loads(text) if "application/json" in header else text
                weight = entry.get("weight", 1)
                if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:
                    raise ValueError("Case weight 必须为有限正数")
                tasks.append(
                    Task(
                        key,
                        public,
                        {
                            "backend": "envarena",
                            "split": split.value,
                            "case_ref": encode(reference(entry["case_ref"])),
                            "env_ref": encode(reference(spec["env_ref"])),
                            "case_content_hash": case.get("content_hash", ""),
                            "benchmark_ref": encode(self.config.benchmark),
                            "reward_ref": encode(reference(self.spec["reward_ref"])),
                            "weight": float(weight),
                        },
                    )
                )
            self.tasks[split] = tasks

    async def list_tasks(self, split: BenchmarkSplit) -> Sequence[Task]:
        return self.tasks[split]

    async def ensure_subset(self, tasks: Sequence[Task]) -> dict[str, str]:
        if not tasks or len({t.task_id for t in tasks}) != len(tasks):
            raise ValueError("远端评测题目必须非空且无重复")
        known = {t.task_id: t for group in self.tasks.values() for t in group}
        if any(known.get(t.task_id) != t for t in tasks):
            raise ValueError("Task 与已固定的 Arena 数据集不一致")
        spec = {
            "case_refs": [self.entries[t.task_id] for t in tasks],
            "reward_ref": self.spec["reward_ref"],
            "aggregation": self.spec["aggregation"],
        }
        return await self.client.ensure_version("benchmarks", self.config.namespace, spec)
