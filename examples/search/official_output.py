"""Spool unchanged official H0 outputs locally and publish verified artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from areal_rsi.api.records import ArtifactFile, DirectorySnapshot
from areal_rsi.infra.artifacts import file_ref, read_record, write_record
from examples.search.final_evaluation import FinalEvalConfig


@dataclass(frozen=True, slots=True)
class OutputState:
    job_id: str
    config: str
    config_sha256: str
    local_output: str
    shared_output: str
    phase: Literal["running", "inference-failed", "inference-complete", "published"]
    return_code: int | None = None


@dataclass(frozen=True, slots=True)
class CopyProgress:
    copied_files: int
    deferred_files: int


def copy_bytes(content: bytes, destination: Path) -> None:
    """A failed shared write must leave a previous valid file intact."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".copy-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def mirror_cases(state: OutputState, copied: set[str]) -> CopyProgress:
    """Copy completed solver JSON only; never expose an early judge/result file."""
    source, destination = Path(state.local_output), Path(state.shared_output)
    deferred = 0
    for path in (source / "runs").rglob("*.json"):
        relative = str(path.relative_to(source))
        if relative in copied:
            continue
        try:
            content = path.read_bytes()
            json.loads(content)
            copy_bytes(content, destination / relative)
        except (OSError, ValueError):
            # A still-open upstream JSON or unavailable shared mount is retried.
            # The inference subprocess uses local files and keeps running.
            deferred += 1
        else:
            copied.add(relative)
    return CopyProgress(len(copied), deferred)


def publish(state: OutputState) -> None:
    """Publish result.json last, after exact-byte verification of every artifact."""
    if state.phase not in {"inference-complete", "published"} or state.return_code != 0:
        raise ValueError("Only a successfully completed frozen H0 run can be published")
    config_path = Path(state.config)
    if file_ref(config_path).digest != state.config_sha256:
        raise ValueError("Local input configuration changed")
    config = read_record(config_path, FinalEvalConfig)
    source, destination = Path(state.local_output), Path(state.shared_output)
    if config.output != str(destination):
        raise ValueError("Publication destination differs from the recorded configuration")
    if read_record(source / "config.json", FinalEvalConfig) != config:
        raise ValueError("Frozen runner used a different configuration")
    result = json.loads((source / "result.json").read_bytes())
    if result.get("questions") != 830:
        raise ValueError("Official H0 did not complete all 830 questions")
    entries = []
    paths = sorted(path for path in source.rglob("*") if path.is_file())
    for path in paths:
        if path.name.endswith(".tmp"):
            continue
        relative = path.relative_to(source)
        reference = file_ref(path)
        entries.append(ArtifactFile(str(relative), path.stat().st_size, reference.digest))
        if relative == Path("result.json"):
            continue
        target = destination / relative
        if not target.exists() or file_ref(target).digest != reference.digest:
            copy_bytes(path.read_bytes(), target)
        if file_ref(target).digest != reference.digest:
            raise ValueError(f"Shared output differs from local artifact: {relative}")
    write_record(
        destination / "replication-manifest.json",
        DirectorySnapshot(str(destination), tuple(entries)),
    )
    write_record(destination / "output-execution.json", replace(state, phase="published"))
    # This atomic replacement is the commit marker visible to downstream scorers.
    copy_bytes((source / "result.json").read_bytes(), destination / "result.json")
    if file_ref(destination / "result.json").digest != file_ref(source / "result.json").digest:
        raise ValueError("Published official result bytes differ")


