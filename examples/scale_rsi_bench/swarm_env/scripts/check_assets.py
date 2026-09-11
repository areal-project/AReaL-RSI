"""Check the exact frozen assets referenced by the upstream verifier."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
ROOT = Path(os.environ["RSI_ROOT"])
cfg = json.loads((ROOT / "config.json").read_text())
task = ROOT / "source/tasks" / cfg["task"]
count = 0
for digest, name in re.findall(r"^([0-9a-f]{64})[ \t]+(/[^\s]+)", (task / "tests/test.sh").read_text(), re.M):
    if name.startswith("/opt/models/"):
        path = ROOT / "models" / name.removeprefix("/opt/models/")
    elif name.startswith("/opt/hf_cache/"):
        path = ROOT / "cache/huggingface" / name.removeprefix("/opt/hf_cache/")
    elif name.startswith("/tests/"):
        path = task / "tests" / name.removeprefix("/tests/")
    else:
        continue
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    assert actual == digest, f"Frozen asset hash mismatch: {name}"
    count += 1
if cfg["task"] == "agent-swarm-optimization":
    subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=task / "tests", check=True)
assert (ROOT / "models" / cfg["model"].split("/")[-1] / "config.json").is_file()
model_files = 0
for path in (ROOT / "models" / cfg["model"].split("/")[-1]).iterdir():
    if not path.is_file():
        continue
    blob = path.resolve()
    if re.fullmatch(r"[0-9a-f]{64}", blob.name):
        with path.open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == blob.name, f"Corrupt HF blob: {path}"
        model_files += 1
    elif re.fullmatch(r"[0-9a-f]{40}", blob.name):
        digest = hashlib.sha1(f"blob {path.stat().st_size}".encode() + bytes([0]))
        with path.open("rb") as stream:
            while data := stream.read(1024 * 1024):
                digest.update(data)
        assert digest.hexdigest() == blob.name, f"Corrupt HF git blob: {path}"
        model_files += 1
print(f"MODEL BLOB CHECK PASS ({model_files} files)")
print(f"ASSET CHECK PASS ({count} embedded verifier hashes)")
