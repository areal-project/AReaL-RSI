"""Resolve experiment roots consistently before starting or attaching to a worker."""

from __future__ import annotations

import os
from pathlib import Path


def data_root(root: Path | None = None) -> Path:
    """The Research CLI, launcher and preflight share this default and precedence."""
    selected = root or Path(os.environ.get("RSI_DATA_ROOT") or ".rsi/research-lab")
    return selected.expanduser().resolve()
