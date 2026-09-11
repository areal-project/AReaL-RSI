"""Pack fixed pool tokens and execute the unmodified upstream trainer."""
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import numpy as np

from bench import validate_selection, write_json

BUDGET = 12_000_000


def pack(selection, tokens, meta, budget=BUDGET):
    validate_selection(selection)
    available = sum(int(meta[i, 1]) + 1 for i in selection)
    if available < budget:
        raise ValueError(f"selection yields only {available} tokens; requires {budget}")
    packed = np.empty(budget, dtype=np.uint16)
    cursor = 0
    used = 0
    for i in selection:
        start, length = int(meta[i, 0]), int(meta[i, 1]) + 1
        take = min(length, budget - cursor)
        packed[cursor:cursor + take] = tokens[start:start + take]
        cursor += take
        used += 1
        if cursor == budget:
            break
    return packed, used


def run_experiment(run):
    root = Path(os.environ["RSI_ROOT"])
    config = json.loads((run / "experiment.json").read_text())
    started = time.time()

    def state(value, **extra):
        write_json(run / "state.json", {"state": value, "updated_at": time.time(), **extra})

    def terminated(signum, frame):
        raise RuntimeError(f"experiment interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, terminated)
    signal.signal(signal.SIGINT, terminated)
    try:
        state("RUNNING", phase="packing")
        payload = (run / "selection.json").read_bytes()
        if hashlib.sha256(payload).hexdigest() != config["selection_sha256"]:
            raise ValueError("selection snapshot changed after submission")
        selection = json.loads(payload)
        pool = root / "pool"
        # Hash exactly the same pool assets as the official verifier.
        for row in (root / "source/tasks/nano-gpt-data-curation/tests/SHA256SUMS").read_text().splitlines():
            digest, name = row.split()
            if name.startswith("pool_"):
                with (pool / name).open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                        raise ValueError(f"pool hash mismatch: {name}")
        tokens = np.load(pool / "pool_tokens.npy", mmap_mode="r")
        meta = np.load(pool / "pool_meta.npy", mmap_mode="r")
        packed, used = pack(selection, tokens, meta)
        train = run / "train.npy"
        np.save(train, packed)
        packing_s = time.time() - started
        print(f"PACKED {BUDGET} tokens from {used} documents in {packing_s:.2f}s", flush=True)
        heldout = root / "workspace/data/multi_dev.npy"
        if config["mode"] == "smoke":
            heldout = run / "smoke-dev.npy"
            np.save(heldout, np.load(root / "workspace/data/multi_dev.npy", mmap_mode="r")[:1025])
        trainer = root / "source/tasks/nano-gpt-data-curation/environment/workspace/train_nano.py"
        command = [sys.executable, "-u", str(trainer), "--train_npy", str(train),
                   "--heldout_npy", str(heldout), "--out_json", str(run / "training-result.json"),
                   "--max_iters", str(config["max_iters"]), "--seed", "1337"]
        state("RUNNING", phase="training", max_iters=config["max_iters"], packing_s=round(packing_s, 2))
        print("TRAINING " + " ".join(command), flush=True)
        subprocess.run(command, check=True, cwd=trainer.parent)
        metrics = json.loads((run / "training-result.json").read_text())
        if not math.isfinite(metrics["heldout_ppl"]):
            raise RuntimeError("non-finite perplexity")
        metrics.update(mode=config["mode"], evaluation_split="dev" if config["mode"] == "dev" else "dev_smoke_slice",
                       official_score=False, token_count=BUDGET, consumed_documents=used,
                       packing_s=round(packing_s, 2), total_s=round(time.time() - started, 2),
                       selection_sha256=config["selection_sha256"])
        write_json(run / "result.json", metrics)
        state("SUCCEEDED")
        print("EXPERIMENT SUCCEEDED", flush=True)
    except BaseException as error:
        state("FAILED", error=str(error))
        raise


if __name__ == "__main__":
    run_experiment(Path(sys.argv[1]).resolve())
