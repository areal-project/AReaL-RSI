"""Standard-library-only submission/status CLI for agent experiments."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import uuid

from env_paths import runtime_root
ROOT = runtime_root(__file__)
DOCS = 182_016


def write_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def validate_selection(selection):
    if not isinstance(selection, list) or not selection:
        raise ValueError("selection must be a non-empty JSON list")
    if any(type(i) is not int or not 0 <= i < DOCS for i in selection):
        raise ValueError(f"document IDs must be integers in [0, {DOCS})")
    if len(set(selection)) != len(selection):
        raise ValueError("duplicate document IDs")


def status(run):
    run = Path(run).resolve()
    config = json.loads((run / "experiment.json").read_text())
    output = {"run_dir": str(run), "mode": config["mode"], "state": "SUBMITTING",
              "log": str(run / "job.log"), "result": str(run / "result.json")}
    if (run / "submission-error.json").exists():
        output.update(json.loads((run / "submission-error.json").read_text()))
    job_path = run / "job.json"
    if job_path.exists():
        job = json.loads(job_path.read_text())
        output.update(job)
        query = subprocess.run(["squeue", "-h", "-j", job["job_id"], "-o", "%T|%R"],
                               capture_output=True, text=True)
        if query.returncode == 0 and query.stdout.strip():
            output["state"], output["reason"] = query.stdout.strip().splitlines()[0].split("|", 1)
        else:
            query = subprocess.run(["sacct", "-n", "-X", "-j", job["job_id"],
                                    "--format=State,ExitCode", "-P"], capture_output=True, text=True)
            rows = query.stdout.strip().splitlines()
            if query.returncode == 0 and rows:
                fields = rows[0].split("|")
                output.update(state=fields[0], slurm_exit_code=fields[1])
            else:
                output.update(state="UNKNOWN", reason="Slurm accounting not yet available")
    if (run / "state.json").exists():
        worker = json.loads((run / "state.json").read_text())
        output["worker"] = worker
        if worker["state"] in ("SUCCEEDED", "FAILED"):
            output["state"] = worker["state"]
    if (run / "result.json").exists():
        output["metrics"] = json.loads((run / "result.json").read_text())
    print(json.dumps(output, indent=2))


def submit(args):
    if args.baseline:
        selection = list(range(DOCS))
        random.Random(1337).shuffle(selection)
        origin = "random baseline: Python random.Random(1337).shuffle(range(182016))"
    else:
        if args.selection is None:
            raise ValueError("provide selection.json or --baseline")
        source = Path(args.selection).resolve()
        selection = json.loads(source.read_text())
        origin = str(source)
    validate_selection(selection)
    run = ROOT / "experiments" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    run.mkdir(parents=True)
    write_json(run / "selection.json", selection)
    config = {"mode": "smoke" if args.smoke else "dev", "source": origin,
              "selection_sha256": hashlib.sha256((run / "selection.json").read_bytes()).hexdigest(),
              "max_iters": 2 if args.smoke else 3000, "seed": 1337,
              "time_limit": "02:00:00", "name": args.name}
    write_json(run / "experiment.json", config)
    command = ["sbatch", "--parsable", "--nodes=1", "--ntasks=1", "--gres=gpu:1",
               "--cpus-per-task=16", "--mem=64G", "--time=02:00:00",
               "--job-name=" + args.name, "--chdir=" + str(ROOT),
               "--output=" + str(run / "job.log"), "--error=" + str(run / "job.log"),
               str(ROOT / "scripts/experiment.sbatch"), str(ROOT), str(run)]
    response = subprocess.run(command, capture_output=True, text=True)
    if response.returncode:
        write_json(run / "submission-error.json", {"state": "SUBMISSION_FAILED", "error": response.stderr})
        raise RuntimeError(f"sbatch failed; details in {run}: {response.stderr.strip()}")
    job_id = response.stdout.strip().split(";", 1)[0]
    if not job_id.isdigit():
        raise RuntimeError(f"Unrecognized sbatch output: {response.stdout!r}; run directory: {run}")
    write_json(run / "job.json", {"job_id": job_id})
    print(json.dumps({"job_id": job_id, "run_dir": str(run), "mode": config["mode"],
                      "time_limit": config["time_limit"], "max_iters": config["max_iters"],
                      "log": str(run / "job.log"), "result": str(run / "result.json")}, indent=2))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        parser = argparse.ArgumentParser(description="Read worker results and live Slurm status")
        parser.add_argument("run_dir")
        status(parser.parse_args(sys.argv[2:]).run_dir)
        return
    parser = argparse.ArgumentParser(description="Submit a fixed-recipe nanoGPT DEV experiment (2h job limit).")
    parser.add_argument("selection", nargs="?", help="ordered document IDs as selection.json")
    parser.add_argument("--baseline", action="store_true", help="generate a reproducible random baseline")
    parser.add_argument("--smoke", action="store_true", help="2 training steps and a tiny dev slice; not a benchmark score")
    parser.add_argument("--name", default="wht-rsi-exp", help="Slurm job name")
    args = parser.parse_args()
    if args.selection and args.baseline:
        parser.error("choose selection.json or --baseline, not both")
    submit(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        sys.exit(1)
