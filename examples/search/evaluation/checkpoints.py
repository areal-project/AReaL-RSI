"""Export HF5 checkpoints for the frozen HF4 inference environment."""

from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path

from examples.search.evaluation.artifacts import (
    CheckpointExportReceipt,
    digest,
)


def export_checkpoint(source_manifest: Path, output: Path, *, source_sha256: str) -> Path:
    """Export HF5 Qwen3 metadata for HF4 inference without changing weight bytes.

    HF5 moves RoPE theta into rope_parameters. HF4 otherwise silently uses
    Qwen3's default theta, which differs from the trained/base model's theta.
    Keep both representations, and retain the original checkpoint unchanged.
    """
    from areal_rsi.api.records import DirectorySnapshot
    from areal_rsi.infra.storage.artifacts import read_record, snapshot_directory, write_record

    def verify(snapshot: DirectorySnapshot) -> None:
        for entry in snapshot.files:
            relative = Path(entry.path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Checkpoint manifest requires relative file paths")
            path = Path(snapshot.root) / relative
            if path.stat().st_size != entry.size or digest(path) != entry.sha256:
                raise ValueError(f"Checkpoint file changed: {entry.path}")

    if digest(source_manifest) != source_sha256:
        raise ValueError("Selected checkpoint manifest changed")
    source = read_record(source_manifest, DirectorySnapshot)
    verify(source)
    root = Path(source.root).resolve(strict=True)
    original = json.loads((root / "config.json").read_text())
    if original.get("model_type") != "qwen3":
        raise ValueError("This export supports the Qwen3 checkpoint format")
    effective = dict(original)
    parameters = original.get("rope_parameters")
    if parameters is not None:
        if not isinstance(parameters, dict) or "rope_theta" not in parameters:
            raise ValueError("Expected flat Qwen3 RoPE parameters with an explicit theta")
        theta = parameters["rope_theta"]
        if type(theta) not in {int, float} or not math.isfinite(theta) or theta <= 0:
            raise ValueError("Invalid saved RoPE theta")
        scaling = {key: value for key, value in parameters.items() if key != "rope_theta"}
        for key, value in (("rope_theta", theta), ("rope_scaling", scaling)):
            if original.get(key) is not None and original[key] != value:
                raise ValueError("Conflicting modern and legacy RoPE metadata")
            effective[key] = value
    if effective.get("rope_theta") is None:
        raise ValueError("Cannot infer a missing RoPE theta from a library default")
    dtype = original.get("dtype") or original.get("torch_dtype")
    if dtype not in {"bfloat16", "float16", "float32"}:
        raise ValueError("Checkpoint requires an explicit supported dtype")
    if original.get("torch_dtype") not in {None, dtype}:
        raise ValueError("Conflicting modern and legacy dtype metadata")
    effective["torch_dtype"] = dtype
    weights = {
        entry.path: entry.sha256 for entry in source.files if entry.path.endswith(".safetensors")
    }
    if not weights:
        raise ValueError("Checkpoint manifest has no model weights")
    output = output.resolve()
    if output.is_relative_to(root):
        raise ValueError("Inference export must be outside the original checkpoint")
    manifest = output.with_suffix(".manifest.json")
    receipt_path = output.with_suffix(".export.json")
    if output.exists():
        receipt = read_record(receipt_path, CheckpointExportReceipt)
        if receipt.source_sha256 != source_sha256 or digest(manifest) != receipt.export_sha256:
            raise ValueError("Existing inference export has different content")
        verify(read_record(manifest, DirectorySnapshot))
        if json.loads((output / "config.json").read_text()) != effective:
            raise ValueError("Existing inference export has different model metadata")
        return manifest
    if manifest.exists() or receipt_path.exists():
        raise ValueError("Inference export records already exist")
    output.mkdir(parents=True)
    for entry in source.files:
        target = output / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.path == "config.json":
            target.write_text(json.dumps(effective, indent=2) + "\n")
        elif entry.path in weights:
            try:
                os.link(root / entry.path, target)
            except OSError:
                shutil.copy2(root / entry.path, target)
        else:
            shutil.copy2(root / entry.path, target)
    reference = snapshot_directory(output, manifest)
    exported = read_record(manifest, DirectorySnapshot)
    exported_weights = {
        entry.path: entry.sha256 for entry in exported.files if entry.path in weights
    }
    if exported_weights != weights:
        raise ValueError("Inference export changed model weights")
    write_record(
        receipt_path,
        CheckpointExportReceipt(
            str(source_manifest.resolve()),
            source_sha256,
            str(manifest),
            reference.digest,
            tuple(sorted(weights)),
            tuple(key for key in effective if effective.get(key) != original.get(key)),
        ),
    )
    return manifest


def stage_model(source: Path, output: Path) -> Path:
    """Match historical inference's explicit YaRN config without modifying weights."""
    source = source.resolve(strict=True)
    effective = json.loads((source / "config.json").read_text())
    effective["max_position_embeddings"] = 131072
    effective["rope_scaling"] = {
        "rope_type": "yarn",
        "factor": 4.0,
        "original_max_position_embeddings": 32768,
    }
    if output.exists():
        if json.loads((output / "config.json").read_text()) != effective:
            raise ValueError("Existing staged model has a different effective configuration")
        for path in source.iterdir():
            if path.is_file() and path.name != "config.json":
                staged = output / path.name
                if not staged.is_symlink() or staged.resolve() != path:
                    raise ValueError(
                        "Existing staged model is incomplete or has a different source"
                    )
        return output
    output.mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file() and path.name != "config.json":
            (output / path.name).symlink_to(path)
    (output / "config.json").write_text(json.dumps(effective, indent=2) + "\n")
    return output
