"""Export the pinned VerilogEval code-completion data with per-file SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path

REPOSITORY = "https://github.com/NVlabs/verilog-eval.git"
COMMIT = "c498220d0a52248f8e3fdffe279075215bde2da6"
DATASET = "dataset_code-complete-iccad2023"


@dataclass(frozen=True)
class FileRecord:
    path: str
    size: int
    sha256: str


def fetch(output: Path, source: Path | None = None) -> None:
    output.mkdir(parents=True, exist_ok=False)
    if source is None:
        source = output / "source"
        subprocess.run(["git", "clone", "--no-checkout", REPOSITORY, str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", COMMIT], check=True)
    archive = output / "upstream.tar"
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "archive",
            "--format=tar",
            "-o",
            str(archive),
            COMMIT,
            "LICENSE",
            DATASET,
        ],
        check=True,
    )
    data = output / "data"
    data.mkdir()
    with tarfile.open(archive) as stream:
        for member in stream:
            target = data / member.name
            if not target.resolve().is_relative_to(data.resolve()) or not (
                member.isfile() or member.isdir()
            ):
                raise ValueError(f"Unexpected archive member: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    counts = {
        suffix: len(list((data / DATASET).glob("*" + suffix)))
        for suffix in ("_prompt.txt", "_ref.sv", "_test.sv", "_ifc.txt")
    }
    if set(counts.values()) != {156}:
        raise ValueError(f"Unexpected task counts: {counts}")
    references = {p.name.removesuffix("_ref.sv") for p in (data / DATASET).glob("*_ref.sv")}
    for suffix in counts:
        identifiers = {p.name.removesuffix(suffix) for p in (data / DATASET).glob("*" + suffix)}
        if identifiers != references:
            raise ValueError(f"Task identifiers differ for {suffix}")
    files = [
        FileRecord(
            str(p.relative_to(data)), p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest()
        )
        for p in sorted(data.rglob("*"))
        if p.is_file()
    ]
    manifest = {
        "repository": REPOSITORY,
        "commit": COMMIT,
        "dataset": DATASET,
        "task_count": 156,
        "counts": counts,
        "file_count": len(files),
        "files": [asdict(record) for record in files],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (data / "SHA256SUMS").write_text(
        "".join(f"{record.sha256}  {record.path}\n" for record in files)
    )
    print(json.dumps({k: v for k, v in manifest.items() if k != "files"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--source", type=Path, help="Existing Git repository; export commit without editing"
    )
    args = parser.parse_args()
    fetch(args.output.resolve(), args.source.resolve() if args.source else None)
