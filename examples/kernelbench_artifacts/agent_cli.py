"""Agent-facing kb/gpuq CLI over the existing, run-scoped RSI job service.

This process may outlive exec_command's yield. It never runs GPU code locally,
opens a queue connection, starts a model, or acknowledges messages for the model.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

TERMINAL = {"succeeded", "failed", "timed_out", "cancelled", "lost", "interrupted"}


def job_state(job: dict[str, Any]) -> str:
    feedback = job.get("feedback", {}).get("data", {})
    # Transport success must not make a failed remote diagnostic look successful.
    if job["state"] == "succeeded" and feedback.get("state") in TERMINAL:
        return str(feedback["state"])
    return str(job["state"])


class Client:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.messages: dict[str, Any] = {}

    def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        result = subprocess.run(
            ["rsi-tools", "call", name, json.dumps(arguments), uuid.uuid4().hex],
            text=True, capture_output=True, check=False,
        )
        if result.returncode:
            raise RuntimeError(
                result.stderr.strip() or "RSI connection closed; check the original job"
            )
        value: dict[str, Any] = json.loads(result.stdout)
        for message in value.get("incoming_messages", []):
            if message["message_id"] not in self.messages:
                self.messages[message["message_id"]] = message
                # Visible through native write_stdin even while the GPU is queued.
                print(
                    "RSI message: " + json.dumps(message, ensure_ascii=False),
                    file=sys.stderr, flush=True,
                )
        if "incoming_messages" in value or value.get("turn_finished"):
            value = value["result"]
        if "error" in value and "job_id" not in value:
            raise RuntimeError(value["error"])
        return value

    def source(self, name: str) -> str:
        path = Path(name).resolve()
        if not path.is_relative_to(self.workspace) or not path.is_file():
            raise ValueError("Source must be a file in your own workspace")
        return path.relative_to(self.workspace).as_posix()

    def submit(self, command: str, parameters: dict[str, str]) -> dict[str, Any]:
        tasks = list((self.workspace / "tasks").iterdir())
        if len(tasks) != 1:
            raise ValueError("This CLI requires one task per study")
        job = self.call(
            "run_job", command_name=command, candidate_id="",
            parameters_json=json.dumps({"task": tasks[0].name, **parameters}), wait=False,
        )
        with (self.workspace / ".kb-jobs.jsonl").open("a") as stream:
            stream.write(json.dumps({"job_id": job["job_id"]}) + "\n")
        # run_job returns only after the input snapshot has been frozen.
        print("Submitted " + job["job_id"], flush=True)
        return job

    def wait(self, job: dict[str, Any], seconds: float) -> dict[str, Any]:
        deadline = time.monotonic() + seconds
        while job["state"] not in TERMINAL and time.monotonic() < deadline:
            job = self.call(
                "wait_for_job", job_id=job["job_id"],
                seconds=min(10, max(0, deadline - time.monotonic())),
            )
        return job

    def output(self, job_id: str, name: str) -> str:
        chunks, offset = [], 0
        while True:
            value = self.call("read_job_output_file", job_id=job_id, path=name, offset=offset)
            chunks.append(value["content"])
            offset = value["next_offset"]
            if offset is None:
                return "".join(chunks)

    def show(self, job: dict[str, Any]) -> None:
        # Keep the baseline's familiar evaluation object, plus RSI evidence IDs.
        value: dict[str, Any] = {k: job[k] for k in (
            "job_id", "state", "error", "uri", "published_candidate_id", "verification_job_id",
        ) if k in job}
        value["id"] = job["job_id"]
        value["state"] = job_state(job)
        if feedback := job.get("feedback"):
            value["evaluation"] = feedback.get("data", feedback.get("content"))
        if job.get("failure_log"):
            value["failure_log"] = job["failure_log"]
        if self.messages:
            value["incoming_messages"] = list(self.messages.values())
        print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)
        if job["state"] not in TERMINAL:
            print("Do not resubmit. Continue with: kb wait " + job["job_id"], flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KernelBench eval/debug through RSI")
    sub = parser.add_subparsers(dest="command", required=True)
    evaluate = sub.add_parser("eval")
    evaluate.add_argument("candidate", nargs="?", default="model_new.py")
    evaluate.add_argument("--wait", type=float, default=240)
    debug = sub.add_parser("run")
    debug.add_argument("--wait", type=float, default=240)
    debug.add_argument("--gpus", type=int, choices=[1], default=1)
    debug.add_argument("argv", nargs=argparse.REMAINDER)
    wait = sub.add_parser("wait")
    wait.add_argument("job_id")
    wait.add_argument("--seconds", type=float, default=240)
    logs = sub.add_parser("logs")
    logs.add_argument("job_id")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    client = Client(Path(os.environ["RSI_WORKSPACE"]))
    if args.command == "eval":
        job = client.submit("evaluate_task", {"candidate": client.source(args.candidate)})
    elif args.command == "run":
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if len(command) < 2 or command[0] not in {"python", "python3"}:
            raise ValueError("Use gpuq run -- python SCRIPT.py [args]")
        candidate = client.workspace / "model_new.py"
        job = client.submit("diagnose_task", {
            "script": client.source(command[1]),
            "candidate": client.source(str(candidate)) if candidate.is_file() else "",
            "arguments": json.dumps(command[2:]),
        })
    elif args.command == "status":
        index = client.workspace / ".kb-jobs.jsonl"
        rows = (
            [json.loads(line) for line in index.read_text().splitlines()] if index.exists() else []
        )
        for row in rows[-10:]:
            client.show(client.call("job_status", job_id=row["job_id"]))
        print("Recent jobs: " + str(min(10, len(rows))))
        return 0
    else:
        job = client.call("job_status", job_id=args.job_id)
        if args.command == "logs":
            if job["state"] == "succeeded" and job.get("command_name") == "diagnose_task":
                print(json.dumps({name: client.output(args.job_id, name + ".log")
                                  for name in ("stdout", "stderr")}, ensure_ascii=False, indent=2))
            elif job["state"] == "succeeded":
                print(client.output(args.job_id, "remote-job.json"))
            else:
                print(json.dumps(
                    client.call("read_job_log", job_id=args.job_id), ensure_ascii=False
                ))
            return 0
    job = client.wait(job, args.seconds if args.command == "wait" else args.wait)
    client.show(job)
    return 1 if job_state(job) in TERMINAL - {"succeeded"} else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", flush=True)
        raise SystemExit(2) from None
