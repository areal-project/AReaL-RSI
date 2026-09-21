#!/usr/bin/env python3
"""Run one approved Scale RSI experiment from an immutable RSI workspace snapshot."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

TERMINAL_SLURM = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "OUT_OF_MEMORY",
    "NODE_FAIL",
    "PREEMPTED",
}


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, env=env, check=True)


def copy_matches(workspace: Path, output: Path, patterns: tuple[str, ...]) -> list[str]:
    copied: list[str] = []
    for pattern in patterns:
        for source in workspace.glob(pattern):
            if not source.is_file():
                continue
            relative = source.relative_to(workspace)
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(relative.as_posix())
    return sorted(set(copied))


def nanogpt(workspace: Path, output: Path, runtime_root: Path) -> dict[str, object]:
    selection = workspace / "submission/selection.json"
    if not selection.is_file():
        raise ValueError("submission/selection.json is required")
    launcher = runtime_root / "nanogpt_env/bench.sh"
    submitted = subprocess.run(
        ["bash", str(launcher), str(selection)], text=True, capture_output=True, check=True
    )
    print(submitted.stdout, end="", flush=True)
    receipt = json.loads(submitted.stdout)
    run_dir = Path(receipt["run_dir"])
    state = "SUBMITTING"
    status: dict[str, object] = receipt
    while state.split("+", 1)[0] not in TERMINAL_SLURM and state not in {"SUCCEEDED", "FAILED"}:
        time.sleep(10)
        checked = subprocess.run(
            ["bash", str(launcher), "status", str(run_dir)],
            text=True,
            capture_output=True,
            check=True,
        )
        status = json.loads(checked.stdout)
        state = str(status.get("state", "UNKNOWN"))
    for name in ("result.json", "experiment.json", "state.json", "job.log"):
        source = run_dir / name
        if source.is_file():
            shutil.copy2(source, output / name)
    if state not in {"COMPLETED", "SUCCEEDED"}:
        raise RuntimeError(f"nanoGPT Slurm job ended in {state}")
    return {"state": state, "status": status}


def gpu_benchmark(
    benchmark: str, workspace: Path, output: Path, runtime_root: Path
) -> dict[str, object]:
    root = runtime_root / f"{benchmark}_env"
    environment = {
        **os.environ,
        "RSI_CANDIDATE_WORKSPACE": str(workspace),
        "RSI_RESTRICTED": "1",
    }
    if benchmark == "opsd":
        shell = (
            "set -euo pipefail; rm -rf /workspace/runs/rsi; "
            "SEED=42 OUT_DIR=/workspace/runs/rsi bash /workspace/train_capped.sh; "
            "ckpt=/workspace/runs/rsi/capped_seed42/checkpoint-100; "
            "bash /workspace/dev_eval.sh \"$ckpt\" /workspace/dev_result.json"
        )
        command = ["bash", str(root / "run.sh"), "bash", "-lc", shell]
        patterns = ("dev_result.json", "runs/rsi/**/trainer_state.json", "submission/recipe.env")
    elif benchmark == "jailbreak":
        shell = (
            "set -euo pipefail; bash /workspace/train.sh; "
            "bash /workspace/dev_eval.sh /workspace/submission/checkpoint /workspace/dev_result"
        )
        command = ["bash", str(root / "run.sh"), "bash", "-lc", shell]
        patterns = ("dev_result/score.json", "submission/*.json", "train.sh")
    elif benchmark == "swarm":
        shell = (
            "set -euo pipefail; cd /workspace/methods/main; python optimize.py; "
            "mkdir -p /workspace/submission; cp graph.json /workspace/submission/graph.json; "
            "python /workspace/selfcheck.py | tee /workspace/selfcheck.log"
        )
        command = ["bash", str(root / "run.sh"), "serve", "bash", "-lc", shell]
        patterns = ("submission/graph.json", "selfcheck.log", "methods/main/optimize.py")
    else:
        raise ValueError(f"Unsupported benchmark: {benchmark}")
    run(command, env=environment)
    return {"state": "COMPLETED", "files": copy_matches(workspace, output, patterns)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", choices=("nanogpt", "opsd", "jailbreak", "swarm"))
    parser.add_argument("runtime_root", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    workspace, output = args.workspace.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.benchmark == "nanogpt":
        summary = nanogpt(workspace, output, args.runtime_root.resolve())
    else:
        summary = gpu_benchmark(
            args.benchmark, workspace, output, args.runtime_root.resolve()
        )
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
