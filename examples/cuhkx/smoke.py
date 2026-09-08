"""Real CPU training/inference on synthetic images, not a competition result.

Run in the separate example environment: python examples/cuhkx/smoke.py --output /tmp/cuhkx-smoke
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from PIL import Image

# This script can run in an isolated torch environment without installing RSI's
# DSH/controller dependencies. The adapter itself is standard-library only.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from areal_rsi.adapters.cuhkx import (
    export_submission,
    prepare_dataset,
    prepare_test_inputs,
    score_predictions,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists():
        raise FileExistsError("Use a fresh smoke output directory")
    root.mkdir(parents=True)
    for label in (0, 1):
        for subject in (1, 2, 3):
            path = root / "raw/Depth_Color" / f"{label}_synthetic" / f"user{subject}" / "trial1"
            path.mkdir(parents=True)
            for frame in range(3):
                color = (
                    (180 + frame, 20 + subject, 20)
                    if label == 0
                    else (20, 180 + frame, 20 + subject)
                )
                Image.new("RGB", (24, 24), color).save(path / f"frame{frame}.png")
    prepared = root / "prepared"
    prepare_dataset(
        root / "raw", prepared, validate_subjects=[3], max_frames=2, dataset_kind="synthetic"
    )
    seed = Path(__file__).with_name("seed").resolve()
    subprocess.run(
        [
            sys.executable,
            str(seed / "train.py"),
            "--manifest",
            str(prepared / "train/manifest.json"),
            "--output",
            str(root / "trained"),
            "--device",
            "cpu",
            "--epochs",
            "10",
            "--learning-rate",
            "0.01",
            "--frames",
            "2",
            "--image-size",
            "16",
            "--width",
            "4",
            "--batch-size",
            "2",
        ],
        check=True,
        timeout=180,
    )
    checkpoint = root / "trained/checkpoint.pt"
    subprocess.run(
        [
            sys.executable,
            str(seed / "predict.py"),
            "--manifest",
            str(prepared / "validate/manifest.json"),
            "--checkpoint",
            str(checkpoint),
            "--output",
            str(root / "validate_predictions.csv"),
        ],
        check=True,
        timeout=120,
    )
    result = score_predictions(
        root / "validate_predictions.csv", prepared / "private/validate_labels.json", checkpoint
    )
    assert result.total == 2 and 0 <= result.accuracy <= 1 and result.checkpoint_bytes > 0
    sample = root / "sample_submission.csv"
    with sample.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["path", "prediction"])
        for index in range(2):
            relative = f"small_model_track_test/SM_test_{index + 1:04d}/"
            directory = root / relative / "Depth_Color"
            directory.mkdir(parents=True)
            Image.new("RGB", (24, 24), (180, 20 + index, 20)).save(directory / "frame1.png")
            writer.writerow([relative, ""])
    test = root / "test-inputs"
    prepare_test_inputs(root, sample, test, max_frames=2)
    subprocess.run(
        [
            sys.executable,
            str(seed / "predict.py"),
            "--manifest",
            str(test / "manifest.json"),
            "--checkpoint",
            str(checkpoint),
            "--output",
            str(root / "test_predictions.csv"),
        ],
        check=True,
        timeout=120,
    )
    export_submission(root / "test_predictions.csv", sample, root / "submission.csv")
    summary = {
        "synthetic_smoke": True,
        "score": asdict(result),
        "submission": str(root / "submission.csv"),
    }
    (root / "smoke.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
