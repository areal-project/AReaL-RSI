"""Warm pinned dataset snapshots independently of model downloads."""
import json
import os
from pathlib import Path
from env_paths import runtime_root
ROOT = runtime_root(__file__)
os.environ["HF_HOME"] = str(ROOT / "cache/huggingface")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
from huggingface_hub import snapshot_download
for repo, revision in json.loads((ROOT / "config.json").read_text())["datasets"]:
    print("Downloading", repo, revision, flush=True)
    snapshot_download(repo, revision=revision, repo_type="dataset", token=False, max_workers=4,
                      ignore_patterns=["*.DS_Store", "images/*", "*.png", "*.jpg"])
print("DATASET DOWNLOADS READY", flush=True)
