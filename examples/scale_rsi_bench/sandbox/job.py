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
            "SEED=42 OUT_DIR=/workspace/runs/rsi bash /rsi/benchmark/train_capped.sh; "
            "ckpt=/workspace/runs/rsi/capped_seed42/checkpoint-100; "
            "python /rsi/benchmark/eval_official.py --base_model /opt/models/Qwen3-1.7B "
            '--checkpoint_path "$ckpt" --data_file /opt/eval_assets/aime24.json '
            "--output_file /workspace/dev_result.json --seed 20260610 "
            "--data_parallel_size 4 --val_n 12 --num_problems 30"
        )
        commands = [(["bash", str(root / "run.sh"), "bash", "-lc", shell], environment)]
        patterns = ("dev_result.json", "runs/rsi/**/trainer_state.json", "submission/recipe.env")
    elif benchmark == "jailbreak":
        generation = (
            "set -euo pipefail; bash /workspace/train.sh; "
            "mkdir -p /workspace/dev_result; "
            "python /opt/eval_src/generate.py --base /opt/models/Qwen3-8B "
            "--checkpoint /workspace/submission/checkpoint "
            "--data /opt/eval_data/dev_split.jsonl "
            "--output /workspace/dev_result/responses.jsonl --tensor-parallel-size 1"
        )
        grading = (
            "set -euo pipefail; python /opt/eval_src/grade.py "
            "--data /opt/eval_data/dev_split.jsonl "
            "--responses /workspace/dev_result/responses.jsonl "
            "--output /workspace/dev_result/score.json"
        )
        untrusted_environment = {
            key: value
            for key, value in environment.items()
            if key
            not in {
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "JAILBREAK_JUDGE_MODEL",
                "JAILBREAK_JUDGE_CONCURRENCY",
            }
        }
        judge_environment = {**environment, "RSI_GPUS": "0", "RSI_CPUS": "4", "RSI_MEM": "16G"}
        commands = [
            (["bash", str(root / "run.sh"), "bash", "-lc", generation], untrusted_environment),
            (["bash", str(root / "run.sh"), "bash", "-lc", grading], judge_environment),
        ]
        patterns = ("dev_result/score.json", "submission/*.json", "train.sh")
    elif benchmark == "swarm":
        shell = (
            "set -euo pipefail; cd /workspace/methods/main; python optimize.py; "
            "mkdir -p /workspace/submission; cp graph.json /workspace/submission/graph.json; "
            "python /rsi/scripts/eval_visible.py "
            "--graph /workspace/submission/graph.json | tee /workspace/selfcheck.log"
        )
        commands = [(["bash", str(root / "run.sh"), "serve", "bash", "-lc", shell], environment)]
        patterns = ("submission/graph.json", "selfcheck.log", "methods/main/optimize.py")
    else:
        raise ValueError(f"Unsupported benchmark: {benchmark}")
    for command, command_environment in commands:
        run(command, env=command_environment)
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
        summary = gpu_benchmark(args.benchmark, workspace, output, args.runtime_root.resolve())
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
