"""Supervise one local training process with an exclusive physical-GPU lease."""

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from bench import write_json


def process_start_ticks(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def process_alive(pid, start_ticks):
    if start_ticks is None or process_start_ticks(pid) != start_ticks:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def resolve_gpu(value):
    if not value or "," in value or not (value.isdigit() or value.startswith("GPU-")):
        raise ValueError(
            "local runs require one GPU index/UUID via --gpu, assignment.json "
            "or CUDA_VISIBLE_DEVICES"
        )
    result = subprocess.run(
        ["nvidia-smi", "-i", value, "--query-gpu=uuid", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=True,
    )
    rows = result.stdout.strip().splitlines()
    if len(rows) != 1 or not rows[0].startswith("GPU-"):
        raise ValueError("GPU selector must resolve to exactly one physical GPU")
    return rows[0]


def stop_child(child):
    if child is None or child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        child.wait()
        return
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()


def run_local(run):
    root = Path(os.environ["RSI_ROOT"])
    config = json.loads((run / "experiment.json").read_text())
    shared = Path(os.environ.get("RSI_SHARED_ROOT", root))
    locks = shared / "gpu-locks"
    locks.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(config["gpu_uuid"].encode()).hexdigest()
    child = None
    deadline = float(os.environ.get("RSI_RESEARCH_DEADLINE_EPOCH", "inf"))

    def remaining(limit):
        budget = min(limit, deadline - time.time())
        if budget <= 0:
            raise RuntimeError("research deadline reached")
        return budget

    def interrupted(signum, frame):
        raise RuntimeError(f"local job interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    with (locks / (key + ".lock")).open("a+") as lease:
        try:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(
                    f"GPU {config['gpu_uuid']} already has a local benchmark job"
                ) from error
            lease.seek(0)
            lease.truncate()
            lease.write(json.dumps({"pid": os.getpid(), "run_dir": str(run)}))
            lease.flush()
            # Check the actual runtime rather than silently using multiple visible GPUs.
            check = (
                "import torch; assert torch.cuda.device_count()==1; "
                "assert torch.cuda.is_bf16_supported(); "
                "print('GPU_CHECK',torch.__version__,torch.cuda.get_device_name(0),flush=True)"
            )
            subprocess.run([sys.executable, "-c", check], check=True, timeout=remaining(60))
            write_json(
                run / "state.json",
                {"state": "RUNNING", "phase": "starting", "updated_at": time.time()},
            )
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    str(Path(__file__).with_name("run_experiment.py")),
                    str(run),
                ],
                start_new_session=True,
            )
            write_json(
                run / "local-child.json",
                {"pid": child.pid, "start_ticks": process_start_ticks(child.pid)},
            )
            child.wait(timeout=remaining(config["timeout_seconds"]))
            if child.returncode:
                raise RuntimeError(f"training worker exited {child.returncode}")
            if not (run / "result.json").is_file():
                raise RuntimeError("training exited without result.json")
        except BaseException as error:
            stop_child(child)
            write_json(
                run / "state.json",
                {"state": "FAILED", "error": str(error), "updated_at": time.time()},
            )
            raise


if __name__ == "__main__":
    run_local(Path(sys.argv[1]).resolve())
