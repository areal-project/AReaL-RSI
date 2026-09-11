"""Validate installed wheel RECORD sizes/hashes, not merely pip's exit code."""
import base64
import csv
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["RSI_ROOT"])
checked = 0
failures = []
for dist in metadata.distributions():
    record = dist.read_text("RECORD")
    if not record:
        failures.append(f"{dist.metadata.get('Name', '?')}: missing RECORD")
        continue
    for name, digest, size in csv.reader(record.splitlines()):
        if not digest:
            continue
        path = Path(dist.locate_file(name))
        try:
            if size and path.stat().st_size != int(size):
                raise ValueError(f"size {path.stat().st_size} != {size}")
            algorithm, expected = digest.split("=", 1)
            with path.open("rb") as stream:
                actual = base64.urlsafe_b64encode(hashlib.file_digest(stream, algorithm).digest()).decode().rstrip("=")
            if actual != expected:
                raise ValueError("hash mismatch")
            checked += 1
        except (OSError, ValueError) as error:
            failures.append(f"{path}: {error}")
report = {"checked_files": checked, "failures": failures, "passed": not failures}
(root / "logs/dependency-integrity.json").write_text(json.dumps(report, indent=2) + "\n")
print(f"DEPENDENCY INTEGRITY: {checked} verified, {len(failures)} failures", flush=True)
for error in failures[:20]:
    print(error)
sys.exit(bool(failures))
