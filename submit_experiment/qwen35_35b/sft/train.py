"""Use AReaL's trajectory preprocessing and SFTTrainer for text messages JSONL."""

from __future__ import annotations

import getpass
import importlib.metadata
import json
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path


def inspect_jsonl(path: str) -> dict[str, int]:
    """Reject malformed or multimodal records before template preprocessing."""
    records = multi_turn = 0
    with open(path, encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            messages = row.get("messages") if isinstance(row, dict) else None
            if not isinstance(messages, list) or not messages:
                raise ValueError(f"{path}:{line_no}: expected a nonempty messages list")
            assistants = users = 0
            for message in messages:
                if not isinstance(message, dict) or message.get("role") not in {
                    "system",
                    "user",
                    "assistant",
                    "tool",
                }:
                    raise ValueError(f"{path}:{line_no}: invalid message role")
                content = message.get("content")
                if not isinstance(content, str) and not (
                    content is None and message.get("tool_calls")
                ):
                    raise ValueError(
                        f"{path}:{line_no}: this profile requires text content; "
                        "image blocks are unsupported"
                    )
                if any(key in message for key in ("images", "image_url", "video", "audio")):
                    raise ValueError(f"{path}:{line_no}: multimodal payload in text profile")
                assistants += message["role"] == "assistant"
                users += message["role"] == "user"
            if not assistants or not users:
                raise ValueError(f"{path}:{line_no}: require both user and assistant turns")
            records += 1
            multi_turn += assistants > 1 and users > 1
    if not records:
        raise ValueError(f"{path}: empty dataset")
    return {"records": records, "multi_turn_records": multi_turn}


def main(args: list[str]) -> None:
    check_only = "--check-only" in args
    args = [arg for arg in args if arg != "--check-only"]
    import areal
    import pyarrow.compute as pc
    from areal import SFTTrainer
    from areal.api.cli_args import load_expr_config
    from areal.dataset.swe_sft import get_swe_sft_dataset
    from areal.utils.hf_utils import load_hf_tokenizer
    from examples.swe.config import SweSFTConfig

    config, _ = load_expr_config(args, SweSFTConfig)
    if os.environ.get("AREAL_SPMD_MODE") != "1":
        raise ValueError("This entry requires AREAL_SPMD_MODE=1")
    if config.scheduler.type is not None:
        raise ValueError("Direct Slurm SPMD uses scheduler.type=null")
    source = Path(areal.__file__).resolve().parent.parent
    if source != Path(os.environ["AREAL_DIR"]).resolve():
        raise ValueError(f"Loaded unexpected AReaL source: {source}")
    tokenizer = load_hf_tokenizer(config.tokenizer_path)
    user = getpass.getuser()
    log_dir = (
        Path(config.cluster.fileroot) / "logs" / user / config.experiment_name / config.trial_name
    )
    cache_root = (
        Path(config.cluster.fileroot)
        / "checkpoints"
        / user
        / config.experiment_name
        / config.trial_name
        / "processed_dataset"
    )
    options = asdict(config.swe)
    options.pop("cleanup_processed_dataset")
    dump_samples = options.pop("dump_samples")
    report = {}
    datasets = {}
    rank = int(os.getenv("RANK", "0"))
    for name, dataset_config in (("train", config.train_dataset), ("valid", config.valid_dataset)):
        if dataset_config is None:
            continue
        report[name] = inspect_jsonl(dataset_config.path) if rank == 0 else {}
        dataset = get_swe_sft_dataset(
            path=dataset_config.path,
            tokenizer=tokenizer,
            max_length=dataset_config.max_length,
            cache_dir=str(cache_root / name),
            dump_dir=str(log_dir / f"dumped_samples_{name}"),
            dump_samples=dump_samples,
            **options,
        )
        datasets[name] = dataset
        if not len(dataset):
            raise ValueError(f"{name} dataset is empty after preprocessing")
        bounds = pc.min_max(pc.list_value_length(dataset.data.column("input_ids"))).as_py()
        report[name].update(
            processed_records=len(dataset), min_tokens=bounds["min"], max_tokens=bounds["max"]
        )
    if len(datasets["train"]) < config.train_dataset.batch_size:
        raise ValueError("Processed training data contains fewer than one global batch")
    total_steps = config.total_train_epochs * (
        len(datasets["train"]) // config.train_dataset.batch_size
    )
    from areal.utils.lr_scheduler import get_num_warmup_steps

    if get_num_warmup_steps(config.actor.optimizer, total_steps) >= total_steps:
        raise ValueError(
            "Optimizer warmup must be shorter than the dataset/epoch training schedule"
        )
    if rank == 0:
        log_dir.mkdir(parents=True, exist_ok=True)
        versions = {}
        for name in ("torch", "transformers", "megatron-core", "megatron-bridge"):
            versions[name] = importlib.metadata.version(name)
        (log_dir / "sft-preflight.json").write_text(
            json.dumps({"runtime": versions, "datasets": report}, indent=2) + "\n"
        )
        print("SFT data/runtime checks: " + json.dumps(report), flush=True)
    if check_only:
        print(
            "SFT CPU runtime and text JSONL preprocessing PASSED; no GPU training started.",
            flush=True,
        )
        return
    with SFTTrainer(
        config, train_dataset=datasets["train"], valid_dataset=datasets.get("valid")
    ) as trainer:
        trainer.train()
    if config.swe.cleanup_processed_dataset and rank == 0 and cache_root.is_dir():
        shutil.rmtree(cache_root)


if __name__ == "__main__":
    main(sys.argv[1:])
