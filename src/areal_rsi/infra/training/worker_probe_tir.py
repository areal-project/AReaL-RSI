"""Check frozen RSI inputs from the Python environment of one GPU worker."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import socket
import sys
from pathlib import Path

from areal_rsi.api.control import validate_record
from areal_rsi.api.research import TirAssetPreflight, TirWorkerProbe
from areal_rsi.infra.storage.files import decode, json_bytes, write_bytes
from areal_rsi.infra.tasks.tir import load_tir_task_set
from areal_rsi.infra.tasks.tir_harness import load_tir_harness
from areal_rsi.infra.training.areal_tir import (
    checkpoint_digest,
    source_files_digest,
    tir_verifier_files,
    validate_areal_import_root,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Probe one TIR worker's source and assets")
    parser.add_argument("--expected-preflight", type=Path, required=True)
    parser.add_argument("--task-set-root", type=Path, required=True)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-backend", action="store_true")
    parser.add_argument("--require-gpu", action="store_true")
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.expected_preflight.is_symlink() or not args.expected_preflight.is_file():
        raise ValueError("Expected TIR preflight must be a regular file")
    expected_bytes = args.expected_preflight.read_bytes()
    expected_raw = json.loads(expected_bytes)
    validate_record(TirAssetPreflight, expected_raw)
    expected: TirAssetPreflight = decode(TirAssetPreflight, expected_raw)

    import areal  # type: ignore[import-not-found]
    import torch  # type: ignore[import-not-found]
    from areal.utils import pkg_version  # type: ignore[import-not-found]
    from examples.tir import tir_workflow  # type: ignore[import-not-found]

    runner_root = Path(tir_workflow.__file__).resolve().parent
    validate_areal_import_root(runner_root, Path(areal.__file__))
    task_set = load_tir_task_set(args.task_set_root)
    harness, _ = load_tir_harness(args.harness_root, runner_root)
    model_digest = checkpoint_digest(args.model_root)
    verifier_digest = source_files_digest(tir_verifier_files(runner_root))
    if (
        task_set.task_set_id != expected.task_set_id
        or harness.harness_id != expected.harness_id
        or harness.runner_digest != expected.runner_digest
        or model_digest != expected.model_digest
        or verifier_digest != expected.verifier_digest
    ):
        raise ValueError("TIR worker inputs differ from the frozen controller preflight")
    try:
        sglang_version = importlib.metadata.version("sglang")
    except importlib.metadata.PackageNotFoundError:
        sglang_version = None
    if args.require_backend:
        if sglang_version is None or not pkg_version.is_version_greater_or_equal(
            "sglang", "0.5.10.post1"
        ):
            raise RuntimeError("TIR worker needs sglang>=0.5.10.post1")
        importlib.import_module("sglang.srt.server_args")
    gpu_count = torch.cuda.device_count()
    if args.require_gpu and gpu_count < 1:
        raise RuntimeError("TIR worker has no visible CUDA GPU")
    receipt = TirWorkerProbe(
        socket.gethostname(),
        str(Path(sys.executable).resolve()),
        "sha256:" + hashlib.sha256(expected_bytes).hexdigest(),
        task_set.task_set_id,
        harness.harness_id,
        model_digest,
        harness.runner_digest,
        verifier_digest,
        sglang_version,
        gpu_count,
    )
    write_bytes(args.output, json_bytes(receipt), immutable=True)


if __name__ == "__main__":
    main(sys.argv[1:])
