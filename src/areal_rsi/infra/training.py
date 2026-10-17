"""AReaL subprocess integration and validation of completed HF checkpoints."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from areal_rsi.api.execution import AReaLRuntime, JobSpec, TrainingReceipt, TrainingResult
from areal_rsi.api.records import ContentRef
from areal_rsi.infra.artifacts import read_record, snapshot_directory, write_record
from areal_rsi.infra.execution import JobExecutor


class AReaLBackend:
    def __init__(self, runtime: AReaLRuntime, jobs: JobExecutor):
        self.runtime = runtime
        self.jobs = jobs
        self.repository = Path(runtime.repository).resolve(strict=True)
        if not (self.repository / "areal" / "__init__.py").is_file():
            raise ValueError(f"Not an AReaL repository: {self.repository}")

    def spec(
        self,
        entrypoint: Path,
        arguments: tuple[str, ...],
        *,
        timeout: float,
        python_paths: tuple[Path, ...] = (),
    ) -> JobSpec:
        project = Path(__file__).resolve().parents[3]
        search = [
            *(str(path.resolve()) for path in python_paths),
            str(entrypoint.resolve().parent),
            str(project / "src"),
            str(project),
            str(self.repository),
        ]
        inherited = self.runtime.environment.get("PYTHONPATH", os.environ.get("PYTHONPATH", ""))
        if inherited:
            search.append(inherited)
        return JobSpec(
            (self.runtime.python, str(entrypoint.resolve()), *arguments),
            cwd=str(project),
            timeout_seconds=timeout,
            env={
                **self.runtime.environment,
                "PYTHONPATH": os.pathsep.join(search),
                "PYTHONUNBUFFERED": "1",
            },
        )

    async def train(self, run_id: str, name: str, spec: JobSpec) -> TrainingResult:
        job = await self.jobs.run(run_id, name, spec)
        directory = Path(job.result_path)
        receipt = read_record(directory / "training.json", TrainingReceipt)
        checkpoint = Path(receipt.checkpoint).resolve(strict=True)
        if not checkpoint.is_relative_to(directory) or receipt.global_step <= 0:
            raise ValueError(
                "Training must publish a new checkpoint inside its job with real steps"
            )
        if any(not math.isfinite(value) for value in receipt.metrics.values()):
            raise ValueError("Training metrics must be finite")
        config = checkpoint / "config.json"
        if not config.is_file() or not isinstance(json.loads(config.read_text()), dict):
            raise ValueError("Checkpoint has no valid HuggingFace config.json")
        weights = [*checkpoint.glob("*.safetensors"), *checkpoint.glob("pytorch_model*.bin")]
        if not weights or any(
            path.stat().st_size == 0 or not path.resolve().is_relative_to(checkpoint)
            for path in weights
        ):
            raise ValueError("Checkpoint has no complete model weights")
        for index in checkpoint.glob("*.index.json"):
            # HF index is an external JSON boundary, not internal business state.
            weight_map = json.loads(index.read_text()).get("weight_map", {})
            if not weight_map or any(
                not (checkpoint / name).is_file()
                or not (checkpoint / name).resolve().is_relative_to(checkpoint)
                for name in weight_map.values()
            ):
                raise ValueError(f"Checkpoint is missing indexed shards: {index}")
        manifest = snapshot_directory(checkpoint, directory / "checkpoint.json")
        result = TrainingResult(
            job.job_id,
            ContentRef(checkpoint.as_uri(), manifest.digest),
            manifest,
            receipt.global_step,
            receipt.metrics,
        )
        write_record(directory / "result.json", result)
        return result
