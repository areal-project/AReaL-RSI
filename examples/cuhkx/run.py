"""Trusted CUHK-X training/inference wrapper.

The default operator runner is Docker. Candidate code sees only its frozen
snapshot, the selected public data split, and a dedicated output directory.
The host scorer reads private labels after the candidate container has stopped.
Use --allow-local only for explicitly synthetic CPU smoke tests; it is not an
isolation boundary and cannot produce a competition evaluation.

Commands:
  python examples/cuhkx/run.py train --candidate WORKSPACE --output JOB --config OPERATOR.json
  python examples/cuhkx/run.py verify --candidate SNAPSHOT --output JOB --config OPERATOR.json \
      --phase search

Collect train output's trained/ directory into the candidate before verification.
Candidate training.json can select standard seed parameters within operator
limits; custom train.py still owns training and must honor its CLI contract.
Real wall time, GPU access, CPU, memory and process limits are enforced externally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import uuid
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from areal_rsi.adapters.cuhkx import MAX_MODEL_BYTES, SCHEMA, score_predictions
from areal_rsi.execution._runner import terminate_group

_TRAIN_DEFAULTS: dict[str, int | float] = {
    "epochs": 10,
    "batch_size": 8,
    "frames": 8,
    "image_size": 64,
    "width": 16,
    "learning_rate": 0.001,
    "seed": 42,
    "threads": 2,
}
_TRAIN_LIMITS = {
    "epochs": 10,
    "batch_size": 128,
    "frames": 16,
    "image_size": 224,
    "width": 128,
    "threads": 4,
}
_DEADLINE_SCRIPT = """import os,signal,subprocess,sys
child = subprocess.Popen(sys.argv[2:], start_new_session=True)
try:
    code = child.wait(timeout=float(sys.argv[1]))
except subprocess.TimeoutExpired:
    os.killpg(child.pid, signal.SIGKILL)
    child.wait()
    code = 124
raise SystemExit(code)
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def regular_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Inputs must use paths inside their assigned directory")
    for index in range(1, len(path.parts) + 1):
        if root.joinpath(*path.parts[:index]).is_symlink():
            raise ValueError("Symbolic links are not allowed in execution inputs")
    target = root / path
    if not stat.S_ISREG(target.stat().st_mode):
        raise ValueError("Execution inputs must be regular files")
    return target


def candidate_digest(root: Path) -> tuple[str, int]:
    entries = []
    size = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Candidate snapshots cannot contain symbolic links")
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        regular_file(root, relative)
        size += path.stat().st_size
        if size > 256_000_000:
            raise ValueError("Candidate snapshot exceeds the 256 MB execution bound")
        entries.append({"path": relative, "sha256": sha256(path)})
    if not entries:
        raise ValueError("Candidate snapshot is empty")
    return digest_json(entries), size


def freeze_candidate(candidate: Path, destination: Path) -> tuple[str, int]:
    expected, size = candidate_digest(candidate)
    shutil.copytree(candidate, destination)
    actual, _ = candidate_digest(destination)
    if actual != expected or candidate_digest(candidate)[0] != expected:
        raise ValueError("Candidate changed while its execution snapshot was copied")
    for path in destination.rglob("*"):
        path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
    destination.chmod(0o555)
    return actual, size


