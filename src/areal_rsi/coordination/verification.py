"""Read trusted verifier output only after binding it to its frozen execution.

The operator supplies ``identity`` from config.metadata["verification_identity"]::

    {"adapter": "kernelbench", "phases": {
        "search": {"protocol_id": "...", "manifest_id": "...",
                   "target_model": "fixed-target", "split": "search"},
        "final": {"protocol_id": "...", "manifest_id": "...",
                  "target_model": "fixed-target", "split": "final"}}}

The generic ``harness`` adapter binds the frozen candidate's file hashes to
``harness_id`` and requires protocol_id and manifest_id. It supports arbitrary
finite metric ranges, including signed improvement scores.

The generic ``content`` adapter requires protocol_id and manifest_id;
its wrapper must echo candidate_id and candidate_digest (the CAS directory
digest). Additional configured identity fields are compared exactly.

The job owner freezes contract_digest, candidate_id, candidate_digest and phase
in JobSpec.metadata before launching the verifier. This reader never infers a
protocol from the result or relabels simulated results as real measurements.
"""

from __future__ import annotations

import hashlib
import json
import math
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from areal_rsi.api.research import CandidateSnapshot, VerifiedResult
from areal_rsi.base.storage import DIRECTORY_TYPE, encode, now, safe_name, safe_relative
from areal_rsi.coordination.store import ResearchStore
from areal_rsi.execution import JobRecord

IDENTITY_FIELDS = {
    "kernelbench": {"protocol_id", "manifest_id", "target_model", "split"},
    "harness": {"protocol_id", "manifest_id"},
    "content": {"protocol_id", "manifest_id"},
}


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, *, max_bytes: int = 1_000_000) -> dict[str, Any]:
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Verifier records must be regular files")
    if path.stat().st_size > max_bytes:
        raise ValueError("Verifier record exceeds its size limit")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field in verifier record")
            result[key] = value
        return result

    value = json.loads(path.read_text(), object_pairs_hook=object_pairs)
    if not isinstance(value, dict):
        raise ValueError("Verifier record must be a JSON object")
    return value


def _frozen_files(
    store: ResearchStore, job: JobRecord, candidate: CandidateSnapshot
) -> tuple[Path, dict[str, str]]:
    root = store.root / "execution-inputs" / safe_name(job.job_id)
    workspace = root / "workspace"
    if root.is_symlink() or workspace.is_symlink():
        raise ValueError("Verifier inputs must not be symbolic links")
    if Path(job.spec.cwd).resolve() != workspace.resolve():
        raise ValueError("Verifier job does not use its assigned immutable input directory")
    if _json(root / "snapshot.json") != encode(candidate.content):
        raise ValueError("Verifier input snapshot belongs to a different candidate")
    if candidate.content.media_type != DIRECTORY_TYPE:
        raise ValueError("Verification requires a directory snapshot")
    digest = candidate.content.digest.removeprefix("sha256:")
    if (
        len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
        or candidate.content.uri != "content://" + digest
    ):
        raise ValueError("Invalid candidate content identity")
    manifest_path = store.content.root / digest
    if _sha_file(manifest_path) != digest:
        raise ValueError("Candidate content manifest was modified")
    manifest = _json(manifest_path, max_bytes=10_000_000)
    expected = {
        safe_relative(name).as_posix(): ref["digest"].removeprefix("sha256:")
        for name, ref in manifest["files"].items()
    }
    actual: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if path.is_symlink():
            raise ValueError("Verifier input contains a symbolic link")
        if path.is_dir():
            continue
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("Verifier input contains a non-regular file")
        actual[path.relative_to(workspace).as_posix()] = _sha_file(path)
    if actual != expected:
        raise ValueError("Verifier input files differ from the frozen candidate")
    return workspace, actual


def _candidate_identity(
    adapter: str, result: dict[str, Any], candidate: CandidateSnapshot, files: dict[str, str]
) -> None:
    if adapter in {"kernelbench", "harness"}:
        digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        if result.get("harness_id") != digest:
            raise ValueError("Verifier evaluated a different harness")
    elif adapter == "content":
        if (
            result.get("candidate_id") != candidate.candidate_id
            or result.get("candidate_digest") != candidate.content.digest
        ):
            raise ValueError("Verifier evaluated a different candidate")
    else:
        raise ValueError("Unknown verification identity adapter")


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Verifier {name} must be a finite number")
    return float(value)


