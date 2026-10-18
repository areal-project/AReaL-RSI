"""Checkpoint inspection shared by the search and TIR GPU workers."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def parameter_probe(model: Path, key: str | None = None) -> tuple[str, Any]:
    from safetensors import safe_open

    paths = sorted(model.glob("*.safetensors"))
    for path in paths:
        with safe_open(path, framework="pt", device="cpu") as reader:
            keys = list(reader.keys())
            selected = key if key in keys else None
            if key is None:
                selected = next((name for name in keys if name.endswith("q_proj.weight")), None)
            if selected is not None:
                return selected, reader.get_tensor(selected).flatten()[:2048].float().clone()
    raise ValueError(f"No matching parameter probe in {model}; this example uses safetensors")
