"""Checkpoint, rollout and source evidence shared by training recipes."""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
from pathlib import Path

from areal_rsi.api.execution import TrustedSourceFile
from areal_rsi.api.research import RsiRolloutEvidence


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
    step_index, selected = max(candidates, key=lambda item: item[0])
    # AReaL names the checkpoint with a zero-based update index. The receipt
    # records the number of completed updates, so the first save is step 1.
    checkpoint_digest(selected)
    return selected, step_index + 1
