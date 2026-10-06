"""AReaL development rollouts: edit candidate source and run independent Bench acceptance."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from areal_rsi.api.control import decode, json_bytes, read_record
from areal_rsi.api.execution import AgentExecutionSpec, McpTool
from areal_rsi.infra.runtime import run_process
from areal_rsi.infra.runtime.claude import ClaudeExecutionError, read_result, run_claude
from areal_rsi.infra.storage.files import write_bytes
from areal_rsi.scripts.train import TrainingPlan, load_config


@dataclass(frozen=True, slots=True)
class BenchTask:
    task_key: str
    model_id: str
    task_id: str
    task_revision: str
    instruction: str
    content_sha256: str

    def __post_init__(self) -> None:
        if (
            not all((self.task_key, self.model_id, self.task_id, self.task_revision))
            or not self.instruction.strip()
            or len(self.content_sha256) != 64
        ):
            raise ValueError("Invalid adaptation task identity")
        bytes.fromhex(self.content_sha256)


@dataclass(frozen=True, slots=True)
class RuntimeBinding:
    task_key: str
    runtime_path: str | None
    baseline_root: str
    environment_path: str | None = None
    task_name: str | None = None
    runner_path: str | None = None
    runner_sha256: str | None = None
    evaluator_root: str | None = None

    def __post_init__(self) -> None:
        paths = (self.baseline_root, self.runtime_path, self.environment_path, self.evaluator_root)
        if not self.task_key or any(
            path is not None and not Path(path).is_absolute() for path in paths
        ):
            raise ValueError("Runtime binding needs absolute evaluator and baseline paths")
        if self.runner_path is None:
            if (
                self.runtime_path is None
                or self.environment_path is not None
                or self.task_name is not None
            ):
                raise ValueError("Legacy task needs a site runtime")
        elif (
            self.runtime_path is not None
            or self.environment_path is None
            or self.task_name != self.task_key.rsplit("@", 1)[0]
            or Path(self.runner_path).is_absolute()
            or ".." in Path(self.runner_path).parts
            or not self.runner_sha256
            or len(self.runner_sha256) != 64
        ):
            raise ValueError("Task runner needs a pinned entrypoint and environment")


@dataclass(frozen=True, slots=True)
class BenchInputs:
    dataset_sha256: str
    tasks: tuple[BenchTask, ...]
    train_task_keys: tuple[str, ...]
    test_task_keys: tuple[str, ...]
    evaluation_scope: str

    def __post_init__(self) -> None:
        keys = tuple(task.task_key for task in self.tasks)
        if (
            not keys
            or len(set(keys)) != len(keys)
            or self.train_task_keys != keys
            or self.test_task_keys != keys
            or self.evaluation_scope != "same_tasks_overfit"
        ):
            raise ValueError("Overfit requires a nonempty set of identical train/test tasks")


@dataclass(frozen=True, slots=True)
class BenchGrade:
    score: int
    verified: bool
    status: str
    scope: str
    result_path: str
    result_sha256: str


class BenchEnvironmentError(RuntimeError):
    """A missing/failed site runtime is not a zero-reward candidate answer."""


def freeze_inputs(dataset: Path, destination: Path) -> BenchInputs:
    raw = dataset.read_bytes()
    tasks = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        # Exported records are a plugin boundary. Decode the fields used internally
        # into a named contract; retain the complete export as immutable evidence.
        tasks.append(
            decode(
                BenchTask,
                {
                    "task_key": row["id"],
                    "model_id": row["model_id"],
                    "task_id": row["task_id"],
                    "task_revision": row["task_revision"],
                    "instruction": row["instruction"],
                    "content_sha256": row["content_sha256"],
                },
            )
        )
    keys = tuple(task.task_key for task in tasks)
    inputs = BenchInputs(
        hashlib.sha256(raw).hexdigest(), tuple(tasks), keys, keys, "same_tasks_overfit"
    )
    write_bytes(destination / "dataset.jsonl", raw, immutable=True)
    write_bytes(destination / "inputs.json", json_bytes(inputs), immutable=True)
    return inputs


def runtime_bindings(path: Path, inputs: BenchInputs) -> dict[str, RuntimeBinding]:
    rows = json.loads(path.read_bytes())
    bindings = decode(
        tuple[RuntimeBinding, ...],
        [
            {
                "environment_path": None,
                "task_name": None,
                "runner_path": None,
                "runner_sha256": None,
                "evaluator_root": None,
                **row,
            }
            for row in rows
        ],
    )
    result = {binding.task_key: binding for binding in bindings}
    if len(result) != len(bindings) or set(result) != set(inputs.train_task_keys):
        raise ValueError("Runtime registry must cover exactly the selected frozen tasks")
    return result


def public_input_files(row: dict[str, Any]) -> dict[str, str]:
    """Resolve only public, hash-bound inputs from a Bench export boundary."""
    files = decode(dict[str, str], row.get("source_files", {}))
    assets = list(row.get("task_assets", {}).values())
    config = row.get("model_asset", {}).get("config")
    if config is not None:
        assets.append(config)
    assets.extend(value["dataset"].get("source", {}) for value in row.get("datasets", {}).values())
    for asset in assets:
        path = asset.get("path")
        if path is None:
            continue  # External weights/data are supplied by the site evaluator.
        digest = asset.get("sha256")
        if (
            not isinstance(path, str)
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or not isinstance(digest, str)
            or len(digest) != 64
            or (path in files and files[path] != digest)
        ):
            raise BenchEnvironmentError("Public task input lacks a consistent content identity")
        files[path] = digest
    return files


def grade_candidate(
    bench_root: Path, task: BenchTask, candidate: Path, binding: RuntimeBinding, output: Path
) -> BenchGrade:
    evaluator_root = Path(binding.evaluator_root) if binding.evaluator_root else bench_root
    timeout: int | None = None
    result_path = output
    command: tuple[str, ...]
    if binding.runner_path is not None:
        runner = evaluator_root / binding.runner_path
        if (
            not runner.resolve().is_relative_to(evaluator_root.resolve())
            or not runner.is_file()
            or hashlib.sha256(runner.read_bytes()).hexdigest() != binding.runner_sha256
        ):
            raise BenchEnvironmentError("Task runner changed after freezing")
        environment = Path(binding.environment_path or "")
        if not environment.is_file():
            raise BenchEnvironmentError(f"Task environment missing for {task.task_key}")
        # Native task runners own experiment timeouts and create a fresh directory.
        artifacts = output.with_suffix(".artifacts")
        result_path = artifacts / "result.json"
        command = (
            sys.executable,
            str(runner),
            "--code",
            str(candidate),
            "--environment",
            str(environment),
            "--output",
            str(artifacts),
        )
    else:
        runtime_path = Path(binding.runtime_path or "")
        if not runtime_path.is_file():
            raise BenchEnvironmentError(f"Site runtime missing for {task.task_key}: {runtime_path}")
        runtime = json.loads(runtime_path.read_bytes())
        duration = runtime.get("timeout_seconds")
        if type(duration) is not int or duration < 1:
            raise BenchEnvironmentError("Invalid site evaluation timeout")
        timeout = 2 * duration + 60
        command = (
            sys.executable,
            str(evaluator_root / "evaluate.py"),
            "--model",
            task.model_id,
            "--task",
            task.task_id,
            "--candidate",
            str(candidate),
            "--runtime",
            str(runtime_path),
            "--output",
            str(output),
        )
    with output.with_suffix(".log").open("wb") as log:
        completed = run_process(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    if result_path.is_symlink() or not result_path.is_file():
        raise BenchEnvironmentError("Benchmark execution omitted its grade")
    raw = result_path.read_bytes()
    result = json.loads(raw)
    if result.get("status") == "ENVIRONMENT_ERROR":
        raise BenchEnvironmentError(str(result.get("reason", "Site collection failed")))
    if (
        result.get("scope") != "fresh_evaluation"
        or type(result.get("score")) is not int
        or result["score"] not in (0, 1)
        or result.get("verified") is not bool(result["score"])
        or result.get("task_content_sha256") != task.content_sha256
        or completed.returncode != (0 if result["score"] else 1)
    ):
        raise BenchEnvironmentError("Benchmark grade lacks fresh independent task evidence")
    if binding.runner_path is None:
        if not result.get("collector_sha256"):
            raise BenchEnvironmentError("Benchmark grade omitted collector identity")
    else:
        if (
            result.get("task_name") != binding.task_name
            or result.get("model_id") != task.model_id
            or result.get("task_id") != task.task_id
            or result.get("task_revision") != task.task_revision
            or result.get("artifacts_dir") != str(result_path.parent.resolve())
            or not result.get("run_id")
            or not result.get("candidate_sha256")
        ):
            raise BenchEnvironmentError("Task runner omitted fresh execution identity")
        evidence_hashes = decode(dict[str, str], result.get("evidence_sha256"))
        if set(evidence_hashes) != {"observations.json", "reference.json"}:
            raise BenchEnvironmentError("Task runner omitted independent evidence")
        for name, digest in evidence_hashes.items():
            evidence_path = result_path.parent / name
            if (
                evidence_path.is_symlink()
                or not evidence_path.is_file()
                or hashlib.sha256(evidence_path.read_bytes()).hexdigest() != digest
            ):
                raise BenchEnvironmentError("Task evidence changed after grading")
            evidence = json.loads(evidence_path.read_bytes())
            if any(evidence.get(key) != result[key] for key in ("run_id", "candidate_sha256")):
                raise BenchEnvironmentError("Task evidence belongs to another execution")
    return BenchGrade(
        result["score"],
        result["verified"],
        str(result["status"]),
        result["scope"],
        str(result_path),
        hashlib.sha256(raw).hexdigest(),
    )


def copy_candidate(source: Path, destination: Path) -> None:
    ignored = shutil.ignore_patterns(
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "data",
        "experiments",
        "checkpoints",
        "*.pyc",
    )
    shutil.copytree(source, destination, ignore=ignored, symlinks=True)
    if any(path.is_symlink() for path in destination.rglob("*")):
        raise ValueError("Candidate baseline contains symbolic links")


class AdaptationBenchWorkflow:
    """A native AReaL agent workflow; rewards only independently collected grades."""

    def __init__(
        self,
        *,
        inputs_root: str,
        runtime_registry: str,
        bench_root: str,
        run_root: str,
        claude_cli: str,
        model: str,
        harness_prompt: str,
        timeout_seconds: int | None = None,
        max_budget_usd: float | None = None,
    ) -> None:
        self.inputs = read_record(Path(inputs_root) / "inputs.json", BenchInputs)
        raw = (Path(inputs_root) / "dataset.jsonl").read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.inputs.dataset_sha256:
            raise ValueError("Frozen benchmark export changed")
        self.source_files: dict[str, str] = {}
        for line in raw.splitlines():
            row = json.loads(line)
            for path, digest in public_input_files(row).items():
                if path in self.source_files and self.source_files[path] != digest:
                    raise ValueError("Public benchmark asset has conflicting identities")
                self.source_files[path] = digest
        self.tasks = {task.task_key: task for task in self.inputs.tasks}
        self.bindings = runtime_bindings(Path(runtime_registry), self.inputs)
        for line in raw.splitlines():
            row = json.loads(line)
            binding = self.bindings[row["id"]]
            if row.get("runner") != binding.runner_path or (
                binding.runner_path is not None
                and row["verifier"]["files"].get(binding.runner_path) != binding.runner_sha256
            ):
                raise BenchEnvironmentError("Runtime entrypoint differs from the frozen task")
        self.bench_root = Path(bench_root).resolve()
        self.run_root = Path(run_root).resolve()
        if any(
            self.run_root.is_relative_to(Path(binding.baseline_root).resolve())
            for binding in self.bindings.values()
        ):
            raise ValueError("Run artifacts must be outside the candidate baseline")
        self.executable = Path(claude_cli)
        self.spec = AgentExecutionSpec(model, timeout_seconds, max_budget_usd)
        self.harness_prompt = harness_prompt

    def _run(self, task: BenchTask, url: str, api_key: str) -> float:
        case = self.run_root / uuid.uuid4().hex
        case.mkdir(parents=True, exist_ok=False)
        candidate = case / "candidate"
        copy_candidate(Path(self.bindings[task.task_key].baseline_root), candidate)
        # Make the pinned public assets accessible through the same confined tool.
        # The original evaluator/collector remain outside candidate and unchanged.
        for relative, digest in self.source_files.items():
            source = self.bench_root / relative
            if Path(relative).is_absolute() or not source.resolve().is_relative_to(self.bench_root):
                raise ValueError("Public benchmark asset escapes its bundle")
            raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("Public benchmark source changed after dataset freezing")
            write_bytes(candidate / "benchmark-inputs" / relative, raw, immutable=True)
        write_bytes(case / "task.json", json_bytes(task), immutable=True)
        write_bytes(case / "harness.txt", self.harness_prompt.encode(), immutable=True)
        write_bytes(case / "execution.json", json_bytes(self.spec), immutable=True)
        prompt = (
            self.harness_prompt
            + "\n\n"
            + task.instruction
            + "\n\nYour candidate source is available through the workspace MCP tool. "
            "Implement the requested behavior in that source. Preserve the task's "
            "acceptance requirements. The independent evaluator executes your candidate "
            "after you finish; do not write observations, references, or grade claims."
            " Public pinned input files are under benchmark-inputs/."
        )
        try:
            raw = run_claude(
                self.executable,
                self.spec,
                prompt,
                McpTool(
                    "rsi_workspace",
                    "workspace",
                    sys.executable,
                    (
                        "-m",
                        "areal_rsi.infra.coordination.tools.workspace",
                        "--root",
                        str(candidate),
                    ),
                ),
                url,
                api_key=api_key,
                cwd=candidate,
                transcript_path=case / "claude-stream.jsonl",
            )
            write_bytes(case / "transcript.jsonl", raw, immutable=True)
            write_bytes(case / "agent.json", json_bytes(read_result(raw)), immutable=True)
            grade = grade_candidate(
                self.bench_root, task, candidate, self.bindings[task.task_key], case / "grade.json"
            )
            write_bytes(case / "receipt.json", json_bytes(grade), immutable=True)
            return float(grade.score)
        except Exception as error:
            if isinstance(error, ClaudeExecutionError):
                write_bytes(case / "failed-transcript.jsonl", error.transcript, immutable=True)
            elif isinstance(error, subprocess.TimeoutExpired):
                write_bytes(case / "failed-transcript.jsonl", error.output or b"", immutable=True)
            write_bytes(
                case / "failure.json",
                json_bytes(
                    {
                        "type": type(error).__name__,
                        "reason": str(error),
                        "task_key": task.task_key,
                    }
                ),
                immutable=True,
            )
            raise

    async def run(self, data: dict[str, Any], **extra_kwargs: Any) -> float:
        key = data.get("task_id")
        if not isinstance(key, str) or key not in self.tasks:
            raise ValueError("Unknown frozen benchmark task")
        task = self.tasks[key]
        if data.get("content_sha256") != task.content_sha256:
            raise ValueError("Training task differs from the frozen benchmark")
        url, api_key = extra_kwargs.get("base_url"), extra_kwargs.get("api_key")
        if not isinstance(url, str) or not isinstance(api_key, str) or not api_key:
            raise ValueError("AReaL omitted its session-scoped Claude inference proxy")
        return await asyncio.to_thread(self._run, task, url, api_key)


def prepare(argv: list[str]) -> TrainingPlan | None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--runtime-registry", type=Path)
    parser.add_argument("--bench-root", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--claude-cli", type=Path)
    parser.add_argument("--harness-prompt", type=Path)
    args, areal_args = parser.parse_known_args(argv)
    inputs = freeze_inputs(args.dataset, args.destination)
    print(f"Frozen {len(inputs.tasks)} identical train/test tasks: {inputs.dataset_sha256}")
    if not args.train:
        if areal_args:
            parser.error("Unknown arguments for dataset freezing")
        return None
    if any(
        value is None
        for value in (
            args.runtime_registry,
            args.bench_root,
            args.run_root,
            args.claude_cli,
            args.harness_prompt,
        )
    ):
        parser.error("Training needs the runtime registry, source/run roots, CLI and frozen prompt")
    # Check site readiness before allocating training workers. Missing trusted
    # evaluation inputs cannot silently turn into negative training examples.
    bindings = runtime_bindings(args.runtime_registry, inputs)
    missing = [
        key
        for key, binding in bindings.items()
        if not Path(binding.environment_path or binding.runtime_path or "").is_file()
    ]
    if missing:
        raise BenchEnvironmentError("Missing site runtimes: " + ", ".join(missing))
    config = load_config(areal_args)
    if config.scheduler.type != "slurm" or config.train_dataset.batch_size != len(inputs.tasks):
        raise ValueError("Bench training requires Slurm and a batch containing all selected tasks")
    for role in (config.actor, config.rollout):
        if any(spec.reservation != "rsi" for spec in role.scheduling_spec):
            raise ValueError("All GPU workers must use the rsi reservation")
    rows = [
        {"task_id": task.task_key, "content_sha256": task.content_sha256} for task in inputs.tasks
    ]
    kwargs = {
        "inputs_root": str(args.destination.resolve()),
        "runtime_registry": str(args.runtime_registry.resolve()),
        "bench_root": str(args.bench_root.resolve()),
        "run_root": str(args.run_root.resolve()),
        "claude_cli": str(args.claude_cli.resolve()),
        "model": config.sglang.model_path,
        "harness_prompt": args.harness_prompt.read_text(),
    }
    workflow = "areal_rsi.infra.coordination.evaluation.bench.AdaptationBenchWorkflow"
    return TrainingPlan(config, tuple(rows), tuple(rows), workflow, kwargs)