def prepared_split(
    prepared: Path, split: str
) -> tuple[dict[str, Any], Path, dict[str, Any] | None]:
    if split not in {"train", "validate", "final"}:
        raise ValueError("Unknown prepared data split")
    protocol = json.loads(regular_file(prepared, "protocol.json").read_text())
    identity = protocol.get("protocol_id")
    if identity != digest_json(
        {key: value for key, value in protocol.items() if key != "protocol_id"}
    ):
        raise ValueError("Prepared protocol identity no longer matches its contents")
    if protocol.get("schema") != SCHEMA:
        raise ValueError("Unknown prepared data protocol")
    manifest = regular_file(prepared, split + "/manifest.json")
    if sha256(manifest) != protocol.get(split + "_manifest_sha256"):
        raise ValueError("Prepared manifest changed after the protocol was fixed")
    data = json.loads(manifest.read_text())
    if data.get("schema") != SCHEMA or not data.get("clips"):
        raise ValueError("Prepared manifest is empty or invalid")
    clip_ids = set()
    for clip in data["clips"]:
        expected = {"clip_id", "frames", "modality"} | ({"label"} if split == "train" else set())
        if set(clip) != expected or not re.fullmatch(r"clip_[0-9a-f]{24}", clip["clip_id"]):
            raise ValueError("Prepared inference inputs must be anonymous and contain no labels")
        if clip["clip_id"] in clip_ids or not clip["frames"]:
            raise ValueError("Prepared clips must be unique and contain frames")
        clip_ids.add(clip["clip_id"])
        for frame in clip["frames"]:
            source = regular_file(prepared / split, frame)
            if sha256(source) != protocol["image_sha256"].get(split + "/" + frame):
                raise ValueError("Prepared image changed after the protocol was fixed")
    labels = None
    if split != "train":
        labels = json.loads(regular_file(prepared, f"private/{split}_labels.json").read_text())
        if (
            labels.get("protocol_id") != identity
            or labels.get(split + "_manifest_sha256") != sha256(manifest)
            or set(labels.get("labels", {})) != clip_ids
            or labels.get("dataset_kind") != protocol.get("dataset_kind")
        ):
            raise ValueError("Private scorer labels do not belong to this input protocol")
    return protocol, manifest, labels


def training_parameters(candidate: Path, config: dict[str, Any]) -> dict[str, int | float]:
    settings = config.get("training", {})
    values = {**_TRAIN_DEFAULTS, **settings.get("defaults", {})}
    custom = candidate / "training.json"
    if custom.exists():
        values.update(json.loads(regular_file(candidate, "training.json").read_text()))
    if set(values) != set(_TRAIN_DEFAULTS):
        raise ValueError("training.json contains unsupported parameters")
    for name, default_limit in _TRAIN_LIMITS.items():
        limit = settings.get("max_" + name, default_limit)
        if type(limit) is not int or limit < 1:
            raise ValueError(f"Invalid operator limit for {name}")
        value = values[name]
        if type(value) is not int or not 1 <= value <= limit:
            raise ValueError(f"Requested {name} exceeds its operator limit {limit}")
    if type(values["seed"]) is not int or not 0 <= values["seed"] <= 2**32 - 1:
        raise ValueError("Training seed must fit an unsigned 32-bit integer")
    rate = values["learning_rate"]
    if not isinstance(rate, int | float) or not math.isfinite(rate) or not 0 < rate < 1:
        raise ValueError("learning_rate must be finite and between zero and one")
    return values


def _seconds(value: Any) -> float:
    if not isinstance(value, int | float) or not math.isfinite(value) or not 0 < value <= 604800:
        raise ValueError("A positive operator timeout of at most seven days is required")
    return float(value)


def _device(runner: dict[str, Any], phase_options: dict[str, Any]) -> str:
    device = phase_options.get("device", runner.get("device", "cuda:0"))
    if not isinstance(device, str) or not re.fullmatch(r"cpu|cuda:\d+", device):
        raise ValueError("Device must be cpu or cuda:<index>")
    return device


