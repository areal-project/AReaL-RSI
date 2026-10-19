"""AReaL GPU-side train/eval entrypoint for the frozen BrowseComp ReAct harness."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
from pathlib import Path
from typing import Any

from areal_rsi.api.control import decode_record
from areal_rsi.api.execution import TrainingReceipt
from areal_rsi.infra.artifacts import read_record, write_record
from examples.search.experiment.config import WorkerRequest
from examples.search.task.dataset import SearchQuestion
from examples.search.task.rollouts import write_evaluation_samples


def prepare_config(request: WorkerRequest) -> Any:
    from areal.api.cli_args import GRPOConfig, load_expr_config
    from omegaconf import OmegaConf

    settings = request.config
    os.environ.setdefault("SEARCH_PROXY_ADMIN_KEY", secrets.token_urlsafe(32))
    output = Path(request.output)
    template = Path(settings.runtime.repository) / "examples/math/gsm8k_grpo.yaml"
    training = request.mode == "train"
    effective = OmegaConf.merge(
        OmegaConf.load(template),
        {
            "experiment_name": "rsi-search",
            "trial_name": request.trial,
            "seed": settings.training.seed if training else settings.evaluation_seed,
            "total_train_epochs": settings.training.epochs,
            "total_train_steps": settings.training.max_steps,
            "tokenizer_path": request.model,
            "cluster": {
                "n_nodes": 1,
                "n_gpus_per_node": settings.actor_gpus + settings.rollout_gpus,
                "fileroot": str(output / "areal"),
                "name_resolve": {"type": "nfs", "nfs_record_root": str(output / "names")},
            },
            "scheduler": {"type": "local"},
            "rollout": {
                "backend": f"sglang:d{settings.rollout_gpus if training else 1}p1t1",
                "max_concurrent_rollouts": settings.max_concurrent_rollouts,
                "max_head_offpolicyness": 0 if training else 1000000000,
                "dump_to_file": True,
                "agent": {
                    "mode": "inline",
                    "admin_api_key": "${oc.env:SEARCH_PROXY_ADMIN_KEY}",
                    "export_style": "individual",
                    "chat_template_type": "hf",
                    "turn_discount": 1.0,
                    "drop_retry_orphans": True,
                    "chat_template_kwargs": {"enable_thinking": settings.harness.thinking},
                    "engine_max_tokens": settings.harness.context_tokens,
                },
            },
            "gconfig": {
                "n_samples": settings.training.n_samples if training else 1,
                "max_new_tokens": settings.harness.max_completion_tokens,
                "max_tokens": settings.harness.context_tokens,
                "temperature": settings.harness.temperature,
                "top_p": settings.harness.top_p,
                "top_k": settings.harness.top_k,
                "greedy": False,
                "reward_normalization": False,
            },
            "actor": {
                "backend": f"fsdp:d{settings.actor_gpus}p1t1",
                "path": request.model,
                "optimizer": {"lr": settings.training.learning_rate, "gradient_clipping": 1.0},
                # Training packing is recipe-specific; inference keeps the fixed harness.
                "mb_spec": {
                    "n_mbs": settings.training.microbatch.n_mbs,
                    "granularity": 1,
                    "max_tokens_per_mb": settings.training.microbatch.max_tokens_per_mb,
                    "n_mbs_divisor": 1,
                    "packing_algorithm": "ffd",
                },
                "pad_to_maximum": False,
                "ppo_n_minibatches": 1,
                "eps_clip": 0.2,
                "kl_ctl": 0.0,
                "discount": 1.0,
                "gae_lambda": 1.0,
                "reward_scaling": 1.0,
                "reward_bias": 0.0,
                "adv_norm": None,
                "reward_norm": {
                    "mean_level": "group",
                    "std_level": "group",
                    "std_unbiased": False,
                    "group_size": settings.training.n_samples,
                },
                "max_new_tokens": settings.harness.max_completion_tokens,
                "min_usable_group_size": 2,
            },
            "sglang": {
                "model_path": request.model,
                "context_length": settings.harness.context_tokens,
                "skip_tokenizer_init": False,
                "mem_fraction_static": 0.8,
                "attention_backend": "flashinfer",
                "max_running_requests": 16,
            },
            "train_dataset": {
                "path": request.dataset,
                "batch_size": settings.training.batch_size,
                "num_workers": 0,
            },
            "valid_dataset": {"path": request.dataset, "num_workers": 0},
            "saver": {
                "freq_steps": settings.training.max_steps,
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
    path = output / "areal-config.yaml"
    OmegaConf.save(effective, path)
    path.chmod(0o600)
    config, _ = load_expr_config(["--config", str(path)], GRPOConfig)
    return config


def workflow_kwargs(request: WorkerRequest) -> dict[str, Any]:
    return {
        "benchmark_repository": request.config.benchmark_repository,
        "mcp_url": request.mcp_url,
        "trace_directory": str(Path(request.output) / "episodes"),
        "max_completion_tokens": request.config.harness.max_completion_tokens,
        "evaluation_seed": request.config.evaluation_seed if request.mode == "eval" else None,
    }


def dataset(path: str) -> Any:
    from datasets import Dataset

    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
    if not rows or len({row["question_id"] for row in rows}) != len(rows):
        raise ValueError("Search dataset requires unique, nonempty question IDs")
    return Dataset.from_list(rows)


def train(request: WorkerRequest, config: Any) -> None:
    from areal import PPOTrainer
    from areal.utils.saver import Saver

    from examples.checkpoints import parameter_probe

    training_data = dataset(request.dataset)
    request.config.training.validate_dataset_size(len(training_data))
    key, initial = parameter_probe(Path(request.model))
    with PPOTrainer(config, training_data, valid_dataset=None) as trainer:
        trainer.train(
            workflow="examples.search.task.agent.BrowseCompTrainAgent",
            workflow_kwargs=workflow_kwargs(request),
            eval_workflow=None,
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
        raise ValueError("AReaL did not save a complete checkpoint")
    step, checkpoint = max(checkpoints)
    if step + 1 != request.config.training.max_steps:
        raise ValueError(
            f"Latest checkpoint has {step + 1} updates; "
            f"expected {request.config.training.max_steps}"
        )
    _, final = parameter_probe(checkpoint, key)
    change = float((final - initial).abs().max())
    if change <= 0:
        raise ValueError("The saved model has no measured parameter change")
    write_record(
        Path(request.output) / "training.json",
        TrainingReceipt(str(checkpoint), step + 1, {"parameter_change_linf": change}),
    )


def evaluate(request: WorkerRequest, config: Any) -> None:
    from areal.api.alloc_mode import ModelAllocation
    from areal.api.cli_args import SGLangConfig
    from areal.engine import RemoteSGLangEngine
    from areal.infra import LocalScheduler
    from areal.utils import seeding
    from areal.utils.stats_logger import StatsLogger

    seeding.set_random_seed(request.config.evaluation_seed, key="search-eval")
    allocation = ModelAllocation.from_str(config.rollout.backend, name="rollout")
    scheduler = LocalScheduler(exp_config=config)
    rollout = RemoteSGLangEngine.as_controller(config.rollout, scheduler)
    items = list(dataset(request.dataset))
    try:
        args = SGLangConfig.build_args(
            config.sglang, tp_size=allocation.parallel.tp_size, base_gpu_id=0
        )
        rollout.initialize(role="search-eval", server_args=args)
        rollout.start_proxy()
        for item in items:
            rollout.submit(
                item,
                workflow="examples.search.task.agent.BrowseCompTrainAgent",
                workflow_kwargs=workflow_kwargs(request),
                group_size=1,
                is_eval=True,
                reward_normalization=False,
                drop_incomplete_group=False,
            )
        results = rollout.wait(len(items), timeout=None)
        if len(results) != len(items) or any(result is None for result in results):
            raise ValueError("Independent evaluation did not return every trajectory")
        log_directory = Path(
            StatsLogger.get_log_path(
                experiment_name=config.experiment_name,
                trial_name=config.trial_name,
                fileroot=config.cluster.fileroot,
            )
        )
        write_evaluation_samples(
            Path(request.output),
            tuple(decode_record(SearchQuestion, item) for item in items),
            log_directory / "eval-rollout",
        )
    finally:
        rollout.destroy()
        scheduler.delete_workers()


def main() -> None:
    def interrupt(signum: int, frame: object) -> None:
        raise KeyboardInterrupt(f"Search worker interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    request = read_record(parser.parse_args().request, WorkerRequest)
    os.environ["QWEN_AGENT_MAX_LLM_CALL_PER_RUN"] = str(request.config.harness.max_model_calls)
    os.environ["QWEN_AGENT_DEFAULT_MAX_INPUT_TOKENS"] = str(request.config.harness.max_input_tokens)
    config = prepare_config(request)
    (train if request.mode == "train" else evaluate)(request, config)


if __name__ == "__main__":
    main()
