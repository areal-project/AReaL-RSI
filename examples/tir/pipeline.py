"""Run the TIR experiment without starting an RSI/Codex session."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from areal_rsi.api.execution import AReaLRuntime, JobSpec, TrainingResult
from areal_rsi.api.records import (
    ContentRef,
    EvaluationResult,
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
from examples.tir.data import DatasetManifest, SynthesisConfig, read_problems, synthesize


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    max_turns: int = 2
    max_length: int = 2048
    tool_timeout: float = 15
    enable_tools: str = "python;calculator"
    is_chat_model: bool = False


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    epochs: int = 1
    batch_size: int = 4
    n_samples: int = 4
    max_steps: int = 2
    max_new_tokens: int = 512
    temperature: float = 1.0
    learning_rate: float = 0.00001


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    seed: int = 42
    max_new_tokens: int = 512
    temperature: float = 0.0


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    runtime: AReaLRuntime
    model: str
    output_root: str = "runs/tir"
    synthesis: SynthesisConfig = field(default_factory=SynthesisConfig)
    harness: HarnessConfig = field(default_factory=HarnessConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    backend_config: str = "examples/tir/tir_math_config.yaml"
    backend_overrides: tuple[str, ...] = ()
    timeout_seconds: float = 3600

    def __post_init__(self) -> None:
        if min(self.training.epochs, self.training.batch_size, self.training.max_steps) <= 0:
            raise ValueError("Epochs, batch size and max_steps must be positive")
        if self.training.n_samples < 2 or self.training.temperature <= 0:
            raise ValueError("GRPO requires multiple samples and positive training temperature")
        if self.synthesis.train_size < self.training.batch_size:
            raise ValueError("Training split must contain at least one full batch")
        if (
            min(
                self.harness.max_turns,
                self.harness.max_length,
                self.harness.tool_timeout,
                self.training.max_new_tokens,
                self.evaluation.max_new_tokens,
                self.timeout_seconds,
                self.training.learning_rate,
            )
            <= 0
        ):
            raise ValueError("Limits and learning rate must be positive")


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    mode: Literal["train", "eval"]
    config: ExperimentConfig
    model: str
    dataset: str
    dev_dataset: str
    output: str
    trial: str


@dataclass(frozen=True, slots=True)
class Provenance:
    config: ContentRef
    project: SourceSnapshot
    backend: SourceSnapshot
    model: ContentRef
    backend_harness: ContentRef
    datasets: DatasetManifest
    baseline: SolutionVersion


@dataclass(frozen=True, slots=True)
class HarnessManifest:
    config: HarnessConfig
    project: ContentRef
    backend_tir: ContentRef


@dataclass(slots=True)
class ExperimentState:
    run_id: str
    status: Literal[
        "preparing",
        "prepared",
        "baseline",
        "training",
        "evaluation",
        "completed",
        "failed",
        "cancelled",
    ] = "preparing"
    provenance: Provenance | None = None
    baseline: EvaluationResult | None = None
    training: TrainingResult | None = None
    evaluation: EvaluationResult | None = None
    candidate: SolutionVersion | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class Comparison:
    baseline: EvaluationResult
    candidate: EvaluationResult
    training: TrainingResult
    provenance: Provenance
    score_delta: float
    accuracy_delta: float
    selected: Literal["baseline", "candidate"]
    candidate_solution: SolutionVersion


def local_path(ref: ContentRef) -> Path:
    parsed = urlparse(ref.uri)
    if parsed.scheme != "file":
        raise ValueError("This local example requires file artifact references")
    path = Path(unquote(parsed.path))
    if file_ref(path).digest != ref.digest:
        raise ValueError(f"Artifact content changed: {path}")
    return path


async def run(config: ExperimentConfig, *, prepare_only: bool = False) -> Path:
    run_id = f"tir-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    root = Path(config.output_root).resolve()
    directory = root / run_id
    directory.mkdir(parents=True, exist_ok=False)
    state = ExperimentState(run_id)

    def save() -> None:
        write_record(directory / "experiment.json", state)

    save()
    print(f"run={run_id} output={directory}", flush=True)
    try:
        config_ref = write_record(directory / "config.json", config)
        datasets = synthesize(config.synthesis, directory / "data")
        repository = Path(config.runtime.repository).resolve(strict=True)
        project = Path(__file__).resolve().parents[2]
        project_paths = ("src", "examples/tir", "examples/__init__.py", "pyproject.toml", "uv.lock")
        backend_paths = ("areal", "examples/tir", "pyproject.toml", "uv.lock")
        project_source = snapshot_source(
            project, directory / "source-project.json", paths=project_paths
        )
        backend_source = snapshot_source(
            repository, directory / "source-backend.json", paths=backend_paths
        )
        model = snapshot_directory(Path(config.model), directory / "base-model.json")
        backend_harness = snapshot_directory(
            repository / "examples" / "tir", directory / "harness.json"
        )
        harness_ref = write_record(
            directory / "harness-version.json",
            HarnessManifest(
                config.harness,
                project_source.content,
                backend_harness,
            ),
        )
        created_at = datetime.now(UTC).isoformat()
        harness_version = HarnessVersion(
            f"harness-{harness_ref.digest[:20]}", "TIR", harness_ref, created_at
        )
        model_version = ModelVersion(
            f"model-{model.digest[:20]}",
            Path(config.model).name,
            ContentRef(Path(config.model).resolve().as_uri(), model.digest),
            created_at,
        )
        baseline_solution = SolutionVersion(
            f"{run_id}-base", model_version, harness_version, created_at
        )
        state.provenance = Provenance(
            config_ref,
            project_source,
            backend_source,
            model,
            backend_harness,
            datasets,
            baseline_solution,
        )
        state.status = "prepared"
        save()
        if prepare_only:
            return directory
        jobs = JobExecutor(root)
        inherited = config.runtime.environment.get("PYTHONPATH", os.environ.get("PYTHONPATH", ""))
        tir_paths = [str(repository / "examples" / "tir")]
        if inherited:
            tir_paths.append(inherited)
        runtime = replace(
            config.runtime,
            environment={
                **config.runtime.environment,
                "PYTHONPATH": os.pathsep.join(tir_paths),
            },
        )
        backend = AReaLBackend(runtime, jobs)
        evaluator = EvaluationService(jobs, LocalTrajectoryStore(directory / "trajectories"))
        worker = Path(__file__).with_name("worker.py")
        evaluation_data = local_path(datasets.evaluation)
        task_ids = tuple(item.task_id for item in read_problems(evaluation_data))

        def spec(mode: Literal["train", "eval"], name: str, model: str) -> JobSpec:
            request = WorkerRequest(
                mode,
                config,
                model,
                str(local_path(datasets.train) if mode == "train" else evaluation_data),
                str(local_path(datasets.dev)),
                str(jobs.directory(run_id, name)),
                f"{run_id}-{name}",
            )
            request_path = directory / f"request-{name}.json"
            write_record(request_path, request)
            return backend.spec(
                worker, ("--request", str(request_path)), timeout=config.timeout_seconds
            )

        state.status = "baseline"
        save()
        state.baseline = await evaluator.evaluate(
            run_id,
            "baseline",
            spec("eval", "baseline", config.model),
            task_ids=task_ids,
            solution_id=baseline_solution.solution_id,
        )
        state.status = "training"
        save()
        state.training = await backend.train(run_id, "train", spec("train", "train", config.model))
        state.status = "evaluation"
        save()
        trained_model = unquote(urlparse(state.training.checkpoint.uri).path)
        created_at = datetime.now(UTC).isoformat()
        state.candidate = SolutionVersion(
            f"{run_id}-trained",
            ModelVersion(
                f"model-{state.training.checkpoint.digest[:20]}",
                "TIR trained",
                state.training.checkpoint,
                created_at,
                parent_version_id=model_version.version_id,
            ),
            harness_version,
            created_at,
            parent_solution_id=baseline_solution.solution_id,
        )
        state.evaluation = await evaluator.evaluate(
            run_id,
            "candidate",
            spec("eval", "candidate", trained_model),
            task_ids=task_ids,
            solution_id=state.candidate.solution_id,
        )
        project_after = snapshot_source(
            project, directory / "source-project-after.json", paths=project_paths
        )
        backend_after = snapshot_source(
            repository, directory / "source-backend-after.json", paths=backend_paths
        )
        if (
            project_after.content.digest != state.provenance.project.content.digest
            or backend_after.content.digest != state.provenance.backend.content.digest
        ):
            raise ValueError("Source code changed during the experiment; comparison is invalid")
        baseline = state.baseline.report
        candidate = state.evaluation.report
        comparison = Comparison(
            state.baseline,
            state.evaluation,
            state.training,
            state.provenance,
            candidate.score - baseline.score,
            candidate.metrics["accuracy"] - baseline.metrics["accuracy"],
            "candidate" if candidate.score > baseline.score else "baseline",
            state.candidate,
        )
        write_record(directory / "comparison.json", comparison)
        (directory / "report.md").write_text(
            f"# TIR experiment {run_id}\n\n"
            f"| | Baseline | Trained |\n| --- | ---: | ---: |\n"
            f"| Score | {baseline.score:.4f} | {candidate.score:.4f} |\n"
            f"| Accuracy | {baseline.metrics['accuracy']:.4f} | "
            f"{candidate.metrics['accuracy']:.4f} |\n\n"
            f"Steps: {state.training.global_step}; selected: {comparison.selected}.\n\n"
            "Full provenance and evidence references: [comparison.json](comparison.json).\n"
        )
        state.status = "completed"
        save()
        print((directory / "report.md").read_text(), flush=True)
        return directory
    except asyncio.CancelledError:
        state.status = "cancelled"
        state.error = "Experiment interrupted"
        save()
        raise
    except Exception as exc:
        state.status = "failed"
        state.error = str(exc)
        save()
        raise


async def _main(config: ExperimentConfig, prepare_only: bool) -> None:
    task = asyncio.create_task(run(config, prepare_only=prepare_only))
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, task.cancel)
    await task


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Generate data and provenance only; no evaluation or training",
    )
    args = parser.parse_args()
    config = read_record(args.config, ExperimentConfig)
    try:
        asyncio.run(_main(config, args.prepare_only))
    except asyncio.CancelledError:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
