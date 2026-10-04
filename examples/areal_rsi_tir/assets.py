"""Trusted AReaL-side handoff for immutable RSI task and Harness snapshots."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from areal_rsi.api.execution import TrustedSourceFile
from areal_rsi.api.records import RsiSplit, TaskSetVersion
from areal_rsi.infra.storage.artifacts import checkpoint_digest, source_files_digest
from areal_rsi_tir.harness import TirHarnessSpec, load_tir_harness
from areal_rsi_tir.tasks import load_tir_task_set, public_tir_tasks

RSI_REWARD_TIMEOUT_SECONDS = 60
RSI_REWARD_MAX_RETRIES = 0


@dataclass(frozen=True, slots=True)
class FrozenTirInputs:
    task_set: TaskSetVersion
    harness: TirHarnessSpec
    prompt_template: str
    train_rows: tuple[dict[str, Any], ...]
    dev_rows: tuple[dict[str, Any], ...]


def _gold_answer(answer: str) -> str:
    answer = answer.strip()
    return answer if answer.startswith("\\boxed{") else f"\\boxed{{{answer}}}"


def tir_verifier_files(runner_root: Path) -> tuple[TrustedSourceFile, ...]:
    import areal_rsi

    rsi_package = Path(areal_rsi.__file__).resolve().parent
    example_package = Path(__file__).resolve().parent
    return (
        TrustedSourceFile(
            "areal/api/reward_api.py", runner_root.parents[1] / "areal/api/reward_api.py"
        ),
        *(
            TrustedSourceFile(label + "/" + path.relative_to(package).as_posix(), path)
            for label, package in (("rsi", rsi_package), ("example", example_package))
            for path in sorted(package.rglob("*.py"))
            if "__pycache__" not in path.parts
        ),
    )


def validate_areal_import_root(runner_root: Path, imported_package: Path) -> None:
    """The hashed checkout must be the package loaded by the recipe process."""

    expected = (runner_root.parents[1] / "areal/__init__.py").resolve()
    if imported_package.resolve() != expected:
        raise ValueError("Loaded AReaL package differs from the frozen source checkout")


def attest_worker_inputs(
    task_set_root: Path,
    harness_root: Path,
    runner_root: Path,
    *,
    task_set_id: str,
    harness_id: str,
    runner_digest: str,
    verifier_digest: str,
    prompt_template: str,
) -> None:
    """Fail a remote rollout if its imported code or shared snapshots differ."""

    task_set = load_tir_task_set(task_set_root)
    harness, prompt = load_tir_harness(harness_root, runner_root)
    if (
        task_set.task_set_id != task_set_id
        or harness.harness_id != harness_id
        or harness.runner_digest != runner_digest
        or prompt != prompt_template
        or source_files_digest(tir_verifier_files(runner_root)) != verifier_digest
    ):
        raise ValueError("Slurm worker TIR source or frozen input differs from controller")


def validate_tir_grpo_signal_config(config: Any) -> None:
    """Reject a singleton or differently normalized group before GPU launch."""

    samples = config.gconfig.n_samples
    reward_norm = config.actor.reward_norm
    if (
        type(samples) is not int
        or samples < 2
        or config.gconfig.greedy
        or not math.isfinite(config.gconfig.temperature)
        or config.gconfig.temperature <= 0
        or not config.rollout.dump_to_file
        or config.gconfig.reward_normalization
        or reward_norm is None
        or reward_norm.mean_level != "group"
        or reward_norm.std_level != "group"
        or reward_norm.group_size != samples
    ):
        raise ValueError(
            "RSI GRPO requires stochastic grouped samples, rollout dumps, and matched rewards"
        )


def validate_tir_train_identity(config: Any, run_id: str) -> None:
    if (
        config.trial_name != run_id
        or config.rollout.trial_name != run_id
        or config.saver.trial_name != run_id
        or config.rollout.experiment_name != config.experiment_name
        or config.saver.experiment_name != config.experiment_name
        or config.rollout.fileroot != config.cluster.fileroot
        or config.saver.fileroot != config.cluster.fileroot
        or not Path(config.cluster.fileroot).is_absolute()
    ):
        raise ValueError("AReaL rollout and Saver must use the frozen RSI trial identity")
    if (
        type(config.total_train_epochs) is not int
        or config.total_train_epochs < 1
        or config.saver.freq_epochs != config.total_train_epochs
        or config.saver.freq_steps is not None
        or config.saver.freq_secs is not None
    ):
        raise ValueError("First RSI GRPO generation requires one final Saver checkpoint")


def validate_frozen_model(config: Any, expected_digest: str, *, rl: bool) -> None:
    base = Path(config.actor.path).resolve()
    if checkpoint_digest(base) != expected_digest:
        raise ValueError("AReaL actor checkpoint differs from the frozen model")
    paths = [config.tokenizer_path]
    if rl:
        paths.extend((config.ref.path, config.sglang.model_path))
    if any(Path(path).resolve() != base for path in paths):
        raise ValueError("AReaL tokenizer, reference, or rollout model differs from actor.path")


def _rows(root: Path, split: RsiSplit, answers: dict[str, str]) -> tuple[dict[str, Any], ...]:
    if split not in {RsiSplit.MODEL_TRAIN, RsiSplit.MODEL_DEV}:
        raise ValueError("AReaL training may only read model_train or model_dev")
    rows: list[dict[str, Any]] = []
    for task in public_tir_tasks(root, split):
        if not isinstance(task.input, dict) or not isinstance(task.input.get("messages"), list):
            raise ValueError("Frozen TIR task has invalid public messages")
        rows.append(
            {
                "task_id": task.task_id,
                "messages": task.input["messages"],
                "answer": _gold_answer(answers[task.task_id]),
            }
        )
    return tuple(rows)


def evaluation_rows(root: Path, split: RsiSplit) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Trusted evaluator reads private answers; candidate workspaces receive only public tasks."""

    if split is RsiSplit.MODEL_TRAIN:
        raise ValueError("Model training cases are not an evaluation split")
    load_tir_task_set(root)
    answers: dict[str, str] = json.loads((root / "private" / "answers.json").read_text())
    tasks = public_tir_tasks(root, split)
    rows: list[tuple[str, dict[str, Any]]] = []
    for task in tasks:
        if not isinstance(task.input, dict) or not isinstance(task.input.get("messages"), list):
            raise ValueError("Frozen TIR task has invalid public messages")
        rows.append(
            (
                task.task_id,
                {
                    "task_id": task.task_id,
                    "messages": task.input["messages"],
                    "answer": _gold_answer(answers[task.task_id]),
                },
            )
        )
    return tuple(rows)


