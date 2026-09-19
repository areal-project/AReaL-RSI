"""Rebuild the pinned Runtime wheel with the reviewable native timing patch."""

import base64
import csv
import hashlib
import io
import subprocess
import tempfile
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent
old = "0.4.0.dev1"
new = old + "+rsi1"
upstream = root / f"areal_agent_runtime-{old}-py3-none-any.whl"
target = root / f"areal_agent_runtime-{new}-py3-none-any.whl"
with tempfile.TemporaryDirectory() as tmp:
    directory = Path(tmp)
    with zipfile.ZipFile(upstream) as archive:
        archive.extractall(directory)
    subprocess.run(
        ["patch", "-p0", "-i", str(root / "codex-availability.patch")], cwd=directory, check=True
    )
    dist = directory / f"areal_agent_runtime-{new}.dist-info"
    (directory / f"areal_agent_runtime-{old}.dist-info").rename(dist)
    metadata = dist / "METADATA"
    metadata.write_text(metadata.read_text().replace(f"Version: {old}\n", f"Version: {new}\n"))
    (dist / "RECORD").unlink()
    records = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            name, data = path.relative_to(directory).as_posix(), path.read_bytes()
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 18, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            records.append((name, "sha256=" + digest, str(len(data))))
        record = f"{dist.name}/RECORD"
        buffer = io.StringIO(newline="")
        csv.writer(buffer).writerows([*records, (record, "", "")])
        archive.writestr(
            zipfile.ZipInfo(record, date_time=(2026, 9, 18, 0, 0, 0)), buffer.getvalue()
        )
print(target.name, hashlib.sha256(target.read_bytes()).hexdigest())
