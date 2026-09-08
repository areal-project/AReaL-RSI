"""Editable training entry. Writes a checkpoint; stdout is diagnostic, never VALIDATE."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

import torch
from model import ClipCNN, Clips
from torch.utils.data import DataLoader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--image-size", type=int, default=64)
    parser.add_argument("--width", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if (
        min(args.epochs, args.batch_size, args.frames, args.image_size, args.width, args.threads)
        < 1
    ):
        parser.error("Training sizes, epochs and threads must be positive")
    if not 0 < args.learning_rate < 1:
        parser.error("learning-rate must be between zero and one")
    if args.output.exists():
        raise FileExistsError("Each training job needs a fresh output directory")
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(args.threads)
    device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable; no silent CPU fallback")
    dataset = Clips(args.manifest, image_size=args.image_size, frames=args.frames, labelled=True)
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True, num_workers=0)
    model = ClipCNN(args.width).to(device)
    if sum(p.numel() * p.element_size() for p in model.parameters()) > 95_000_000:
        raise ValueError("Model weights would exceed the 100 MB checkpoint budget")
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    args.output.mkdir(parents=True)
    config = {
        "architecture": "clip-cnn-v1",
        "width": args.width,
        "classes": 40,
        "frames": args.frames,
        "image_size": args.image_size,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
        "seed": args.seed,
        "pretrained": False,
        "class_ids": list(range(40)),
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "model_source_sha256": hashlib.sha256(
            Path(__file__).with_name("model.py").read_bytes()
        ).hexdigest(),
    }
    (args.output / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    with (args.output / "training.jsonl").open("w") as logs:
        for epoch in range(args.epochs):
            model.train()
            total_loss, count = 0.0, 0
            for frames, labels in loader:
                frames, labels = frames.to(device), labels.to(device)
                optimizer.zero_grad(set_to_none=True)
                loss = torch.nn.functional.cross_entropy(model(frames), labels)
                if not torch.isfinite(loss):
                    raise RuntimeError("Non-finite training loss")
                loss.backward()
                optimizer.step()
                total_loss += loss.item() * len(labels)
                count += len(labels)
            record = {"epoch": epoch + 1, "train_loss": total_loss / count, "clips": count}
            logs.write(json.dumps(record) + "\n")
            logs.flush()
            print(json.dumps(record), flush=True)
    # Save tensors and primitive configuration only, without a pickled module or
    # optimizer. Prediction uses weights_only=True and reconstructs the model.
    payload = {"config": config, "state_dict": {k: v.cpu() for k, v in model.state_dict().items()}}
    checkpoint = args.output / "checkpoint.pt"
    torch.save(payload, checkpoint)
    if checkpoint.stat().st_size > 100_000_000:
        checkpoint.unlink()
        raise ValueError("Inference checkpoint exceeds 100 MB")
    print(
        json.dumps({"checkpoint": str(checkpoint), "bytes": checkpoint.stat().st_size}), flush=True
    )


if __name__ == "__main__":
    main()
