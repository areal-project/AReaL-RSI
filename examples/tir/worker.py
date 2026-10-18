"""AReaL-side entrypoint, executed in its own Python environment."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from areal_rsi.api.control import encode_record
from areal_rsi.api.execution import TrainingReceipt
from areal_rsi.api.records import EvaluationSample
from areal_rsi.infra.artifacts import read_record, write_record
from examples.checkpoints import parameter_probe
from examples.tir.benchmark import evaluation_cases, read_examples
from examples.tir.data import read_problems
from examples.tir.pipeline import WorkerRequest


@dataclass(frozen=True, slots=True)
class WorkerAllocation:
    slurm_job_id: str
    node: str


def prepare_config(request: WorkerRequest) -> Any:
    from areal.api.cli_args import load_expr_config
    from omegaconf import OmegaConf
    from tir_workflow import TIRGRPOConfig

    config = request.config
    output = Path(request.output)
    base = Path(config.backend_config)
    if not base.is_absolute():
        base = Path(config.runtime.repository) / base
    # External AReaL/Hydra config boundary; AReaL decodes into TIRGRPOConfig below.
    overrides = OmegaConf.from_dotlist(list(config.backend_overrides))
    effective = OmegaConf.merge(
        OmegaConf.load(base),
        overrides,
        {
            "experiment_name": "rsi-tir",
            "trial_name": request.trial,
            "seed": config.synthesis.seed if request.mode == "train" else config.evaluation.seed,
            "cluster": {
                "fileroot": str(output / "areal"),
                "n_nodes": 1,
                "n_gpus_per_node": 2,
                "name_resolve": {"type": "nfs", "nfs_record_root": str(output / "names")},
            },
            "scheduler": {"type": "local"},
            "total_train_epochs": config.training.epochs,
            "total_train_steps": config.training.max_steps,
            "tokenizer_path": request.model,
            "actor": {
                "backend": "fsdp:d1p1t1",
                "path": request.model,
                "optimizer": {"lr": config.training.learning_rate},
                "mb_spec": {"max_tokens_per_mb": config.harness.max_length * 2},
                "max_new_tokens": config.training.max_new_tokens,
                "reward_norm": {"group_size": config.training.n_samples},
            },
            "rollout": {
                "backend": "sglang:d1p1t1",
                "max_concurrent_rollouts": 16,
                "max_head_offpolicyness": 0,
            },
            "sglang": {
                "model_path": request.model,
                "context_length": config.harness.max_length + 16,
                "mem_fraction_static": 0.55,
            },
            "gconfig": {
                "n_samples": config.training.n_samples,
                "max_new_tokens": config.training.max_new_tokens,
                "temperature": config.training.temperature,
            },
            "tir": encode_record(config.harness),
            "train_dataset": {
                "path": request.dataset,
                "batch_size": config.training.batch_size,
                "num_workers": 0,
            },
            "valid_dataset": {
                "path": request.valid_dataset,
                "batch_size": config.training.batch_size,
                "num_workers": 0,
            },
            "saver": {
                "freq_steps": config.training.max_steps,
                "freq_epochs": None,
                "freq_secs": None,
                "mode": "sync",
            },
            "evaluator": {"freq_epochs": None, "freq_steps": None, "freq_secs": None},
            "recover": {
                "mode": "disabled",
                "freq_epochs": None,
                "freq_steps": None,
                "freq_secs": None,
            },
            "stats_logger": {"wandb": {"mode": "disabled"}},
        },
    )
    if request.mode == "eval":
        # Standalone evaluation has no trainer advancing the policy version.
        effective.rollout.max_head_offpolicyness = 1_000_000_000
        effective.gconfig.n_samples = 1
        effective.gconfig.temperature = config.evaluation.temperature
        effective.gconfig.greedy = config.evaluation.temperature == 0
        effective.gconfig.max_new_tokens = config.evaluation.max_new_tokens
    path = output / "areal-config.yaml"
    OmegaConf.save(effective, path)
    resolved, _ = load_expr_config(["--config", str(path)], TIRGRPOConfig)
    return resolved


def workflow_kwargs(
    config: Any,
    reward: Literal["integer", "math_verify"],
    trace_directory: Path | None = None,
) -> dict[str, Any]:
    return {
        "reward_fn": (
            "examples.tir.reward.math_reward"
            if reward == "math_verify"
            else "examples.tir.reward.integer_reward"
        ),
        "gconfig": config.gconfig,
        "tokenizer": config.tokenizer_path,
        "tir_config": config.tir,
        "enable_thinking": False,
        "trace_directory": str(trace_directory) if trace_directory else None,
    }


def dataset(path: str, request: WorkerRequest) -> Any:
    from datasets import Dataset

    if request.dataset_format == "benchmark":
        examples = read_examples(Path(path))
        items = evaluation_cases(examples) if request.mode == "eval" else examples
    else:
        items = read_problems(Path(path))
    return Dataset.from_list(
        [
            {
                "task_id": item.task_id,
                "messages": [{"role": "user", "content": item.prompt}],
                "answer": item.answer,
            }
            for item in items
        ]
    )


def train(request: WorkerRequest, config: Any) -> None:
    from areal import PPOTrainer
    from areal.utils.saver import Saver

    key, initial = parameter_probe(Path(request.model))
    training_data = dataset(request.dataset, request)
    possible_steps = (
        len(training_data) // config.train_dataset.batch_size * config.total_train_epochs
    )
    if possible_steps < request.config.training.max_steps:
        raise ValueError("Training epochs and dataset cannot supply the requested update count")
    with PPOTrainer(config, training_data, dataset(request.valid_dataset, request)) as trainer:
        trainer.train(
            workflow="examples.tir.workflow.RecordedTIRWorkflow",
            workflow_kwargs=workflow_kwargs(config, request.config.reward),
        )
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
    if step + 1 != request.config.training.max_steps:
        raise ValueError("AReaL did not save the requested final training step")
    _, final = parameter_probe(checkpoint, key)
    delta = float((final - initial).abs().max())
    if delta <= 0:
        raise ValueError("Training did not change the probed model parameters")
    write_record(
        Path(request.output) / "training.json",
        TrainingReceipt(
            str(checkpoint),
            step + 1,
            {"parameter_change_linf": delta},
        ),
    )


def evaluate(request: WorkerRequest, config: Any) -> None:
    from areal.api.alloc_mode import ModelAllocation
    from areal.api.cli_args import SGLangConfig
    from areal.engine import RemoteSGLangEngine
    from areal.infra import LocalScheduler
    from areal.utils import seeding

    seeding.set_random_seed(request.config.evaluation.seed, key="eval")
    allocation = ModelAllocation.from_str(config.rollout.backend, name="rollout")
    scheduler = LocalScheduler(exp_config=config)
    rollout = RemoteSGLangEngine.as_controller(config.rollout, scheduler)
    trace_directory = Path(request.output) / "worker-traces"
    trace_directory.mkdir()
    try:
        server_args = SGLangConfig.build_args(
            config.sglang, tp_size=allocation.parallel.tp_size, base_gpu_id=0
        )
        rollout.initialize(role="eval-rollout", server_args=server_args)
        items = dataset(request.dataset, request)
        for item in items:
            rollout.submit(
                item,
                workflow="examples.tir.workflow.RecordedTIRWorkflow",
                workflow_kwargs=workflow_kwargs(config, request.config.reward, trace_directory),
                group_size=1,
                is_eval=True,
                reward_normalization=False,
                drop_incomplete_group=False,
            )
        results = rollout.wait(len(items), timeout=None)
        if len(results) != len(items) or any(result is None for result in results):
            raise ValueError("Evaluation failed to return all trajectories")
        with (Path(request.output) / "samples.jsonl").open("x") as output:
            for item in items:
                sample = read_record(
                    trace_directory / f"{item['task_id']}-0.json", EvaluationSample
                )
                output.write(json.dumps(encode_record(sample), ensure_ascii=False) + "\n")
    finally:
        rollout.destroy()
        scheduler.delete_workers()


def main() -> None:
    def interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt(f"Worker interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    request = read_record(parser.parse_args().request, WorkerRequest)
    write_record(
        Path(request.output) / "allocation.json",
        WorkerAllocation(os.environ.get("SLURM_JOB_ID", ""), os.environ.get("SLURMD_NODENAME", "")),
    )
    config = prepare_config(request)
    if request.mode == "train":
        train(request, config)
    else:
        evaluate(request, config)


if __name__ == "__main__":
    main()