def load_frozen_tir_inputs(
    task_set_root: Path,
    harness_root: Path,
    runner_root: Path,
) -> FrozenTirInputs:
    task_set = load_tir_task_set(task_set_root)
    harness, prompt = load_tir_harness(harness_root, runner_root)
    answers = json.loads((task_set_root / "private" / "answers.json").read_text())
    return FrozenTirInputs(
        task_set,
        harness,
        prompt,
        _rows(task_set_root, RsiSplit.MODEL_TRAIN, answers),
        _rows(task_set_root, RsiSplit.MODEL_DEV, answers),
    )


def validate_tir_recipe_config(config: Any, frozen: FrozenTirInputs, root: Path) -> None:
    tir = config.tir
    harness = frozen.harness
    if (
        config.scheduler.type not in ("slurm", "local")
        or not Path(config.cluster.fileroot).is_absolute()
        or config.cluster.name_resolve.type != "nfs"
        or not Path(config.cluster.name_resolve.nfs_record_root).is_absolute()
        or tir.is_chat_model
        or tir.max_turns != harness.max_turns
        or tir.max_length != harness.max_length
        or tir.tool_timeout != harness.tool_timeout
        or tir.enable_tools != ";".join(harness.enabled_tools)
    ):
        raise ValueError("AReaL TIR execution settings differ from the frozen Harness")
    for value, split in (
        (config.train_dataset.path, RsiSplit.MODEL_TRAIN),
        (config.valid_dataset.path, RsiSplit.MODEL_DEV),
    ):
        expected = root / "public" / f"{split.value}.jsonl"
        if Path(value).resolve() != expected.resolve():
            raise ValueError(f"AReaL {split.value} path differs from the frozen task set")


def main() -> None:
    parser = argparse.ArgumentParser(description="Hash a local AReaL model and tokenizer snapshot")
    parser.add_argument("--checkpoint", type=Path, required=True)
    args = parser.parse_args()
    print(checkpoint_digest(args.checkpoint))


if __name__ == "__main__":
    main()
