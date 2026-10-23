"""Fetch immutable official inputs; do not manufacture missing golden solutions."""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import shutil
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HF_REVISION = "5b807d945f6a99aa645f7e43a64a2115e281b4bf"
SOURCES = (
    ("rtllm", "hkust-zhiyao/RTLLM", "51ed553d0ffd32797a1a0a13e051656bf302c81f"),
    ("verilog-eval", "NVlabs/verilog-eval", "c498220d0a52248f8e3fdffe279075215bde2da6"),
    ("cvdp", "NVlabs/cvdp_benchmark", "8e894cf74414ab1eaea1e2b4e80a02f123df07b6"),
)
SPLITS = (
    "nonagentic_code_generation_no_commercial",
    "agentic_code_generation_no_commercial",
    "agentic_heavy_code_generation_no_commercial",
    "nonagentic_code_comprehension",
)


@dataclass(frozen=True)
class FileDigest:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class LFSFile:
    size: int
    sha256: str


def lfs_file(name: str) -> LFSFile:
    url = f"https://huggingface.co/datasets/nvidia/cvdp-benchmark-dataset/raw/{HF_REVISION}/{name}"
    with urllib.request.urlopen(url, timeout=120) as response:
        pointer = response.read().decode()
    match = re.fullmatch(
        r"version https://git-lfs.github.com/spec/v1\noid sha256:([a-f0-9]{64})\nsize ([0-9]+)\n?",
        pointer,
    )
    if match is None:
        raise ValueError(f"Invalid upstream LFS pointer: {name}")
    return LFSFile(int(match[2]), match[1])


def matches_lfs(path: Path, expected: LFSFile) -> bool:
    return path.stat().st_size == expected.size and digest(path).sha256 == expected.sha256


def digest(path: Path) -> FileDigest:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(chunk)
    value = hasher.hexdigest()
    return FileDigest(str(path.relative_to(ROOT)), path.stat().st_size, value)


def download(name: str) -> None:
    target = ROOT / "data/cvdp" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = lfs_file(name) if name.endswith(".bundle") else None
    if target.is_file():
        if expected is None or matches_lfs(target, expected):
            return
        # Preserve evidence of a truncated/corrupt prior download, outside data/.
        quarantine = ROOT / "cache/invalid-bundles" / (target.name + "." + digest(target).sha256)
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        target.replace(quarantine)
    url = (
        "https://huggingface.co/datasets/nvidia/cvdp-benchmark-dataset/resolve/"
        f"{HF_REVISION}/{name}"
    )
    temporary = target.with_suffix(target.suffix + ".part")
    if expected is not None and temporary.exists():
        if matches_lfs(temporary, expected):
            temporary.replace(target)
            return
        if temporary.stat().st_size >= expected.size:
            temporary.unlink()
    for attempt in range(3):
        try:
            offset = temporary.stat().st_size if temporary.exists() else 0
            request = urllib.request.Request(url, headers={"Range": f"bytes={offset}-"})
            with urllib.request.urlopen(request, timeout=120) as src:
                mode = "ab" if offset and src.status == 206 else "wb"
                received_from = offset if mode == "ab" else 0
                content_length = src.headers.get("Content-Length")
                remote_size = expected.size if expected is not None else None
                if src.status == 206:
                    content_range = src.headers.get("Content-Range", "")
                    bounds = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                    if bounds is None or int(bounds[1]) != offset:
                        raise OSError(f"Unexpected resumed response range: {name}: {content_range}")
                    remote_size = int(bounds[3])
                with temporary.open(mode) as dst:
                    shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
            if content_length is not None and temporary.stat().st_size - received_from != int(
                content_length
            ):
                raise OSError(f"Truncated HTTP response: {name}")
            if remote_size is not None and temporary.stat().st_size != remote_size:
                raise OSError(f"Incomplete HTTP file: {name}")
            if expected is not None and not matches_lfs(temporary, expected):
                if temporary.stat().st_size >= expected.size:
                    temporary.unlink()
                raise OSError(f"Incomplete or corrupt bundle download: {name}")
            temporary.replace(target)
            print(f"Downloaded {name}: {target.stat().st_size} bytes", flush=True)
            return
        except (OSError, TimeoutError, http.client.IncompleteRead):
            if attempt == 2:
                raise


