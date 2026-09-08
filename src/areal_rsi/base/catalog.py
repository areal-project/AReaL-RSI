"""按内容复用模型和目标基线，同一数据目录可保存多组实验配置。"""

from __future__ import annotations

import fcntl
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from areal_rsi.api.base import ContentRef, ModelVersion, SolutionVersion
from areal_rsi.base.storage import FileRecords, FileVersionManager, json_bytes


class LocalModelCatalog:
    def __init__(self, root: Path, versions: FileVersionManager):
        self.root, self.versions = root, versions
        self.models = FileRecords(root / "model-index")
        self.baselines = FileRecords(root / "baselines")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        directory = self.root / "locks"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "model-catalog.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _legacy(self) -> SolutionVersion | None:
        return FileRecords(self.root).get("baseline", SolutionVersion)

    async def model(self, name: str, content: ContentRef) -> ModelVersion:
        key = hashlib.sha256(json_bytes({"name": name, "content": content})).hexdigest()
        with self._locked():
            model = self.models.get(key, ModelVersion)
            if model is None:
                legacy = self._legacy()
                if legacy and legacy.model.name == name and legacy.model.content == content:
                    model = legacy.model
                else:
                    model = await self.versions.create_model(name, content)
                self.models.save(key, model)
            if (
                model.name != name
                or model.content != content
                or await self.versions.get_model(model.version_id) != model
            ):
                raise ValueError("模型索引与注册内容不一致")
            return model

    async def baseline(self, target: ModelVersion, harness: ContentRef) -> SolutionVersion:
        key = hashlib.sha256(
            json_bytes({"target_model_id": target.version_id, "harness": harness})
        ).hexdigest()
        with self._locked():
            baseline = self.baselines.get(key, SolutionVersion)
            if baseline is None:
                legacy = self._legacy()
                if legacy and legacy.model == target and legacy.harness.content == harness:
                    baseline = legacy
                else:
                    version = await self.versions.create_harness("dsh-arithmetic", harness)
                    baseline = await self.versions.create_solution(target, version)
                self.baselines.save(key, baseline)
            if (
                baseline.model != target
                or baseline.harness.content != harness
                or await self.versions.get_solution(baseline.solution_id) != baseline
            ):
                raise ValueError("基线索引与注册内容不一致")
            return baseline
