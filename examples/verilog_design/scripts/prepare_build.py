"""Download checksum-pinned toolchain archives before submitting image builds."""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Archive:
    name: str
    url: str
    sha256: str
    bytes: int


def checksum(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def fetch(archive: Archive) -> None:
    target = ROOT / "cache/direct" / (archive.name + ".tar.gz")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and checksum(target) == archive.sha256:
        print(f"Verified {archive.name}", flush=True)
        return
    # Cluster submission shells may export a proxy that is unavailable on compute nodes.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    temporary = target.with_suffix(".part")
    with opener.open(archive.url, timeout=120) as src, temporary.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    if temporary.stat().st_size != archive.bytes or checksum(temporary) != archive.sha256:
        raise ValueError(f"Archive checksum mismatch: {archive.name}")
    temporary.replace(target)
    print(f"Downloaded and verified {archive.name}", flush=True)


def main() -> None:
    records = json.loads((ROOT / "containers/build-inputs.json").read_text())
    archives = [Archive(**row) for row in records]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, archives))


if __name__ == "__main__":
    main()
