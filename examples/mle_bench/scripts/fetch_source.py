#!/usr/bin/env python3
"""Pin upstream and materialize leaderboard LFS files without requiring git-lfs."""
import concurrent.futures
import hashlib
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "507f92e1138bb6e40dac5c6ee7a6758e6424bf97"
SOURCE = ROOT / "source/mle-bench"


def materialize(path):
    data = path.read_bytes()
    if not data.startswith(b"version https://git-lfs.github.com/spec/v1"):
        return
    fields = dict(line.split(" ", 1) for line in data.decode().splitlines())
    relative = path.relative_to(SOURCE).as_posix()
    url = f"https://media.githubusercontent.com/media/openai/mle-bench/{COMMIT}/{relative}"
    for attempt in range(5):
        try:
            body = urllib.request.urlopen(url, timeout=120).read()
            assert len(body) == int(fields["size"]), relative
            assert hashlib.sha256(body).hexdigest() == fields["oid"].split(":")[1], relative
            break
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    path.write_bytes(body)
    print(f"Fetched {relative}", flush=True)


def main():
    if not SOURCE.exists():
        git_env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
        subprocess.run(["git", "clone", "--no-checkout", "https://github.com/openai/mle-bench.git", str(SOURCE)], check=True, env=git_env)
        subprocess.run(["git", "-C", str(SOURCE), "-c", "filter.lfs.required=false", "checkout", COMMIT], check=True, env=git_env)
    actual = subprocess.check_output(["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True).strip()
    if actual != COMMIT:
        raise SystemExit(f"Unexpected source revision {actual}; expected {COMMIT}")
    (ROOT / "source/UPSTREAM_COMMIT").write_text(COMMIT + "\n")
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(materialize, sorted((SOURCE / "mlebench/competitions").glob("*/leaderboard.csv"))))
    target = ROOT / "cache/build-context/mle-bench"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    shutil.copytree(SOURCE / "mlebench", target / "mlebench", ignore=shutil.ignore_patterns("top_solutions", "__pycache__", "*.pyc"))
    shutil.copytree(SOURCE / "experiments/splits", target / "experiments/splits")
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(SOURCE / name, target / name)
    print(f"Build context ready: {target}")


if __name__ == "__main__":
    main()
