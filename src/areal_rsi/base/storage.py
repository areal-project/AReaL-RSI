"""基础存储：内容按摘要寻址，版本只追加，运行状态原子替换。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import tempfile
import types
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

from areal_rsi.api.base import (
    TRAJECTORY_BUNDLE_MEDIA_TYPE,
    ArtifactVersion,
    ContentRef,
    HarnessVersion,
    JsonValue,
    MemoryHit,
    MemoryRecord,
    ModelVersion,
    SolutionVersion,
    Trajectory,
    TrajectoryRef,
)

T = TypeVar("T")
DIRECTORY_TYPE = "application/vnd.areal-rsi.directory+json"


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def encode(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    return value


def decode(kind: Any, value: Any) -> Any:
    """恢复已确认 API 的 dataclass/enum/tuple，不把反序列化字典泄漏给调用方。"""
    if value is None:
        return None
    origin = get_origin(kind)
    if origin is types.UnionType:
        for option in get_args(kind):
            if dataclasses.is_dataclass(option):
                names = {f.name for f in dataclasses.fields(option)}
                if isinstance(value, dict) and set(value) <= names:
                    return decode(option, value)
        return value
    if origin in (tuple, list):
        items = [decode(get_args(kind)[0], v) for v in value]
        return tuple(items) if origin is tuple else items
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    if isinstance(kind, type) and dataclasses.is_dataclass(kind):
        hints = get_type_hints(kind)
        return kind(**{k: decode(hints[k], v) for k, v in value.items()})
    return value


def json_bytes(value: Any) -> bytes:
    return json.dumps(encode(value), ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def write_json(path: Path, value: Any, *, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json_bytes(value)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise ValueError(f"禁止覆盖历史记录：{path.name}") from None
        else:
            os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def safe_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError(f"非法记录标识：{name!r}")
    return name


def safe_relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"非法相对路径：{name!r}")
    return path


class FileContentStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    async def put_bytes(self, content: bytes, *, media_type: str | None = None) -> ContentRef:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / digest
        fd, temporary = tempfile.mkstemp(dir=self.root, prefix=".pending-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != content:
                    raise ValueError("内容摘要冲突或已有内容损坏") from None
        finally:
            Path(temporary).unlink(missing_ok=True)
        return ContentRef(f"content://{digest}", f"sha256:{digest}", media_type)

    async def put_text(
        self, content: str, *, media_type: str = "text/plain; charset=utf-8"
    ) -> ContentRef:
        return await self.put_bytes(content.encode(), media_type=media_type)

    async def put_json(self, content: JsonValue) -> ContentRef:
        return await self.put_bytes(json_bytes(content), media_type="application/json")

    async def resolve_path(self, ref: ContentRef) -> Path:
        digest = ref.digest.removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or ref.uri != f"content://{digest}":
            raise ValueError("FileContentStore 只解析本地 content://sha256 引用")
        path = self.root / digest
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("存储内容摘要校验失败")
        return path

    async def read_bytes(self, ref: ContentRef) -> bytes:
        return (await self.resolve_path(ref)).read_bytes()

    async def read_text(self, ref: ContentRef) -> str:
        return (await self.read_bytes(ref)).decode()

    async def read_json(self, ref: ContentRef) -> JsonValue:
        return json.loads(await self.read_text(ref))  # type: ignore[no-any-return]

    async def snapshot(self, directory: Path) -> ContentRef:
        files = {}
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError("快照不接受符号链接")
            if path.is_file():
                files[path.relative_to(directory).as_posix()] = encode(
                    await self.put_bytes(path.read_bytes())
                )
        return await self.put_bytes(json_bytes({"files": files}), media_type=DIRECTORY_TYPE)

    async def materialize(self, ref: ContentRef, destination: Path) -> Path:
        """复制快照到新目录；读取引用不会赋予 Agent 内容仓库的访问权。"""
        if ref.media_type != DIRECTORY_TYPE:
            raise ValueError("要求目录快照引用")
        manifest = json.loads(await self.read_text(ref))
        if destination.exists():
            raise FileExistsError(destination)
        destination.mkdir(parents=True)
        for name, content in manifest["files"].items():
            path = destination / safe_relative(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(await self.read_bytes(decode(ContentRef, content)))
        return destination


class FileRecords:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def save(self, key: str, value: Any, *, immutable: bool = True) -> None:
        write_json(self.root / f"{safe_name(key)}.json", value, immutable=immutable)

    def get(self, key: str, kind: type[T]) -> T | None:
        path = self.root / f"{safe_name(key)}.json"
        return decode(kind, json.loads(path.read_text())) if path.exists() else None

    def list(self, kind: type[T]) -> list[T]:
        return [decode(kind, json.loads(p.read_text())) for p in sorted(self.root.glob("*.json"))]


class FileVersionManager:
    def __init__(self, root: Path):
        self.records = FileRecords(root)

    def _component(
        self,
        kind: type[T],
        prefix: str,
        name: str,
        content: ContentRef,
        parent_version_id: str | None,
    ) -> T:
        if parent_version_id and (
            not parent_version_id.startswith(f"{prefix}_")
            or self.records.get(parent_version_id, kind) is None
        ):
            raise ValueError("父版本不存在")
        version = kind(
            version_id=new_id(prefix),
            name=name,
            content=content,  # type: ignore[call-arg]
            created_at=now(),
            parent_version_id=parent_version_id,
        )
        self.records.save(version.version_id, version)  # type: ignore[attr-defined]
        return version

    async def create_model(
        self, name: str, content: ContentRef, *, parent_version_id: str | None = None
    ) -> ModelVersion:
        return self._component(ModelVersion, "model", name, content, parent_version_id)

    async def create_harness(
        self, name: str, content: ContentRef, *, parent_version_id: str | None = None
    ) -> HarnessVersion:
        return self._component(HarnessVersion, "harness", name, content, parent_version_id)

    async def create_artifact(
        self, name: str, content: ContentRef, *, parent_version_id: str | None = None
    ) -> ArtifactVersion:
        return self._component(ArtifactVersion, "artifact", name, content, parent_version_id)

    async def create_solution(
        self,
        model: ModelVersion,
        harness: HarnessVersion,
        *,
        artifacts: Sequence[ArtifactVersion] = (),
        parent_solution_id: str | None = None,
    ) -> SolutionVersion:
        if await self.get_model(model.version_id) != model:
            raise ValueError("模型版本未注册或已被修改")
        if await self.get_harness(harness.version_id) != harness:
            raise ValueError("Harness 版本未注册或已被修改")
        if len({a.name for a in artifacts}) != len(artifacts):
            raise ValueError("一个 Solution 内的 Artifact 名称不能重复")
        for artifact in artifacts:
            if await self.get_artifact(artifact.version_id) != artifact:
                raise ValueError("Artifact 版本未注册或已被修改")
        if parent_solution_id and await self.get_solution(parent_solution_id) is None:
            raise ValueError("父 Solution 不存在")
        solution = SolutionVersion(
            new_id("solution"), model, harness, now(), tuple(artifacts), parent_solution_id
        )
        self.records.save(solution.solution_id, solution)
        return solution

    async def get_model(self, version_id: str) -> ModelVersion | None:
        if not version_id.startswith("model_"):
            return None
        return self.records.get(version_id, ModelVersion)

    async def get_harness(self, version_id: str) -> HarnessVersion | None:
        if not version_id.startswith("harness_"):
            return None
        return self.records.get(version_id, HarnessVersion)

    async def get_artifact(self, version_id: str) -> ArtifactVersion | None:
        if not version_id.startswith("artifact_"):
            return None
        return self.records.get(version_id, ArtifactVersion)

    async def get_solution(self, solution_id: str) -> SolutionVersion | None:
        if not solution_id.startswith("solution_"):
            return None
        return self.records.get(solution_id, SolutionVersion)

    async def list_child_solutions(self, solution_id: str) -> Sequence[SolutionVersion]:
        children = []
        for path in sorted(self.records.root.glob("solution_*.json")):
            item = self.records.get(path.stem, SolutionVersion)
            if item is not None and item.parent_solution_id == solution_id:
                children.append(item)
        return children


class FileTrajectoryStore:
    def __init__(self, root: Path, content: FileContentStore):
        self.records = FileRecords(root)
        self.content = content

    async def save(self, trajectory: Trajectory) -> TrajectoryRef:
        self.records.save(trajectory.trajectory_id, trajectory)
        return TrajectoryRef(trajectory.trajectory_id)

    async def get(self, trajectory_id: str) -> Trajectory | None:
        return self.records.get(trajectory_id, Trajectory)

    async def bundle(self, trajectories: Sequence[TrajectoryRef]) -> ContentRef:
        entries = []
        for ref in trajectories:
            item = await self.get(ref.trajectory_id)
            if item is None:
                raise KeyError(ref.trajectory_id)
            entries.append({"trajectory_id": ref.trajectory_id, "content": encode(item.content)})
        return await self.content.put_bytes(
            json_bytes({"trajectories": entries}), media_type=TRAJECTORY_BUNDLE_MEDIA_TYPE
        )


class FileMemory:
    def __init__(self, root: Path):
        self.records = FileRecords(root)

    async def add(self, text: str, *, source: str, tags: Sequence[str] = ()) -> MemoryRecord:
        if not text.strip() or not source.strip():
            raise ValueError("Memory 内容和来源不能为空")
        record = MemoryRecord(new_id("memory"), text, source, tuple(tags), now())
        self.records.save(record.memory_id, record)
        return record

    async def get(self, memory_id: str) -> MemoryRecord | None:
        return self.records.get(memory_id, MemoryRecord)

    async def search(self, query: str, *, limit: int = 10) -> Sequence[MemoryHit]:
        terms = memory_terms(query)
        hits = []
        for item in self.records.list(MemoryRecord):
            words = memory_terms(item.text + " " + " ".join(item.tags))
            score = len(terms & words) / max(1, len(terms))
            if score:
                hits.append(MemoryHit(item, score))
        return sorted(hits, key=lambda h: (-h.score, h.memory.memory_id))[: max(0, min(limit, 50))]


def memory_terms(text: str) -> set[str]:
    """轻量中英文检索；中文用双字片段，不把整句话当一个词。"""
    terms = set(re.findall(r"[a-z0-9_]+", text.lower()))
    for fragment in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(fragment[i : i + 2] for i in range(max(1, len(fragment) - 1)))
    return terms