def run(config_path: Path, runtime: Path, image: Path, work_root: Path) -> Path:
    job_id = os.environ.get("SLURM_JOB_ID", "")
    allocation = set(os.environ.get("SEARCH_SLURM_JOB_INFO", "").split())
    if not job_id.isdigit() or {f"JobId={job_id}", "Reservation=rsi"} - allocation:
        raise ValueError("Local spooling requires an owned host-verified rsi allocation")
    config = read_record(config_path, FinalEvalConfig)
    destination = Path(config.output)
    parent = destination.parent.resolve(strict=True)
    if not destination.is_absolute() or destination != parent / destination.name:
        raise ValueError("Output must use an absolute canonical artifact path")
    if destination.exists():
        raise ValueError("Fresh inference cannot overwrite existing output; use --publish-only")
    # Only output placement changes. Every input remains at its frozen path.
    for name in (
        "benchmark_repository",
        "historical_environment",
        "data_root",
        "model",
        "base_model",
        "embedding_model",
        "judge_model",
        "checkpoint_manifest",
        "asset_lock",
        "qwen_overlay",
    ):
        if Path(getattr(config, name)).resolve().is_relative_to(parent):
            raise ValueError(f"Output bind would hide a frozen input: {name}")
    entrypoint = runtime.resolve(strict=True) / "examples/search/final_evaluation.py"
    if not entrypoint.is_file() or not image.is_file():
        raise ValueError("Missing immutable H0 runtime or image")
    work = work_root.resolve(strict=True) / f"search-rsi-official-{job_id}"
    work.mkdir(exist_ok=False)
    local_parent = work / "official"
    local_parent.mkdir()
    local_config = work / "input-config.json"
    local_config.write_bytes(config_path.read_bytes())
    state = OutputState(
        job_id,
        str(local_config),
        file_ref(local_config).digest,
        str(local_parent / destination.name),
        str(destination),
        "running",
    )
    state_path = work / "state.json"
    write_record(state_path, state)
    script = work / "inside.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy "
        "RSI_API_KEY OPENAI_API_KEY SEARCH_PROXY_ADMIN_KEY\n"
        "export NO_PROXY='*' no_proxy='*' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1\n"
        "export PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=8 TOKENIZERS_PARALLELISM=false\n"
        f"export PYTHONPATH={shlex.quote(str(runtime.resolve()))}\ncd /\nexec "
        + shlex.join([config.vllm_python, "-u", str(entrypoint), "--config", str(local_config)])
        + "\n"
    )
    command = [
        "srun",
        "--mpi=none",
        "env",
        "-u",
        "LD_LIBRARY_PATH",
        "apptainer",
        "exec",
        "--no-eval",
        "--nv",
        "--bind",
        "/storage:/storage",
        "--bind",
        f"{local_parent}:{parent}",
        "--bind",
        f"{work}:{work}",
        str(image),
        "bash",
        str(script),
    ]
    copied: set[str] = set()
    with (work / "driver.log").open("wb") as log:
        process = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            while process.poll() is None:
                progress = mirror_cases(state, copied)
                write_record(work / "copy-progress.json", progress)
                time.sleep(15)
            code = process.wait()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    state = replace(
        state, phase="inference-complete" if code == 0 else "inference-failed", return_code=code
    )
    write_record(state_path, state)
    mirror_cases(state, copied)
    if code != 0:
        raise RuntimeError(f"Frozen H0 failed; local artifacts retained at {work}")
    # A publication failure retains this complete local source and state for a
    # later CPU-only --publish-only; it must never regenerate model answers.
    publish(state)
    write_record(state_path, replace(state, phase="published"))
    return state_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--work-root", type=Path, default=Path("/tmp"))
    parser.add_argument("--publish-only", type=Path, help="Completed local state.json; uses no GPU")
    args = parser.parse_args()
    if args.publish_only:
        if args.config or args.runtime or args.image:
            parser.error("Publication recovery accepts only the existing local state")
        state = read_record(args.publish_only, OutputState)
        publish(state)
        write_record(args.publish_only, replace(state, phase="published"))
    else:
        if not args.config or not args.runtime or not args.image:
            parser.error("Inference requires --config, --runtime and --image")
        run(args.config.resolve(strict=True), args.runtime, args.image, args.work_root)


if __name__ == "__main__":
    main()
