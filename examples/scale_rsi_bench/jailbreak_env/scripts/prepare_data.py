"""Reproduce the upstream Dockerfile's pinned training data and dev/test splits."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from datasets import load_dataset
from huggingface_hub import hf_hub_download

root = Path(os.environ["RSI_ROOT"])
raw = root / "assets/raw"
work = root / "workspace"
for config, filename in (("or-bench-hard-1k", "or-bench-hard-1k.jsonl"), ("or-bench-toxic", "or-bench-toxic.jsonl")):
    ds = load_dataset("bench-llms/or-bench", config, revision="fd6ee135ee63ff6c4f3ff72c0e39627bf0a7f314", split="train")
    with (raw / filename).open("w") as output:
        for row in ds:
            output.write(json.dumps({"prompt": row["prompt"], "category": row.get("category", "")}) + "\n")
shutil.copyfile(hf_hub_download("google/IFEval", "ifeval_input_data.jsonl", repo_type="dataset",
                revision="966cd89545d6b6acfd7638bc708b98261ca58e84"), raw / "ifeval_input_data.jsonl")
subprocess.run([sys.executable, str(root / "eval_src/make_splits.py"),
    "--strongreject-csv", str(raw / "strongreject_dataset.csv"),
    "--orbench-hard", str(raw / "or-bench-hard-1k.jsonl"),
    "--orbench-toxic", str(raw / "or-bench-toxic.jsonl"),
    "--ifeval", str(raw / "ifeval_input_data.jsonl"),
    "--cb-train", str(raw / "circuit_breakers_train.json"),
    "--out-dev", str(root / "assets/dev_split.jsonl"),
    "--out-test", str(root / "assets/test_split.jsonl"),
    "--out-dedup", str(root / "assets/train_dedup_report.json")], check=True)
assert hashlib.sha256((root / "assets/test_split.jsonl").read_bytes()).hexdigest() == "171e6c1adcd89806ea3c4435d1c8396eb63d121652886631b965204294c53376", "Official test split did not reproduce"
shutil.copyfile(root / "assets/dev_split.jsonl", work / "data/dev_split.jsonl")
rows = json.loads((raw / "circuit_breakers_train.json").read_text())
with (work / "data/refusal_safety.jsonl").open("w") as output:
    for row in rows:
        output.write(json.dumps({"prompt": row["prompt"], "refusal": row.get("llama3_output", ""),
            "harmful_completion": row.get("output", ""), "category": row.get("category", "")}) + "\n")
ds = load_dataset("tatsu-lab/alpaca", revision="dce01c9b08f87459cf36a430d809084718273017", split="train")
with (work / "data/benign_retain.jsonl").open("w") as output:
    for row in ds.select(range(min(3000, len(ds)))):
        output.write(json.dumps({"instruction": row["instruction"], "input": row.get("input", ""), "output": row["output"]}) + "\n")
print("JAILBREAK DATA READY; frozen test split hash matches")
