"""Short synthetic training check; the result is NOT a benchmark score."""
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
import torch
from transformers import AutoTokenizer

root = Path(os.environ["RSI_ROOT"])
versions = {p: importlib.metadata.version(p) for p in
            ("torch", "numpy", "transformers", "pyarrow", "tokenizers", "huggingface_hub")}
print(versions, flush=True)
assert torch.cuda.is_available(), "CUDA unavailable"
assert torch.cuda.is_bf16_supported(), "BF16 unavailable"
print(torch.cuda.get_device_name(0), flush=True)
print(f"CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES')}; visible GPUs={torch.cuda.device_count()}", flush=True)
for revision in ("607a30d783dfa663caf39e06633721c8d4cfcd7e", "main"):
    assert AutoTokenizer.from_pretrained("gpt2", revision=revision, local_files_only=True).encode("hello")
with tempfile.TemporaryDirectory(prefix="rsi-smoke-") as tmp:
    train = Path(tmp) / "train.npy"
    heldout = Path(tmp) / "heldout.npy"
    rng = np.random.default_rng(1337)
    np.save(train, rng.integers(0, 50257, 8192, dtype=np.uint16))
    np.save(heldout, rng.integers(0, 50257, 1025, dtype=np.uint16))
    result = root / "logs/smoke-result.json"
    workspace = root / "workspace"
    subprocess.run([sys.executable, str(workspace / "train_nano.py"), "--train_npy", str(train),
                    "--heldout_npy", str(heldout), "--out_json", str(result),
                    "--max_iters", "2"], check=True, cwd=workspace)
    output = json.loads(result.read_text())
    assert math.isfinite(output["heldout_ppl"])
    output.update(purpose="synthetic smoke test; not a benchmark score", versions=versions,
                  gpu=torch.cuda.get_device_name(0))
    result.write_text(json.dumps(output, indent=2) + "\n")
print("SMOKE PASS", flush=True)
