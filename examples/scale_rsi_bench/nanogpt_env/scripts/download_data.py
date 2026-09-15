"""Run on the login host with huggingface_hub installed; cache is shared with SIF."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from env_paths import runtime_root
ROOT = runtime_root(__file__)
os.environ["HF_HOME"] = str(ROOT / "cache/huggingface")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
from huggingface_hub import hf_hub_download, snapshot_download

if __name__ == "__main__":
    snapshot_download(
        "gpt2", revision="607a30d783dfa663caf39e06633721c8d4cfcd7e",
        allow_patterns=["config.json", "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt"],
        max_workers=4, token=False,
    )
    # Warm main too, as the upstream packer uses from_pretrained('gpt2').
    snapshot_download(
        "gpt2", allow_patterns=["config.json", "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt"],
        max_workers=4, token=False,
    )
    sources = [
        ("HuggingFaceFW/fineweb", "9bb295ddab0e05d785b879661af7260fed5140fc", "sample/10BT/000_00000.parquet"),
        ("allenai/c4", "1588ec454efa1a09f29cd18ddd04fe05fc8653a2", "en.noclean/c4-train.00000-of-07168.json.gz"),
    ]
    def download(source):
        repo, revision, filename = source
        print(f"Downloading {repo}/{filename}", flush=True)
        hf_hub_download(repo, filename, repo_type="dataset", revision=revision, token=False)
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(download, sources))