def docker_command(
    runner: dict[str, Any],
    *,
    image: str,
    name: str,
    candidate: Path,
    data: Path,
    output: Path,
    command: list[str],
    timeout: float,
    device: str,
) -> list[str]:
    """Only public split and dedicated output mounts; never mount prepared/private."""
    cpus = runner.get("cpus", 4)
    memory = runner.get("memory", "8g")
    pids = runner.get("pids_limit", 256)
    if not isinstance(cpus, int | float) or not math.isfinite(cpus) or cpus <= 0:
        raise ValueError("Docker CPU allocation must be positive and finite")
    if not isinstance(memory, str) or not re.fullmatch(r"[1-9]\d*[mMgG]", memory):
        raise ValueError("Docker memory must use a positive m/g limit")
    if type(pids) is not int or not 1 <= pids <= 65536:
        raise ValueError("Docker process limit must be positive")
    uid, gid = (os.getuid(), os.getgid()) if os.getuid() else (65534, 65534)
    if os.getuid() == 0:
        os.chown(output, uid, gid)
    argv = [
        runner.get("docker", "docker"),
        "run",
        "--rm",
        "--name",
        name,
        "--init",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(pids),
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--cpus",
        str(cpus),
        "--user",
        f"{uid}:{gid}",
        "--tmpfs",
        "/tmp:rw,nosuid,size=1g",
        "--workdir",
        "/candidate",
        "--env",
        "HOME=/tmp",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
    ]
    if device != "cpu":
        # JobManager's allocation wins over an operator default.
        visible = os.environ.get("CUDA_VISIBLE_DEVICES")
        if visible is None:
            visible = ",".join(str(item) for item in runner.get("gpu_ids", ()))
        if not re.fullmatch(r"\d+(?:,\d+)*", visible):
            raise ValueError("CUDA execution requires explicitly assigned numeric GPU IDs")
        if int(device.split(":")[1]) >= len(visible.split(",")):
            raise ValueError("CUDA device index exceeds the allocated GPU count")
        argv.extend(["--gpus", json.dumps("device=" + visible)])
    for source, target, readonly in (
        (candidate, "/candidate", True),
        (data, "/data", True),
        (output, "/output", False),
    ):
        if "," in str(source):
            raise ValueError("Docker bind paths cannot contain commas")
        mount = f"type=bind,src={source},dst={target}" + (",readonly" if readonly else "")
        argv.extend(["--mount", mount])
    argv.extend(
        [
            "--entrypoint",
            runner.get("python", "python"),
            image,
            "-c",
            _DEADLINE_SCRIPT,
            str(timeout),
            *command,
        ]
    )
    return argv


def execute(command: list[str], log: Path, timeout: float, *, cwd: Path) -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "HOME",
            "TMPDIR",
            "LD_LIBRARY_PATH",
            "DYLD_LIBRARY_PATH",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "SSH_AUTH_SOCK",
        }
    }
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    with log.open("xb") as stream:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=timeout)
            if code:
                raise subprocess.CalledProcessError(code, command)
        finally:
            terminate_group(process.pid)
            process.wait(timeout=3)


def phase_split(config: dict[str, Any], phase: str) -> str:
    if phase not in {"search", "final", "diagnostic"}:
        raise ValueError("Unknown verification phase")
    mapping = config.get(
        "phases", {"search": "validate", "diagnostic": "validate", "final": "final"}
    )
    split = mapping.get(phase)
    if split not in {"validate", "final"} or (phase == "final" and split != "final"):
        raise ValueError(
            "Phase must select its fixed anonymous split; final cannot reuse validation"
        )
    return str(split)


def execution_environment(
    config: dict[str, Any], operator_config: Path, action: str
) -> dict[str, Any]:
    runner = config.get("runner", {})
    kind = runner.get("kind", "docker")
    options = config.get("training" if action == "train" else "verification", {})
    image = None
    if kind == "docker":
        if not runner.get("image"):
            raise ValueError("An operator-owned Docker image with torch/Pillow/numpy is required")
        image = subprocess.check_output(
            [
                runner.get("docker", "docker"),
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                runner["image"],
            ],
            text=True,
            timeout=20,
        ).strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ValueError("Docker must resolve the configured image to an immutable image ID")
    elif kind != "local":
        raise ValueError("Use the Docker runner or explicit synthetic local smoke")
    return {
        "runner": kind,
        "image_id": image,
        "device": _device(runner, options),
        "timeout_seconds": _seconds(
            options.get("timeout_seconds", 3600 if action == "train" else 600)
        ),
        "operator_config_sha256": sha256(operator_config),
    }


def protocol_identity(
    protocol: dict[str, Any], labels: dict[str, Any] | None, split: str, environment: dict[str, Any]
) -> str:
    return digest_json(
        {
            "prepared_protocol_id": protocol["protocol_id"],
            "split": split,
            "labels_sha256": digest_json(labels) if labels else None,
            "environment": environment,
        }
    )


