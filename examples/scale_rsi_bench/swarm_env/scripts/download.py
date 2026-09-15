"""Download public, revision-pinned assets on the host, using the shared HF cache."""
import json
import os
from pathlib import Path
from env_paths import runtime_root
ROOT = runtime_root(__file__)
os.environ["HF_HOME"] = str(ROOT / "cache/huggingface")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
from huggingface_hub import snapshot_download
config = json.loads((ROOT / "config.json").read_text())
manifest_path = ROOT / "assets/downloads.json"
manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
revision = manifest.get("model_revision") or config["revision"]
print("Downloading model", config["model"], revision or "main", flush=True)
path = Path(snapshot_download(config["model"], revision=revision, token=False, max_workers=8,
    ignore_patterns=["*.bin", "*.pt", "*.pth", "*.gguf", "*.md"]))
manifest.update(model=config["model"], model_revision=path.name)
(ROOT / "assets").mkdir(exist_ok=True)
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
destination = ROOT / "models" / config["model"].split("/")[-1]
destination.parent.mkdir(exist_ok=True)
if not destination.exists():
    destination.symlink_to(path)
for repo, revision in config["datasets"]:
    print("Downloading dataset", repo, revision, flush=True)
    snapshot_download(repo, revision=revision, repo_type="dataset", token=False, max_workers=4)
print("DOWNLOADS READY", flush=True)
