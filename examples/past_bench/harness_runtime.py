"""Trusted process bridge for editable Hermes source; never imported by RSI infra.

The official runner still owns sessions, history, mock services and grading. A fresh
worker loads only the candidate Hermes for each episode, and writes learned state
to the HERMES_HOME supplied by the runner. This is process separation, not an OS
security sandbox: workers run under the operator's local account.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def source_digest(root: Path) -> str:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Harness source cannot contain symlinks")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.is_dir():
            raise ValueError("Harness source must contain regular files")
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def worker_environment() -> dict[str, str]:
    # Only the task model credential is delivered, via the session request on stdin.
    # This does not prevent arbitrary local code from reading the operator's files.
    return {
        **{
            key: value
            for key, value in os.environ.items()
            if not (
                key.startswith("RSI_")
                or any(
                    word in key.upper()
                    for word in (
                        "API_KEY",
                        "TOKEN",
                        "PASSWORD",
                        "SECRET",
                        "CREDENTIAL",
                        "AUTHORIZATION",
                    )
                )
            )
        },
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def terminate_group(process: subprocess.Popen[str]) -> None:
    # The group belongs only to this worker; include descendants spawned by tools.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def execute_worker(source: Path, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    expected = source_digest(source)
    with tempfile.TemporaryDirectory(prefix="past-harness-") as directory:
        staged = Path(directory).resolve() / "hermes"
        shutil.copytree(source, staged)
        before = source_digest(staged)
        if before != expected or expected != source_digest(source):
            raise ValueError("Harness changed while staging")
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(Path(__file__).resolve()),
                "--worker",
                "--harness",
                str(staged),
            ],
            cwd=staged,
            env=worker_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, _ = process.communicate(json.dumps(payload), timeout=timeout)
            if process.returncode:
                raise ValueError("Candidate Hermes worker failed; see private runtime log")
            result = json.loads(stdout)
            if not isinstance(result, dict) or not isinstance(result.get("response"), dict):
                raise ValueError("Invalid candidate runtime response")
            entry = Path(result.get("loaded_entry", "")).resolve()
            if entry != staged / "run_agent.py":
                raise ValueError("Runtime did not load the candidate Hermes entry")
            result["source_digest"] = before
            return result
        finally:
            terminate_group(process)


def candidate_worker(source: Path, payload: dict[str, Any]) -> dict[str, Any]:
    adapter_module: Any = importlib.import_module("past_bench.runtime.adapters.hermes")
    protocol = importlib.import_module("past_bench.runtime.protocol")
    registry = importlib.import_module("past_bench.runtime.registry")
    original = adapter_module._HERMES_ROOT_DEFAULT.resolve()
    sys.path[:] = [
        str(source),
        *[value for value in sys.path if not Path(value or ".").resolve().is_relative_to(original)],
    ]
    adapter_module._HERMES_ROOT_DEFAULT = source
    adapter = adapter_module.HermesAdapter(
        registry.AgentSpec.model_validate(payload["spec"]),
        protocol.StartSessionRequest.model_validate(payload["session"]),
    )
    try:
        response = adapter.step(protocol.StepRequest.model_validate(payload["step"]))
        loaded = Path(getattr(sys.modules.get("run_agent"), "__file__", "")).resolve()
        if loaded != source / "run_agent.py":
            raise ValueError("Candidate entry was not imported")
        # Editable installs must not silently fill deleted modules from the baseline.
        for module in list(sys.modules.values()):
            file = getattr(module, "__file__", None)
            if file and Path(file).resolve().is_relative_to(original):
                raise ValueError("Runtime fell back to the upstream Hermes source")
        return {"response": response.model_dump(mode="json"), "loaded_entry": str(loaded)}
    finally:
        adapter.close()


def adapter_class(source: Path, receipts: Path) -> type[Any]:
    protocol = importlib.import_module("past_bench.runtime.protocol")

    class ProcessAdapter:
        def __init__(self, spec: Any, request: Any):
            self.spec, self.request = spec, request
            self.response: Any = None

        def step(self, request: Any) -> Any:
            if self.response is None:
                payload = {
                    "spec": self.spec.model_dump(mode="json"),
                    "session": self.request.model_dump(mode="json"),
                    "step": request.model_dump(mode="json"),
                }
                result = execute_worker(source, payload, float(self.request.timeout_seconds) + 30)
                self.response = protocol.StepResponse.model_validate(result["response"])
                with receipts.open("a") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "session_id": self.request.session_id,
                                "task_id": self.request.task_id,
                                "source_digest": result["source_digest"],
                                "loaded_entry": "hermes/run_agent.py",
                            }
                        )
                        + "\n"
                    )
            return self.response

        def close(self, reason: str = "") -> None:
            pass  # Worker closes the official adapter before replying.

        def interrupt(self, reason: str = "") -> None:
            pass  # Synchronous step is bounded and interrupted by the runner's signal.

    return ProcessAdapter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", type=Path, required=True)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("runner_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    source = args.harness.resolve()
    if args.worker:
        payload = json.load(sys.stdin)
        # Hermes writes progress to stdout; keep the transport JSON-only.
        with contextlib.redirect_stdout(sys.stderr):
            result = candidate_worker(source, payload)
        print(json.dumps(result))
    else:
        if args.receipts is None:
            parser.error("--receipts is required for the official runner")
        manager = importlib.import_module("past_bench.runtime.manager")
        manager._ADAPTERS["hermes"] = adapter_class(source, args.receipts)
        cli = importlib.import_module("past_bench.cli")
        cli.main(args.runner_args[1:] if args.runner_args[:1] == ["--"] else args.runner_args)


if __name__ == "__main__":
    main()