def describe_identity(operator_config: Path, phase: str = "search") -> dict[str, Any]:
    """Compute expected verifier identity from trusted inputs before a job runs.

    Docker image tags are resolved to immutable local image IDs. Call this on the
    configured Docker host; it executes no candidate code and reads no checkpoint.
    """
    operator_config = operator_config.resolve()
    config = json.loads(operator_config.read_text())
    split = phase_split(config, phase)
    protocol, manifest, labels = prepared_split(
        Path(config["prepared_data"]).expanduser().resolve(), split
    )
    environment = execution_environment(config, operator_config, "verify")
    return {
        "adapter": "cuhkx",
        "phase": phase,
        "split": split,
        "protocol_id": protocol_identity(protocol, labels, split, environment),
        "prepared_protocol_id": protocol["protocol_id"],
        "manifest_id": sha256(manifest),
        "dataset_kind": protocol["dataset_kind"],
        "environment": environment,
    }


def run(
    action: str,
    candidate: Path,
    output: Path,
    operator_config: Path,
    phase: str = "search",
    *,
    allow_local: bool = False,
) -> dict[str, Any]:
    candidate, output = candidate.resolve(), output.resolve()
    operator_config = operator_config.resolve()
    if action not in {"train", "verify"} or phase not in {"search", "final", "diagnostic"}:
        raise ValueError("Expected train/verify and search/final/diagnostic phase")
    if operator_config.is_relative_to(candidate) or output.is_relative_to(candidate):
        raise ValueError("Operator configuration and trusted output must be outside the candidate")
    config = json.loads(operator_config.read_text())
    prepared = Path(config["prepared_data"]).expanduser().resolve()
    if prepared.is_relative_to(candidate):
        raise ValueError("Prepared private data must be outside the candidate")
    split = "train" if action == "train" else phase_split(config, phase)
    protocol, manifest, labels = prepared_split(prepared, split)
    runner = config.get("runner", {})
    kind = runner.get("kind", "docker")
    if kind == "local" and not (allow_local and protocol.get("dataset_kind") == "synthetic"):
        raise ValueError(
            "Local candidate execution is permitted only with --allow-local on synthetic data"
        )
    if kind not in {"docker", "local"}:
        raise ValueError("Use the Docker runner or explicit synthetic local smoke")
    options = config.get("training" if action == "train" else "verification", {})
    timeout = _seconds(options.get("timeout_seconds", 3600 if action == "train" else 600))
    device = _device(runner, options)
    if kind == "local" and device != "cpu":
        raise ValueError("--allow-local is limited to synthetic CPU smoke")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "started.json", {"action": action, "phase": phase})
    private = output / "private"
    private.mkdir(mode=0o700)
    snapshot = private / "candidate"
    identity, package_size = freeze_candidate(candidate, snapshot)
    regular_file(snapshot, "train.py" if action == "train" else "predict.py")
    if action == "verify":
        checkpoint = regular_file(snapshot, "trained/checkpoint.pt")
        if package_size > MAX_MODEL_BYTES or not 0 < checkpoint.stat().st_size <= MAX_MODEL_BYTES:
            raise ValueError("The entire inference candidate package must be at most 100 MB")
    work = private / "worker-output"
    work.mkdir(mode=0o700)
    environment = execution_environment(config, operator_config, action)
    image = environment["image_id"]
    input_candidate = Path("/candidate") if kind == "docker" else snapshot
    input_manifest = Path("/data/manifest.json") if kind == "docker" else manifest
    inner_output = Path("/output") if kind == "docker" else work
    python = runner.get("python", "python" if kind == "docker" else sys.executable)
    parameters = training_parameters(snapshot, config) if action == "train" else {}
    command = [
        python,
        str(input_candidate / ("train.py" if action == "train" else "predict.py")),
        "--manifest",
        str(input_manifest),
        "--output",
        str(inner_output / ("trained" if action == "train" else "predictions.csv")),
        "--device",
        device,
    ]
    if action == "train":
        command.extend(
            part
            for key, value in parameters.items()
            for part in ("--" + key.replace("_", "-"), str(value))
        )
    else:
        command.extend(
            [
                "--checkpoint",
                str(input_candidate / "trained/checkpoint.pt"),
                "--batch-size",
                str(options.get("batch_size", 8)),
                "--threads",
                str(options.get("threads", 2)),
            ]
        )
    evaluation_identity = protocol_identity(protocol, labels, split, environment)
    result: dict[str, Any] = {
        "adapter": "cuhkx",
        "status": "incomplete",
        "action": action,
        "phase": phase,
        "split": split,
        "metric": "accuracy",
        "score": None,
        "metrics": {},
        "simulated": False,
        "dataset_kind": protocol["dataset_kind"],
        "candidate_sha256": identity,
        "protocol_id": evaluation_identity,
        "prepared_protocol_id": protocol["protocol_id"],
        "manifest_id": sha256(manifest),
        "environment": environment,
    }
    name = "rsi-cuhkx-" + uuid.uuid4().hex
    try:
        if kind == "docker":
            assert image is not None
            command = docker_command(
                runner,
                image=image,
                name=name,
                candidate=snapshot,
                data=manifest.parent,
                output=work,
                command=command,
                timeout=timeout,
                device=device,
            )
        execute(
            command,
            output / "execution.log",
            timeout + (15 if kind == "docker" else 0),
            cwd=private,
        )
        if candidate_digest(snapshot)[0] != identity:
            raise ValueError("Candidate snapshot changed during execution")
        # Recheck selected immutable inputs after execution, before host scoring.
        current_protocol, _manifest, current_labels = prepared_split(prepared, split)
        if current_protocol != protocol or current_labels != labels:
            raise ValueError("Prepared data or private labels changed during execution")
        if action == "train":
            trained = work / "trained"
            produced = regular_file(trained, "checkpoint.pt")
            if not 0 < produced.stat().st_size <= MAX_MODEL_BYTES:
                raise ValueError("Training checkpoint exceeds the 100 MB model limit")
            candidate_digest(trained)  # Reject symlinks/FIFOs before collection.
            shutil.copytree(trained, output / "trained")
            result.update(
                status="completed",
                checkpoint_sha256=sha256(produced),
                training_parameters=parameters,
                artifact="trained",
            )
        else:
            prediction = regular_file(work, "predictions.csv")
            if prediction.stat().st_size > 10_000_000:
                raise ValueError("Prediction CSV exceeds the bounded scorer input size")
            shutil.copyfile(prediction, private / "predictions.csv")
            scored = score_predictions(
                private / "predictions.csv", prepared / f"private/{split}_labels.json", checkpoint
            )
            result.update(
                status="completed",
                score=scored.accuracy,
                metrics={
                    "accuracy": scored.accuracy,
                    "correct": scored.correct,
                    "total": scored.total,
                    "model_bytes": package_size,
                },
                checkpoint_sha256=scored.checkpoint_sha256,
                predictions_sha256=scored.predictions_sha256,
            )
            write_json(private / "score.json", asdict(scored))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # A failed command is not a zero score; never consume its stdout metrics.
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        if kind == "docker":
            # A separate cleanup process can finish even if an outer supervisor
            # terminates this wrapper while Docker is stopping the container.
            cleanup = subprocess.Popen(
                [runner.get("docker", "docker"), "rm", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            with suppress(subprocess.TimeoutExpired):
                cleanup.wait(timeout=10)
    write_json(output / ("evaluation.json" if action == "verify" else "training-run.json"), result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["train", "verify", "describe"])
    parser.add_argument("--candidate", "--workspace", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--phase", choices=["search", "final", "diagnostic"], default="search")
    parser.add_argument("--allow-local", action="store_true")
    args = parser.parse_args()
    if args.action == "describe":
        print(json.dumps(describe_identity(args.config, args.phase), allow_nan=False))
        return
    if args.candidate is None or args.output is None:
        parser.error("train/verify require --candidate and --output")

    def interrupted(_signal: int, _frame: Any) -> None:
        raise KeyboardInterrupt("Execution cancelled")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        result = run(
            args.action,
            args.candidate,
            args.output,
            args.config,
            args.phase,
            allow_local=args.allow_local,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        args.output.mkdir(parents=True, exist_ok=True)
        result = {
            "adapter": "cuhkx",
            "status": "incomplete",
            "metric": "accuracy",
            "score": None,
            "metrics": {},
            "simulated": False,
            "phase": args.phase,
            "candidate_sha256": None,
            "protocol_id": None,
            "manifest_id": None,
            "error": f"{type(error).__name__}: {error}",
        }
        target = args.output / (
            "evaluation.json" if args.action == "verify" else "training-run.json"
        )
        if not target.exists():
            write_json(target, result)
    print(json.dumps(result, allow_nan=False))
    raise SystemExit(0 if result["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
