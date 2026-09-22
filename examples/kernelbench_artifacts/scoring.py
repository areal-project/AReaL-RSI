"""Task-owned search scoring. The RSI coordinator only receives ordinary scores."""

from __future__ import annotations

import math
from statistics import median
from typing import Any

METHOD = "fixed-reference-v1"
HARDWARE_FIELDS = ("driver_name", "capability", "memory_bytes", "torch", "triton")


def calibration(jobs: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any] | None:
    """Freeze a reference only after independent batches agree, before exploration."""
    samples = []
    hardware = None
    for job in jobs:
        value = job.get("evaluation") or {}
        mean = value.get("ref_ms", 0)
        std = value.get("ref_stats", {}).get("std", float("inf"))
        identity = {key: value.get("hardware", {}).get(key) for key in HARDWARE_FIELDS}
        if (
            job["state"] != "succeeded"
            or value.get("correct") is not True
            or value.get("evaluation_policy") != "strict-fp32-v1"
            or not math.isfinite(mean)
            or mean <= 0
            or not math.isfinite(std)
            or std < 0
            or std / mean > policy["max_relative_std"]
        ):
            continue
        if hardware is not None and identity != hardware:
            raise ValueError("Reference 校准使用了不同的硬件或软件环境")
        hardware = identity
        samples.append({"job_id": job["id"], "ref_ms": mean, "ref_std_ms": std})
    count = policy["calibration_runs"]
    if len(samples) < count:
        return None
    samples = samples[-count:]
    fixed = median(sample["ref_ms"] for sample in samples)
    spread = max(sample["ref_ms"] for sample in samples) - min(s["ref_ms"] for s in samples)
    if spread / fixed > policy["max_relative_spread"]:
        return None
    used = {sample["job_id"] for sample in samples}
    return {
        "method": METHOD,
        "ref_ms": fixed,
        "hardware": hardware,
        "samples": samples,
        "discarded_job_ids": [job["id"] for job in jobs if job["id"] not in used],
    }


def evaluation(manifest: dict[str, Any], task: str, value: dict[str, Any]) -> dict[str, Any]:
    """Normalize search feedback; preserve the evaluator's measurements for auditing.

    Final verification and historical manifests retain their original score.
    Re-projecting already normalized feedback is harmless.
    """
    policy = manifest.get("search_scoring")
    if not policy or value.get("seed") != manifest["exploration_seed"]:
        return value
    if policy["method"] != METHOD:
        raise ValueError("Unknown search scoring policy")
    fixed = manifest["tasks"][task]["reference_calibration"]
    identity = {key: value.get("hardware", {}).get(key) for key in HARDWARE_FIELDS}
    if identity != fixed["hardware"]:
        raise ValueError("评测环境与固定 reference 的校准环境不同，不能比较成绩")
    runtime = value.get("runtime_ms", 0)
    passed = value.get("correct") is True and math.isfinite(runtime) and runtime > 0
    result = {
        **value,
        "scoring_method": METHOD,
        "speedup": fixed["ref_ms"] / runtime if passed else 0.0,
        "ref_ms": fixed["ref_ms"],
        "ref_us": fixed["ref_ms"] * 1000,
        "measured_ref_ms": value.get("measured_ref_ms", value.get("ref_ms")),
        "measured_speedup": value.get("measured_speedup", value.get("speedup")),
        "measured_ref_stats": value.get("measured_ref_stats", value.get("ref_stats", {})),
    }
    result.pop("ref_stats", None)  # Per-job dispersion is not the fixed reference's dispersion.
    return result
