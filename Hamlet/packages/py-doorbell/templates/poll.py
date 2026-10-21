#!/usr/bin/env doorbell-python
"""Poll a directory; persist cursor only after a successful ring."""
import os
from pathlib import Path
from doorbell import ring, state, every

@every(seconds=60)
def check():
    directory = Path(os.environ.get("WATCH_DIR", "."))
    seen = state.get("seen", {})
    for path in directory.glob("*.txt"):
        revision = str(path.stat().st_mtime_ns)
        if seen.get(str(path)) != revision:
            ring(vars={"file": str(path.resolve())}, dedup_key=f"{path}:{revision}")
            seen[str(path)] = revision
            state.set("seen", seen)
