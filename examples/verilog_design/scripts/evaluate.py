"""Run golden or submitted RTL in SIFs; keep official tests and record failures."""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {
    "rtllm": "rtllm-v2.1-icarus.sif",
    "verilogeval": "verilogeval-v2.sif",
    "cvdp": "cvdp-v1.1.0-oss.sif",
}


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    COMPILE_ERROR = "compile_error"
    TIMEOUT = "timeout"
    ERROR = "error"
    NO_GOLDEN = "no_golden"


@dataclass(frozen=True)
class Execution:
    command: list[str]
    returncode: int
    seconds: float
    timed_out: bool
    log: str


@dataclass(frozen=True)
class Result:
    task: str
    status: Status
    detail: str
    executions: list[Execution]


@dataclass(frozen=True)
class Bind:
    source: Path
    target: str
    readonly: bool = False


@dataclass(frozen=True)
class Service:
    name: str
    command: list[str]
    cwd: str
    environment: dict[str, str]
    env_files: list[str]
    volumes: list[str]


@dataclass(frozen=True)
class CVDPTask:
    identifier: str
    categories: list[str]
    context: dict[str, str]
    golden: dict[str, str]
    is_patch: bool
    harness: dict[str, str]
    repo: str | None
    commit: str | None
    root_dir: str | None

    @classmethod
    def decode(cls, row: dict) -> CVDPTask:
        categories = row["categories"]
        if set(categories) & {"cid012", "cid013", "cid014"}:
            raise ValueError(f"Commercial datapoint rejected: {row['id']}")
        agentic = "patch" in row
        context = row.get("context", {}) if agentic else row.get("input", {}).get("context", {})
        golden = row["patch"] if agentic else row.get("output", {}).get("context", {})
        harness = row.get("harness", {})
        harness = harness.get("files", harness)
        repo = context.get("repo")
        return cls(
            row["id"],
            categories,
            {} if repo else context,
            golden,
            agentic,
            harness,
            repo,
            context.get("commit") if repo else None,
            context.get("root_dir") if repo else None,
        )


