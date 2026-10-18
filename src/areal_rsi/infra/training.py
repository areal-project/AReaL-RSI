"""AReaL subprocess integration and validation of completed HF checkpoints."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from areal_rsi.api.control import decode_record
from areal_rsi.api.execution import (
    AReaLRuntime,
    JobSpec,
    TokenizedTrainingSample,
    TrainingReceipt,
    TrainingResult,
)
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


def read_training_samples(path: Path) -> tuple[TokenizedTrainingSample, ...]:
    with path.open() as stream:
        samples = tuple(decode_record(TokenizedTrainingSample, json.loads(line)) for line in stream)
    if not samples or len({item.task_id for item in samples}) != len(samples):
        raise ValueError("SFT dataset must contain distinct nonempty training samples")
    return samples


def train_sft(config: Any, path: Path) -> None:
    """Backend-side SFT execution shared by tasks supplying tokenized examples.

    ``config`` is AReaL's decoded SFTConfig, an optional dependency present only
    in the training runtime. No task prompt, teacher, or data-selection policy
    belongs here. AReaL shifts the unshifted target mask inside LMEngine.
    """
    from areal.trainer.sft_trainer import SFTTrainer  # type: ignore[import-not-found]
    from datasets import Dataset  # type: ignore[import-not-found]

    samples = read_training_samples(path)
    possible_steps = len(samples) // config.train_dataset.batch_size * config.total_train_epochs
    if possible_steps < config.total_train_steps:
        raise ValueError("SFT dataset and epochs cannot supply the requested updates")
    data = Dataset.from_list(
        [{"input_ids": list(item.input_ids), "loss_mask": list(item.loss_mask)} for item in samples]
    )
    with SFTTrainer(config, data) as trainer:
        trainer.train()


def parameter_probe(model: Path, key: str | None = None) -> tuple[str, Any]:
    from safetensors import safe_open  # type: ignore[import-not-found]

    for path in sorted(model.glob("*.safetensors")):
        with safe_open(path, framework="pt", device="cpu") as reader:
            keys = list(reader.keys())
            selected = key if key in keys else None
            if key is None:
                selected = next((name for name in keys if name.endswith("q_proj.weight")), None)
            if selected is not None:
                return selected, reader.get_tensor(selected).flatten()[:2048].float().clone()
    raise ValueError(f"No matching parameter probe in {model}; this worker uses safetensors")


def canonicalize_tied_checkpoint(checkpoint: Path) -> bool:
    """Make a newly written tied checkpoint preserve sharing when loaded again.

    Some HF loaders deliberately untie a model when both matrix names are in
    the checkpoint, while serving engines honor tie_word_embeddings. Remove
    an exactly redundant output matrix. Conflicting matrices require an
    explicit migration decision; silently choosing one would change a policy.
    This runs in the backend environment, before the checkpoint is published.
    """
    import torch  # type: ignore[import-not-found]
    from safetensors import safe_open
    from safetensors.torch import save_file  # type: ignore[import-not-found]

    # HuggingFace config and index are external JSON boundaries.
    config = json.loads((checkpoint / "config.json").read_text())
    if config.get("tie_word_embeddings") is not True:
        return False
    embedding, head = "model.embed_tokens.weight", "lm_head.weight"
    locations: dict[str, Path] = {}
    for path in checkpoint.glob("*.safetensors"):
        with safe_open(path, framework="pt", device="cpu") as reader:
            names = tuple(reader.keys())
            for name in (embedding, head):
                if name in names:
                    locations[name] = path
    if len(locations) != 2:
        return False
    with safe_open(locations[embedding], framework="pt", device="cpu") as reader:
        input_weight = reader.get_tensor(embedding)
    with safe_open(locations[head], framework="pt", device="cpu") as reader:
        output_weight = reader.get_tensor(head)
    if input_weight.dtype != output_weight.dtype or not torch.equal(input_weight, output_weight):
        raise ValueError(
            "Checkpoint declares tied embeddings but stores different input/output weights; "
            "HF training and serving would load different policies"
        )
    removed_bytes = output_weight.numel() * output_weight.element_size()
    path = locations[head]
    with safe_open(path, framework="pt", device="cpu") as reader:
        names = tuple(reader.keys())
        tensors = {name: reader.get_tensor(name).clone() for name in names if name != head}
        metadata = reader.metadata()
    if tensors:
        fd, temporary = tempfile.mkstemp(prefix=".canonical-", dir=checkpoint)
        os.close(fd)
        try:
            save_file(tensors, temporary, metadata=metadata)
            with open(temporary, "rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    else:
        path.unlink()
    for index in checkpoint.glob("*.safetensors.index.json"):
        value = json.loads(index.read_text())
        if head in value["weight_map"]:
            del value["weight_map"][head]
            if "total_size" in value.get("metadata", {}):
                value["metadata"]["total_size"] -= removed_bytes
            index.write_text(json.dumps(value, indent=2) + "\n")
    return True


def save_training_receipt(
    config: Any, output: Path, expected_steps: int, probe: tuple[str, Any]
) -> None:
    """Publish completion only after a real final checkpoint and changed weights."""
    from areal.utils.saver import Saver  # type: ignore[import-not-found]

    root = Path(
        Saver.get_model_save_root(
            config.experiment_name, config.trial_name, config.cluster.fileroot
        )
    )
    checkpoints = [
        (int(match.group(1)), path)
        for path in root.iterdir()
        if path.is_dir()
        and (match := re.fullmatch(r"epoch\d+epochstep\d+globalstep(\d+)", path.name))
    ]
    if not checkpoints:
        raise ValueError(f"AReaL completed without a saved checkpoint in {root}")
    step, checkpoint = max(checkpoints)
    if step + 1 != expected_steps:
        raise ValueError("AReaL did not save the requested final training step")
    canonicalized = canonicalize_tied_checkpoint(checkpoint)
    _, final = parameter_probe(checkpoint, probe[0])
    delta = float((final - probe[1]).abs().max())
    if not math.isfinite(delta) or delta <= 0:
        raise ValueError("Training did not change the probed model parameters")
    write_record(
        output / "training.json",
        TrainingReceipt(
            str(checkpoint),
            step + 1,
            {"parameter_change_linf": delta, "canonicalized_tied_weights": float(canonicalized)},
        ),
    )
