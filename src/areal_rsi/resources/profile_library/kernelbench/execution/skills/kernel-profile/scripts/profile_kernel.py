"""Self-contained KernelBench diagnostic; copy into the workspace and use gpuq run."""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def check_tools() -> None:
    for name in ("ncu", "nsys"):
        executable = shutil.which(name)
        if executable is None:
            print(json.dumps({"tool": name, "available": False, "reason": "not found"}))
            continue
        try:
            result = subprocess.run(
                [executable, "--version"], capture_output=True, text=True, timeout=15, check=False
            )
            print(
                json.dumps(
                    {
                        "tool": name,
                        "path": executable,
                        "available": result.returncode == 0,
                        "output": (result.stdout + result.stderr)[-2000:],
                    }
                )
            )
        except subprocess.TimeoutExpired:
            print(json.dumps({"tool": name, "available": False, "reason": "version timed out"}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-tools", action="store_true")
    parser.add_argument("--target", choices=("candidate", "reference"), default="candidate")
    parser.add_argument("--backend", choices=("torch", "ncu"), default="torch")
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--steps", type=int, default=2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--record-shapes", action="store_true")
    parser.add_argument("--profile-memory", action="store_true")
    parser.add_argument("--trace")
    parser.add_argument("--kernel", help="Nsight demangled kernel-name regular expression")
    parser.add_argument("--section", action="append", default=[])
    parser.add_argument("--ncu-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.check_tools:
        check_tools()
        return 0
    if args.warmup < 0 or args.steps < 1:
        parser.error("warmup must be nonnegative and steps must be positive")
    if args.backend == "ncu" and not args.ncu_child:
        if args.device != "cuda" or not args.kernel:
            parser.error("ncu requires --device cuda and an explicit --kernel filter")
        executable = shutil.which("ncu")
        if executable is None:
            print("Nsight Compute is unavailable; use the torch backend.", file=sys.stderr)
            return 2
        command = [
            executable,
            "--target-processes",
            "all",
            "--profile-from-start",
            "off",
            "--kernel-name-base",
            "demangled",
            "--kernel-name",
            "regex:" + args.kernel,
            "--launch-count",
            "1",
        ]
        if args.section:
            for section in args.section:
                command.extend(["--section", section])
        else:
            command.extend(["--set", "basic"])
        command.extend(
            [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--ncu-child"]
        )
        return subprocess.run(command, check=False, timeout=240).returncode

    import torch  # type: ignore[import-not-found]  # Available in the GPU job environment.

    torch.manual_seed(args.seed)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    reference = importlib.import_module("reference")
    constructor = (
        reference.Model
        if args.target == "reference"
        else importlib.import_module("model_new").ModelNew
    )
    model = constructor(*reference.get_init_inputs()).to(args.device)

    def move(value: Any) -> Any:
        if isinstance(value, torch.Tensor):
            return value.to(args.device)
        if isinstance(value, dict):
            return {key: move(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(move(item) for item in value)
        return value

    inputs = move(reference.get_inputs())

    def synchronize() -> None:
        if args.device == "cuda":
            torch.cuda.synchronize()

    print(
        json.dumps(
            {
                "target": args.target,
                "training": model.training,
                "device": torch.cuda.get_device_name() if args.device == "cuda" else "cpu",
                "torch": torch.__version__,
                "cuda": torch.version.cuda,
                "diagnostic_only": True,
            }
        ),
        flush=True,
    )
    with torch.no_grad():
        for _ in range(args.warmup):
            model(*inputs)
        synchronize()
        if args.ncu_child:
            torch.cuda.profiler.start()
            model(*inputs)
            synchronize()
            torch.cuda.profiler.stop()
            return 0
        activities = [torch.profiler.ProfilerActivity.CPU]
        if args.device == "cuda":
            activities.append(torch.profiler.ProfilerActivity.CUDA)
        with torch.profiler.profile(
            activities=activities,
            record_shapes=args.record_shapes,
            profile_memory=args.profile_memory,
        ) as profile:
            for _ in range(args.steps):
                model(*inputs)
            synchronize()
    order = "self_cuda_time_total" if args.device == "cuda" else "self_cpu_time_total"
    print(profile.key_averages().table(sort_by=order, row_limit=25), flush=True)
    kernels: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for event in profile.events():
        if event.device_type == torch.autograd.DeviceType.CUDA:
            entry = kernels[event.name]
            entry[0] += 1
            entry[1] += event.device_time_total
    for name, (count, total) in sorted(kernels.items(), key=lambda item: -item[1][1])[:25]:
        print(
            json.dumps(
                {"kernel": name, "calls": count, "total_us": total, "mean_us": total / count}
            )
        )
    if args.trace:
        profile.export_chrome_trace(args.trace)
        print("Trace saved in the diagnostic job workspace:", args.trace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