def execute(command: list[str], cwd: Path, log: Path, timeout: int) -> Execution:
    start = time.monotonic()
    timed_out = False
    # A process group includes vvp/Verilator/compiler children; timeouts kill the whole group.
    with log.open("w") as stream:
        process = subprocess.Popen(
            command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            returncode = process.wait()
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
    return Execution(command, returncode, time.monotonic() - start, timed_out, str(log))


def container_command(
    image: Path, binds: list[Bind], cwd: str, command: list[str], env: dict[str, str] | None = None
) -> list[str]:
    args = ["apptainer", "exec", "--cleanenv", "--containall", "--no-mount", "hostfs,cwd"]
    for bind in binds:
        mode = "ro" if bind.readonly else "rw"
        args.extend(["--bind", f"{bind.source}:{bind.target}:{mode}"])
    for key, value in (env or {}).items():
        args.extend(["--env", f"{key}={value}"])
    return [*args, "--pwd", cwd, str(image), *command]


def rtl_evaluate(
    bench: str,
    task: Path,
    out: Path,
    timeout: int,
    negative: bool,
    icarus_compat: bool,
    candidate: Path | None = None,
    image: Path | None = None,
) -> Result:
    identifier = task.name if bench == "rtllm" else task.name.removesuffix("_ref.sv")
    work = out / identifier
    work.mkdir()
    image = image or ROOT / "images" / IMAGES[bench]
    preparation_runs = []
    if bench == "rtllm":
        shutil.copytree(task, work / "files")
        files = work / "files"
        references = sorted(files.glob("verified*.v"))
        if not references:
            return Result(identifier, Status.NO_GOLDEN, "No official verified RTL", [])
        adaptations = []
        for reference in references:
            original = reference.read_text()
            # Some official golden modules carry a verified_ prefix; tests use the task ABI.
            modules = re.findall(r"\bmodule\s+(verified_\w+)", original)
            adapted = original
            for module in modules:
                adapted = re.sub(rf"\b{re.escape(module)}\b", identifier, adapted)
            if adapted != original:
                reference.write_text(adapted)
                adaptations.extend(
                    difflib.unified_diff(
                        original.splitlines(True),
                        adapted.splitlines(True),
                        fromfile=reference.name,
                        tofile=reference.name,
                    )
                )
        compat_patch = ROOT / "compat" / (identifier + ".patch")
        if icarus_compat and compat_patch.exists():
            shutil.copy2(compat_patch, files / "icarus-compat.patch")
            patch_command = container_command(
                image,
                [Bind(files, "/workspace")],
                "/workspace",
                ["patch", "--batch", "-p0", "-i", "icarus-compat.patch"],
            )
            patched = execute(patch_command, work, work / "compat.log", timeout)
            preparation_runs.append(patched)
            if patched.returncode:
                raise ValueError(f"Icarus syntax patch failed: {patched.log}")
            adaptations.append(compat_patch.read_text())
        (work / "adaptations.patch").write_text("".join(adaptations))
        if candidate is not None:
            for reference in references:
                reference.unlink()
            shutil.copy2(candidate, files / "candidate.sv")
            references = [files / "candidate.sv"]
        sources = [p.name for p in references] + ["testbench.v"]
        modules = re.findall(r"^\s*module\s+(\w+)", (files / "testbench.v").read_text(), re.M)
        if not modules:
            raise ValueError("No testbench module found")
        top = modules[0]
    else:
        files = work / "files"
        files.mkdir()
        reference = task.read_text()
        # Official tests instantiate RefModule and TopModule side by side.
        if candidate is None:
            (files / "candidate.sv").write_text(re.sub(r"\bRefModule\b", "TopModule", reference))
        else:
            shutil.copy2(candidate, files / "candidate.sv")
        shutil.copy2(task, files / task.name)
        test = task.with_name(identifier + "_test.sv")
        shutil.copy2(test, files / test.name)
        sources = ["candidate.sv", test.name, task.name]
        top = "tb"
    if negative:
        (files / sources[0]).write_text("deliberately invalid Verilog;\n")
    binds = [Bind(files, "/workspace")]
    compile_cmd = container_command(
        image,
        binds,
        "/workspace",
        ["iverilog", "-Wall", "-g2012", "-s", top, "-o", "simulation", *sources],
    )
    compile_run = execute(compile_cmd, work, work / "compile.log", timeout)
    runs = [*preparation_runs, compile_run]
    if compile_run.timed_out:
        return Result(identifier, Status.TIMEOUT, "Compilation timeout", runs)
    if compile_run.returncode:
        return Result(identifier, Status.COMPILE_ERROR, "Compilation failed", runs)
    sim_cmd = container_command(image, binds, "/workspace", ["vvp", "-n", "simulation"])
    simulation = execute(sim_cmd, work, work / "simulation.log", timeout)
    runs.append(simulation)
    log = (work / "simulation.log").read_text(errors="replace")
    if simulation.timed_out:
        return Result(identifier, Status.TIMEOUT, "Simulation timeout", runs)
    if simulation.returncode:
        return Result(identifier, Status.FAIL, "Simulation returned nonzero", runs)
    if bench == "verilogeval":
        passed = re.search(r"^Mismatches: 0 in \d+ samples$", log, re.MULTILINE) is not None
    else:
        # Use each official testbench's terminal success message, never exit status alone.
        passed = re.search(r"\bpass(?:ed)?\b", log, re.IGNORECASE) is not None
        bad = r"\b(?:error|failed|failure|mismatch)\b|\b[1-9]\d*\s*/\s*\d+\s+failures"
        passed = passed and re.search(bad, log, re.IGNORECASE) is None
    return Result(identifier, Status.PASS if passed else Status.FAIL, "Official test output", runs)


def services(task: CVDPTask) -> list[Service]:
    config = yaml.safe_load(task.harness["docker-compose.yml"])
    result = []
    for name, raw in config["services"].items():
        allowed = {"image", "build", "volumes", "working_dir", "env_file", "command", "networks"}
        if set(raw) - allowed:
            raise ValueError(f"Unsupported service options: {set(raw) - allowed}")
        image = raw.get("image", "__OSS_SIM_IMAGE__")
        if image not in {"__OSS_SIM_IMAGE__", "__OSS_PNR_IMAGE__"}:
            raise ValueError(f"Unsupported service image: {image}")
        command = raw["command"]
        command = shlex.split(command) if isinstance(command, str) else command
        env_files = raw.get("env_file", [])
        env_files = [env_files] if isinstance(env_files, str) else env_files
        result.append(
            Service(
                name,
                command,
                raw.get("working_dir", "/code/rundir"),
                {},
                env_files,
                raw.get("volumes", []),
            )
        )
    for path, content in task.harness.items():
        if "Dockerfile" not in path:
            continue
        # All published OSS Dockerfiles use the shared toolchain, with optional pytest setup.
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "FROM __OSS_")):
                continue
            if line.startswith(
                (
                    "ADD https://bootstrap.pypa.io/",
                    "RUN python3 ./get-pip.py",
                    "RUN python3 -m pip install pytest==8.3.2",
                )
            ):
                continue
            raise ValueError(f"Unaccounted Dockerfile instruction in {path}: {line}")
    return result


