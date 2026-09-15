"""Resolve runtime assets separately from version-controlled deployment code."""
import os
from pathlib import Path


def runtime_root(script):
    script = Path(script).resolve()
    task = script.parents[1].name
    default = script.parents[5] / "scale_rsi_bench"
    base = Path(os.environ.get("RSI_WORK_ROOT") or default)
    return Path(os.environ.get("RSI_ROOT") or base / task).resolve()
