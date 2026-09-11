"""Rebuild and validate the official pool using the project's downloaded cache."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

root = Path(os.environ["RSI_ROOT"])
pool = root / "pool"
task = root / "source/tasks/nano-gpt-data-curation"
expected = "01b71c620642b8e8318d665d8de6a9988eaec1596586e8d7ee63602d605bc187"
required = [pool / name for name in ("pool_tokens.npy", "pool_meta.npy", "pool.jsonl", "pool_tokens.sha256")]
if not all(p.is_file() for p in required):
    subprocess.run([sys.executable, str(task / "environment/build_pool.py"),
                    "--out", str(pool), "--emit-meta"], check=True,
                   env={**os.environ, "TOKENIZERS_PARALLELISM": "true",
                        "RAYON_NUM_THREADS": os.environ.get("OMP_NUM_THREADS", "8")})
with (pool / "pool_tokens.npy").open("rb") as f:
    assert hashlib.file_digest(f, "sha256").hexdigest() == expected, "Pool token hash mismatch"
for line in (task / "tests/SHA256SUMS").read_text().splitlines():
    digest, name = line.split()
    asset = pool / name if name.startswith("pool_") else task / "tests" / name
    with asset.open("rb") as f:
        assert hashlib.file_digest(f, "sha256").hexdigest() == digest, f"Verifier hash mismatch: {name}"
destination = root / "workspace/data/pool.jsonl"
if destination.exists():
    with destination.open("rb") as a, (pool / "pool.jsonl").open("rb") as b:
        assert hashlib.file_digest(a, "sha256").digest() == hashlib.file_digest(b, "sha256").digest()
else:
    # Both container paths remain real files, sharing one copy on disk.
    os.link(pool / "pool.jsonl", destination)
print("All official verifier asset hashes verified; /workspace/data/pool.jsonl ready.")
