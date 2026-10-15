"""Trusted GPU entry for the pinned KernelBench environment (Python 3.10).

Consumes target-generated kernels and frozen references; writes structured JSON.
Run inside the operator's GPU runner. Do not grant the Research Agent this command
with arbitrary result paths or give it write access to target-generated kernels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

REVISION = "423217d9fda91e0c2d67e4a43bf62f96f6d104f1"
ACTIVE_CHILD = None


class EvalInfrastructureError(RuntimeError):
    pass


def terminate_worker(signum, frame):
    if ACTIVE_CHILD is not None and ACTIVE_CHILD.poll() is None:
        with suppress(ProcessLookupError):
            os.killpg(ACTIVE_CHILD.pid, signal.SIGKILL)
        ACTIVE_CHILD.wait()
    raise SystemExit(128 + signum)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False, default=str) + "\n"
    )


def worker(request_path, result_path):
    request = json.loads(request_path.read_text())
    result = dict(request["identity"])
    result.update(
        task_id=request["task_id"], status="infra_error", compiled=False, correctness=False
    )
    stage = "environment"
    try:
        sys.path.insert(0, str(Path(request["repository"]) / "src"))
        import torch
        from kernelbench.eval import eval_kernel_against_ref
        from kernelbench.kernel_static_checker import validate_kernel_static
        from kernelbench.timing import measure_ref_program_time

        reference, kernel = Path(request["reference"]), Path(request["kernel"])
        if (
            digest(reference) != request["reference_sha256"]
            or digest(kernel) != request["kernel_sha256"]
        ):
            raise ValueError("Frozen evaluation input changed")
        source, generated = reference.read_text(), kernel.read_text()
        try:
            compile(generated, "model_new.py", "exec")
        except SyntaxError:
            result.update(status="invalid_kernel", error="syntax_error")
            dump(result_path, result)
            return
        ok, errors, warnings = validate_kernel_static(
            generated,
            backend=request["backend"],
            precision=request["precision"],
        )
        if not ok:
            result.update(status="invalid_kernel", error="static_check", checker_errors=errors)
            dump(result_path, result)
            return
        precision = {"fp32": torch.float32, "fp16": torch.float16, "bf16": torch.bfloat16}[
            request["precision"]
        ]
        device = torch.device("cuda:0")
        (request_path.parent / "build").mkdir()
        # Measure reference before loading generated code. This remains the
        # upstream checker, not a claim of resistance to arbitrary Python hacks.
        reference_time = measure_ref_program_time(
            ref_arch_name=request["task_id"],
            ref_arch_src=source,
            num_trials=request["num_perf_trials"],
            use_torch_compile=False,
            timing_method=request["timing_method"],
            device=device,
            verbose=False,
            precision=precision,
        )["mean"]
        stage = "candidate"
        evaluated = eval_kernel_against_ref(
            source,
            generated,
            seed_num=request["seed"],
            num_correct_trials=request["num_correct_trials"],
            num_perf_trials=request["num_perf_trials"],
            measure_performance=True,
            timing_method=request["timing_method"],
            device=device,
            backend=request["backend"],
            precision=precision,
            build_dir=str(request_path.parent / "build"),
            # This pin's optional excessive-speedup path can access model_new
            # after a correctness failure. Compute the flag below instead.
            check_for_excessive_speedup=False,
        )
        if evaluated is None:
            raise EvalInfrastructureError(
                "KernelBench requested retry (for example compilation cache lock)"
            )
        result.update(
            status="evaluated",
            compiled=bool(evaluated.compiled),
            correctness=bool(evaluated.correctness),
            metadata=evaluated.metadata,
            checker_warnings=warnings,
        )
        if evaluated.correctness:
            if not all(math.isfinite(v) and v > 0 for v in (evaluated.runtime, reference_time)):
                raise EvalInfrastructureError("Timing was incomplete or invalid")
            result.update(
                runtime_ms=float(evaluated.runtime),
                reference_ms=float(reference_time),
                excessive_speedup=reference_time / evaluated.runtime > 10,
            )
    except Exception as exc:
        status = (
            "invalid_kernel"
            if stage == "candidate" and not isinstance(exc, EvalInfrastructureError)
            else "infra_error"
        )
        result.update(status=status, correctness=False, error=type(exc).__name__)
    dump(result_path, result)


def evaluation_environment(args, target_budget):
    environment = {
        "revision": REVISION,
        "evaluator_sha256": digest(Path(__file__).resolve()),
        "backend": args.backend,
        "precision": args.precision,
        "num_correct_trials": args.num_correct_trials,
        "num_perf_trials": args.num_perf_trials,
        "timing_method": args.timing_method,
        "seed": args.seed,
        "timeout": args.timeout,
        "target_budget": target_budget,
    }
    preflight_error = None
    try:
        if sys.version_info[:2] != (3, 10):
            raise RuntimeError("Pinned KernelBench requires Python 3.10")
        revision = subprocess.run(
            ["git", "-C", str(args.repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if revision != REVISION:
            raise ValueError("Wrong KernelBench checkout revision")
        subprocess.run(
            [
                "git",
                "-C",
                str(args.repository),
                "diff",
                "--exit-code",
                "HEAD",
                "--",
                "src/kernelbench",
            ],
            check=True,
            capture_output=True,
        )
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable")
        environment.update(
            gpu=torch.cuda.get_device_name(0), torch=torch.__version__, cuda=torch.version.cuda
        )
    except Exception as exc:
        preflight_error = type(exc).__name__ + ": " + str(exc)
    return environment, preflight_error


def main():
    global ACTIVE_CHILD
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--target-budget", type=json.loads)
    parser.add_argument("--worker", type=Path, nargs=2, metavar=("REQUEST", "RESULT"))
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--generation", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--num-correct-trials", type=int, default=5)
    parser.add_argument("--num-perf-trials", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--backend", choices=["cuda", "triton"], default="cuda")
    parser.add_argument("--precision", choices=["fp32", "fp16", "bf16"], default="fp32")
    parser.add_argument(
        "--timing-method", choices=["cuda_event", "do_bench_impl"], default="cuda_event"
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.worker:
        worker(*args.worker)
        return
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, terminate_worker)
    if args.describe:
        if not args.repository or not args.output or not isinstance(args.target_budget, dict):
            parser.error("describe requires repository, output and target-budget JSON")
        if args.output.exists():
            raise FileExistsError(args.output)
        environment, error = evaluation_environment(args, args.target_budget)
        if error is not None:
            raise EvalInfrastructureError(error)
        dump(
            args.output,
            {
                "protocol_id": hashlib.sha256(
                    json.dumps(environment, sort_keys=True).encode()
                ).hexdigest(),
                "environment": environment,
            },
        )
        return
    if not all((args.repository, args.manifest, args.generation, args.output)):
        parser.error("repository, manifest, generation and output are required")
    if min(args.num_correct_trials, args.num_perf_trials, args.timeout) <= 0:
        parser.error("Evaluation budgets must be positive")
    if args.output.exists():
        raise FileExistsError(args.output)
    manifest = json.loads(args.manifest.read_text())
    generation = json.loads((args.generation / "generation.json").read_text())
    manifest_id = hashlib.sha256(
        json.dumps(
            {k: v for k, v in manifest.items() if k != "manifest_id"}, sort_keys=True
        ).encode()
    ).hexdigest()
    if (
        manifest_id != manifest["manifest_id"]
        or generation["manifest_id"] != manifest_id
        or manifest["revision"] != REVISION
    ):
        raise ValueError("Manifest or generation identity mismatch")
    ids = [row["task_id"] for row in manifest["tasks"]]
    generated = {row["task_id"]: row for row in generation["tasks"]}
    if (
        len(generated) != len(generation["tasks"])
        or set(ids) != set(generated)
        or len(ids) != len(set(ids))
    ):
        raise ValueError("Generation must cover the selected task pool exactly once")
    environment, preflight_error = evaluation_environment(args, generation["budget"])
    protocol_id = hashlib.sha256(json.dumps(environment, sort_keys=True).encode()).hexdigest()
    identity = {
        "manifest_id": manifest_id,
        "protocol_id": protocol_id,
        "harness_id": generation["harness_id"],
        "target_model": generation["target"]["model"],
        "simulated": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(dir=args.output.parent) as temporary:
        for task in manifest["tasks"]:
            task_id = task["task_id"]
            if not re_task_id(task_id):
                raise ValueError("Invalid task ID")
            item = generated[task_id]
            result = {**identity, "task_id": task_id, "compiled": False, "correctness": False}
            if (
                item.get("producer") != "task-agent"
                or item.get("target") != generation["target"]
                or item.get("harness_id") != generation["harness_id"]
            ):
                raise ValueError("Kernel was not exported by this fixed-target task run")
            if preflight_error:
                result.update(status="infra_error", error=preflight_error)
            elif item["status"] != "generated":
                if item["status"] not in {
                    "no_output",
                    "budget_exhausted",
                    "infra_error",
                    "invalid_kernel",
                }:
                    raise ValueError("Unknown generation status")
                result.update(status=item["status"])
            else:
                directory = Path(temporary) / task_id
                directory.mkdir()
                reference = (args.manifest.parent / task["reference"]).resolve()
                kernel = (args.generation / "tasks" / task_id / "model_new.py").resolve()
                if not reference.is_relative_to(
                    args.manifest.parent.resolve()
                ) or not kernel.is_relative_to(args.generation.resolve()):
                    raise ValueError("Evaluation input path escaped its operator-provided root")
                request = {
                    "identity": identity,
                    "task_id": task_id,
                    "repository": str(args.repository.resolve()),
                    "reference": str(reference),
                    "reference_sha256": task["reference_sha256"],
                    "kernel": str(kernel),
                    "kernel_sha256": item["kernel_sha256"],
                    **environment,
                }
                request_path, result_path = directory / "request.json", directory / "result.json"
                dump(request_path, request)
                with (directory / "worker.log").open("w") as log:
                    child = subprocess.Popen(
                        [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--worker",
                            str(request_path),
                            str(result_path),
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    ACTIVE_CHILD = child
                    try:
                        child.wait(timeout=args.timeout)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
                        result.update(status="invalid_kernel", error="task_execution_timeout")
                    else:
                        if child.returncode or not result_path.is_file():
                            result.update(status="infra_error", error="worker_failed")
                        else:
                            result = json.loads(result_path.read_text())
                    ACTIVE_CHILD = None
                # Persist diagnostics separately; never parse them as metrics.
                log_path = args.output.parent / (args.output.stem + "-" + task_id + ".log")
                log_path.write_bytes((directory / "worker.log").read_bytes())
            results.append(result)
    dump(args.output, results)
    dump(args.output.with_suffix(".environment.json"), environment)


def re_task_id(value):
    import re

    return isinstance(value, str) and re.fullmatch(r"level[1-9][0-9]*_[1-9][0-9]*", value)


if __name__ == "__main__":
    main()
