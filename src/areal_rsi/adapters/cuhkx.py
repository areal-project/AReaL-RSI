"""CUHK-X data preparation and trusted scoring, using only the standard library.

Candidate training/inference live in examples/cuhkx/seed. This module never imports
candidate code or deserializes weights; it scores a complete prediction CSV.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import secrets
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

UPSTREAM_REVISION = "df03910a6960db5af9179e370e130bf61d9616d0"
UPSTREAM_URL = f"https://github.com/openaiotlab/CUHK-X/tree/{UPSTREAM_REVISION}/SM"
IMAGE_MODALITIES = frozenset({"Depth_Color", "IR", "Thermal"})
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})
MAX_MODEL_BYTES = 100_000_000
SCHEMA = "cuhkx-clips-v1"


@dataclass(frozen=True)
class Clip:
    clip_id: str
    subject: int
    label: int
    modality: str
    frames: tuple[Path, ...]


@dataclass(frozen=True)
class ClipScore:
    accuracy: float
    correct: int
    total: int
    checkpoint_bytes: int
    checkpoint_sha256: str
    predictions_sha256: str
    protocol_id: str
    dataset_kind: str


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _below(root: Path, path: Path) -> Path:
    root = root.resolve()
    if path.is_absolute():
        raise ValueError("Frame paths must be relative to the supplied data root")
    if ".." in path.parts:
        raise ValueError("Frame paths must not escape the data root")
    target = root / path
    if any((root.joinpath(*path.parts[:i])).is_symlink() for i in range(1, len(path.parts) + 1)):
        raise ValueError("Dataset inputs must not contain symlinks")
    resolved = target.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("Frame must be a regular file inside the data root")
    if resolved.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported frame format: {resolved.suffix}")
    return resolved


def _natural(path: Path) -> tuple[tuple[int, str | int], ...]:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.lower())
        for part in re.split(r"(\d+)", path.name)
    )


def read_clip_csv(manifest: Path, data_root: Path) -> list[Clip]:
    """Read explicit clip_id,subject,label,modality,frames (JSON array) rows.

    Use this path for dataset releases whose layout differs from the pinned
    upstream image loader. We do not infer labels from an unfamiliar directory.
    """
    clips = []
    with manifest.open(newline="") as stream:
        rows = csv.DictReader(stream)
        if set(rows.fieldnames or ()) != {"clip_id", "subject", "label", "modality", "frames"}:
            raise ValueError("Expected clip_id,subject,label,modality,frames CSV columns")
        for row in rows:
            paths = json.loads(row["frames"])
            if (
                not isinstance(paths, list)
                or not paths
                or any(not isinstance(p, str) for p in paths)
            ):
                raise ValueError("frames must be a nonempty JSON array of relative file paths")
            clips.append(
                Clip(
                    row["clip_id"],
                    int(row["subject"]),
                    int(row["label"]),
                    row["modality"],
                    tuple(_below(data_root, Path(p)) for p in paths),
                )
            )
    _validate_clips(clips)
    return clips


def scan_clips(data_root: Path, modality: str = "Depth_Color") -> list[Clip]:
    """Read the pinned official image layout: modality/action/user/trial/frame.

    data_root is HAR/data, NOT the archive parent. Only the explicit image
    modalities are supported by this seed. Never selects RGB automatically.
    """
    if modality not in IMAGE_MODALITIES:
        raise ValueError("This image seed supports only Depth_Color, IR or Thermal")
    directory = data_root / modality
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError(f"Expected {modality}/ under data root; use --manifest for another layout")
    clips = []
    for activity in sorted(directory.iterdir()):
        if not activity.is_dir():
            continue
        label_match = re.fullmatch(r"(\d+)_.+", activity.name)
        if not label_match:
            raise ValueError(f"Unknown activity directory {activity.name!r}; use an explicit CSV")
        for subject in sorted(activity.iterdir()):
            if not subject.is_dir():
                continue
            # Upstream extracts a numeric user identifier. Be narrower here to
            # avoid silently treating an arbitrary filename as a subject split.
            subject_match = re.fullmatch(r"(?:user[_-]?)?(\d+)", subject.name, re.IGNORECASE)
            if not subject_match:
                raise ValueError(f"Unknown subject directory {subject.name!r}; use an explicit CSV")
            for trial in sorted(subject.iterdir()):
                if not trial.is_dir():
                    continue
                frames = tuple(
                    _below(data_root, path.relative_to(data_root))
                    for path in sorted(trial.iterdir(), key=_natural)
                    if path.suffix.lower() in IMAGE_EXTENSIONS
                )
                if not frames:
                    continue
                clips.append(
                    Clip(
                        trial.relative_to(directory).as_posix(),
                        int(subject_match[1]),
                        int(label_match[1]),
                        modality,
                        frames,
                    )
                )
    _validate_clips(clips)
    return clips


def _validate_clips(clips: Sequence[Clip]) -> None:
    if not clips or len({c.clip_id for c in clips}) != len(clips):
        raise ValueError("Clips must be nonempty and have unique clip_id values")
    seen: set[Path] = set()
    for clip in clips:
        if not clip.clip_id.strip() or clip.subject < 1 or not 0 <= clip.label < 40:
            raise ValueError("Each clip needs an id, a positive subject and a label in 0..39")
        if clip.modality not in IMAGE_MODALITIES or not clip.frames:
            raise ValueError("Each clip needs frames from Depth_Color, IR or Thermal")
        for frame in clip.frames:
            if frame.resolve() in seen:
                raise ValueError("A source frame is assigned to multiple clips")
            seen.add(frame.resolve())


def _sample_frames(frames: Sequence[Path], limit: int) -> Sequence[Path]:
    if len(frames) <= limit:
        return frames
    if limit == 1:
        return frames[len(frames) // 2 : len(frames) // 2 + 1]
    return [frames[round(i * (len(frames) - 1) / (limit - 1))] for i in range(limit)]


def prepare_dataset(
    data_root: Path,
    destination: Path,
    *,
    validate_subjects: Sequence[int],
    final_subjects: Sequence[int] = (),
    modality: str = "Depth_Color",
    manifest: Path | None = None,
    max_frames: int = 16,
    dataset_kind: str = "competition",
) -> dict[str, Any]:
    """Copy selected frames into separate training and anonymous validation trees.

    The private tree contains labels/provenance and belongs only to the trusted
    scorer. Only train/ is mounted for training; only validate/ for inference.
    max_frames is a fixed protocol choice, applied before any research begins.
    """
    if destination.exists():
        raise FileExistsError("Prepared data is immutable; choose a new output directory")
    if max_frames < 1 or dataset_kind not in {"competition", "synthetic"}:
        raise ValueError("max_frames must be positive and dataset_kind competition or synthetic")
    clips = read_clip_csv(manifest, data_root) if manifest else scan_clips(data_root, modality)
    holdout = set(validate_subjects)
    final_holdout = set(final_subjects)
    subjects = {c.subject for c in clips}
    if (
        not holdout
        or not holdout <= subjects
        or not final_holdout <= subjects
        or holdout & final_holdout
        or not subjects - holdout - final_holdout
    ):
        raise ValueError(
            "Validation/final subjects must be observed, disjoint and leave training subjects"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "prepared"
        staged.mkdir()
        public: dict[str, list[dict[str, Any]]] = {"train": [], "validate": []}
        if final_holdout:
            public["final"] = []
        labels: dict[str, dict[str, int]] = {split: {} for split in public if split != "train"}
        provenance = []
        image_hashes: dict[str, str] = {}
        # Random IDs AND shuffled ordering: neither the source path nor its
        # activity-sorted position appears in a validation input.
        ordered = list(clips)
        secrets.SystemRandom().shuffle(ordered)
        for clip in ordered:
            split = (
                "validate"
                if clip.subject in holdout
                else "final"
                if clip.subject in final_holdout
                else "train"
            )
            clip_id = "clip_" + secrets.token_hex(12)
            paths = []
            selected = _sample_frames(clip.frames, max_frames)
            for index, frame in enumerate(selected):
                relative = Path("inputs") / clip_id / f"frame_{index:04d}{frame.suffix.lower()}"
                target = staged / split / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                # Copy bytes only: source names, symlinks and file metadata are
                # not used as inference features.
                shutil.copyfile(frame, target)
                paths.append(relative.as_posix())
                image_hashes[f"{split}/{relative.as_posix()}"] = _sha(target)
            row: dict[str, Any] = {"clip_id": clip_id, "frames": paths, "modality": clip.modality}
            if split == "train":
                row["label"] = clip.label
            else:
                labels[split][clip_id] = clip.label
            public[split].append(row)
            provenance.append(
                {
                    "clip_id": clip_id,
                    "source_id": clip.clip_id,
                    "subject": clip.subject,
                    "label": clip.label,
                    "split": split,
                    "source_frames": [str(p) for p in selected],
                }
            )
        for split, rows in public.items():
            _json(staged / split / "manifest.json", {"schema": SCHEMA, "clips": rows})
        protocol: dict[str, Any] = {
            "schema": SCHEMA,
            "dataset_kind": dataset_kind,
            "source_code": UPSTREAM_URL,
            "source_layout": "explicit-csv" if manifest else "modality/action/user/trial/frame",
            "max_frames": max_frames,
            "train_subjects": sorted(subjects - holdout - final_holdout),
            "validate_subjects": sorted(holdout),
            "final_subjects": sorted(final_holdout),
            "counts": {split: len(rows) for split, rows in public.items()},
            "image_sha256": image_hashes,
            "train_manifest_sha256": _sha(staged / "train/manifest.json"),
            "validate_manifest_sha256": _sha(staged / "validate/manifest.json"),
        }
        if final_holdout:
            protocol["final_manifest_sha256"] = _sha(staged / "final/manifest.json")
        protocol_id = hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()
        protocol["protocol_id"] = protocol_id
        _json(staged / "protocol.json", protocol)
        _json(staged / "private/provenance.json", provenance)
        for split, values in labels.items():
            _json(
                staged / "private" / f"{split}_labels.json",
                {
                    "schema": SCHEMA,
                    "protocol_id": protocol_id,
                    "dataset_kind": dataset_kind,
                    "split": split,
                    "labels": values,
                    f"{split}_manifest_sha256": protocol[f"{split}_manifest_sha256"],
                },
            )
        staged.rename(destination)
    return protocol


def read_predictions(path: Path, expected_ids: Iterable[str]) -> dict[str, int]:
    """Reject missing/extra/duplicate clips and non-integral or invalid labels."""
    expected = set(expected_ids)
    predictions = {}
    with path.open(newline="") as stream:
        rows = csv.DictReader(stream)
        if rows.fieldnames != ["clip_id", "prediction"]:
            raise ValueError("Prediction CSV must have exactly clip_id,prediction columns")
        for row in rows:
            clip_id, value = row["clip_id"], row["prediction"]
            if None in row or not clip_id or clip_id in predictions:
                raise ValueError("Invalid or duplicate prediction row")
            if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9]|[1-3][0-9])", value):
                raise ValueError("Predictions must be integers in 0..39")
            predictions[clip_id] = int(value)
    if not expected or set(predictions) != expected:
        raise ValueError("Predictions must cover every expected clip exactly once")
    return predictions


def score_predictions(
    predictions_path: Path,
    private_labels: Path,
    checkpoint: Path,
) -> ClipScore:
    """Trusted entry: no torch, pickle, candidate imports, or training-log metrics."""
    if checkpoint.is_symlink() or not checkpoint.is_file():
        raise ValueError("Checkpoint must be a regular file")
    size = checkpoint.stat().st_size
    if not 0 < size <= MAX_MODEL_BYTES:
        raise ValueError("The inference checkpoint must be nonempty and at most 100 MB")
    private = json.loads(private_labels.read_text())
    if private.get("schema") != SCHEMA:
        raise ValueError("Unknown private labels schema")
    labels = private.get("labels")
    if (
        not isinstance(labels, dict)
        or not labels
        or any(
            not isinstance(k, str) or type(v) is not int or not 0 <= v < 40
            for k, v in labels.items()
        )
    ):
        raise ValueError("Invalid trusted clip labels")
    predictions = read_predictions(predictions_path, labels)
    correct = sum(predictions[key] == value for key, value in labels.items())
    return ClipScore(
        correct / len(labels),
        correct,
        len(labels),
        size,
        _sha(checkpoint),
        _sha(predictions_path),
        private["protocol_id"],
        private["dataset_kind"],
    )


def prepare_test_inputs(
    data_root: Path,
    sample_submission: Path,
    destination: Path,
    *,
    modality: str = "Depth_Color",
    max_frames: int = 16,
) -> None:
    """Prepare official anonymous test clips; no labels or leaderboard are used.

    Expected layout is <sample path>/<modality>/<frames>. If a downloaded release
    differs, prepare the public manifest explicitly rather than guessing paths.
    """
    if modality not in IMAGE_MODALITIES or max_frames < 1:
        raise ValueError("Select a supported image modality and positive max_frames")
    if destination.exists():
        raise FileExistsError(destination)
    with sample_submission.open(newline="") as stream:
        rows = csv.DictReader(stream)
        if rows.fieldnames != ["path", "prediction"]:
            raise ValueError("Expected actual competition sample columns path,prediction")
        samples = list(rows)
    paths = [row["path"] for row in samples]
    if not paths or len(set(paths)) != len(paths):
        raise ValueError("Sample submission must have unique nonempty paths")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staged = Path(temporary) / "test"
        staged.mkdir()
        clips = []
        for index, source in enumerate(paths):
            relative = Path(source)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Sample paths must remain inside the data root")
            directory = data_root / relative / modality
            frames = [
                _below(data_root, p.relative_to(data_root))
                for p in sorted(directory.iterdir(), key=_natural)
                if p.suffix.lower() in IMAGE_EXTENSIONS
            ]
            if not frames:
                raise ValueError(f"No {modality} frames for {source}; choose a supported modality")
            selected = []
            for frame_index, frame in enumerate(_sample_frames(frames, max_frames)):
                path = (
                    Path("inputs")
                    / f"test_{index:05d}"
                    / f"{frame_index:04d}{frame.suffix.lower()}"
                )
                target = staged / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(frame, target)
                selected.append(path.as_posix())
            clips.append({"clip_id": source, "frames": selected, "modality": modality})
        _json(staged / "manifest.json", {"schema": SCHEMA, "clips": clips})
        staged.rename(destination)


def export_submission(predictions: Path, sample_submission: Path, output: Path) -> None:
    """Preserve exactly the official sample's path values and ordering."""
    if output.exists():
        raise FileExistsError(output)
    with sample_submission.open(newline="") as stream:
        rows = csv.DictReader(stream)
        if rows.fieldnames != ["path", "prediction"]:
            raise ValueError("Sample submission must have path,prediction columns")
        paths = [row["path"] for row in rows]
    if len(set(paths)) != len(paths) or any(not p for p in paths):
        raise ValueError("Invalid or duplicate sample paths")
    values = read_predictions(predictions, paths)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["path", "prediction"])
        writer.writerows((path, values[path]) for path in paths)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--data", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--validate-subjects", type=int, nargs="+", required=True)
    prepare.add_argument("--final-subjects", type=int, nargs="+", default=[])
    prepare.add_argument("--modality", choices=sorted(IMAGE_MODALITIES), default="Depth_Color")
    prepare.add_argument("--manifest", type=Path)
    prepare.add_argument("--max-frames", type=int, default=16)
    prepare.add_argument(
        "--dataset-kind", choices=["competition", "synthetic"], default="competition"
    )
    test = commands.add_parser("prepare-test")
    test.add_argument("--data", type=Path, required=True)
    test.add_argument("--sample", type=Path, required=True)
    test.add_argument("--output", type=Path, required=True)
    test.add_argument("--modality", choices=sorted(IMAGE_MODALITIES), default="Depth_Color")
    test.add_argument("--max-frames", type=int, default=16)
    score = commands.add_parser("score")
    score.add_argument("--predictions", type=Path, required=True)
    score.add_argument("--labels", type=Path, required=True)
    score.add_argument("--checkpoint", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    export = commands.add_parser("export")
    export.add_argument("--predictions", type=Path, required=True)
    export.add_argument("--sample", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_dataset(
            args.data,
            args.output,
            validate_subjects=args.validate_subjects,
            final_subjects=args.final_subjects,
            modality=args.modality,
            manifest=args.manifest,
            max_frames=args.max_frames,
            dataset_kind=args.dataset_kind,
        )
        print(json.dumps({"protocol_id": result["protocol_id"], "counts": result["counts"]}))
    elif args.command == "prepare-test":
        prepare_test_inputs(
            args.data, args.sample, args.output, modality=args.modality, max_frames=args.max_frames
        )
    elif args.command == "score":
        if args.output.exists():
            raise FileExistsError(args.output)
        result_score = score_predictions(args.predictions, args.labels, args.checkpoint)
        _json(args.output, asdict(result_score))
        print(json.dumps(asdict(result_score)))
    else:
        export_submission(args.predictions, args.sample, args.output)


if __name__ == "__main__":
    main()
