"""Editable inference entry: anonymous clip inputs -> clip_id,prediction CSV."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import torch
from model import ClipCNN, Clips
from torch.utils.data import DataLoader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if min(args.batch_size, args.threads) < 1:
        parser.error("batch-size and threads must be positive")
    if args.output.exists():
        raise FileExistsError(args.output)
    if not 0 < args.checkpoint.stat().st_size <= 100_000_000:
        raise ValueError("Checkpoint must be nonempty and at most 100 MB")
    torch.set_num_threads(args.threads)
    device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    config = checkpoint["config"]
    if config["classes"] != 40 or config["pretrained"] or config["class_ids"] != list(range(40)):
        raise ValueError("Unsupported seed checkpoint configuration")
    model = ClipCNN(config["width"], config["classes"])
    model.load_state_dict(checkpoint["state_dict"], strict=True)
    model.to(device).eval()
    dataset = Clips(
        args.manifest, image_size=config["image_size"], frames=config["frames"], labelled=False
    )
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=False, num_workers=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", newline="") as stream, torch.inference_mode():
        writer = csv.writer(stream)
        writer.writerow(["clip_id", "prediction"])
        for frames, ids in loader:
            scores = model(frames.to(device))
            if not torch.isfinite(scores).all():
                raise RuntimeError("Non-finite prediction logits")
            writer.writerows(zip(ids, scores.argmax(dim=1).cpu().tolist(), strict=True))


if __name__ == "__main__":
    main()
