"""Re-fetch only model files that fail their Hugging Face content hash."""
import hashlib
import json
import os
from pathlib import Path
from huggingface_hub import hf_hub_download
from range_download import download
root = Path(os.environ["RSI_ROOT"])
cfg = json.loads((root / "config.json").read_text())
manifest = json.loads((root / "assets/downloads.json").read_text())
directory = root / "models" / cfg["model"].split("/")[-1]
def valid(path):
    blob = path.resolve()
    if len(blob.name) == 64:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == blob.name
    digest = hashlib.sha1(f"blob {path.stat().st_size}".encode() + bytes([0]))
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            digest.update(data)
    return digest.hexdigest() == blob.name
for path in sorted(directory.iterdir()):
    if not valid(path):
        print(f"REPAIRING {path.name}", flush=True)
        if len(path.resolve().name) == 64:
            download(cfg["model"], path.name, manifest["model_revision"], path.resolve())
        else:
            hf_hub_download(cfg["model"], path.name, revision=manifest["model_revision"], force_download=True)
        assert valid(path), f"Still corrupt after download: {path}"
    print(f"VERIFIED {path.name}", flush=True)
print("MODEL CONTENT HASHES PASS", flush=True)
