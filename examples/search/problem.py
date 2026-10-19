"""Search-specific recipes and execution plans for the shared RSI lifecycle."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from areal_rsi.api.control import decode_record
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
from examples.search.evaluation.artifacts import FinalEvalConfig
from examples.search.evaluation.checkpoints import export_checkpoint
from examples.search.experiment.config import SearchConfig, TrainingConfig, WorkerRequest
from examples.search.task.dataset import SearchQuestion


@dataclass(frozen=True, slots=True)
class SearchProblemConfig:
    search: SearchConfig
    image: str
    slurm: SlurmResources = field(default_factory=lambda: SlurmResources(cpus=32, memory_gb=256))
    min_rounds: int = 1
    target_delta: float | None = None
    official: FinalEvalConfig | None = None
    official_image: str = ""


@dataclass(frozen=True, slots=True)
class SearchRecipe:
    training: TrainingConfig | None = None
    dataset_directory: str | None = None
    index_directory: str | None = None


@dataclass(frozen=True, slots=True)
class StageRequest:
    worker: WorkerRequest | None = None
    official: FinalEvalConfig | None = None
    reservation: str = "rsi"

    def __post_init__(self) -> None:
        if (self.worker is None) == (self.official is None):
            raise ValueError("A Search stage must contain exactly one operation")


def questions(path: Path) -> tuple[SearchQuestion, ...]:
    with path.open() as source:
        items = tuple(decode_record(SearchQuestion, json.loads(line)) for line in source)
    if not items or len({item.question_id for item in items}) != len(items):
        raise ValueError("Search requires nonempty, unique question identities")
    return items


class SearchProblem(ResearchProblem[SearchProblemConfig, SearchRecipe]):
    config_type = SearchProblemConfig
    recipe_type = SearchRecipe

    def __init__(self, config: SearchProblemConfig):
        self.config = config
        if not config.search.project:
            raise ValueError("Search requires an explicit frozen project path")
        self.project = Path(config.search.project).resolve(strict=True)

    def prepare(self, root: Path) -> ProblemSpec:
        config = self.config.search
        data = Path(config.dataset_directory)
        splits = [questions(data / f"{name}.jsonl") for name in ("train", "dev", "test")]
        identities = [{item.question_id for item in items} for items in splits]
        groups = [{group for item in items for group in item.source_groups} for items in splits]
        if any(
            identities[i] & identities[j] or groups[i] & groups[j]
            for i in range(3)
            for j in range(i)
        ):
            raise ValueError("Search training and held-out data overlap")
        inputs = tuple(
            file_ref(path)
            for name in ("dev", "test")
            for path in (
                data / f"{name}.jsonl",
                data / f"{name}-corpus.jsonl",
                Path(config.index_directory) / f"{name}.npy",
            )
        )
        model_manifest = snapshot_directory(Path(config.model), root / "base-model.json")
        harness = write_record(root / "harness.json", config.harness)
        created = datetime.now(UTC).isoformat()
        baseline = SolutionVersion(
            "base",
            ModelVersion(
                "base-" + model_manifest.digest[:20],
                Path(config.model).name,
                ContentRef(Path(config.model).resolve().as_uri(), model_manifest.digest),
                created,
            ),
            HarnessVersion("search-" + harness.digest[:20], "Search", harness, created),
            created,
        )
        backend = Path(config.runtime.repository).resolve(strict=True)
        backend_source = snapshot_source(
            backend, root / "backend-source.json", paths=("areal", "pyproject.toml", "uv.lock")
        )
        backend_manifest = read_record(local_path(backend_source.content), DirectorySnapshot)
        locked = tuple(
            file_ref(path)
            for path in (
                *sorted((self.project / "src").rglob("*.py")),
                *sorted((self.project / "examples/search").rglob("*.py")),
                self.project / "examples/checkpoints.py",
                self.project / "src/areal_rsi/infra/worker.sh",
                *(backend / entry.path for entry in backend_manifest.files),
            )
        )
        fixed = [*inputs, harness, write_record(root / "evaluation-config.json", self.config)]
        if self.config.official is not None:
            official = self.config.official
            fixed.extend(
                file_ref(Path(path))
                for path in (
                    official.historical_environment,
                    official.asset_lock,
                    str(Path(official.data_root) / "datasets/queries.tsv"),
                )
            )
            if not self.config.official_image:
                raise ValueError("Official Search evaluation requires its frozen inference image")
        return ProblemSpec(
            "search",
            "Read examples/search/README.md. Optimize the search model through typed training "
            "recipes and versioned training data using dev feedback. Keep reward, evaluation "
            "data, evaluation retrieval, harness and "
            "inference budget fixed. Do not train on dev/test or official benchmark questions, "
            "answers or traces. "
            "Use rsi_submit_candidate, inspect valid evidence, and rsi_select_candidate before "
            "completion. GPU experiment jobs are distinct from the controller's own Slurm job. "
            "Final evaluation is run separately by the host after selection is frozen.",
            baseline,
            model_manifest,
            ComparisonContract(
                min_rounds=self.config.min_rounds,
                target_delta=self.config.target_delta,
                conditions=tuple(fixed),
            ),
            locked,
            (
                SourceTree(
                    str(self.project), ("src", "examples/search", "examples/checkpoints.py")
                ),
                SourceTree(str(backend), ("areal", "pyproject.toml", "uv.lock")),
            ),
        )

    def _job(self, root: Path, stage: StageRequest, gpus: int, *, image: str = "") -> JobSpec:
        config = self.config.search
        if stage.worker is not None:
            output = stage.worker.output
        else:
            assert stage.official is not None
            output = str(Path(stage.official.output).parent)
        path = root / "requests" / (Path(output).name + ".json")
        write_record(path, stage)
        search = ":".join(
            (
                str(self.project / "src"),
                str(self.project),
                config.runtime.repository,
                config.runtime.environment.get("PYTHONPATH", ""),
            )
        )
        return JobSpec(
            command=(
                "apptainer",
                "exec",
                "--no-eval",
                "--nv",
                "--bind",
                "/storage:/storage",
                image or self.config.image,
                "bash",
                str(self.project / "src/areal_rsi/infra/worker.sh"),
                stage.official.vllm_python if stage.official is not None else config.runtime.python,
                str(self.project / "examples/search/experiment/pipeline.py"),
                "--request",
                str(path),
            ),
            cwd=str(self.project),
            timeout_seconds=(
                self.config.slurm.minutes * 60
                if stage.official is not None
                else config.timeout_seconds
            ),
            env={**config.runtime.environment, "PYTHONPATH": search, "PYTHONUNBUFFERED": "1"},
            slurm=replace(self.config.slurm, gpus=gpus),
        )

    def build_candidate(self, context: CandidateContext, recipe: SearchRecipe) -> CandidatePlan:
        training = recipe.training or self.config.search.training
        config = replace(
            self.config.search,
            training=training,
            dataset_directory=recipe.dataset_directory or self.config.search.dataset_directory,
            index_directory=recipe.index_directory or self.config.search.index_directory,
        )
        dataset = Path(config.dataset_directory) / "train.jsonl"
        items = questions(dataset)
        training.validate_dataset_size(len(items))
        heldout = tuple(
            item
            for split in ("dev", "test")
            for item in questions(Path(self.config.search.dataset_directory) / f"{split}.jsonl")
        )
        if (
            any(item.split != "train" for item in items)
            or {item.question_id for item in items} & {item.question_id for item in heldout}
            or {group for item in items for group in item.source_groups}
            & {group for item in heldout for group in item.source_groups}
        ):
            raise ValueError("Candidate training data overlaps frozen held-out data")
        inputs = tuple(
            file_ref(path)
            for path in (
                dataset,
                Path(config.dataset_directory) / "train-corpus.jsonl",
                Path(config.index_directory) / "train.npy",
            )
        )
        request = WorkerRequest(
            "train",
            config,
            unquote(urlparse(context.parent.model.content.uri).path),
            str(dataset),
            "",
            context.output,
            context.run_id + "-" + context.candidate_id,
        )
        plan = self._job(
            Path(context.root),
            StageRequest(worker=request, reservation=self.config.slurm.reservation),
            config.actor_gpus + config.rollout_gpus + 1,
        )
        return CandidatePlan(
            replace(
                context.parent,
                solution_id=context.candidate_id,
                parent_solution_id=context.parent.solution_id,
            ),
            inputs,
            plan,
            config.runtime,
        )

    def build_evaluation(self, context: EvaluationContext) -> EvaluationPlan:
        config, root = self.config.search, Path(context.root)
        if context.split == BenchmarkSplit.TEST and self.config.official is not None:
            official = self.config.official
            model = Path(unquote(urlparse(context.solution.model.content.uri).path))
            source = snapshot_directory(
                model, root / "exports" / (context.solution.solution_id + "-source.json")
            )
            exported = export_checkpoint(
                local_path(source),
                root / "exports" / context.solution.solution_id,
                source_sha256=source.digest,
            )
            exported_ref = file_ref(exported)
            final = replace(
                official,
                model=str(root / "exports" / context.solution.solution_id),
                checkpoint_manifest=str(exported),
                checkpoint_manifest_sha256=exported_ref.digest,
                output=str(Path(context.output) / "official"),
            )
            query_path = Path(final.data_root) / "datasets/queries.tsv"
            with query_path.open() as stream:
                task_ids = tuple(row[0] for row in csv.reader(stream, delimiter="\t"))
            return EvaluationPlan(
                self._job(root, StageRequest(official=final), 3, image=self.config.official_image),
                task_ids,
                (file_ref(query_path), exported_ref),
            )
        split = "test" if context.split == BenchmarkSplit.TEST else "dev"
        data = Path(config.dataset_directory) / f"{split}.jsonl"
        items = questions(data)
        request = WorkerRequest(
            "eval",
            config,
            unquote(urlparse(context.solution.model.content.uri).path),
            str(data),
            "",
            context.output,
            context.run_id + "-" + Path(context.output).name,
        )
        return EvaluationPlan(
            self._job(
                root, StageRequest(worker=request, reservation=self.config.slurm.reservation), 2
            ),
            tuple(item.question_id for item in items),
            (file_ref(data),),
        )
