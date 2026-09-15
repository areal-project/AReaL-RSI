"""Trusted harness verifier: fixed-target DSH -> GPU evaluation -> evaluation.json.

This controller entry runs in the RSI environment. Only evaluate.py runs in the
separate Python 3.10 GPU environment. An operator-owned configuration selects the
task pools, fixed budget and local/SSH GPU runner.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from areal_rsi.adapters.kernelbench import (
    aggregate_results,
    check_edit_scope,
    generate,
    mock_results,
    read_manifest,
)


def gpu_arguments(config):
    options = {
        "num_correct_trials": 5,
        "num_perf_trials": 100,
        "timeout": 300,
        "backend": "cuda",
        "precision": "fp32",
        "timing_method": "cuda_event",
        "seed": 42,
    }
    unknown = set(config) - set(options)
    if unknown:
        raise ValueError(f"Unknown GPU evaluation options: {sorted(unknown)}")
    options.update(config)
    return [
        part
        for key, value in options.items()
        for part in ("--" + key.replace("_", "-"), str(value))
    ]


def evaluate_local(gpu, manifest, generation, result, options):
    command = [
        gpu["python"],
        str(Path(__file__).with_name("evaluate.py")),
        "--repository",
        gpu["repository"],
        "--manifest",
        str(manifest),
        "--generation",
        str(generation),
        "--output",
        str(result),
        *options,
    ]
    subprocess.run(command, check=True)


def ssh_location(gpu):
    host, remote_root = gpu["host"], gpu["root"]
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", host):
        raise ValueError("Use an SSH config host alias or user@hostname")
    if (
        not re.fullmatch(r"/[A-Za-z0-9_./-]+", remote_root)
        or ".." in PurePosixPath(remote_root).parts
    ):
        raise ValueError("SSH job root must be an absolute operator path without parent traversal")
    return host, remote_root


def evaluate_ssh(gpu, manifest, generation, result, options):
    """Stage only frozen references and exported kernels, never host credentials.

    The SSH identity comes from the operator's SSH config/agent. GPU isolation
    and exclusive resource assignment belong to the runner deployment.
    """
    host, remote_root = ssh_location(gpu)
    job = remote_root.rstrip("/") + "/kernelbench-" + uuid.uuid4().hex
    bundle = result.parent / "gpu-inputs"
    shutil.copytree(manifest.parent, bundle / "pool")
    (bundle / "generation").mkdir()
    shutil.copyfile(generation / "generation.json", bundle / "generation/generation.json")
    for source in (generation / "tasks").glob("*/model_new.py"):
        target = bundle / "generation/tasks" / source.parent.name / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            raise ValueError("Frozen target outputs must not be symlinks")
        shutil.copyfile(source, target)
    shutil.copyfile(Path(__file__).with_name("evaluate.py"), bundle / "evaluate.py")
    ssh_options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    subprocess.run(["ssh", *ssh_options, host, shlex.join(["mkdir", "-p", "--", job])], check=True)
    (result.parent / "remote-job.json").write_text(
        json.dumps({"host": host, "directory": job}) + "\n"
    )
    subprocess.run(["scp", *ssh_options, "-r", str(bundle), f"{host}:{job}/"], check=True)
    remote_bundle = job + "/gpu-inputs"
    command = [
        gpu["python"],
        remote_bundle + "/evaluate.py",
        "--repository",
        gpu["repository"],
        "--manifest",
        remote_bundle + "/pool/manifest.json",
        "--generation",
        remote_bundle + "/generation",
        "--output",
        job + "/results.json",
        *options,
    ]
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", gpu.get("cuda_visible_devices", ""))
    if visible:
        if not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", visible):
            raise ValueError("CUDA_VISIBLE_DEVICES must contain assigned numeric GPU IDs")
        command = ["env", "CUDA_VISIBLE_DEVICES=" + visible, *command]
    # A remote PTY propagates disconnect/hangup to the evaluator. Its signal
    # handler terminates the active CUDA worker process group before exiting.
    subprocess.run(["ssh", "-tt", *ssh_options, host, shlex.join(command)], check=True)
    subprocess.run(["scp", *ssh_options, f"{host}:{job}/results.json", str(result)], check=True)
    subprocess.run(
        [
            "scp",
            *ssh_options,
            f"{host}:{job}/results.environment.json",
            str(result.with_suffix(".environment.json")),
        ],
        check=True,
    )


def phase_pool(config, phase):
    # search is the collaboration protocol's canonical validation phase.
    # Accept older operator configs and CLI invocations using validate.
    phase = "search" if phase == "validate" else phase
    phases = config.get(
        "phases", {"diagnostic": "development", "search": "search", "final": "final"}
    )
    if "search" not in phases and "validate" in phases:
        phases = {**phases, "search": phases["validate"]}
    if phase not in phases or phases[phase] not in {"development", "search", "final"}:
        raise ValueError("Phase is not assigned to a fixed operator task pool")
    split = phases[phase]
    if phase == "diagnostic" and split != "development":
        raise ValueError("Diagnostic publication may only use the public development task pool")
    pool = Path(config["prepared_data"]) / (split if split == "development" else "private/" + split)
    return phase, split, pool


async def publish_development(generation_root, generated, manifest, rows, evaluation, destination):
    """Release only the explicit development run's files, never its runtime/config tree."""
    from areal_rsi.api.base import ContentRef
    from areal_rsi.base.storage import FileContentStore, FileTrajectoryStore, decode, safe_name

    if manifest["split"] != "development" or evaluation["phase"] != "diagnostic":
        raise ValueError("Only diagnostic development traces may be published")
    destination.mkdir(parents=True)
    content = FileContentStore(generation_root / "content")
    trajectories = FileTrajectoryStore(generation_root / "trajectories", content)
    by_task = {item["task_id"]: item for item in generated["tasks"]}
    scores = {item["task_id"]: item for item in rows}
    released = []

    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    for task in manifest["tasks"]:
        task_id = safe_name(task["task_id"])
        record = by_task[task_id]
        directory = destination / "tasks" / task_id
        directory.mkdir(parents=True)
        # Public task outputs are deliberately enumerated. Do not copy private
        # session configs, credentials, harness installs or the whole CAS tree.
        write(directory / "generation.json", record)
        write(directory / "result.json", scores[task_id])
        kernel = generation_root / "tasks" / task_id / "model_new.py"
        if kernel.is_file():
            if kernel.is_symlink():
                raise ValueError("Target output must be a regular file")
            shutil.copyfile(kernel, directory / "model_new.py")
        if record.get("output"):
            value = await content.read_json(decode(ContentRef, record["output"]))
            write(directory / "output.json", value)
        identifiers = set()
        if record.get("trajectory"):
            identifiers.add(record["trajectory"]["trajectory_id"])
        # Failed DSH turns still archive a trajectory, even when FileTaskExecutor
        # raises before its result can supply the trajectory reference.
        session_root = generation_root / "sessions" / task_id
        identifiers.update(p.name for p in session_root.glob("trajectory_*") if p.is_dir())
        for identifier in sorted(identifiers):
            trajectory = await trajectories.get(safe_name(identifier))
            if trajectory is not None:
                write(
                    directory / (safe_name(identifier) + ".json"),
                    await content.read_json(trajectory.content),
                )
        released.append(
            {"task_id": task_id, "status": record["status"], "directory": "tasks/" + task_id}
        )
    write(
        destination / "summary.json",
        {
            "purpose": "Development evidence only; not a formal comparison score",
            "phase": "diagnostic",
            "split": "development",
            "simulated": evaluation["simulated"],
            "manifest_id": manifest["manifest_id"],
            "harness_id": generated["harness_id"],
            "target_model": generated["target"]["model"],
            "evaluation": evaluation,
            "tasks": released,
        },
    )


