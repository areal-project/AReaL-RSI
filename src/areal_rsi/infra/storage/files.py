"""Small atomic file operations; JSON encoding belongs to api.control."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_bytes(path: Path, data: bytes, *, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.is_symlink():
                    raise ValueError(f"Immutable record is symbolic: {path}") from None
                if path.read_bytes() != data:
                    raise ValueError(f"Immutable record changed: {path}") from None
        else:
            os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
