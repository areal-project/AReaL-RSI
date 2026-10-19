"""TIR's data and worker plans for the common research lifecycle."""

from __future__ import annotations

import math
import os
import subprocess
import sys
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from areal_rsi.api.execution import JobSpec, SlurmResources
from areal_rsi.api.interfaces import ResearchProblem
from areal_rsi.api.records import (
    BenchmarkSplit,
    ContentRef,
    DirectorySnapshot,
    HarnessVersion,
    ModelVersion,
    SolutionVersion,
)
from areal_rsi.api.research import (
    CandidateContext,
    CandidatePlan,
    ComparisonContract,
    EvaluationContext,
    EvaluationPlan,
    ProblemSpec,
    SourceTree,
)
from areal_rsi.infra.artifacts import (
    file_ref,
    local_path,
    read_record,
    snapshot_directory,
    snapshot_source,
    write_record,
)
from areal_rsi.infra.execution import JobExecutor
from areal_rsi.infra.training import AReaLBackend
from examples.tir.benchmark import Benchmark, evaluation_cases, read_examples
from examples.tir.pipeline import (
    BACKEND_PATHS,
    PROJECT,
    PROJECT_PATHS,
    HarnessManifest,
    TIRConfig,
    WorkerRequest,
)
from examples.tir.synthesis import DataRecipe, SynthesisManifest


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    tir: TIRConfig
    benchmark: str
    slurm: SlurmResources = field(default_factory=SlurmResources)
    image: str = "/storage/openpsi/images/areal-dev-sglang-0821.sif"
    min_rounds: int = 2
    target_delta: float = 0.03

    def __post_init__(self) -> None:
        if self.min_rounds < 2 or not 0 < self.target_delta < 1:
            raise ValueError("Require multiple research rounds and a positive improvement target")


@dataclass(frozen=True, slots=True)
class RoundRecipe:
    hypothesis: str
    parent: str = "base"
    data: DataRecipe = field(default_factory=DataRecipe)
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
            raise ValueError("Invalid GRPO sampling or optimization parameters")
        if self.data.real_count + self.data.synthetic_count < self.batch_size:
            raise ValueError("Training mixture must supply at least one full batch")


