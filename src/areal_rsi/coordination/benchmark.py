"""Full-benchmark admission and closeout policy, independent of research prompts."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen

from areal_rsi.api.research import CandidateSnapshot, VerifiedResult
from areal_rsi.base.storage import now


def read_baseline_source(source: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, dict):
        return source
    if not isinstance(source, str):
        raise ValueError("full_baseline_source must be a path, HTTP(S) URL or result object")
    url = urlsplit(source)
    if url.scheme in {"http", "https"}:
        if url.username or url.password:
            raise ValueError("Baseline URL must not contain credentials")
        with urlopen(source, timeout=30) as response:
            raw = response.read(10_000_001)
    else:
        with Path(source).open("rb") as stream:
            raw = stream.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValueError("Baseline result exceeds 10 MB")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Baseline must be a JSON evaluation result object")
    return value


def imported_baseline(
    raw: dict[str, Any],
    *,
    candidate: CandidateSnapshot,
    files: dict[str, str],
    metadata: dict[str, Any],
    contract_digest: str,
    metric: str,
) -> VerifiedResult:
    validate_full_benchmark(metadata)
    identity = metadata["verification_identity"]
    for key, expected in identity["phases"]["baseline"].items():
        if raw.get(key) != expected:
            raise ValueError(f"Imported baseline has missing or mismatched {key}")
    if raw.get("status") != "completed" or raw.get("simulated") is not False:
        raise ValueError("Imported baseline must be a completed, real evaluation")
    if raw.get("metric") != metric:
        raise ValueError("Imported baseline metric does not match this run")
    if identity["adapter"] in {"harness", "kernelbench"}:
        expected_harness = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        if raw.get("harness_id") != expected_harness:
            raise ValueError("Imported baseline does not evaluate this initial Harness")
    elif identity["adapter"] == "content":
        if raw.get("candidate_digest") != candidate.content.digest:
            raise ValueError("Imported baseline does not evaluate this initial candidate")
    else:
        raise ValueError("Unsupported baseline identity adapter")
    score, metrics, passed = raw.get("score"), raw.get("metrics", {}), raw.get("passed")
    if not isinstance(metrics, dict) or not isinstance(passed, bool):
        raise ValueError("Imported baseline requires metrics and passed fields")
    for number in [score, *metrics.values()]:
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(number)
        ):
            raise ValueError("Imported baseline scores must be finite numbers")
    if metric in metrics and metrics[metric] != score:
        raise ValueError("Imported baseline score disagrees with its metric")
    return VerifiedResult(
        "imported-full-baseline",
        candidate.candidate_id,
        contract_digest,
        now(),
        "succeeded",
        score=score,
        passed=passed,
        metrics=metrics,
        phase="baseline",
    )


def validate_full_benchmark(metadata: dict[str, Any]) -> None:
    if not metadata.get("full_benchmark_evaluation"):
        return
    phases = metadata.get("verification_identity", {}).get("phases", {})
    baseline, final = phases.get("baseline"), phases.get("final")
    if not isinstance(baseline, dict) or not isinstance(final, dict) or baseline != final:
        raise ValueError(
            "Full baseline and final must pin the same benchmark identity and coverage"
        )
    tasks = baseline.get("task_ids")
    if (
        not isinstance(tasks, list)
        or not tasks
        or any(not isinstance(task, str) or not task for task in tasks)
        or len(set(tasks)) != len(tasks)
        or not baseline.get("manifest_id")
        or not baseline.get("protocol_id")
    ):
        raise ValueError("Full benchmark requires a nonempty, unique ordered task_ids manifest")


def baseline_pending(state: dict[str, Any]) -> bool:
    return bool(state.get("full_benchmark_evaluation") and not state.get("full_baseline_ready"))
