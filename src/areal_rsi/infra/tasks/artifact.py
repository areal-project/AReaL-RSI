"""用户产物目录的 patch 构建器，不执行产物、不承担评测或选版。"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from areal_rsi.api.records import ArtifactChange, ArtifactVersion, ContentRef
from areal_rsi.infra.storage.files import FileContentStore


class LocalArtifactBuilder:
    def __init__(self, content: FileContentStore, root: Path):
        self.content, self.root = content, root
        root.mkdir(parents=True, exist_ok=True)

    async def build(self, base: ArtifactVersion | None, change: ArtifactChange) -> ContentRef:
        if not change.artifact_name.strip() or (base and base.name != change.artifact_name):
            raise ValueError("Artifact 修改必须指定目标产物，且与基线名称一致")
        patch = await self.content.read_bytes(change.patch)
        if not patch.strip():
            raise ValueError("拒绝空 Artifact patch")
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            directory = Path(temporary) / "artifact"
            if base:
                await self.content.materialize(base.content, directory)
            else:
                directory.mkdir()
            for check in (True, False):
                command = ["git", "apply", "--no-index", *(["--check"] if check else []), "-"]
                child = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=directory,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, error = await asyncio.wait_for(child.communicate(patch), timeout=30)
                except BaseException:
                    if child.returncode is None:
                        child.kill()
                    await child.wait()
                    raise
                if child.returncode:
                    raise ValueError(
                        f"Artifact patch 无法应用：{error.decode(errors='replace')[:1000]}"
                    )
            snapshot = await self.content.snapshot(directory)  # 快照层拒绝符号链接。
            if base and snapshot.digest == base.content.digest:
                raise ValueError("候选没有改变 Artifact 内容")
            return snapshot