def read_trusted_result(
    store: ResearchStore,
    job: JobRecord,
    candidate: CandidateSnapshot,
    phase: str,
    *,
    identity: Mapping[str, Any],
) -> VerifiedResult:
    """Validate a real, operator-owned result; raise on ambiguous/mismatched input.

    No candidate module is imported and neither process stdout nor agent reports
    are read. The caller can record a reader exception as an infrastructure error;
    it must never turn such an error into a comparable numeric score.
    """
    if phase not in {"search", "final", "diagnostic", "baseline"}:
        raise ValueError("Unknown verification phase")
    if job.spec.backend == "mock":
        raise ValueError("Simulated execution cannot produce real verification")
    if not job.terminal:
        raise ValueError("Verification execution has not completed")
    if store.get("candidates", candidate.candidate_id, CandidateSnapshot) != candidate:
        raise ValueError("Candidate does not match its immutable run record")
    contract = store.brief().verification
    if contract is None:
        raise ValueError("The run has no verification contract")
    frozen = job.spec.metadata
    for field, expected in {
        "contract_digest": store.contract_digest(),
        "candidate_id": candidate.candidate_id,
        "candidate_digest": candidate.content.digest,
        "phase": phase,
    }.items():
        if frozen.get(field) != expected:
            raise ValueError(f"Verifier job has a missing or mismatched frozen {field}")
    kwargs: dict[str, Any] = {
        "result_id": job.job_id,
        "candidate_id": candidate.candidate_id,
        "contract_digest": frozen["contract_digest"],
        "created_at": now(),
        "phase": cast(Literal["search", "final", "diagnostic", "baseline"], phase),
        "simulated": False,
    }
    if job.state != "succeeded":
        return VerifiedResult(
            **kwargs,
            status="cancelled" if job.state == "cancelled" else "infra_error",
            error=job.error or job.state,
        )
    output = store.root / "execution-outputs" / safe_name(job.job_id)
    if output.is_symlink() or output.parent.is_symlink():
        raise ValueError("Trusted output directory must not be a symbolic link")
    result = _json(output / "evaluation.json")
    if result.get("simulated") is not False:
        raise ValueError("Real verifier must explicitly declare simulated=false")
    if result.get("status") != "completed":
        return VerifiedResult(
            **kwargs,
            status="infra_error",
            error=str(result.get("error", "Incomplete verification")),
        )
    if result.get("phase") != phase or result.get("metric") != contract.metric:
        raise ValueError("Verifier reported a different phase or metric")
    adapter = identity.get("adapter")
    required = IDENTITY_FIELDS
    if adapter not in required:
        raise ValueError("Configure an explicit verification identity adapter")
    phases = identity.get("phases", {})
    expected_identity = phases.get(phase) if isinstance(phases, Mapping) else None
    if not isinstance(expected_identity, Mapping) or not required[adapter] <= set(
        expected_identity
    ):
        raise ValueError("Operator must pin all comparison identities for this phase")
    for field, expected in expected_identity.items():
        if expected is None or expected == "" or result.get(field) != expected:
            raise ValueError(f"Verifier reported a different or unpinned {field}")
    _workspace, files = _frozen_files(store, job, candidate)
    _candidate_identity(adapter, result, candidate, files)
    passed = result.get("passed", True)
    if not isinstance(passed, bool):
        raise ValueError("Verifier passed must be a boolean")
    score = _number(result.get("score"), "score")
    raw_metrics = result.get("metrics", {})
    if not isinstance(raw_metrics, dict):
        raise ValueError("Verifier metrics must be an object")
    metrics = {key: _number(value, "metric " + key) for key, value in raw_metrics.items()}
    if contract.metric in metrics and metrics[contract.metric] != score:
        raise ValueError("Verifier score disagrees with its primary metric")
    if adapter == "kernelbench" and not 0 <= score <= 1:
        raise ValueError("Example accuracy/fast_1 score must be between zero and one")
    return VerifiedResult(**kwargs, status="succeeded", score=score, passed=passed, metrics=metrics)
