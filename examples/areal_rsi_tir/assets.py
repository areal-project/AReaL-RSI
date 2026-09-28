"""Trusted AReaL-side handoff for immutable RSI task and Harness snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from areal_rsi.api.records import RsiSplit, TaskSetVersion
from areal_rsi.api.research import RsiRolloutEvidence
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


def checkpoint_digest(root: Path) -> str:
    """Hash all local model/tokenizer files; an alias is not a frozen checkpoint."""

    if root.is_symlink() or not root.is_dir():
        raise ValueError("AReaL actor.path must be a local checkpoint directory")
    files = sorted(root.rglob("*"))
    if not files:
        raise ValueError("AReaL checkpoint directory is empty")
    digest = hashlib.sha256()
    count = 0
    for path in files:
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("AReaL checkpoint contains a symlink or special file")
        count += 1
        relative = path.relative_to(root).as_posix().encode()
        file_digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                file_digest.update(block)
        digest.update(relative + b"\0" + file_digest.digest())
    if not count:
        raise ValueError("AReaL checkpoint directory contains no files")
    return "sha256:" + digest.hexdigest()


def validate_hf_model_files(root: Path) -> str:
    """Require local model configuration and nonempty weight shards before launch."""

    checkpoint_digest(root)
    config_path = root / "config.json"
    if config_path.is_symlink() or not config_path.is_file():
        raise ValueError("Frozen model checkpoint lacks config.json")
    try:
        config = json.loads(config_path.read_text())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Frozen model config.json is malformed") from exc
    model_type = config.get("model_type") if isinstance(config, dict) else None
    if not isinstance(model_type, str) or not model_type.strip():
        raise ValueError("Frozen model config.json has no model_type")
    weights = [
        path
        for path in root.iterdir()
        if path.is_file()
        and (
            path.name == "model.safetensors"
            or (path.name.startswith("model-") and path.suffix == ".safetensors")
            or path.name == "pytorch_model.bin"
            or (path.name.startswith("pytorch_model-") and path.suffix == ".bin")
        )
    ]
    if not weights or any(path.stat().st_size < 1 for path in weights):
        raise ValueError("Frozen model checkpoint has no nonempty weight shard")
    return model_type


def rollout_dump_evidence(
    root: Path, *, allowed_task_ids: frozenset[str] | None = None
) -> RsiRolloutEvidence:
    """Bind AReaL's task-labelled trajectory dumps without loading them into RAM."""

    if root.is_symlink() or not root.is_dir():
        raise ValueError("AReaL rollout dump directory is missing or symbolic")
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("AReaL rollout dump contains a symbolic link")
        if path.suffix == ".jsonl":
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("AReaL rollout dump contains a special file")
            files.append(path)
    if not files:
        raise ValueError("AReaL training produced no rollout dump files")
    digest = hashlib.sha256()
    task_ids: set[str] = set()
    records = 0
    for path in sorted(files):
        file_digest = hashlib.sha256()
        with path.open("rb") as stream:
            for line_number, line in enumerate(stream, 1):
                file_digest.update(line)
                try:
                    item = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"Malformed rollout record: {path}:{line_number}") from exc
                if not isinstance(item, dict):
                    raise ValueError(f"Rollout record is not an object: {path}:{line_number}")
                task_id, reward = item.get("source_task_id"), item.get("reward")
                if (
                    not isinstance(task_id, str)
                    or not task_id.strip()
                    or not isinstance(reward, (int, float))
                    or isinstance(reward, bool)
                    or not math.isfinite(float(reward))
                    or (allowed_task_ids is not None and task_id not in allowed_task_ids)
                ):
                    raise ValueError(f"Rollout identity or reward is invalid: {path}:{line_number}")
                task_ids.add(task_id)
                records += 1
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + file_digest.digest())
    if not records:
        raise ValueError("AReaL rollout dump files contain no records")
    return RsiRolloutEvidence(
        str(root.resolve()), "sha256:" + digest.hexdigest(), records, len(task_ids)
    )


def rollout_dump_roots(fileroot: Path, experiment_name: str, trial_name: str) -> tuple[Path, ...]:
    """Find worker-written dumps even when its OS user differs from the controller."""

    logs = fileroot / "logs"
    if not logs.exists():
        return ()
    if logs.is_symlink() or not logs.is_dir():
        raise ValueError("AReaL log root is not a regular directory")
    roots: list[Path] = []
    for user_directory in logs.iterdir():
        if user_directory.is_symlink():
            raise ValueError("AReaL log root contains a symbolic user directory")
        if not user_directory.is_dir():
            continue
        root = user_directory / experiment_name / trial_name / "rollout"
        if root.is_symlink():
            raise ValueError("AReaL rollout dump root is symbolic")
        if root.is_dir() and any(root.rglob("*.jsonl")):
            roots.append(root)
    return tuple(sorted(roots))


def source_files_digest(paths: tuple[TrustedSourceFile, ...]) -> str:
    if not paths or len({source.label for source in paths}) != len(paths):
        raise ValueError("Trusted source files need distinct stable labels")
    digest = hashlib.sha256()
    for source in sorted(paths, key=lambda item: item.label):
        if (
            not source.path.exists()
            or source.path.is_symlink()
            or not stat.S_ISREG(source.path.lstat().st_mode)
        ):
            raise ValueError(f"Trusted source file is missing or symbolic: {source.path}")
        digest.update(
            source.label.encode() + b"\0" + hashlib.sha256(source.path.read_bytes()).digest()
        )
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True, slots=True)
class TrustedSourceFile:
    label: str
    path: Path


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
        config.total_train_epochs != 1
        or config.saver.freq_epochs != 1
        or config.saver.freq_steps is not None
        or config.saver.freq_secs is not None
    ):
        raise ValueError("First RSI GRPO generation requires one final Saver checkpoint")


def latest_hf_checkpoint(save_root: Path, *, require_unique: bool = False) -> tuple[Path, int]:
    """Find the newest completed AReaL Saver export, not a recover checkpoint."""

    pattern = re.compile(r"epoch[0-9]+epochstep[0-9]+globalstep([0-9]+)\Z")
    candidates: list[tuple[int, Path]] = []
    for path in save_root.glob("epoch*globalstep*"):
        match = pattern.fullmatch(path.name)
        if (
            match
            and path.is_dir()
            and not path.is_symlink()
            and (path / "config.json").is_file()
            and any(path.glob("*.safetensors"))
        ):
            candidates.append((int(match[1]), path))
    if not candidates:
        raise FileNotFoundError("AReaL Saver produced no complete HuggingFace checkpoint")
    if require_unique and len(candidates) != 1:
        raise ValueError("First RSI training generation expected exactly one complete checkpoint")
    step, selected = max(candidates, key=lambda item: item[0])
    if step < 1:
        raise ValueError("AReaL checkpoint has no completed training step")
    checkpoint_digest(selected)
    return selected, step


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
        config.scheduler.type != "slurm"
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
