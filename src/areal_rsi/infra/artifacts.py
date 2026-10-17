"""Local immutable content references and atomic typed record boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from areal_rsi.api.control import decode_record, encode_record
from areal_rsi.api.records import ArtifactFile, ContentRef, DirectorySnapshot, SourceSnapshot

T = TypeVar("T")


def write_record(path: Path, value: Any) -> ContentRef:
    content = json.dumps(encode_record(value), ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".record-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(content + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return file_ref(path)


def read_record(path: Path, kind: type[T]) -> T:
    return decode_record(kind, json.loads(path.read_text()))


def file_ref(path: Path) -> ContentRef:
    path = path.resolve(strict=True)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return ContentRef(path.as_uri(), digest.hexdigest())


def snapshot_directory(root: Path, manifest: Path) -> ContentRef:
    root = root.resolve(strict=True)
    files = tuple(
        ArtifactFile(str(path.relative_to(root)), path.stat().st_size, file_ref(path).digest)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.resolve() != manifest.resolve()
    )
    if not files:
        raise ValueError(f"Empty artifact directory: {root}")
    return write_record(manifest, DirectorySnapshot(str(root), files))


def snapshot_source(root: Path, manifest: Path, *, paths: tuple[str, ...]) -> SourceSnapshot:
    """Hash tracked and unignored source, including uncommitted changes."""
    root = root.resolve(strict=True)

    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(root), *args])

    files = sorted(
        set(
            git("ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *paths)
            .decode()
            .split("\0")
        )
        - {""}
    )
    entries = tuple(
        ArtifactFile(name, (root / name).stat().st_size, file_ref(root / name).digest)
        for name in files
        if (root / name).is_file()
    )
    content = write_record(manifest, DirectorySnapshot(str(root), entries))
    archive = manifest.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as output:
        for entry in entries:
            output.add(root / entry.path, arcname=entry.path, recursive=False)
    return SourceSnapshot(
        str(root),
        git("rev-parse", "HEAD").decode().strip(),
        bool(git("status", "--porcelain")),
        content,
        file_ref(archive),
    )
