"""Copy the locally installed native Codex bundle; never copy CODEX_HOME into an image."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", type=Path, default=shutil.which("codex"))
    args = parser.parse_args()
    if args.codex is None:
        parser.error("codex was not found on PATH")
    executable = args.codex.resolve()
    if executable.read_bytes()[:4] != b"\x7fELF":
        package = executable.parent.parent
        candidates = list(package.glob("node_modules/@openai/codex-*/vendor/*/bin/codex"))
        if len(candidates) != 1:
            parser.error("Pass --codex with the exact native binary path")
        executable = candidates[0]
    directory = executable.parent
    binaries = [executable, directory / "codex-code-mode-host", directory.parent / "codex-path/rg"]
    destination = ROOT / "cache/agent-build"
    destination.mkdir(parents=True, exist_ok=True)
    files = []
    for source in binaries:
        if not source.is_file():
            parser.error(f"Missing native companion: {source}")
        target = destination / source.name
        shutil.copy2(source, target)
        files.append(
            {
                "name": source.name,
                "source": str(source),
                "size": target.stat().st_size,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
    version = subprocess.check_output([str(executable), "--version"], text=True).strip()
    record = {"version": version, "files": files}
    (ROOT / "containers/agent-cli-lock.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"Prepared {version}: {', '.join(file['name'] for file in files)}")


if __name__ == "__main__":
    main()
