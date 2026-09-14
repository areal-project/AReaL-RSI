"""Editable, from-scratch image-clip CNN. No pretrained weights or RGB inputs."""

from __future__ import annotations

import json
from pathlib import Path

import torch
from PIL import Image
from torch import nn
from torch.utils.data import Dataset


class ClipCNN(nn.Module):
    def __init__(self, width: int = 16, classes: int = 40):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv2d(3, width, 3, stride=2, padding=1),
            nn.GroupNorm(1, width),
            nn.SiLU(),
            nn.Conv2d(width, width * 2, 3, stride=2, padding=1),
            nn.GroupNorm(1, width * 2),
            nn.SiLU(),
            nn.Conv2d(width * 2, width * 4, 3, stride=2, padding=1),
            nn.GroupNorm(1, width * 4),
            nn.SiLU(),
            nn.AdaptiveAvgPool2d(1),
        )
        self.classifier = nn.Linear(width * 4, classes)

    def forward(self, frames: torch.Tensor) -> torch.Tensor:
        batch, count, channels, height, width = frames.shape
        encoded = self.encoder(frames.reshape(batch * count, channels, height, width))
        # One prediction and one loss per clip, not a frame-count-weighted score.
        pooled = encoded.reshape(batch, count, -1).mean(dim=1)
        return self.classifier(pooled)


class Clips(Dataset):
    def __init__(self, manifest: Path, *, image_size: int, frames: int, labelled: bool):
        body = json.loads(manifest.read_text())
        if body.get("schema") != "cuhkx-clips-v1":
            raise ValueError("Unsupported clip manifest")
        self.rows = body["clips"]
        if not self.rows or len({r["clip_id"] for r in self.rows}) != len(self.rows):
            raise ValueError("The manifest must contain unique clips")
        self.root, self.size, self.frames, self.labelled = (
            manifest.parent.resolve(),
            image_size,
            frames,
            labelled,
        )
        for row in self.rows:
            if row.get("modality") not in {"Depth_Color", "IR", "Thermal"} or not row.get("frames"):
                raise ValueError("This seed requires non-RGB image clips")
            if labelled and (type(row.get("label")) is not int or not 0 <= row["label"] < 40):
                raise ValueError("Training labels must be original action IDs 0..39")
            if not labelled and "label" in row:
                raise ValueError("Inference manifest must not contain labels")
            for name in row["frames"]:
                path = Path(name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Frames must be relative to the manifest directory")
                resolved = (self.root / path).resolve(strict=True)
                if not resolved.is_relative_to(self.root) or not resolved.is_file():
                    raise ValueError("Frame escapes manifest directory")

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int):
        row = self.rows[index]
        paths = row["frames"]
        selected = [
            round(i * (len(paths) - 1) / max(1, self.frames - 1)) for i in range(self.frames)
        ]
        images = []
        for frame_index in selected:
            with Image.open(self.root / paths[frame_index]) as image:
                # Depth_Color/IR/Thermal images can be stored in three channels;
                # converting their pixel format does not introduce RGB-camera data.
                image = image.convert("RGB").resize(
                    (self.size, self.size), Image.Resampling.BILINEAR
                )
                pixels = torch.frombuffer(bytearray(image.tobytes()), dtype=torch.uint8)
                tensor = pixels.reshape(self.size, self.size, 3).permute(2, 0, 1).float() / 255
                images.append(tensor)
        return torch.stack(images), row["label"] if self.labelled else row["clip_id"]