def main() -> None:
    for directory in ("source", "data", "images", "logs", "cache", "runs"):
        (ROOT / directory).mkdir(exist_ok=True)
    for name, repo, revision in SOURCES:
        path = ROOT / "source" / name
        if not path.exists():
            subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(path),
                    "remote",
                    "add",
                    "origin",
                    f"https://github.com/{repo}.git",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(path), "fetch", "--depth=1", "origin", revision], check=True
            )
            subprocess.run(["git", "-C", str(path), "checkout", "--detach", revision], check=True)
        actual = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True)
        if actual.strip() != revision:
            raise ValueError(f"Unexpected source revision: {path}")
        print(f"Source {name}: {revision}", flush=True)
    rtllm = ROOT / "source/rtllm"
    for name in ("Arithmetic", "Memory", "Control", "Miscellaneous"):
        shutil.copytree(rtllm / name, ROOT / "data/rtllm" / name, dirs_exist_ok=True)
    for name in ("LICENSE", "README.md", "File_list.md"):
        shutil.copy2(rtllm / name, ROOT / "data/rtllm" / name)
    for task in ("spec-to-rtl", "code-complete-iccad2023"):
        shutil.copytree(
            ROOT / "source/verilog-eval" / f"dataset_{task}",
            ROOT / "data/verilogeval" / task,
            dirs_exist_ok=True,
        )
    shutil.copy2(ROOT / "source/verilog-eval/LICENSE", ROOT / "data/verilogeval/LICENSE")
    for name in ("LICENSE", "NOTICE", "CHANGELOG", "README.md"):
        download(name)
    for split in SPLITS:
        download(f"cvdp_v1.1.0_{split}.jsonl")
    examples = ROOT / "data/cvdp/examples"
    examples.mkdir(exist_ok=True)
    for path in (ROOT / "source/cvdp/example_dataset").glob("*.jsonl"):
        if "no_commercial" in path.name or "comprehension" in path.name:
            shutil.copy2(path, examples / path.name)
    heavy = ROOT / "data/cvdp/cvdp_v1.1.0_agentic_heavy_code_generation_no_commercial.jsonl"
    repos = {json.loads(line)["context"]["repo"] for line in heavy.read_text().splitlines()}
    bundle_root = "cvdp_v1.1.0_agentic_heavy_code_generation_public"
    names = [f"{bundle_root}/{repo}.bundle" for repo in sorted(repos)]
    names.extend([f"{bundle_root}/README.md", f"{bundle_root}/manifest.txt"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, names))
    advertised: dict[str, set[str]] = {}
    for repo in sorted(repos):
        bundle = ROOT / "data/cvdp" / bundle_root / (repo + ".bundle")
        heads = subprocess.check_output(["git", "bundle", "list-heads", str(bundle)], text=True)
        advertised[repo] = {line.split()[0] for line in heads.splitlines()}
    for line in heavy.read_text().splitlines():
        row = json.loads(line)
        context = row["context"]
        if context["commit"] not in advertised[context["repo"]]:
            raise ValueError(f"Heavy context commit missing from bundle: {row['id']}")
    files = [digest(p) for p in sorted((ROOT / "data").rglob("*")) if p.is_file()]
    manifest = {
        "sources": [{"name": n, "repository": r, "commit": c} for n, r, c in SOURCES],
        "cvdp_dataset_revision": HF_REVISION,
        "files": [asdict(f) for f in files],
    }
    (ROOT / "data/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Prepared {len(files)} files; {len(repos)} heavy repositories", flush=True)


if __name__ == "__main__":
    main()
