from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.research import CandidateSnapshot, TaskBrief, VerificationContract
from areal_rsi.base.storage import write_json
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.store import ResearchStore
from areal_rsi.coordination.verification import read_trusted_result
from areal_rsi.execution import JobRecord, JobSpec


def fixture(
    tmp_path: Path, adapter: str = "content", phase: str = "search"
) -> tuple[ResearchStore, JobRecord, CandidateSnapshot, dict[str, Any], dict[str, Any], Path]:
    async def build() -> tuple[ResearchStore, CandidateSnapshot, Path]:
        c = Coordinator.create(
            tmp_path,
            TaskBrief(
                "Improve", "Use fixed validation", verification=VerificationContract("score")
            ),
        )
        source = tmp_path / "seed"
        source.mkdir()
        (source / "model.py").write_text("raise RuntimeError('Never import the candidate')")
        (source / "trained").mkdir()
        (source / "trained/checkpoint.pt").write_bytes(b"frozen weights")
        candidate = await c.store.snapshot(source, agent_id="operator", description="candidate")
        root = c.store.root / "execution-inputs" / "job-one"
        write_json(root / "snapshot.json", candidate.content)
        await c.store.content.materialize(candidate.content, root / "workspace")
        return c.store, candidate, root / "workspace"

    store, candidate, workspace = asyncio.run(build())
    job = JobRecord(
        "job-one",
        JobSpec(
            ("operator-verifier",),
            cwd=str(workspace),
            metadata={
                "contract_digest": store.contract_digest(),
                "candidate_id": candidate.candidate_id,
                "candidate_digest": candidate.content.digest,
                "phase": phase,
            },
        ),
        "succeeded",
        1,
        2,
    )
    fields = {
        "protocol_id": "protocol-" + phase,
        "manifest_id": "manifest-" + phase,
        "target_model": "fixed-target",
        "prepared_protocol_id": "prepared",
        "split": phase,
    }
    identity = {"adapter": adapter, "phases": {phase: fields}}
    result: dict[str, Any] = {
        **fields,
        "status": "completed",
        "simulated": False,
        "phase": phase,
        "metric": "score",
        "score": 0.7,
        "metrics": {"score": 0.7},
        "candidate_id": candidate.candidate_id,
        "candidate_digest": candidate.content.digest,
    }
    files = {
        p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(workspace.rglob("*"))
        if p.is_file()
    }
    result["harness_id"] = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    result["candidate_sha256"] = hashlib.sha256(
        json.dumps(
            [{"path": path, "sha256": digest} for path, digest in files.items()], sort_keys=True
        ).encode()
    ).hexdigest()
    result["checkpoint_sha256"] = files["trained/checkpoint.pt"]
    output = store.root / "execution-outputs" / job.job_id / "evaluation.json"
    write_json(output, result)
    return store, job, candidate, identity, result, output


@pytest.mark.parametrize("adapter", ["content", "kernelbench", "cuhkx"])
@pytest.mark.parametrize("phase", ["search", "final"])
def test_real_result_binds_frozen_candidate_contract_and_phase(
    tmp_path: Path, adapter: str, phase: str
) -> None:
    store, job, candidate, identity, _result, _output = fixture(tmp_path, adapter, phase)
    verified = read_trusted_result(store, job, candidate, phase, identity=identity)
    assert verified.score == 0.7 and verified.phase == phase
    assert verified.contract_digest == job.spec.metadata["contract_digest"]
    assert verified.candidate_id == candidate.candidate_id and not verified.simulated


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("phase", "final"),
        ("metric", "another-score"),
        ("simulated", True),
        ("simulated", 0),
        ("protocol_id", "unknown-protocol"),
        ("manifest_id", "another-task-pool"),
        ("target_model", "stronger-target"),
        ("candidate_id", "another-candidate"),
        ("candidate_digest", "sha256:other"),
        ("passed", "false"),
        ("score", True),
        ("score", float("inf")),
        ("metrics", {"score": 0.8}),
        ("metrics", {"count": True}),
    ],
)
def test_misbound_or_invalid_result_cannot_become_best(
    tmp_path: Path, field: str, value: Any
) -> None:
    store, job, candidate, identity, result, output = fixture(tmp_path)
    result[field] = value
    output.write_text(json.dumps(result))
    with pytest.raises(ValueError):
        read_trusted_result(store, job, candidate, "search", identity=identity)
    assert store.run().best_result_ref is None


@pytest.mark.parametrize("field", ["contract_digest", "candidate_id", "candidate_digest", "phase"])
def test_job_must_pin_identity_before_execution(tmp_path: Path, field: str) -> None:
    store, job, candidate, identity, _result, _output = fixture(tmp_path)
    metadata = {key: value for key, value in job.spec.metadata.items() if key != field}
    job = replace(job, spec=replace(job.spec, metadata=metadata))
    with pytest.raises(ValueError, match="frozen"):
        read_trusted_result(store, job, candidate, "search", identity=identity)


@pytest.mark.parametrize(
    "adapter,field", [("kernelbench", "harness_id"), ("cuhkx", "checkpoint_sha256")]
)
def test_artifact_identity_is_computed_from_frozen_input(
    tmp_path: Path, adapter: str, field: str
) -> None:
    store, job, candidate, identity, result, output = fixture(tmp_path, adapter)
    result[field] = "different-product"
    write_json(output, result)
    with pytest.raises(ValueError, match="different"):
        read_trusted_result(store, job, candidate, "search", identity=identity)


def test_modified_execution_input_or_unpinned_protocol_is_rejected(tmp_path: Path) -> None:
    store, job, candidate, identity, _result, _output = fixture(tmp_path)
    (Path(job.spec.cwd) / "model.py").write_text("modified after freeze")
    with pytest.raises(ValueError, match="frozen candidate"):
        read_trusted_result(store, job, candidate, "search", identity=identity)
    del identity["phases"]["search"]["manifest_id"]
    with pytest.raises(ValueError, match="pin all"):
        read_trusted_result(store, job, candidate, "search", identity=identity)


def test_incomplete_cancelled_and_mock_jobs_never_produce_real_score(tmp_path: Path) -> None:
    store, job, candidate, identity, result, output = fixture(tmp_path)
    result.update(status="incomplete", score=None, error="CUDA unavailable")
    write_json(output, result)
    verified = read_trusted_result(store, job, candidate, "search", identity=identity)
    assert verified.status == "infra_error" and verified.score is None
    output.unlink()
    cancelled = replace(job, state="cancelled")
    verified = read_trusted_result(store, cancelled, candidate, "search", identity=identity)
    assert verified.status == "cancelled" and verified.score is None
    mock = replace(job, spec=replace(job.spec, backend="mock"))
    with pytest.raises(ValueError, match="Simulated"):
        read_trusted_result(store, mock, candidate, "search", identity=identity)
