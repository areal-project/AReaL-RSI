"""Artifact storage helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ArtifactStore:
    """Small file-backed artifact helper for run outputs."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, name: str, payload: dict[str, Any] | list[Any]) -> Path:
        path = self.path(name)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=repr), encoding="utf-8")
        return path

    def read_json(self, name: str) -> Any:
        return json.loads(self.path(name).read_text(encoding="utf-8"))

    def write_text(self, name: str, text: str) -> Path:
        path = self.path(name)
        path.write_text(text, encoding="utf-8")
        return path

    def read_text(self, name: str) -> str:
        return self.path(name).read_text(encoding="utf-8")
