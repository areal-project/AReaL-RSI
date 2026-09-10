"""CPU-only research fixture: actual cache hits, not a table of scripted scores.

This evaluator is test infrastructure, outside the agent's editable artifact.
It is not a security sandbox; use an isolated backend for untrusted candidates.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
from pathlib import Path


def workloads(seed: int):
    rng = random.Random(seed)
    n = 6000
    yield (
        "stable_hotset",
        [rng.randrange(24) if rng.random() < 0.85 else rng.randrange(4096) for _ in range(n)],
    )
    yield (
        "scans",
        [
            key
            for block in range(10)
            for key in (
                [rng.randrange(40) for _ in range(400)]
                + list(range(1000 + block * 200, 1200 + block * 200))
            )
        ],
    )
    yield (
        "changing_hotset",
        [
            (i // 1500) * 500 + (rng.randrange(32) if rng.random() < 0.9 else rng.randrange(400))
            for i in range(n)
        ],
    )
    yield "cyclic", [i % 80 for i in range(n)]
    yield "bursts", [key for _ in range(300) for key in [rng.randrange(200)] * 20]
    yield "mixed", [rng.randrange(48) if i % 1000 < 600 else 1000 + i % 96 for i in range(n)]


def evaluate(workspace: Path, output: Path, seed: int = 41871):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("candidate_policy", workspace / "policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metrics = {}
    errors = []
    for name, trace in workloads(seed):
        cache = set()
        policy = module.Policy(64)
        hits = 0
        try:
            for key in trace:
                hit = key in cache
                victim = policy.access(key, tuple(sorted(cache)))
                if hit:
                    hits += 1
                elif len(cache) < 64:
                    cache.add(key)
                elif victim == key:
                    pass  # Admission policy may bypass a miss.
                elif victim in cache:
                    cache.remove(victim)
                    cache.add(key)
                else:
                    raise ValueError(
                        "A full cache miss must evict a cached key or bypass the incoming key"
                    )
            metrics[name] = hits / len(trace)
        except Exception as exc:
            metrics[name] = 0
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    result = {
        "score": sum(metrics.values()) / len(metrics),
        "metrics": metrics,
        "passed": not errors,
        "errors": errors,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "result.json").write_text(json.dumps(result))
    (output / "public").mkdir(exist_ok=True)
    (output / "public" / "feedback.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, default=41871)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.workspace, args.output, args.seed)))
