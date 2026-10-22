"""Export public inputs, run Codex in an isolated SIF, freeze RTL, then judge privately."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import shutil
import signal
import stat
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from agent_entry import ModelSettings
from agent_proxy import ProxyEndpoint, start_unix
from evaluate import (
    IMAGES,
    ROOT,
    CVDPSplit,
    CVDPTask,
    Execution,
    JudgeSettings,
    Result,
    Status,
    comprehension_evaluate,
    cvdp_evaluate,
    cvdp_tasks,
    execute,
    prepare_cvdp_code,
    rtl_evaluate,
    summarize_scores,
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
    deleted: bool = False


@dataclass(frozen=True)
class AgentResult:
    task: str
    agent_returncode: int
    agent_timed_out: bool
    seconds: float
    submission: list[SubmissionFile]
    judge: Result | None
    error: str | None


def task_list(
    bench: str, identifiers: list[str] | None, task_format: str, cvdp_split: str = "nonagentic"
) -> list[Task]:
    if identifiers is not None and len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate task selection")
    tasks = []
    if bench == "cvdp":
        for official in cvdp_tasks(cvdp_split):
            if identifiers is not None and official.identifier not in identifiers:
                continue
            outputs = (
                ("subjective.txt",) if official.reference is not None else tuple(official.golden)
            )
            if not outputs:
                raise ValueError(f"No declared submission targets: {official.identifier}")
            for name in outputs:
                safe_name(name)
            # Public input/context and output paths only; no output values or harness.
            tasks.append(
                Task(
                    bench,
                    official.identifier,
                    official.prompt,
                    official.context,
                    outputs,
                    cvdp=official,
                )
            )
    elif bench == "rtllm":
        for source in sorted((ROOT / "data/rtllm").rglob("design_description.txt")):
            if identifiers is None or source.parent.name in identifiers:
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
            if identifiers is None or identifier in identifiers:
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
    if identifiers is not None and {task.identifier for task in tasks} != set(identifiers):
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


def freeze(
    workspace: Path,
    destination: Path,
    outputs: tuple[str, ...],
    originals: dict[str, str] | None = None,
) -> list[SubmissionFile]:
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
        if originals is not None and not source.exists() and name in originals:
            records.append(SubmissionFile(name, hashlib.sha256(b"").hexdigest(), 0, deleted=True))
            continue
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
    if originals is not None:
        patch = []
        for record in records:
            before = originals.get(record.path, "")
            after = "" if record.deleted else (destination / record.path).read_text()
            for line in difflib.unified_diff(
                before.splitlines(True),
                after.splitlines(True),
                fromfile="a/" + record.path if record.path in originals else "/dev/null",
                tofile="/dev/null" if record.deleted else "b/" + record.path,
            ):
                patch.append(
                    line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                )
        (destination / "changes.patch").write_text("".join(patch))
        (destination / "changes.patch").chmod(0o444)
    return records


def prepare_workspace(task: Task, workspace: Path) -> dict[str, str] | None:
    if task.cvdp is not None:
        prepare_cvdp_code(task.cvdp, workspace)
    else:
        write_public(workspace, task.context)
    write_public(workspace, {"problem.txt": task.prompt})
    if task.cvdp is None or not task.cvdp.is_patch:
        return None
    originals = {}
    for name in task.outputs:
        target = workspace / safe_name(name)
        if target.is_symlink() or not target.resolve().is_relative_to(workspace.resolve()):
            raise ValueError(f"Invalid baseline patch target: {name}")
        if target.exists():
            originals[name] = target.read_text()
    return originals


def task_instructions(task: Task) -> str:
    shared = (
        "Solve the task in /workspace/problem.txt. Work entirely in /workspace.\n"
        "You have Icarus Verilog (iverilog, vvp), Verilator, Yosys, Python and a shell.\n"
        "Do not search online, access hidden official tests/reference solutions, "
        "use external integrations or request other agents. "
        "Official hidden judging runs only after you finish.\n"
    )
    if task.cvdp is not None and task.cvdp.reference is not None:
        return shared + (
            "Inspect the public source context and write your final answer to subjective.txt.\n"
            "Only subjective.txt is submitted. Reference answers and judge feedback are hidden.\n"
        )
    return shared + (
        "Write your own testbench/reference model and run compile/simulation checks.\n"
        "For CVDP, /code and /workspace refer to the same directory.\n"
        f"Write the final files to these exact paths: {', '.join(task.outputs)}.\n"
        "Only these declared files are submitted; put self-tests/build artifacts under selftest/.\n"
        "Preserve required module interfaces. Do not include self-test modules in submitted RTL.\n"
        "For patch tasks, edit the source files directly; the runner freezes these files "
        "and creates changes.patch against the original public context.\n"
    )


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
    task: Task, work: Path, codex_home: Path, settings: ModelSettings, image: Path, probe_only: bool
) -> list[str]:
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
        (work / "workspace", "/code", "rw"),
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
            settings.model,
            "--effort",
            settings.effort,
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
    settings: ModelSettings,
    image_tag: str,
    timeout: int,
    judge_timeout: int,
    probe_only: bool,
    judge_settings: JudgeSettings | None = None,
) -> AgentResult:
    image = ROOT / "images" / IMAGES[task.benchmark].replace(".sif", f"-{image_tag}.sif")
    work = output / task.identifier
    work.mkdir()
    for name in ("workspace", "codex-home", "home", "control"):
        (work / name).mkdir(mode=0o700 if name == "codex-home" else 0o755)
    originals = prepare_workspace(task, work / "workspace")
    instructions = task_instructions(task)
    (work / "control/instructions.txt").write_text(instructions)
    (work / "control/model.json").write_text(json.dumps(asdict(settings)))
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
    if settings.base_url is None and not auth.is_file():
        raise ValueError(f"Missing file-backed login credentials: {auth}")
    records = []
    judged = None
    error = None
    execution: Execution | None = None
    proxy = None
    proxy_directory = None
    try:
        if settings.base_url is None:
            shutil.copyfile(auth, work / "codex-home/auth.json")
            (work / "codex-home/auth.json").chmod(0o600)
        # Do not mount user configuration, skills, plugins, hooks, MCP, memory, or session history.
        for directory in (work / "workspace", work / "codex-home", work / "home"):
            for target in [directory, *directory.rglob("*")]:
                if not target.is_symlink():
                    os.chown(target, 65534, 65534)
            directory.chmod(0o700)
        proxy_directory = tempfile.TemporaryDirectory(prefix="rtl-proxy-")
        Path(proxy_directory.name).chmod(0o755)
        (work / "proxy").symlink_to(proxy_directory.name, target_is_directory=True)
        proxy = start_unix(Path(proxy_directory.name) / "relay.sock", endpoint)
        execution = execute(
            agent_command(task, work, codex_home, settings, image, probe_only),
            work,
            work / "agent.jsonl",
            timeout,
        )
        (work / "agent-execution.json").write_text(json.dumps(asdict(execution), indent=2))
        if execution.returncode:
            error = "Agent timeout" if execution.timed_out else "Agent process failed"
        elif not probe_only:
            records = freeze(work / "workspace", work / "submission", task.outputs, originals)
            judge = work / "judge"
            judge.mkdir()
            if task.benchmark == "cvdp":
                assert task.cvdp is not None
                if task.cvdp.reference is not None:
                    judged = comprehension_evaluate(
                        task.cvdp,
                        judge,
                        work / "submission",
                        judge_timeout,
                        judge_settings or JudgeSettings(),
                        image,
                    )
                else:
                    judged = cvdp_evaluate(
                        task.cvdp,
                        judge,
                        judge_timeout,
                        False,
                        work / "submission",
                        image,
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
                    image,
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
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--task", action="append")
    selection.add_argument("--all", action="store_true")
    parser.add_argument(
        "--task-format", default="spec-to-rtl", choices=("spec-to-rtl", "code-complete-iccad2023")
    )
    parser.add_argument(
        "--cvdp-split", choices=[item.value for item in CVDPSplit] + ["all"], default="nonagentic"
    )
    parser.add_argument(
        "--judge-model", help="Independent LLM judge for comprehension cid009/cid010"
    )
    parser.add_argument("--judge-base-url", help="Private judge Chat Completions API base URL")
    parser.add_argument("--judge-api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--proxy-endpoint", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument(
        "--effort", choices=("low", "medium", "high", "xhigh", "max", "ultra"), default="high"
    )
    parser.add_argument("--model-base-url")
    parser.add_argument("--context-window", type=int)
    parser.add_argument("--auto-compact-limit", type=int)
    parser.add_argument("--image-tag", default="codex")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--judge-timeout", type=int, default=120)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args()
    if args.timeout < 1 or args.judge_timeout < 1 or args.jobs < 1:
        parser.error("Timeouts and --jobs must be positive")
    clean_environment()
    args.output = args.output.resolve()
    tasks = task_list(args.bench, args.task, args.task_format, args.cvdp_split)
    args.output.mkdir(parents=True, exist_ok=False)
    endpoint = ProxyEndpoint.read(args.proxy_endpoint)
    started = time.time()
    settings = ModelSettings(
        args.model, args.effort, args.model_base_url, args.context_window, args.auto_compact_limit
    )
    judge_settings = JudgeSettings(args.judge_model, args.judge_base_url, args.judge_api_key_env)
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {
            pool.submit(
                run_task,
                task,
                args.output,
                endpoint,
                args.codex_home,
                settings,
                args.image_tag,
                args.timeout,
                args.judge_timeout,
                args.probe_only,
                judge_settings,
            ): task
            for task in tasks
        }
        for future in as_completed(pending):
            task = pending[future]
            try:
                result = future.result()
            except Exception as error:
                result = AgentResult(task.identifier, -1, False, 0, [], None, str(error))
            results.append(result)
            print(
                f"{len(results)}/{len(tasks)} {task.identifier}: agent={result.agent_returncode} "
                f"judge={result.judge.status if result.judge else None} error={result.error}",
                flush=True,
            )
            summary = {
                "benchmark": args.bench,
                "model": args.model,
                "effort": args.effort,
                "task_format": args.task_format,
                "cvdp_split": args.cvdp_split if args.bench == "cvdp" else None,
                "judge_settings": asdict(judge_settings),
                "codex_home": str(args.codex_home),
                "settings": asdict(settings),
                "image_tag": args.image_tag,
                "jobs": args.jobs,
                "probe_only": args.probe_only,
                "expected_tasks": len(tasks),
                "complete": len(results) == len(tasks),
                "started": started,
                "results": [asdict(item) for item in sorted(results, key=lambda item: item.task)],
                "text_scores": [
                    asdict(score)
                    for score in summarize_scores(
                        [item.judge for item in results if item.judge is not None]
                    )
                ],
            }
            temporary = args.output / "summary.tmp"
            temporary.write_text(json.dumps(summary, indent=2))
            temporary.replace(args.output / "summary.json")
    passed = all(
        item.error is None
        and (
            args.probe_only
            or (item.judge is not None and item.judge.status in {Status.PASS, Status.SCORED})
        )
        for item in results
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
