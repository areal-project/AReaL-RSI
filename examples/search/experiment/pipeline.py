"""Run baseline → training → independent validation → incumbent selection."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import socket
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from areal_rsi.api.execution import TrainingResult
from areal_rsi.api.records import BenchmarkSplit, EvaluationResult
from areal_rsi.infra.artifacts import file_ref, read_record, write_record
from areal_rsi.infra.coordination.evaluation import EvaluationService
from areal_rsi.infra.execution import JobExecutor, ProcessIdentity
from areal_rsi.infra.training import AReaLBackend
from areal_rsi.infra.trajectories import LocalTrajectoryStore
from examples.search import PROJECT_ROOT, SEARCH_ROOT
from examples.search.experiment.config import HarnessConfig, SearchConfig, WorkerRequest
from examples.search.experiment.slurm import submit


@dataclass(slots=True)
class ExperimentState:
    run_id: str
    config_sha256: str
    phase: str = "prepared"
    baseline: EvaluationResult | None = None
    training: TrainingResult | None = None
    candidate: EvaluationResult | None = None
    selected_model: str = ""
    error: str = ""
    evaluation_split: Literal["dev", "test"] | None = None


@dataclass(frozen=True, slots=True)
class Incumbent:
    model: str
    accuracy: float
    run_id: str
    evaluation: EvaluationResult
    dev_sha256: str
    harness: HarnessConfig


async def wait_for_dense(process: asyncio.subprocess.Process, url: str) -> None:
    import urllib.error
    import urllib.request

    def probe() -> bool:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return bool(response.status == 200)
        except (urllib.error.URLError, TimeoutError):
            return False

    for _ in range(240):
        if process.returncode is not None:
            raise RuntimeError("Dense service exited before readiness; inspect its log")
        if await asyncio.to_thread(probe):
            return
        await asyncio.sleep(0.5)
    raise TimeoutError("Dense service readiness deadline exceeded")


async def run(
    config: SearchConfig,
    run_id: str,
    *,
    evaluation_split: Literal["dev", "test"] | None = None,
) -> Path:
    """Run one candidate or independent evaluation in a verified Slurm allocation.

    A native ResearchSystem Goal chooses subsequent configs from dev evidence.
    This runner owns execution and incumbent integrity, never the research policy.
    """
    job_id = os.environ.get("SLURM_JOB_ID")
    if not job_id:
        raise ValueError("Search experiments require an existing Slurm reservation allocation")
    GPUs = tuple(item for item in os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",") if item)
    required = 2 if evaluation_split else config.actor_gpus + config.rollout_gpus + 1
    if len(GPUs) < required:
        raise ValueError(f"Allocation requires {required} GPUs including dense retrieval")
    # The training SIF omits Slurm clients. The host submission script supplies
    # the authoritative allocation record before entering that container.
    job_info = os.environ.get("SEARCH_SLURM_JOB_INFO", "")
    if not job_info:
        check = await asyncio.create_subprocess_exec(
            "scontrol", "show", "job", "-o", job_id, stdout=asyncio.subprocess.PIPE
        )
        payload, _ = await check.communicate()
        if check.returncode:
            raise ValueError("Cannot verify the Slurm allocation")
        job_info = payload.decode()
    fields = set(job_info.split())
    if "Reservation=rsi" not in fields or f"JobId={job_id}" not in fields:
        raise ValueError("The experiment must run in the rsi reservation")
    root = Path(config.output_root).resolve()
    jobs = JobExecutor(root)
    directory = jobs.directory(run_id, "train").parents[1]
    directory.mkdir(parents=True, exist_ok=True)
    # Training serializes incumbent writes. Independent evaluation never selects
    # a model and uses its own lease, so a held-out result cannot update incumbent.
    lock = directory / "evaluation.lock" if evaluation_split else root / "experiment.lock"
    with lock.open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config_path = directory / "config.json"
        if config_path.exists():
            if read_record(config_path, SearchConfig) != config:
                raise ValueError("Resume configuration differs from the frozen experiment")
        else:
            write_record(config_path, config)
        digest = file_ref(config_path).digest
        state_path = directory / "experiment.json"
        state = (
            read_record(state_path, ExperimentState)
            if state_path.exists()
            else ExperimentState(run_id, digest, evaluation_split=evaluation_split)
        )
        if state.config_sha256 != digest:
            raise ValueError("Experiment configuration was changed")
        if state.evaluation_split != evaluation_split:
            raise ValueError("Resume evaluation split differs from the frozen experiment")
        evaluator = EvaluationService(jobs, LocalTrajectoryStore(directory / "trajectories"))
        dev = Path(config.dataset_directory) / f"{evaluation_split or 'dev'}.jsonl"
        dev_digest = file_ref(dev).digest
        task_ids = tuple(json.loads(line)["question_id"] for line in dev.read_text().splitlines())
        backend = AReaLBackend(config.runtime, jobs)

        async def stage(
            mode: Literal["train", "eval"], name: str, model: str
        ) -> TrainingResult | EvaluationResult:
            job_directory = jobs.directory(run_id, name)
            result_path = job_directory / ("result.json" if mode == "train" else "evaluation.json")
            if result_path.exists():
                record = jobs.detail(run_id, name)
                if record.state != "succeeded":
                    raise ValueError("Saved result belongs to an unsuccessful job")
                if mode == "train":
                    result = read_record(result_path, TrainingResult)
                    # Check every recorded checkpoint file on resume, not merely its receipt.
                    from areal_rsi.api.records import DirectorySnapshot

                    snapshot = read_record(
                        Path(result.manifest.uri.removeprefix("file://")), DirectorySnapshot
                    )
                    checkpoint = Path(result.checkpoint.uri.removeprefix("file://"))
                    for entry in snapshot.files:
                        if file_ref(checkpoint / entry.path).digest != entry.sha256:
                            raise ValueError("Saved checkpoint content changed")
                    return result
                evaluation = read_record(result_path, EvaluationResult)
                samples = Path(evaluation.samples.uri.removeprefix("file://"))
                if file_ref(samples).digest != evaluation.samples.digest:
                    raise ValueError("Saved evaluation content changed")
                return evaluation
            if job_directory.exists():
                record = jobs.detail(run_id, name)
                if record.pid is not None and ProcessIdentity.read(record.pid) is not None:
                    raise RuntimeError(
                        f"Existing stage PID {record.pid} is live; do not duplicate it"
                    )
                raise RuntimeError(
                    "Incomplete previous stage retained; inspect it and use a new run ID"
                )
            split = "train" if mode == "train" else (evaluation_split or "dev")
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            url = f"http://127.0.0.1:{port}/mcp"
            request = WorkerRequest(
                mode,
                config,
                model,
                str(Path(config.dataset_directory) / f"{split}.jsonl"),
                url,
                str(job_directory),
                f"{run_id}-{name}",
            )
            request_path = directory / f"request-{name}.json"
            write_record(request_path, request)
            spec = backend.spec(
                SEARCH_ROOT / "training/worker.py",
                ("--request", str(request_path)),
                timeout=config.timeout_seconds,
            )
            policy_gpus = (
                GPUs[: config.actor_gpus + config.rollout_gpus] if mode == "train" else GPUs[:1]
            )
            spec = replace(spec, env={**spec.env, "CUDA_VISIBLE_DEVICES": ",".join(policy_gpus)})
            with (directory / f"dense-{name}.log").open("wb") as log:
                dense = await asyncio.create_subprocess_exec(
                    config.dense_python,
                    "-m",
                    "examples.search.task.retrieval",
                    "serve",
                    "--corpus",
                    str(Path(config.dataset_directory) / f"{split}-corpus.jsonl"),
                    "--index",
                    str(Path(config.index_directory) / f"{split}.npy"),
                    "--model",
                    config.embedding_model,
                    "--tokenizer",
                    config.snippet_tokenizer,
                    "--benchmark-repository",
                    config.benchmark_repository,
                    "--port",
                    str(port),
                    env={**os.environ, **spec.env, "CUDA_VISIBLE_DEVICES": GPUs[-1]},
                    cwd=str(PROJECT_ROOT),
                    stdout=log,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    await wait_for_dense(dense, url)
                    if mode == "train":
                        return await backend.train(run_id, name, spec)
                    return await evaluator.evaluate(
                        run_id,
                        name,
                        spec,
                        task_ids=task_ids,
                        solution_id=f"{run_id}-{name}",
                        split=BenchmarkSplit.TEST if split == "test" else BenchmarkSplit.VALIDATE,
                    )
                finally:
                    await JobExecutor._terminate(dense)

        try:
            if evaluation_split:
                state.phase = "evaluation"
                write_record(state_path, state)
                result = await stage("eval", evaluation_split, config.model)
                if not isinstance(result, EvaluationResult):
                    raise TypeError("Independent evaluation returned a training receipt")
                state.candidate = result
                state.selected_model = config.model
                state.phase = "completed"
                state.error = ""
                write_record(state_path, state)
                return directory
            state.phase = "baseline"
            write_record(state_path, state)
            baseline = await stage("eval", "baseline", config.model)
            if not isinstance(baseline, EvaluationResult):
                raise TypeError("Baseline stage returned a training receipt")
            state.baseline = baseline
            state.phase = "training"
            write_record(state_path, state)
            training = await stage("train", "train", config.model)
            if not isinstance(training, TrainingResult):
                raise TypeError("Training stage returned an evaluation receipt")
            state.training = training
            state.phase = "evaluation"
            write_record(state_path, state)
            model = str(Path(training.checkpoint.uri.removeprefix("file://")))
            candidate = await stage("eval", "candidate", model)
            if not isinstance(candidate, EvaluationResult):
                raise TypeError("Candidate stage returned a training receipt")
            state.candidate = candidate
            incumbent_path = root / "incumbent.json"
            incumbent = (
                read_record(incumbent_path, Incumbent)
                if incumbent_path.exists()
                else Incumbent(
                    config.model,
                    baseline.report.score,
                    run_id,
                    baseline,
                    dev_digest,
                    config.harness,
                )
            )
            if incumbent.dev_sha256 != dev_digest or incumbent.harness != config.harness:
                raise ValueError("Incumbent and candidate comparison conditions differ")
            if candidate.report.score > incumbent.accuracy:
                incumbent = Incumbent(
                    model, candidate.report.score, run_id, candidate, dev_digest, config.harness
                )
            write_record(incumbent_path, incumbent)
            state.selected_model = incumbent.model
            state.phase = "completed"
            state.error = ""
            write_record(state_path, state)
            return directory
        except BaseException as exc:
            state.phase = "interrupted" if isinstance(exc, asyncio.CancelledError) else "failed"
            state.error = str(exc)
            write_record(state_path, state)
            raise


def stage_model(source: Path, output: Path) -> Path:
    """Match historical inference's explicit YaRN config without modifying weights."""
    source = source.resolve(strict=True)
    effective = json.loads((source / "config.json").read_text())
    effective["max_position_embeddings"] = 131072
    effective["rope_scaling"] = {
        "rope_type": "yarn",
        "factor": 4.0,
        "original_max_position_embeddings": 32768,
    }
    if output.exists():
        if json.loads((output / "config.json").read_text()) != effective:
            raise ValueError("Existing staged model has a different effective configuration")
        for path in source.iterdir():
            if path.is_file() and path.name != "config.json":
                staged = output / path.name
                if not staged.is_symlink() or staged.resolve() != path:
                    raise ValueError(
                        "Existing staged model is incomplete or has a different source"
                    )
        return output
    output.mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file() and path.name != "config.json":
            (output / path.name).symlink_to(path)
    (output / "config.json").write_text(json.dumps(effective, indent=2) + "\n")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--time-limit", type=int, default=7200)
    parser.add_argument("--evaluate-only", choices=("dev", "test"))
    arguments = parser.parse_args()
    config = read_record(arguments.config, SearchConfig)
    if arguments.submit:
        if arguments.image is None:
            parser.error("--submit requires --image")
        print(
            submit(
                arguments.config,
                arguments.run_id,
                arguments.image,
                arguments.time_limit,
                evaluation_split=arguments.evaluate_only,
            ),
            flush=True,
        )
    else:
        print(
            asyncio.run(run(config, arguments.run_id, evaluation_split=arguments.evaluate_only)),
            flush=True,
        )


if __name__ == "__main__":
    main()