class TIRProblem(ResearchProblem[ExperimentConfig, RoundRecipe]):
    config_type = ExperimentConfig
    recipe_type = RoundRecipe

    def __init__(self, config: ExperimentConfig):
        self.config = config

    def benchmark(self) -> Benchmark:
        return read_record(Path(self.config.benchmark), Benchmark)

    def prepare(self, root: Path) -> ProblemSpec:
        config, benchmark = self.config.tir, self.benchmark()
        splits = [
            read_examples(local_path(ref))
            for ref in (benchmark.train, benchmark.valid, benchmark.test)
        ]
        ids = [{item.task_id for item in split} for split in splits]
        if any(ids[i] & ids[j] for i in range(3) for j in range(i)):
            raise ValueError("Training, valid and test data overlap")
        if ids[0] & set(benchmark.excluded_ids):
            raise ValueError("Training pool includes a held-out identity")
        if any(item.source.startswith("torl-") for split in splits for item in split) and (
            config.reward != "math_verify"
        ):
            raise ValueError("ToRL requires the native mathematical equivalence reward")
        repository = Path(config.runtime.repository).resolve(strict=True)
        backend = snapshot_source(repository, root / "backend-source.json", paths=BACKEND_PATHS)
        manifest = read_record(local_path(backend.content), DirectorySnapshot)
        locked = tuple(
            file_ref(path)
            for path in (
                *sorted((PROJECT / "src").rglob("*.py")),
                PROJECT / "examples/checkpoints.py",
                *(
                    PROJECT / "examples/tir" / name
                    for name in (
                        "benchmark.py",
                        "worker.py",
                        "workflow.py",
                        "reward.py",
                        "pipeline.py",
                        "problem.py",
                    )
                ),
                PROJECT / "src/areal_rsi/infra/worker.sh",
                *(repository / entry.path for entry in manifest.files),
                repository / config.backend_config,
            )
        )
        model_manifest = snapshot_directory(Path(config.model), root / "base-model.json")
        harness = write_record(
            root / "harness.json", HarnessManifest(config.harness, locked, config.reward)
        )
        created = datetime.now(UTC).isoformat()
        baseline = SolutionVersion(
            "base",
            ModelVersion(
                "base-" + model_manifest.digest[:20],
                Path(config.model).name,
                ContentRef(Path(config.model).resolve().as_uri(), model_manifest.digest),
                created,
            ),
            HarnessVersion("tir-" + harness.digest[:20], "TIR", harness, created),
            created,
        )
        conditions = (
            file_ref(Path(self.config.benchmark)),
            benchmark.train,
            benchmark.valid,
            write_record(root / "evaluation-config.json", config.evaluation),
            harness,
        )
        return ProblemSpec(
            "tir",
            "Read examples/tir/RESEARCH.md. Improve training data, synthesis.py and GRPO recipes "
            "using only train/valid evidence. Evaluator, reward, worker, infrastructure and "
            "backend are frozen. Never read heldout/test or tune using test. Every round has "
            "a fresh optimizer; "
            "parent specifies model warm start. Submit candidates through the research tools, "
            "compare all completed rounds, and freeze the best validated candidate with "
            "rsi_select_candidate before completing. Final test is run separately by the host.",
            baseline,
            model_manifest,
            ComparisonContract(
                min_rounds=self.config.min_rounds,
                target_delta=self.config.target_delta,
                conditions=conditions,
            ),
            locked,
            (SourceTree(str(PROJECT), PROJECT_PATHS), SourceTree(str(repository), BACKEND_PATHS)),
        )

    def _job(self, root: Path, request: WorkerRequest) -> JobSpec:
        config = request.config
        path = root / "requests" / (request.trial + ".json")
        write_record(path, request)
        local = AReaLBackend(config.runtime, JobExecutor(root / "execution")).spec(
            PROJECT / "examples/tir/worker.py",
            ("--request", str(path)),
            timeout=config.timeout_seconds,
            python_paths=(Path(config.runtime.repository) / "examples/tir",),
            project=PROJECT,
        )
        return replace(
            local,
            command=(
                "apptainer",
                "exec",
                "--nv",
                "--bind",
                "/storage:/storage",
                self.config.image,
                "bash",
                str(PROJECT / "src/areal_rsi/infra/worker.sh"),
                *local.command,
            ),
            slurm=self.config.slurm,
        )

    def build_candidate(self, context: CandidateContext, recipe: RoundRecipe) -> CandidatePlan:
        if recipe.parent != context.parent.solution_id:
            raise ValueError("Recipe parent differs from the proposed parent")
        root, benchmark = Path(context.root), self.benchmark()
        directory = root / "candidates" / context.candidate_id
        data_recipe = directory / "data-recipe.json"
        write_record(data_recipe, recipe.data)
        # A fresh interpreter loads the editable generator for every candidate.
        # Keeping an imported build_dataset in this long-lived host would execute stale policy.
        with (directory / "data-preparation.log").open("wb") as log:
            subprocess.run(
                (
                    sys.executable,
                    str(PROJECT / "examples/tir/synthesis.py"),
                    "--recipe",
                    str(data_recipe),
                    "--benchmark",
                    self.config.benchmark,
                    "--output",
                    str(directory / "data"),
                ),
                cwd=PROJECT,
                env={
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": os.pathsep.join((str(PROJECT / "src"), str(PROJECT))),
                },
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=300,
            )
        data = read_record(directory / "data/manifest.json", SynthesisManifest)
        size = recipe.data.real_count + recipe.data.synthetic_count
        config = replace(
            self.config.tir,
            training=replace(
                self.config.tir.training,
                epochs=math.ceil(recipe.training_steps / (size // recipe.batch_size)),
                max_steps=recipe.training_steps,
                batch_size=recipe.batch_size,
                n_samples=recipe.n_samples,
                learning_rate=recipe.learning_rate,
                max_new_tokens=recipe.max_new_tokens,
                temperature=recipe.temperature,
            ),
            synthesis=replace(self.config.tir.synthesis, seed=recipe.data.seed, train_size=size),
        )
        solution = replace(
            context.parent,
            solution_id=context.candidate_id,
            parent_solution_id=context.parent.solution_id,
        )
        request = WorkerRequest(
            "train",
            config,
            unquote(urlparse(context.parent.model.content.uri).path),
            str(local_path(data.dataset)),
            str(local_path(benchmark.valid)),
            context.output,
            context.run_id + "-" + context.candidate_id,
            "benchmark",
        )
        data_ref = write_record(root / "candidates" / context.candidate_id / "data.json", data)
        return CandidatePlan(
            solution,
            (data.dataset, data_ref, benchmark.train),
            self._job(root, request),
            config.runtime,
        )

    def build_evaluation(self, context: EvaluationContext) -> EvaluationPlan:
        benchmark = self.benchmark()
        data = benchmark.test if context.split == BenchmarkSplit.TEST else benchmark.valid
        cases = evaluation_cases(read_examples(local_path(data)))
        request = WorkerRequest(
            "eval",
            self.config.tir,
            unquote(urlparse(context.solution.model.content.uri).path),
            str(local_path(data)),
            str(local_path(benchmark.valid)),
            context.output,
            context.run_id + "-" + Path(context.output).name,
            "benchmark",
        )
        return EvaluationPlan(
            self._job(Path(context.root), request),
            tuple(item.task_id for item in cases),
            (data,),
            {item.task_id: item.question_id for item in cases},
        )