def execution_protocol_id(gpu_protocol_id, config):
    """Include an explicitly selected Task Runtime in the comparison identity."""
    if config.get("task_runtime") is None:
        return gpu_protocol_id
    import hashlib

    from areal_rsi.modules.runtime_package import RuntimeSettings

    runtime = RuntimeSettings(config["task_runtime"])
    runtime.select("task-agent")
    return hashlib.sha256(json.dumps({
        "gpu_protocol_id": gpu_protocol_id, "task_runtime": runtime.identity,
    }, sort_keys=True).encode()).hexdigest()


def describe_identity(operator_config, phase, target_model=None):
    """Probe fixed GPU conditions without a model request or candidate execution."""
    config = json.loads(operator_config.read_text())
    phase, split, pool = phase_pool(config, phase)
    manifest = read_manifest(pool / "manifest.json")
    model = target_model or os.environ.get("RSI_TARGET_MODEL")
    if not model:
        raise ValueError("Set RSI_TARGET_MODEL or pass --target-model to pin the target")
    gpu = config["gpu"]
    if gpu["kind"] not in {"local", "ssh"}:
        raise ValueError("Real comparison identity requires a real GPU environment")
    budget = config.get("target_budget", {})
    target_budget = {
        "max_model_requests": budget.get("max_model_requests", 8),
        "max_output_tokens_per_request": budget.get("max_output_tokens", 8192),
        "timeout_seconds": budget.get("timeout_seconds", 600),
    }
    options = [
        "--describe",
        "--repository",
        gpu["repository"],
        "--target-budget",
        json.dumps(target_budget),
        *gpu_arguments(config.get("evaluation", {})),
    ]
    with tempfile.TemporaryDirectory(prefix="kernelbench-protocol-") as temporary:
        result = Path(temporary) / "identity.json"
        evaluator = Path(__file__).with_name("evaluate.py")
        if gpu["kind"] == "local":
            subprocess.run(
                [gpu["python"], str(evaluator), *options, "--output", str(result)], check=True
            )
        else:
            host, root = ssh_location(gpu)
            remote = root.rstrip("/") + "/protocol-" + uuid.uuid4().hex
            ssh = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
            subprocess.run(["ssh", *ssh, host, shlex.join(["mkdir", "-p", remote])], check=True)
            subprocess.run(
                ["scp", *ssh, str(evaluator), f"{host}:{remote}/evaluate.py"], check=True
            )
            command = [
                gpu["python"],
                remote + "/evaluate.py",
                *options,
                "--output",
                remote + "/identity.json",
            ]
            visible = os.environ.get("CUDA_VISIBLE_DEVICES", gpu.get("cuda_visible_devices", ""))
            if visible:
                if not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", visible):
                    raise ValueError("CUDA_VISIBLE_DEVICES must contain assigned numeric GPU IDs")
                command = ["env", "CUDA_VISIBLE_DEVICES=" + visible, *command]
            subprocess.run(["ssh", *ssh, host, shlex.join(command)], check=True)
            subprocess.run(["scp", *ssh, f"{host}:{remote}/identity.json", str(result)], check=True)
        description = json.loads(result.read_text())
    return {
        "adapter": "kernelbench",
        "phase": phase,
        "split": split,
        "manifest_id": manifest["manifest_id"],
        "protocol_id": execution_protocol_id(description["protocol_id"], config),
        "target_model": model,
        "environment": description["environment"],
    }


