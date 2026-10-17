"""Phased TIR research: freeze, baseline, train/dev iterations, select, final audit.

This is task policy. Execution, trajectory storage, evaluation and checkpoint
validation use the public infrastructure services unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import math
import random
import re
import signal
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from areal_rsi.api.control import decode_record
from areal_rsi.api.execution import JobSpec, TrainingResult
from areal_rsi.api.records import (
    BenchmarkSplit,
    ContentRef,
    DirectorySnapshot,
    EvaluationResult,
    EvaluationSample,
    HarnessVersion,
    ModelVersion,
    SolutionVersion,
    SourceSnapshot,
)
from areal_rsi.infra.artifacts import (
    file_ref,
    read_record,
    snapshot_directory,
    snapshot_source,
    write_record,
)
from areal_rsi.infra.coordination.evaluation import EvaluationService
from areal_rsi.infra.execution import JobExecutor
from areal_rsi.infra.training import AReaLBackend
from areal_rsi.infra.trajectories import LocalTrajectoryStore
from examples.tir.benchmark import Benchmark, evaluation_cases, read_examples
from examples.tir.pipeline import ExperimentConfig, HarnessConfig, WorkerRequest, local_path
from examples.tir.synthesis import DataRecipe, SynthesisManifest, build_dataset

PROJECT = Path(__file__).resolve().parents[2]
PROJECT_PATHS = ("src", "examples/tir", "examples/__init__.py", "pyproject.toml", "uv.lock")
BACKEND_PATHS = ("areal", "examples/tir", "pyproject.toml", "uv.lock")


@dataclass(frozen=True, slots=True)
class SlurmResources:
    image: str = "/storage/openpsi/images/areal-dev-sglang-0821.sif"
    reservation: str = "rsi"
    partition: str = "all"
    cpus: int = 16
    memory_gb: int = 128
    minutes: int = 180

    def __post_init__(self) -> None:
        if min(self.cpus, self.memory_gb, self.minutes) < 1 or not self.reservation:
            raise ValueError("This study requires positive resources and a named reservation")


@dataclass(frozen=True, slots=True)
class StudyConfig:
    experiment: ExperimentConfig
    benchmark: str
    slurm: SlurmResources = field(default_factory=SlurmResources)
    min_rounds: int = 2
    target_delta: float = 0.03

    def __post_init__(self) -> None:
        if self.min_rounds < 2 or not 0 < self.target_delta < 1:
            raise ValueError("Require multiple research rounds and a positive improvement target")


@dataclass(frozen=True, slots=True)
class Protocol:
    config: StudyConfig
    benchmark: Benchmark
    locked_files: tuple[ContentRef, ...]
    project: SourceSnapshot
    backend: SourceSnapshot
    base_model: ContentRef
    baseline: SolutionVersion


@dataclass(frozen=True, slots=True)
class StudyHarness:
    config: HarnessConfig
    files: tuple[ContentRef, ...]
    reward: Literal["integer", "math_verify"]


@dataclass(frozen=True, slots=True)
class RoundRecipe:
    hypothesis: str
    parent: str = "base"
    data: DataRecipe = field(default_factory=DataRecipe)
    # Fresh optimizer per round; parent specifies warm-started model weights.
    training_steps: int = 32
    batch_size: int = 16
    n_samples: int = 4
    learning_rate: float = 0.00001
    max_new_tokens: int = 768
    temperature: float = 1.0

    def __post_init__(self) -> None:
        if not self.hypothesis.strip():
            raise ValueError("A research round must state its hypothesis before training")
        if min(self.training_steps, self.batch_size, self.max_new_tokens) < 1:
            raise ValueError("Training steps, batch size and token limit must be positive")
        if self.n_samples < 2 or min(self.learning_rate, self.temperature) <= 0:
            raise ValueError("GRPO requires multiple samples, positive learning rate/temperature")
        if self.data.real_count + self.data.synthetic_count < self.batch_size:
            raise ValueError("Training mixture must supply at least one full batch")


@dataclass(slots=True)
class Candidate:
    name: str
    recipe: ContentRef
    data: SynthesisManifest
    project: SourceSnapshot
    parent: SolutionVersion
    status: Literal["prepared", "training", "evaluating", "completed", "failed"] = "prepared"
    training: TrainingResult | None = None
    solution: SolutionVersion | None = None
    dev: EvaluationResult | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class PairedComparison:
    count: int
    baseline_accuracy: float
    candidate_accuracy: float
    wins: int
    losses: int
    both_correct: int
    both_wrong: int
    delta: float
    ci95_low: float
    ci95_high: float
    mcnemar_exact_p: float | None
    independent_questions: int


@dataclass(frozen=True, slots=True)
class PairedBlock:
    difference: int
    count: int


@dataclass(frozen=True, slots=True)
class Selection:
    candidate: str
    solution: SolutionVersion
    dev_comparison: PairedComparison
    completed_rounds: tuple[str, ...]
    selected_at: str


@dataclass(frozen=True, slots=True)
class FinalResult:
    selection: ContentRef
    baseline: EvaluationResult
    candidate: EvaluationResult
    comparison: PairedComparison
    target_met: bool


def _name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise ValueError("Use a simple 1..64 character study/candidate name")
    return value


def _model_path(solution: SolutionVersion) -> str:
    return unquote(urlparse(solution.model.content.uri).path)


def _source(root: Path, output: Path, *, backend: bool = False) -> SourceSnapshot:
    return snapshot_source(root, output, paths=BACKEND_PATHS if backend else PROJECT_PATHS)


def prepare(config: StudyConfig, root: Path) -> None:
    _name(root.name)
    if (root / "protocol.json").exists():
        raise FileExistsError("Study protocol is already frozen; use a new study directory")
    root.mkdir(parents=True, exist_ok=True)
    benchmark = read_record(Path(config.benchmark), Benchmark)
    splits = [
        read_examples(local_path(ref)) for ref in (benchmark.train, benchmark.dev, benchmark.final)
    ]
    ids = [{item.task_id for item in split} for split in splits]
    if any(ids[i] & ids[j] for i in range(3) for j in range(i)):
        raise ValueError("Training, dev and final data overlap")
    if ids[0] & set(benchmark.excluded_ids):
        raise ValueError("Training pool includes a held-out identity")
    if (
        any(item.source.startswith("torl-") for split in splits for item in split)
        and config.experiment.reward != "math_verify"
    ):
        raise ValueError("ToRL requires the native mathematical equivalence reward")
    repository = Path(config.experiment.runtime.repository).resolve(strict=True)
    project = _source(PROJECT, root / "source-project.json")
    backend = _source(repository, root / "source-backend.json", backend=True)
    model = snapshot_directory(Path(config.experiment.model), root / "base-model.json")
    # Changing synthesis and recipe policy is allowed. Changing any executed
    # evaluator, rollout, data reader, reward or infrastructure invalidates a study.
    locked_paths = [
        *sorted((PROJECT / "src").rglob("*.py")),
        *(
            PROJECT / "examples/tir" / name
            for name in (
                "benchmark.py",
                "worker.py",
                "workflow.py",
                "reward.py",
                "pipeline.py",
                "slurm_worker.sh",
                "study.py",
                "rsi.py",
            )
        ),
        repository / config.experiment.backend_config,
    ]
    backend_manifest = read_record(local_path(backend.content), DirectorySnapshot)
    locked_paths.extend(repository / entry.path for entry in backend_manifest.files)
    locked = tuple(file_ref(path) for path in locked_paths)
    harness_ref = write_record(
        root / "harness.json",
        StudyHarness(config.experiment.harness, locked, config.experiment.reward),
    )
    created = datetime.now(UTC).isoformat()
    baseline = SolutionVersion(
        "base",
        ModelVersion(
            "base-" + model.digest[:20],
            Path(config.experiment.model).name,
            ContentRef(Path(config.experiment.model).resolve().as_uri(), model.digest),
            created,
        ),
        HarnessVersion("tir-" + harness_ref.digest[:20], "TIR frozen study", harness_ref, created),
        created,
    )
    ref = write_record(
        root / "protocol.json",
        Protocol(config, benchmark, locked, project, backend, model, baseline),
    )
    write_record(root / "freeze.json", ref)


def protocol(root: Path) -> Protocol:
    path = local_path(read_record(root / "freeze.json", ContentRef))
    record = read_record(path, Protocol)
    for ref in (*record.locked_files, record.benchmark.train, record.benchmark.dev):
        local_path(ref)
    return record


def paired(
    baseline: EvaluationResult,
    candidate: EvaluationResult,
    *,
    clusters: dict[str, str] | None = None,
) -> PairedComparison:
    def samples(result: EvaluationResult) -> dict[str, EvaluationSample]:
        with local_path(result.samples).open() as stream:
            items = [decode_record(EvaluationSample, json.loads(line)) for line in stream]
        if len({item.task_id for item in items}) != len(items):
            raise ValueError("Duplicate paired evaluation identities")
        return {item.task_id: item for item in items}

    left, right = samples(baseline), samples(candidate)
    if not left or left.keys() != right.keys():
        raise ValueError("Paired comparison requires exactly the same tasks")
    if clusters is not None and clusters.keys() != left.keys():
        raise ValueError("Question clusters must cover exactly the paired tasks")
    differences = [int(right[key].passed) - int(left[key].passed) for key in sorted(left)]
    wins, losses = differences.count(1), differences.count(-1)
    both_correct = sum(left[key].passed and right[key].passed for key in left)
    n = len(left)
    grouped: dict[str, list[int]] = {}
    for key, difference in zip(sorted(left), differences, strict=True):
        group = clusters[key] if clusters is not None else key
        grouped.setdefault(group, []).append(difference)
    blocks = tuple(PairedBlock(sum(values), len(values)) for values in grouped.values())
    rng = random.Random(20261017)
    bootstrap = []
    for _ in range(10000):
        draw = rng.choices(blocks, k=len(blocks))
        bootstrap.append(sum(item.difference for item in draw) / sum(item.count for item in draw))
    bootstrap.sort()
    discordant = wins + losses
    p = (
        min(
            1.0,
            2 * sum(math.comb(discordant, k) for k in range(min(wins, losses) + 1)) / 2**discordant,
        )
        if discordant
        else 1.0
    )
    return PairedComparison(
        n,
        sum(item.passed for item in left.values()) / n,
        sum(item.passed for item in right.values()) / n,
        wins,
        losses,
        both_correct,
        n - both_correct - wins - losses,
        (wins - losses) / n,
        bootstrap[249],
        bootstrap[9749],
        p if len(blocks) == n else None,
        len(blocks),
    )


class Study:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.protocol = protocol(self.root)
        self.config = self.protocol.config.experiment
        self.jobs = JobExecutor(self.root / "execution")
        runtime = replace(
            self.config.runtime,
            environment={
                **self.config.runtime.environment,
                "PYTHONPATH": str(Path(self.config.runtime.repository) / "examples/tir"),
            },
        )
        self.backend = AReaLBackend(runtime, self.jobs)
        self.evaluator = EvaluationService(
            self.jobs, LocalTrajectoryStore(self.root / "trajectories")
        )

    def candidate(self, name: str) -> Candidate:
        return read_record(self.root / "rounds" / _name(name) / "candidate.json", Candidate)

    def solution(self, name: str) -> SolutionVersion:
        if name == "base":
            solution, manifest_ref = self.protocol.baseline, self.protocol.base_model
        else:
            candidate = self.candidate(name)
            if candidate.solution is None or candidate.training is None:
                raise ValueError(f"Parent has no validated trained checkpoint: {name}")
            solution, manifest_ref = candidate.solution, candidate.training.manifest
        manifest = read_record(local_path(manifest_ref), DirectorySnapshot)
        for entry in manifest.files:
            path = Path(manifest.root) / entry.path
            if file_ref(path).digest != entry.sha256:
                raise ValueError(f"Model artifact changed: {path}")
        return solution

    def spec(self, request: WorkerRequest, name: str) -> JobSpec:
        path = self.root / "requests" / f"{name}.json"
        write_record(path, request)
        local = self.backend.spec(
            PROJECT / "examples/tir/worker.py",
            ("--request", str(path)),
            timeout=self.config.timeout_seconds,
        )
        resources = self.protocol.config.slurm
        prefix = (
            "srun",
            f"--reservation={resources.reservation}",
            f"--partition={resources.partition}",
            "--nodes=1",
            "--ntasks=1",
            "--gres=gpu:2",
            f"--cpus-per-task={resources.cpus}",
            f"--mem={resources.memory_gb}G",
            f"--time={resources.minutes}",
            "--mpi=none",
            f"--job-name=fh-tir-{name[:30]}",
            "apptainer",
            "exec",
            "--nv",
            "--bind",
            "/storage:/storage",
            resources.image,
            "bash",
            str(PROJECT / "examples/tir/slurm_worker.sh"),
        )
        return replace(local, command=(*prefix, *local.command))

    def request(
        self,
        mode: Literal["train", "eval"],
        name: str,
        solution: SolutionVersion,
        data: ContentRef,
        config: ExperimentConfig | None = None,
    ) -> WorkerRequest:
        return WorkerRequest(
            mode,
            config or self.config,
            _model_path(solution),
            str(local_path(data)),
            str(local_path(self.protocol.benchmark.dev)),
            str(self.jobs.directory(self.root.name, name)),
            f"{self.root.name}-{name}",
            "benchmark",
        )

    async def evaluate(self, name: str, *, final: bool = False) -> EvaluationResult:
        if final:
            selection = read_record(self.root / "selection.json", Selection)
            if name not in {"base", selection.candidate}:
                raise ValueError("Final evaluation is restricted to the frozen selection")
        if not final and (self.root / "selection.json").exists():
            raise ValueError("Selection is frozen; further dev tuning belongs to a new study")
        solution = self.solution(name)
        saved = self.root / ("final" if final else "dev") / f"{_name(name)}.json"
        if saved.exists():
            result = read_record(saved, EvaluationResult)
            local_path(result.samples)
            return result
        data = self.protocol.benchmark.final if final else self.protocol.benchmark.dev
        cases = evaluation_cases(read_examples(local_path(data)))
        task_ids = tuple(item.task_id for item in cases)
        stem = f"{name}-{'final' if final else 'dev'}"
        attempt = 1
        while self.jobs.directory(self.root.name, f"{stem}-{attempt}").exists():
            attempt += 1
        job_name = f"{stem}-{attempt}"
        before = _source(PROJECT, self.root / "requests" / f"{job_name}-source.json")
        result = await self.evaluator.evaluate(
            self.root.name,
            job_name,
            self.spec(self.request("eval", job_name, solution, data), job_name),
            task_ids=task_ids,
            solution_id=solution.solution_id,
            split=BenchmarkSplit.TEST if final else BenchmarkSplit.VALIDATE,
        )
        self.verify_source(before, job_name)
        write_record(saved, result)
        if not final and name != "base":
            candidate = self.candidate(name)
            candidate.dev, candidate.status, candidate.error = result, "completed", ""
            write_record(self.root / "rounds" / name / "candidate.json", candidate)
            write_record(
                self.root / "dev" / f"{name}-comparison.json",
                paired(
                    read_record(self.root / "dev/base.json", EvaluationResult),
                    result,
                    clusters={item.task_id: item.question_id for item in cases},
                ),
            )
        print(f"{stem} accuracy={result.report.metrics['accuracy']:.4f}", flush=True)
        return result

    def verify_source(self, before: SourceSnapshot, name: str) -> None:
        after = _source(PROJECT, self.root / "requests" / f"{name}-source-after.json")
        protocol(self.root)
        if before.content.digest != after.content.digest:
            raise ValueError("Source changed during a GPU stage; its result is invalid")

    async def train(self, name: str, recipe_path: Path) -> Candidate:
        _name(name)
        if name == "base" or (self.root / "selection.json").exists():
            raise ValueError("Invalid candidate name or study already selected")
        if not (self.root / "dev/base.json").exists():
            raise ValueError("Measure the frozen base model on dev before training")
        directory = self.root / "rounds" / name
        directory.mkdir(parents=True, exist_ok=False)
        recipe = read_record(recipe_path, RoundRecipe)
        recipe_ref = write_record(directory / "recipe.json", recipe)
        parent = self.solution(recipe.parent)
        benchmark = self.protocol.benchmark
        data = build_dataset(
            recipe.data,
            read_examples(local_path(benchmark.train)),
            benchmark.train,
            benchmark.excluded_ids,
            directory / "data",
        )
        source = _source(PROJECT, directory / "source-project.json")
        candidate = Candidate(name, recipe_ref, data, source, parent)
        state = directory / "candidate.json"
        write_record(state, candidate)
        size = recipe.data.real_count + recipe.data.synthetic_count
        epochs = math.ceil(recipe.training_steps / (size // recipe.batch_size))
        training = replace(
            self.config.training,
            epochs=epochs,
            max_steps=recipe.training_steps,
            batch_size=recipe.batch_size,
            n_samples=recipe.n_samples,
            learning_rate=recipe.learning_rate,
            max_new_tokens=recipe.max_new_tokens,
            temperature=recipe.temperature,
        )
        config = replace(
            self.config,
            training=training,
            synthesis=replace(self.config.synthesis, seed=recipe.data.seed, train_size=size),
        )
        job_name = f"{name}-train"
        try:
            candidate.status = "training"
            write_record(state, candidate)
            candidate.training = await self.backend.train(
                self.root.name,
                job_name,
                self.spec(self.request("train", job_name, parent, data.dataset, config), job_name),
            )
            self.verify_source(source, job_name)
            created = datetime.now(UTC).isoformat()
            candidate.solution = SolutionVersion(
                name,
                ModelVersion(
                    "model-" + candidate.training.checkpoint.digest[:20],
                    name,
                    candidate.training.checkpoint,
                    created,
                    parent_version_id=parent.model.version_id,
                ),
                parent.harness,
                created,
                parent_solution_id=parent.solution_id,
            )
            candidate.status = "evaluating"
            write_record(state, candidate)
            await self.evaluate(name)
            return self.candidate(name)
        except BaseException as exc:
            candidate.status, candidate.error = "failed", str(exc) or type(exc).__name__
            write_record(state, candidate)
            raise

    def select(self, name: str) -> Selection:
        path = self.root / "selection.json"
        if path.exists():
            selection = read_record(path, Selection)
            if selection.candidate != name:
                raise ValueError("Final selection is immutable")
            return selection
        candidates = [
            read_record(path, Candidate)
            for path in sorted((self.root / "rounds").glob("*/candidate.json"))
        ]
        completed = [item for item in candidates if item.status == "completed" and item.dev]
        if len(completed) < self.protocol.config.min_rounds:
            raise ValueError("Complete the preregistered minimum number of research rounds")
        best = max(completed, key=lambda item: item.dev.report.score if item.dev else -1)
        selected = self.candidate(name)
        if (
            selected.status != "completed"
            or selected.dev is None
            or selected.dev.report.score < (best.dev.report.score if best.dev else 0)
        ):
            raise ValueError("Select a candidate with the best dev score")
        comparison = paired(
            read_record(self.root / "dev/base.json", EvaluationResult), selected.dev
        )
        if comparison.delta <= 0:
            raise ValueError("Continue research: no dev improvement yet")
        selection = Selection(
            name,
            self.solution(name),
            comparison,
            tuple(item.name for item in completed),
            datetime.now(UTC).isoformat(),
        )
        write_record(path, selection)
        return selection

    async def finalize(self, name: str) -> FinalResult:
        self.select(name)
        baseline = await self.evaluate("base", final=True)
        candidate = await self.evaluate(name, final=True)
        comparison = paired(
            baseline,
            candidate,
            clusters={
                item.task_id: item.question_id
                for item in evaluation_cases(
                    read_examples(local_path(self.protocol.benchmark.final))
                )
            },
        )
        result = FinalResult(
            file_ref(self.root / "selection.json"),
            baseline,
            candidate,
            comparison,
            comparison.delta >= self.protocol.config.target_delta,
        )
        write_record(self.root / "final/result.json", result)
        p_text = (
            f"exact McNemar p={comparison.mcnemar_exact_p:.4g}"
            if comparison.mcnemar_exact_p is not None
            else "McNemar omitted because repeated samples are clustered by question"
        )
        (self.root / "report.md").write_text(
            f"# TIR study: {self.root.name}\n\nSelected: {name}. "
            f"Completed rounds: {', '.join(self.select(name).completed_rounds)}.\n\n"
            f"Final ({comparison.count} samples, {comparison.independent_questions} questions): "
            f"base {comparison.baseline_accuracy:.2%}, "
            f"candidate {comparison.candidate_accuracy:.2%}; "
            f"delta {comparison.delta:+.2%}.\n\n"
            f"Question-clustered paired bootstrap 95% CI "
            f"[{comparison.ci95_low:+.2%}, {comparison.ci95_high:+.2%}]; "
            f"wins/losses {comparison.wins}/{comparison.losses}; "
            f"{p_text}.\n\n"
            f"Target met: {result.target_met}. "
            "See protocol.json, selection.json, final/result.json and rounds/ "
            "for complete evidence.\n"
        )
        return result


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--config", type=Path, required=True)
    eval_parser = commands.add_parser("evaluate")
    eval_parser.add_argument("--candidate", default="base")
    train_parser = commands.add_parser("round")
    train_parser.add_argument("--name", required=True)
    train_parser.add_argument("--recipe", type=Path, required=True)
    final_parser = commands.add_parser("finalize")
    final_parser.add_argument("--candidate", required=True)
    commands.add_parser("status")
    args = parser.parse_args()
    root = args.study.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # A single writer owns a complete stage, including its Slurm allocation.
    with (root / ".study.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command == "prepare":
            prepare(read_record(args.config, StudyConfig), root)
            return
        study = Study(root)
        if args.command == "evaluate":
            await study.evaluate(args.candidate)
        elif args.command == "round":
            await study.train(args.name, args.recipe)
        elif args.command == "finalize":
            await study.finalize(args.candidate)
        else:
            for path in sorted((root / "dev").glob("*.json")):
                if path.name.endswith("-comparison.json"):
                    print(path.name, read_record(path, PairedComparison))
                else:
                    print(path.stem, read_record(path, EvaluationResult).report.metrics)


async def _entry() -> None:
    task = asyncio.create_task(main())
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, task.cancel)
    await task


if __name__ == "__main__":
    try:
        asyncio.run(_entry())
    except asyncio.CancelledError:
        raise SystemExit(130) from None
