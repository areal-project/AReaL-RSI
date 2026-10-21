"""Export public inputs, run Codex in an isolated SIF, freeze RTL, then judge privately."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import stat
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from agent_proxy import ProxyEndpoint, start_unix
from evaluate import (
    IMAGES,
    ROOT,
    CVDPTask,
    Execution,
    Result,
    Status,
    cvdp_evaluate,
    execute,
    rtl_evaluate,
)


@dataclass(frozen=True)
class Task:
    benchmark: str
    identifier: str
    prompt: str
    context: dict[str, str]
    outputs: tuple[str, ...]
    source: Path | None = None
    cvdp: CVDPTask | None = None


@dataclass(frozen=True)
class SubmissionFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class AgentResult:
    task: str
    agent_returncode: int
    agent_timed_out: bool
    seconds: float
    submission: list[SubmissionFile]
    judge: Result | None
    error: str | None


def task_list(bench: str, identifiers: list[str], task_format: str) -> list[Task]:
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate task selection")
    tasks = []
    if bench == "cvdp":
        dataset = ROOT / "data/cvdp/cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl"
        for line in dataset.read_text().splitlines():
            raw = json.loads(line)
            if raw["id"] not in identifiers:
                continue
            official = CVDPTask.decode(raw)
            outputs = tuple(official.golden)
            if not outputs or any(
                Path(name).suffix not in {".v", ".sv", ".vh", ".svh"} for name in outputs
            ):
                raise ValueError("Agent entry currently supports CVDP RTL-generation outputs only")
            # Only input.context is public. output values and harness never enter the export.
            tasks.append(
                Task(
                    bench,
                    official.identifier,
                    raw["input"]["prompt"],
                    official.context,
                    outputs,
                    cvdp=official,
                )
            )
    elif bench == "rtllm":
        for source in sorted((ROOT / "data/rtllm").rglob("design_description.txt")):
            if source.parent.name in identifiers:
                tasks.append(
                    Task(
                        bench,
                        source.parent.name,
                        source.read_text(),
                        {},
                        ("submission.sv",),
                        source.parent,
                    )
                )
    else:
        for source in sorted((ROOT / "data/verilogeval" / task_format).glob("*_prompt.txt")):
            identifier = source.name.removesuffix("_prompt.txt")
            if identifier in identifiers:
                tasks.append(
                    Task(
                        bench,
                        identifier,
                        source.read_text(),
                        {},
                        ("submission.sv",),
                        source.with_name(identifier + "_ref.sv"),
                    )
                )
    if {task.identifier for task in tasks} != set(identifiers):
        raise ValueError("Unknown, duplicate or unsupported task selection")
    return tasks


def safe_name(name: str) -> Path:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
        raise ValueError(f"Invalid public/submission path: {name}")
    return Path(*path.parts)


def write_public(directory: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = directory / safe_name(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def freeze(workspace: Path, destination: Path, outputs: tuple[str, ...]) -> list[SubmissionFile]:
    destination.mkdir()
    records = []
    for name in outputs:
        relative = safe_name(name)
        source = workspace / relative
        # Reject links in every component, not just the leaf, and reject nonregular files.
        current = workspace
        for component in relative.parts:
            current /= component
            if current.is_symlink():
                raise ValueError(f"Symlink submission rejected: {name}")
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as file:
            metadata = os.fstat(file.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4 * 1024 * 1024:
                raise ValueError(f"Invalid RTL submission: {name}")
            data = file.read()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o444)
        records.append(SubmissionFile(name, hashlib.sha256(data).hexdigest(), len(data)))
    (destination / "manifest.json").write_text(
        json.dumps([asdict(record) for record in records], indent=2)
    )
    return records


def clean_environment() -> None:
    # Apptainer processes these on the host before --cleanenv takes effect.
    for key in list(os.environ):
        if key.startswith(("APPTAINER_", "SINGULARITY_")) or key in {
            "http_proxy",
            "https_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "no_proxy",
            "ALL_PROXY",
            "all_proxy",
        }:
            del os.environ[key]


def agent_command(
    task: Task, work: Path, codex_home: Path, model: str, effort: str, probe_only: bool
) -> list[str]:
    image = ROOT / "images" / IMAGES[task.benchmark].replace(".sif", "-codex.sif")
    command = [
        "apptainer",
        "exec",
        "--cleanenv",
        "--containall",
        "--no-home",
        "--no-mount",
        "hostfs,cwd,bind-paths,sys",
        "--net",
        "--network",
        "none",
        "--no-privs",
        "--security",
        "uid:65534,gid:65534",
        "--pwd",
        "/workspace",
    ]
    for source, target, mode in (
        (work / "workspace", "/workspace", "rw"),
        (work / "codex-home", str(codex_home), "rw"),
        (work / "home", "/agent-home", "rw"),
        (work / "control", "/control", "ro"),
        (work / "proxy", "/proxy", "ro"),
    ):
        command.extend(["--bind", f"{source}:{target}:{mode}"])
    for key, value in {
        "CODEX_HOME": str(codex_home),
        "HTTPS_PROXY": "http://127.0.0.1:1081",
        "HTTP_PROXY": "http://127.0.0.1:1081",
        "https_proxy": "http://127.0.0.1:1081",
        "http_proxy": "http://127.0.0.1:1081",
        "NO_PROXY": "localhost,127.0.0.1",
        "CODEX_DISABLE_UPDATE_CHECK": "1",
    }.items():
        command.extend(["--env", f"{key}={value}"])
    command.extend(
        [
            str(image),
            "/opt/venv/bin/python",
            "/opt/agent_entry.py",
            "--model",
            model,
            "--effort",
            effort,
        ]
    )
    if probe_only:
        command.append("--probe-only")
    return command


def run_task(
    task: Task,
    output: Path,
    endpoint: ProxyEndpoint,
    codex_home: Path,
    model: str,
    effort: str,
    timeout: int,
    judge_timeout: int,
    probe_only: bool,
) -> AgentResult:
    work = output / task.identifier
    work.mkdir()
    for name in ("workspace", "codex-home", "home", "control"):
        (work / name).mkdir(mode=0o700 if name == "codex-home" else 0o755)
    write_public(work / "workspace", {"problem.txt": task.prompt, **task.context})
    instructions = (
        "Implement the hardware task in /workspace/problem.txt. Work entirely in /workspace.\n"
        "You have Icarus Verilog (iverilog, vvp), Verilator, Python and a shell.\n"
        "Write your own testbench/reference model and run compile/simulation checks "
        "before finishing.\n"
        "Do not search online, access official tests/reference solutions, "
        "use external integrations, "
        "or request other agents. Official hidden tests are run only after you finish.\n"
        f"Write the final RTL to these exact files: {', '.join(task.outputs)}. "
        "Only these files are submitted; put self-tests under selftest/.\n"
        "Preserve the module interface in the problem. "
        "Self-test modules must not appear in submitted RTL.\n"
    )
    (work / "control/instructions.txt").write_text(instructions)
    denied = [
        str(ROOT),
        str(ROOT.parent.parent / ".git"),
        str(ROOT / "data"),
        str(ROOT / "source"),
        str(ROOT / "runs"),
        str(codex_home / "sessions"),
        str(codex_home / "history.jsonl"),
        str(codex_home / "memories"),
        str(codex_home / "config.toml"),
        "/proc/1/root" + str(ROOT),
    ]
    # An actual symlink escape must also fail under the exact Agent mount set.
    (work / "workspace/host-escape").symlink_to(ROOT)
    denied.append("/workspace/host-escape/data")
    (work / "control/denied.json").write_text(json.dumps(denied))
    auth = codex_home / "auth.json"
    if not auth.is_file():
        raise ValueError(f"Missing file-backed login credentials: {auth}")
    records = []
    judged = None
    error = None
    execution: Execution | None = None
    proxy = None
    proxy_directory = None
    try:
        shutil.copyfile(auth, work / "codex-home/auth.json")
        # Do not mount user configuration, skills, plugins, hooks, MCP, memory, or session history.
        for directory in (work / "workspace", work / "codex-home", work / "home"):
            for target in [directory, *directory.rglob("*")]:
                if not target.is_symlink():
                    os.chown(target, 65534, 65534)
            directory.chmod(0o700)
        (work / "codex-home/auth.json").chmod(0o600)
        proxy_directory = tempfile.TemporaryDirectory(prefix="rtl-proxy-")
        Path(proxy_directory.name).chmod(0o755)
        (work / "proxy").symlink_to(proxy_directory.name, target_is_directory=True)
        proxy = start_unix(Path(proxy_directory.name) / "relay.sock", endpoint)
        execution = execute(
            agent_command(task, work, codex_home, model, effort, probe_only),
            work,
            work / "agent.jsonl",
            timeout,
        )
        (work / "agent-execution.json").write_text(json.dumps(asdict(execution), indent=2))
        if execution.returncode:
            error = "Agent timeout" if execution.timed_out else "Agent process failed"
        elif not probe_only:
            records = freeze(work / "workspace", work / "submission", task.outputs)
            judge = work / "judge"
            judge.mkdir()
            if task.benchmark == "cvdp":
                assert task.cvdp is not None
                judged = cvdp_evaluate(
                    task.cvdp,
                    judge,
                    judge_timeout,
                    False,
                    work / "submission",
                    ROOT / "images" / IMAGES[task.benchmark].replace(".sif", "-codex.sif"),
                )
            else:
                assert task.source is not None
                judged = rtl_evaluate(
                    task.benchmark,
                    task.source,
                    judge,
                    judge_timeout,
                    False,
                    True,
                    work / "submission" / task.outputs[0],
                    ROOT / "images" / IMAGES[task.benchmark].replace(".sif", "-codex.sif"),
                )
    except (OSError, ValueError, RuntimeError) as exc:
        error = str(exc)
        if execution is None:
            raise
    finally:
        if proxy is not None:
            proxy.shutdown()
            proxy.server_close()
        if proxy_directory is not None:
            proxy_directory.cleanup()
        (work / "proxy").unlink(missing_ok=True)
        # Credentials are never an output artifact. Source CODEX_HOME remains untouched.
        shutil.rmtree(work / "codex-home")
    assert execution is not None
    result = AgentResult(
        task.identifier,
        execution.returncode,
        execution.timed_out,
        execution.seconds,
        records,
        judged,
        error,
    )
    (work / "result.json").write_text(json.dumps(asdict(result), indent=2))
    return result


def main() -> None:
    def interrupted(signum: int, frame: object) -> None:
        raise KeyboardInterrupt(f"Agent controller received signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench", required=True, choices=tuple(IMAGES))
    parser.add_argument("--task", action="append", required=True)
    parser.add_argument(
        "--task-format", default="spec-to-rtl", choices=("spec-to-rtl", "code-complete-iccad2023")
    )
    parser.add_argument("--proxy-endpoint", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh"), default="high")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--judge-timeout", type=int, default=120)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args()
    if args.timeout < 1 or args.judge_timeout < 1:
        parser.error("Timeouts must be positive")
    clean_environment()
    args.output = args.output.resolve()
    tasks = task_list(args.bench, args.task, args.task_format)
    args.output.mkdir(parents=True, exist_ok=False)
    endpoint = ProxyEndpoint.read(args.proxy_endpoint)
    started = time.time()
    results = []
    for task in tasks:
        result = run_task(
            task,
            args.output,
            endpoint,
            args.codex_home,
            args.model,
            args.effort,
            args.timeout,
            args.judge_timeout,
            args.probe_only,
        )
        results.append(result)
        print(
            f"{task.identifier}: agent={result.agent_returncode} "
            f"judge={result.judge.status if result.judge else None} error={result.error}",
            flush=True,
        )
        summary = {
            "benchmark": args.bench,
            "model": args.model,
            "effort": args.effort,
            "task_format": args.task_format,
            "codex_home": str(args.codex_home),
            "probe_only": args.probe_only,
            "expected_tasks": len(tasks),
            "complete": len(results) == len(tasks),
            "started": started,
            "results": [asdict(item) for item in results],
        }
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    passed = all(
        item.error is None
        and (args.probe_only or (item.judge is not None and item.judge.status == Status.PASS))
        for item in results
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
