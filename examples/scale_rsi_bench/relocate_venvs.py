"""Mechanical relocation of venv entrypoints and their wheel RECORD metadata."""
import csv
import base64
import hashlib
import io
import os
from pathlib import Path

root = Path(os.environ.get("RSI_WORK_ROOT", Path(__file__).resolve().parents[3] / "scale_rsi_bench")).resolve()
old = str(root.parent).encode() + b"/"
new = str(root).encode() + b"/"
for name in ("nanogpt_env", "opsd_env", "jailbreak_env", "swarm_env"):
    venv = root / name / ".venv"
    changed = set()
    candidates = list((venv / "bin").iterdir())
    candidates += list(venv.glob("lib/python*/site-packages/*.dist-info/direct_url.json"))
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        # Match only old environment paths, making this operation idempotent.
        updated = data
        for env in ("nanogpt_env", "opsd_env", "jailbreak_env", "swarm_env"):
            updated = updated.replace(old + env.encode() + b"/", new + env.encode() + b"/")
        if updated != data:
            if b"\0" in data:
                raise RuntimeError(f"Refusing to rewrite binary: {path}")
            path.write_bytes(updated)
            changed.add(path.resolve())
        elif any(new + env.encode() + b"/" in data for env in ("nanogpt_env", "opsd_env", "jailbreak_env", "swarm_env")):
            changed.add(path.resolve())
    records = 0
    for record in venv.glob("lib/python*/site-packages/*.dist-info/RECORD"):
        rows = list(csv.reader(io.StringIO(record.read_text())))
        touched = False
        for row in rows:
            path = Path(os.path.abspath(record.parent.parent / row[0]))
            if path in changed:
                data = path.read_bytes()
                row[1] = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
                row[2] = str(len(data))
                touched = True
        if touched:
            output = io.StringIO(newline="")
            csv.writer(output).writerows(rows)
            record.write_text(output.getvalue())
            records += 1
    print(f"{name}: relocated {len(changed)} entrypoint/metadata files; updated {records} RECORDs")
