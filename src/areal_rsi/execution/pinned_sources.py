"""Run Python against operator-pinned source files in a private Linux mount namespace.

This standalone module can be copied to a remote executor; it needs only Python
and util-linux. Config and snapshots belong to the operator, never a candidate.
Shared files are overlaid only in the child namespace, without changing their
paths, imports or the host copies. External services are not isolated by this.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def mounts(config: dict[str, Any]) -> list[tuple[Path, Path]]:
    root = Path(config["root"]).resolve(strict=True)
    result = []
    for relative, record in config["files"].items():
        destination = root / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Source path escapes the configured root")
        if destination.is_symlink() or not destination.is_file():
            raise ValueError(f"Shared source is missing or linked: {relative}")
        if not destination.resolve().is_relative_to(root):
            raise ValueError("Source path escapes the configured root")
        source = Path(record["path"])
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Pinned source is missing or linked: {relative}")
        if hashlib.sha256(source.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"Pinned source checksum mismatch: {relative}")
        result.append((source.resolve(), destination))
    return result


def main() -> None:
    arguments = sys.argv[1:]
    inside = bool(arguments and arguments[0] == "--inside")
    if inside:
        original_namespace = arguments[1]
        arguments = arguments[2:]
        if os.readlink("/proc/self/ns/mnt") == original_namespace:
            raise RuntimeError("Refusing to mount in the original namespace")
    if len(arguments) < 3 or arguments[1] != "--":
        raise ValueError("Usage: pinned_sources.py CONFIG -- PYTHON_ARGUMENTS")
    config_path = Path(arguments[0]).resolve(strict=True)
    config = json.loads(config_path.read_text())
    pairs = mounts(config)
    if not inside:
        os.execvp(
            "unshare",
            [
                "unshare",
                "--mount",
                "--propagation",
                "private",
                sys.executable,
                str(Path(__file__).resolve()),
                "--inside",
                os.readlink("/proc/self/ns/mnt"),
                str(config_path),
                "--",
                *arguments[2:],
            ],
        )
    for source, destination in pairs:
        subprocess.run(
            ["mount", "--bind", str(source), str(destination)], check=True, capture_output=True
        )
        subprocess.run(
            ["mount", "-o", "remount,bind,ro", str(destination)], check=True, capture_output=True
        )
    os.execv(config["python"], [config["python"], *arguments[2:]])


if __name__ == "__main__":
    main()
