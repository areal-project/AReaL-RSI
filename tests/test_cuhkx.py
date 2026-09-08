"""Synthetic clip fixtures exercise trusted preparation/scoring without torch."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from areal_rsi.adapters.cuhkx import (
    MAX_MODEL_BYTES,
    export_submission,
    prepare_dataset,
    prepare_test_inputs,
    read_clip_csv,
    read_predictions,
    scan_clips,
    score_predictions,
)


def fixture(root: Path) -> None:
    for label in (0, 39):
        for subject in (1, 2, 3):
            clip = root / "Depth_Color" / f"{label}_action" / f"user{subject}" / "trial1"
            clip.mkdir(parents=True)
            for index in (1, 2, 10):
                (clip / f"frame{index}.png").write_bytes(
                    f"fixture:{label}:{subject}:{index}".encode()
                )


def predictions(path: Path, rows: list[tuple[str, int | str]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["clip_id", "prediction"])
        writer.writerows(rows)


class CuhkxTest(unittest.TestCase):
    def test_subject_split_anonymous_inputs_and_clip_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root / "raw")
            prepared = root / "prepared"
            protocol = prepare_dataset(
                root / "raw",
                prepared,
                validate_subjects=[3],
                max_frames=2,
                dataset_kind="synthetic",
            )
            self.assertEqual(protocol["counts"], {"train": 4, "validate": 2})
            self.assertEqual(protocol["train_subjects"], [1, 2])
            train = json.loads((prepared / "train/manifest.json").read_text())["clips"]
            validate = json.loads((prepared / "validate/manifest.json").read_text())["clips"]
            for row in validate:
                self.assertEqual(set(row), {"clip_id", "frames", "modality"})
                self.assertNotIn("user", row["clip_id"])
                self.assertEqual(len(row["frames"]), 2)
                self.assertTrue(all((prepared / "validate" / p).is_file() for p in row["frames"]))
                self.assertTrue(all("action" not in p and "user" not in p for p in row["frames"]))
            self.assertTrue(all("label" in row for row in train))
            private = prepared / "private/validate_labels.json"
            labels = json.loads(private.read_text())["labels"]
            output = root / "predictions.csv"
            ids = list(labels)
            predictions(output, [(ids[0], labels[ids[0]]), (ids[1], (labels[ids[1]] + 1) % 40)])
            checkpoint = root / "checkpoint.pt"
            # The trusted scorer deliberately treats a checkpoint as bytes; it
            # never executes candidate code or torch/pickle deserialization.
            checkpoint.write_bytes(b"synthetic-checkpoint-fixture")
            result = score_predictions(output, private, checkpoint)
            self.assertEqual((result.accuracy, result.total, result.correct), (0.5, 2, 1))
            self.assertEqual(result.dataset_kind, "synthetic")
            self.assertEqual(result.protocol_id, protocol["protocol_id"])
            with self.assertRaises(FileExistsError):
                prepare_dataset(root / "raw", prepared, validate_subjects=[3])

    def test_explicit_manifest_and_path_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "frame.png").write_bytes(b"fixture")
            manifest = root / "clips.csv"
            with manifest.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["clip_id", "subject", "label", "modality", "frames"])
                writer.writerow(["different-release-layout", 1, 4, "IR", '["frame.png"]'])
            clips = read_clip_csv(manifest, root)
            self.assertEqual(clips[0].label, 4)
            self.assertEqual(clips[0].frames, ((root / "frame.png").resolve(),))
            for invalid in ('["../frame.png"]', '["/etc/passwd"]', '["link.png"]'):
                if not (root / "link.png").exists():
                    (root / "link.png").symlink_to(root / "frame.png")
                with manifest.open("w", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["clip_id", "subject", "label", "modality", "frames"])
                    writer.writerow(["bad", 1, 4, "IR", invalid])
                with self.assertRaises(ValueError, msg=invalid):
                    read_clip_csv(manifest, root)

    def test_layout_split_and_modality_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root / "raw")
            self.assertEqual(
                [p.name for p in scan_clips(root / "raw")[0].frames],
                ["frame1.png", "frame2.png", "frame10.png"],
            )
            for subjects in ([], [99], [1, 2, 3]):
                with self.assertRaises(ValueError):
                    prepare_dataset(root / "raw", root / "prepared", validate_subjects=subjects)
            with self.assertRaises(ValueError):
                scan_clips(root / "raw", "RGB")
            (root / "raw/Depth_Color/unrecognized").mkdir()
            with self.assertRaisesRegex(ValueError, "explicit CSV"):
                scan_clips(root / "raw")

    def test_predictions_require_complete_finite_integer_clip_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "predictions.csv"
            invalid: list[list[tuple[str, int | str]]] = [
                [("a", 0)],
                [("a", 0), ("b", 1), ("c", 2)],
                [("a", 0), ("a", 1)],
                [("a", "NaN"), ("b", 0)],
                [("a", "0.0"), ("b", 0)],
                [("a", 40), ("b", 0)],
            ]
            for rows in invalid:
                predictions(path, rows)
                with self.assertRaises(ValueError, msg=str(rows)):
                    read_predictions(path, ["a", "b"])
            predictions(path, [("b", 39), ("a", 0)])
            self.assertEqual(read_predictions(path, ["a", "b"]), {"a": 0, "b": 39})

    def test_model_limit_and_submission_preserve_official_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sample = root / "sample_submission.csv"
            paths = ["small_model_track_test/SM_test_0002/", "small_model_track_test/SM_test_0001/"]
            with sample.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["path", "prediction"])
                writer.writerows((path, "") for path in paths)
            for path in paths:
                directory = root / path / "Depth_Color"
                directory.mkdir(parents=True)
                (directory / "frame1.png").write_bytes(b"synthetic-frame")
            prepared = root / "test-inputs"
            prepare_test_inputs(root, sample, prepared)
            manifest = json.loads((prepared / "manifest.json").read_text())
            self.assertEqual([row["clip_id"] for row in manifest["clips"]], paths)
            predicted = root / "predicted.csv"
            predictions(predicted, [(paths[1], 3), (paths[0], 4)])
            output = root / "submission.csv"
            export_submission(predicted, sample, output)
            with output.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(
                rows, [{"path": paths[0], "prediction": "4"}, {"path": paths[1], "prediction": "3"}]
            )
            with self.assertRaises(FileExistsError):
                export_submission(predicted, sample, output)
            checkpoint = root / "large.pt"
            with checkpoint.open("wb") as stream:
                stream.truncate(MAX_MODEL_BYTES + 1)
            with self.assertRaisesRegex(ValueError, "100 MB"):
                score_predictions(predicted, root / "unused-labels.json", checkpoint)


if __name__ == "__main__":
    unittest.main()
