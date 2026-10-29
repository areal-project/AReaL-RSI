"""Create an offline multi-turn text JSONL example from the shared ShareGPT copy."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

DEFAULT_SOURCE = Path(
    "/storage/openpsi/data/ShareGPT_Vicuna_unfiltered/ShareGPT_V3_unfiltered_cleaned_split.json"
)
SOURCE_URL = "https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered"


def json_records(path: Path):
    """Read a top-level JSON array without loading the entire dataset into RAM."""
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8") as stream:
        buffer = stream.read(131072).lstrip()
        if not buffer.startswith("["):
            raise ValueError("Source must be a JSON array")
        buffer = buffer[1:]
        while True:
            buffer = buffer.lstrip(" \t\r\n,")
            if buffer.startswith("]"):
                return
            try:
                record, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                chunk = stream.read(131072)
                if not chunk:
                    raise ValueError("Incomplete source JSON array") from None
                buffer += chunk
                continue
            yield record
            buffer = buffer[end:]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_records(path: Path, count: int) -> list[dict]:
    selected = []
    seen = set()
    roles = {"human": "user", "gpt": "assistant", "system": "system"}
    for row in json_records(path):
        raw = row.get("conversations", [])
        if not raw or any(
            m.get("from") not in roles or not isinstance(m.get("value"), str) for m in raw
        ):
            continue
        messages = [{"role": roles[m["from"]], "content": m["value"]} for m in raw]
        if (
            sum(m["role"] == "user" for m in messages) < 2
            or sum(m["role"] == "assistant" for m in messages) < 2
        ):
            continue
        if messages[-1]["role"] != "assistant" or sum(len(m["content"]) for m in messages) > 65536:
            continue
        source_id = str(row.get("id", ""))
        # Do not put two chunks of the same original conversation in different splits.
        group_id = (
            source_id.rsplit("_", 1)[0] if source_id.rsplit("_", 1)[-1].isdigit() else source_id
        )
        if not group_id or group_id in seen:
            continue
        seen.add(group_id)
        selected.append({"messages": messages, "source_id": source_id})
        if len(selected) == count:
            return selected
    raise ValueError(f"Could not select {count} distinct multi-turn conversations")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "data")
    parser.add_argument("--train-samples", type=int, default=64)
    parser.add_argument("--valid-samples", type=int, default=16)
    args = parser.parse_args()
    if min(args.train_samples, args.valid_samples) < 1:
        parser.error("Sample counts must be positive")
    root = args.output_dir.resolve()
    names = ("sharegpt-train.jsonl", "sharegpt-valid.jsonl", "source.json")
    if any((root / name).exists() for name in names):
        parser.error("Output files already exist; select a new --output-dir")
    rows = select_records(args.source, args.train_samples + args.valid_samples)
    root.mkdir(parents=True, exist_ok=True)
    print("Recording the shared source SHA256...", flush=True)
    manifest = {
        "dataset": "anon8231489123/ShareGPT_Vicuna_unfiltered",
        "url": SOURCE_URL,
        "declared_license": "Apache-2.0",
        "source": str(args.source.resolve()),
        "source_sha256": file_sha256(args.source),
        "files": {},
    }
    for name, split in (
        (names[0], rows[: args.train_samples]),
        (names[1], rows[args.train_samples :]),
    ):
        text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in split)
        (root / name).write_text(text)
        manifest["files"][name] = {
            "rows": len(split),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "source_ids": [row["source_id"] for row in split],
        }
        print(f"Prepared {len(split)} multi-turn conversations: {root / name}")
    (root / names[2]).write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
