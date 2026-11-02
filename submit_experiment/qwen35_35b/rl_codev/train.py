"""CodeV GRPO using AReaL's inline Agent/proxy integration."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from areal.api.cli_args import GRPOConfig, load_expr_config
from codev import prepare_public


@dataclass
class Ref:
    key: str = ""
    version: str = ""


@dataclass
class CodeVConfig:
    benchmark_ref: Ref = field(default_factory=Ref)
    benchmark_content_hash: str = ""
    harness_ref: Ref = field(default_factory=Ref)
    reward_ref: Ref = field(default_factory=Ref)
    base_url: str = ""
    evidence_dir: str = ""
    judge_concurrency: int = 32
    reward_timeout: float = 900.0
    api_timeout: float = 60.0
    generation_timeout: float = 12000.0
    poll_interval: float = 5.0
    read_retries: int = 3
    max_response_bytes: int = 1048576
    enable_thinking: bool = True


@dataclass
class CodeVPPOConfig(GRPOConfig):
    codev: CodeVConfig = field(default_factory=CodeVConfig)


def dataset_rows(config, root):
    from areal.utils.hf_utils import apply_chat_template, load_hf_tokenizer
    from datasets import Dataset

    tokenizer = load_hf_tokenizer(config.tokenizer_path)
    rows = [json.loads(line) for line in (root / "public-dataset.jsonl").read_text().splitlines()]
    cap = config.gconfig.max_tokens - config.gconfig.max_new_tokens
    for row in rows:
        tokens = apply_chat_template(
            tokenizer,
            row["messages"],
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=config.codev.enable_thinking,
        )
        if len(tokens) > cap or len(tokens) > config.train_dataset.max_length:
            raise ValueError(
                f"Prompt {row['task_id']} exceeds context budget; no silent truncation"
            )
    return Dataset.from_list(rows)


def prepare_dataset(config, root):
    summary = prepare_public(asdict(config.codev), root)
    summary["tokenized_count"] = len(dataset_rows(config, root))
    return summary


def main():
    import areal

    config, _ = load_expr_config(sys.argv[1:], CodeVPPOConfig)
    rows = dataset_rows(config, Path(os.environ["EXPERIMENT_RUN_DIR"]))
    kwargs = {
        "codev": asdict(config.codev),
        "gen_args": {
            "temperature": config.gconfig.temperature,
            "top_p": config.gconfig.top_p,
            "top_k": config.gconfig.top_k,
            "max_completion_tokens": config.gconfig.max_new_tokens,
            "frequency_penalty": config.gconfig.frequency_penalty,
        },
    }
    with areal.PPOTrainer(config, train_dataset=rows, valid_dataset=None) as trainer:
        trainer.train(workflow="codev_agent.CodeVAgent", workflow_kwargs=kwargs, eval_workflow=None)


if __name__ == "__main__":
    main()