async def run(harness, output, operator_config, phase, target_model=None):
    config = json.loads(operator_config.read_text())
    phase, split, pool = phase_pool(config, phase)
    manifest_path = pool / "manifest.json"
    manifest = read_manifest(manifest_path)
    scope = config.get("edit_scope", "harness")
    if scope == "skills" and not config.get("baseline_harness"):
        raise ValueError("Skill-only verification requires the operator baseline harness path")
    if config.get("baseline_harness"):
        check_edit_scope(Path(config["baseline_harness"]), harness, scope)
    output.mkdir(parents=True, exist_ok=True)
    evaluation_path = output / "evaluation.json"
    if evaluation_path.exists():
        raise FileExistsError("Never overwrite a verification result")
    # Keep validation traces/kernel sources under private/, separate from the
    # concise report consumed by Research/RSI Agents.
    private = output / "private"
    private.mkdir(exist_ok=True)
    budget = config.get("target_budget", {})
    generated = await generate(
        manifest_path,
        harness,
        private / "generation",
        max_model_requests=budget.get("max_model_requests", 8),
        max_output_tokens=budget.get("max_output_tokens", 8192),
        timeout_seconds=budget.get("timeout_seconds", 600),
        runtime_root=Path(config["dsh_root"]) if config.get("dsh_root") else None,
        expected_target_model=target_model or config.get("target_model"),
        runtime_config=config.get("task_runtime"),
    )
    gpu = config["gpu"]
    result = private / "results.json"
    if gpu["kind"] == "mock":
        rows = mock_results(
            manifest,
            harness_id=generated["harness_id"],
            target_model=generated["target"]["model"],
            outcomes=gpu.get("outcomes"),
        )
        generation_status = {item["task_id"]: item["status"] for item in generated["tasks"]}
        for row in rows:
            if generation_status[row["task_id"]] != "generated":
                row.update(
                    status=generation_status[row["task_id"]], compiled=False, correctness=False
                )
        result.write_text(json.dumps(rows) + "\n")
    else:
        options = gpu_arguments(config.get("evaluation", {}))
        operation = {"local": evaluate_local, "ssh": evaluate_ssh}.get(gpu["kind"])
        if operation is None:
            raise ValueError("GPU runner must be local, ssh or explicitly mock")
        await asyncio.to_thread(
            operation, gpu, manifest_path, private / "generation", result, options
        )
        rows = json.loads(result.read_text())
    aggregate = aggregate_results(manifest, rows)
    evaluation = {
        **asdict(aggregate),
        "protocol_id": execution_protocol_id(aggregate.protocol_id, config),
        "metric": "fast_1",
        "score": aggregate.fast_1,
        "phase": phase,
        "split": split,
        "metrics": {
            "fast_1": aggregate.fast_1,
            "correctness": aggregate.correctness,
            "compiled": aggregate.compiled,
            "task_count": aggregate.task_count,
        },
    }
    evaluation_path.write_text(
        json.dumps(evaluation, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    if phase == "diagnostic":
        await publish_development(
            private / "generation",
            generated,
            manifest,
            rows,
            evaluation,
            output / "public" / "development",
        )
    print(json.dumps(evaluation, sort_keys=True))
    return evaluation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--target-model")
    parser.add_argument("--harness", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--phase", default="search")
    args = parser.parse_args()
    if args.describe:
        print(json.dumps(describe_identity(args.config, args.phase, args.target_model), indent=2))
        return
    if not args.harness or not args.output:
        parser.error("harness and output are required for verification")
    asyncio.run(
        run(
            args.harness.resolve(),
            args.output.resolve(),
            args.config,
            args.phase,
            args.target_model,
        )
    )


if __name__ == "__main__":
    main()
