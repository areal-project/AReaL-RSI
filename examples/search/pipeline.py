"""Search experiment contracts shared by the RSI host and AReaL workers."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import shlex
import socket
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from areal_rsi.api.execution import AReaLRuntime, TrainingResult
from areal_rsi.api.records import BenchmarkSplit, EvaluationResult
from areal_rsi.infra.artifacts import file_ref, read_record, write_record
from areal_rsi.infra.coordination.evaluation import EvaluationService
from areal_rsi.infra.execution import JobExecutor, ProcessIdentity
from areal_rsi.infra.training import AReaLBackend
from areal_rsi.infra.trajectories import LocalTrajectoryStore


@dataclass(frozen=True, slots=True)
class TrainingConfig:
    batch_size: int = 8
    n_samples: int = 4
    max_steps: int = 10
    epochs: int = 1
    learning_rate: float = 1e-6
    seed: int = 17

    def __post_init__(self) -> None:
        if min(self.batch_size, self.max_steps, self.epochs) < 1 or self.n_samples < 2:
            raise ValueError("GRPO requires positive steps/batches and at least two samples")
        if not 0 < self.learning_rate < 0.001:
            raise ValueError("Invalid model learning rate")

    def validate_dataset_size(self, questions: int) -> None:
        available_steps = self.epochs * (questions // self.batch_size)
        if available_steps < self.max_steps:
            raise ValueError(
                f"Dataset and epochs allow {available_steps} full updates; "
                f"requested {self.max_steps}. Increase epochs or provide more training questions."
            )


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    max_model_calls: int = 50
    max_input_tokens: int = 115000
    max_completion_tokens: int = 10000
    context_tokens: int = 131072
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    thinking: bool = True

    def __post_init__(self) -> None:
        expected = (50, 115000, 10000, 131072, 0.7, 0.8, 20, True)
        actual = (
            self.max_model_calls,
            self.max_input_tokens,
            self.max_completion_tokens,
            self.context_tokens,
            self.temperature,
            self.top_p,
            self.top_k,
            self.thinking,
        )
        if actual != expected:
            raise ValueError("The primary model comparison requires the frozen original harness")


@dataclass(frozen=True, slots=True)
class SearchConfig:
    runtime: AReaLRuntime
    model: str
    benchmark_repository: str
    dataset_directory: str
    index_directory: str
    embedding_model: str
    snippet_tokenizer: str
    output_root: str
    training: TrainingConfig = field(default_factory=TrainingConfig)
    harness: HarnessConfig = field(default_factory=HarnessConfig)
    actor_gpus: int = 4
    rollout_gpus: int = 2
    max_concurrent_rollouts: int = 8
    evaluation_seed: int = 42
    timeout_seconds: float = 7200.0
    dense_python: str = sys.executable
    project: str = ""

    def __post_init__(self) -> None:
        if min(self.actor_gpus, self.rollout_gpus, self.max_concurrent_rollouts) < 1:
            raise ValueError("Actor, rollout and concurrency allocations must be positive")
        if self.actor_gpus + self.rollout_gpus + 1 > 8:
            raise ValueError("Actor + rollout + dense retrieval must fit within eight GPUs")


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    mode: Literal["train", "eval"]
    config: SearchConfig
    model: str
    dataset: str
    mcp_url: str
    output: str
    trial: str


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


@dataclass(frozen=True, slots=True)
class SlurmSubmission:
    job_id: str
    kind: str
    gpus: int
    release_pending: bool = False


def release_submission(receipt: Path, job: SlurmSubmission) -> str:
    if job.release_pending:
        try:
            status = subprocess.check_output(
                ["scontrol", "show", "job", "-o", job.job_id], text=True
            )
            fields = set(status.split())
            if (
                f"JobId={job.job_id}" not in fields
                or not any(field.startswith("JobState=") for field in fields)
                or not any(field.startswith("Reason=") for field in fields)
                or "Reason=JobHeldAdmin" in fields
            ):
                raise RuntimeError("Cannot confirm the submitted job's release state")
            if "Reason=JobHeldUser" in fields or (
                "JobState=PENDING" in fields and "Priority=0" in fields
            ):
                subprocess.check_call(["scontrol", "release", job.job_id])
            write_record(receipt, replace(job, release_pending=False))
        except Exception as exc:
            raise RuntimeError(
                f"Slurm job {job.job_id} is recorded at {receipt}; release is not confirmed. "
                "Inspect the job and retry the same run ID."
            ) from exc
    return job.job_id


def submit(
    config_path: Path,
    run_id: str,
    image: Path,
    time_limit: int,
    *,
    evaluation_split: Literal["dev", "test"] | None = None,
) -> str:
    """Submit a candidate or independent evaluation, retaining its Slurm receipt."""
    config = read_record(config_path, SearchConfig)
    gpus = 2 if evaluation_split else config.actor_gpus + config.rollout_gpus + 1
    kind = f"external-{evaluation_split}-evaluation" if evaluation_split else "training-candidate"
    time_limit = ((time_limit + 59) // 60) * 60
    if not 0 < time_limit <= 24 * 3600 or not config.project:
        raise ValueError("Submission requires a frozen project path and a positive time limit")
    project = Path(config.project).resolve(strict=True)
    entrypoint = project / "examples/search/pipeline.py"
    if not entrypoint.is_file():
        raise ValueError("Frozen project has no search pipeline entrypoint")
    root = Path(config.output_root).resolve()
    JobExecutor(root).directory(run_id, "train")  # Validate the caller-owned identifier.
    submissions = root / "submissions"
    submissions.mkdir(parents=True, exist_ok=True)
    directory = submissions / run_id
    with (submissions / f"{run_id}.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = directory / "slurm-job.json"
        if receipt.exists():
            job = read_record(receipt, SlurmSubmission)
            if (
                job.kind != kind
                or job.gpus != gpus
                or read_record(directory / "config.json", SearchConfig) != config
            ):
                raise ValueError("Existing submission has a different configuration or mode")
            return release_submission(receipt, job)
        if directory.exists():
            raise RuntimeError(
                f"Incomplete submission at {directory}; inspect Slurm job name search-{run_id} "
                "before retrying. Automatic resubmission is disabled."
            )
        directory.mkdir(parents=True, exist_ok=False)
        frozen_config = directory / "config.json"
        write_record(frozen_config, config)
        overlay = config.runtime.environment.get("PYTHONPATH", "")
        search_path = os.pathsep.join(
            [str(project / "src"), str(project), config.runtime.repository, overlay]
        )
        command = [
            config.runtime.python,
            str(entrypoint),
            "--config",
            str(frozen_config),
            "--run-id",
            run_id,
        ]
        if evaluation_split:
            command.extend(("--evaluate-only", evaluation_split))
        # Pass paths as argv with shell quoting; neither config text nor credentials
        # become executable shell substitutions. The planner key is not exported.
        inside = "\n".join(
            [
                "set -euo pipefail",
                "unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy",
                "export NO_PROXY='*' no_proxy='*' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1",
                "export PYTHONDONTWRITEBYTECODE=1",
                "export OMP_NUM_THREADS=8 MKL_NUM_THREADS=8 TOKENIZERS_PARALLELISM=false",
                "export PYTHONPATH=" + shlex.quote(search_path),
                "cd " + shlex.quote(str(project)),
                "cudnn_lib=$("
                + shlex.quote(config.runtime.python)
                + " -c "
                + shlex.quote(
                    "from importlib.metadata import distribution; "
                    'print(distribution("nvidia-cudnn-cu12").locate_file("nvidia/cudnn/lib"))'
                )
                + ")",
                'export LD_LIBRARY_PATH="$cudnn_lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"',
                "exec " + shlex.join(command),
            ]
        )
        (directory / "inside.sh").write_text(inside + "\n")
        script = directory / "run.sbatch"
        script.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'allocation_info=$(scontrol show job -o "$SLURM_JOB_ID")\n'
            'export SEARCH_SLURM_JOB_INFO="$allocation_info"\n'
            + shlex.join(
                [
                    "srun",
                    "--mpi=none",
                    "apptainer",
                    "exec",
                    "--no-eval",
                    "--nv",
                    "--bind",
                    "/storage:/storage",
                    str(image.resolve(strict=True)),
                    "bash",
                    str(directory / "inside.sh"),
                ]
            )
            + "\n"
        )
        args = [
            "sbatch",
            "--parsable",
            "--hold",
            "--reservation=rsi",
            "--partition=all",
            "--nodes=1",
            "--ntasks=1",
            f"--gres=gpu:{gpus}",
            "--cpus-per-task=32",
            "--mem=256G",
            f"--time={time_limit // 60 + bool(time_limit % 60)}",
            f"--job-name=search-{run_id}",
            f"--output={directory}/slurm-%j.log",
            str(script),
        ]
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"RSI_API_KEY", "OPENAI_API_KEY", "SEARCH_PROXY_ADMIN_KEY"}
        }
        job_id = subprocess.check_output(args, env=environment, text=True).strip().split(";")[0]
        job = SlurmSubmission(job_id, kind, gpus, release_pending=True)
        try:
            write_record(receipt, job)
        except BaseException as exc:
            try:
                subprocess.check_call(["scancel", job_id])
            except Exception as cancel_error:
                raise RuntimeError(
                    f"Slurm job {job_id} was submitted held, but its receipt could not be saved "
                    "and cancellation was not confirmed. "
                    f"Inspect it with scontrol show job {job_id}."
                ) from cancel_error
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise RuntimeError(
                f"Slurm job {job_id} was cancelled because its receipt could not be saved."
            ) from exc
        return release_submission(receipt, job)


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
                Path(__file__).with_name("worker.py"),
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
                    "examples.search.dense",
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
                    cwd=str(Path(__file__).resolve().parents[2]),
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