def write_files(directory: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = (directory / name).resolve()
        if not target.is_relative_to(directory.resolve()):
            raise ValueError(f"File outside workspace: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def cvdp_evaluate(
    task: CVDPTask,
    out: Path,
    timeout: int,
    negative: bool,
    candidate: Path | None = None,
    image: Path | None = None,
) -> Result:
    if candidate is None and (not task.golden or not all(task.golden.values())):
        return Result(task.identifier, Status.NO_GOLDEN, "Official public solution is empty", [])
    work = out / task.identifier
    work.mkdir()
    code = work / "code"
    code.mkdir()
    if task.repo:
        bundle = ROOT / "data/cvdp/cvdp_v1.1.0_agentic_heavy_code_generation_public"
        subprocess.run(
            ["git", "clone", str(bundle / (task.repo + ".bundle")), str(code)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(code), "checkout", task.commit], check=True, capture_output=True
        )
    write_files(code, task.context)
    solution = (
        task.golden
        if candidate is None
        else {name: (candidate / name).read_text() for name in task.golden}
    )
    if task.is_patch and candidate is None:
        spec = importlib.util.spec_from_file_location(
            "official_diff", ROOT / "source/cvdp/src/merge_in_memory.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        solution = {}
        for name, patch in task.golden.items():
            original = (code / name).read_text() if (code / name).exists() else ""
            solution[name] = module.diff_apply(original, patch)
    write_files(code, solution)
    if negative:
        write_files(code, {next(iter(solution)): "deliberately invalid Verilog;\n"})
    harness = work / "harness"
    harness.mkdir()
    write_files(harness, task.harness)
    for directory in ("rtl", "verif", "docs", "rundir", "src"):
        (code / directory).mkdir(exist_ok=True)
    runs = []
    for service in services(task):
        environment = dict(service.environment)
        for name in service.env_files:
            for line in (harness / name).read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    environment[key.strip()] = value.strip().strip("\"'")
        binds = [Bind(code, "/code"), Bind(code / "rundir", "/rundir")]
        for volume in service.volumes:
            parts = volume.split(":")
            source = (harness / parts[0]).resolve()
            if not source.exists():
                raise ValueError(f"Missing service volume: {source}")
            binds.append(Bind(source, parts[1], len(parts) > 2 and parts[2] == "ro"))
        command = container_command(
            image or ROOT / "images" / IMAGES["cvdp"],
            binds,
            service.cwd,
            service.command,
            environment,
        )
        run = execute(command, work, work / (service.name + ".log"), timeout)
        runs.append(run)
        if run.timed_out:
            return Result(task.identifier, Status.TIMEOUT, service.name, runs)
        if run.returncode:
            return Result(task.identifier, Status.FAIL, service.name, runs)
    return Result(task.identifier, Status.PASS, "All official harness services passed", runs)


def cvdp_audit(out: Path) -> None:
    records = []
    for dataset in sorted((ROOT / "data/cvdp").glob("*no_commercial.jsonl")):
        for line in dataset.read_text().splitlines():
            task = CVDPTask.decode(json.loads(line))
            try:
                plans = services(task)
                error = None
            except (ValueError, KeyError, yaml.YAMLError) as exc:
                plans = []
                error = str(exc)
            records.append(
                {
                    "id": task.identifier,
                    "dataset": dataset.name,
                    "golden_available": bool(task.golden) and all(task.golden.values()),
                    "services": [asdict(service) for service in plans],
                    "repo": task.repo,
                    "commit": task.commit,
                    "error": error,
                }
            )
    (out / "cvdp-audit.json").write_text(json.dumps(records, indent=2) + "\n")


def comprehension_checks(out: Path) -> list[Result]:
    """Exercise official objective metrics on golden answers, without an LLM judge."""
    spec = importlib.util.spec_from_file_location(
        "official_subjective", ROOT / "source/cvdp/src/subjective.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dataset = ROOT / "data/cvdp/cvdp_v1.1.0_nonagentic_code_comprehension.jsonl"
    scores = []
    results = []
    for line in dataset.read_text().splitlines():
        row = json.loads(line)
        reference = row["output"]["response"]
        bleu = module.calculate_BLEU(reference, reference, 2)
        rouge = module.calculate_ROUGE(reference, reference, 2)
        scores.append({"id": row["id"], "bleu": bleu, "rouge": rouge})
        ok = abs(bleu - 1.0) < 1e-9 and 0.0 <= rouge <= 1.0
        results.append(
            Result(
                row["id"],
                Status.PASS if ok else Status.FAIL,
                "Official objective metric self-check; LLM judge not run",
                [],
            )
        )
    (out / "comprehension-metrics.json").write_text(json.dumps(scores, indent=2) + "\n")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench", choices=IMAGES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--task", help="Run just one task ID")
    parser.add_argument("--negative-control", action="store_true")
    parser.add_argument(
        "--comprehension",
        action="store_true",
        help="CVDP objective metric environment check; does not run an LLM judge",
    )
    parser.add_argument(
        "--icarus-compat",
        action="store_true",
        help="Apply the three documented RTLLM testbench syntax patches",
    )
    parser.add_argument(
        "--task-format", default="spec-to-rtl", choices=("spec-to-rtl", "code-complete-iccad2023")
    )
    args = parser.parse_args()
    if args.jobs < 1 or args.timeout < 1:
        parser.error("--jobs and --timeout must be positive")
    if args.comprehension and (args.bench != "cvdp" or args.negative_control or args.task):
        parser.error("--comprehension requires --bench cvdp and no task/negative control")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    if not (ROOT / "images" / IMAGES[args.bench]).is_file():
        parser.error(f"Missing image: {IMAGES[args.bench]}")
    results = []
    if args.comprehension:
        results = comprehension_checks(args.output)
    elif args.bench == "cvdp":
        cvdp_audit(args.output)
        tasks = []
        for dataset in sorted((ROOT / "data/cvdp/examples").glob("*no_commercial_with_solutions*")):
            tasks.extend(
                CVDPTask.decode(json.loads(line)) for line in dataset.read_text().splitlines()
            )
        if args.task:
            tasks = [task for task in tasks if task.identifier == args.task]
        for task in tasks:
            try:
                result = cvdp_evaluate(task, args.output, args.timeout, args.negative_control)
            except Exception as exc:
                result = Result(task.identifier, Status.ERROR, str(exc), [])
            results.append(result)
            print(f"{result.task}: {result.status} {result.detail}", flush=True)
    else:
        if args.bench == "rtllm":
            tasks = sorted(p.parent for p in (ROOT / "data/rtllm").rglob("testbench.v"))
        else:
            tasks = sorted((ROOT / "data/verilogeval" / args.task_format).glob("*_ref.sv"))
        if args.task:
            tasks = [p for p in tasks if p.name.removesuffix("_ref.sv") == args.task]

        def run(task: Path) -> Result:
            try:
                return rtl_evaluate(
                    args.bench,
                    task,
                    args.output,
                    args.timeout,
                    args.negative_control,
                    args.icarus_compat,
                )
            except Exception as exc:
                return Result(task.name, Status.ERROR, str(exc), [])

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for result in pool.map(run, tasks):
                results.append(result)
                print(f"{result.task}: {result.status} {result.detail}", flush=True)
    if not results:
        parser.error("No tasks selected")
    summary = {
        "benchmark": args.bench,
        "negative_control": args.negative_control,
        "comprehension_metric_check": args.comprehension,
        "icarus_compat": args.icarus_compat,
        "task_format": args.task_format if args.bench == "verilogeval" else None,
        "image": str(ROOT / "images" / IMAGES[args.bench]),
        "counts": {status.value: sum(r.status == status for r in results) for status in Status},
        "results": [asdict(result) for result in results],
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    success = all(r.status == Status.PASS for r in results)
    if args.negative_control:
        success = all(r.status in {Status.COMPILE_ERROR, Status.FAIL} for r in results)
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
