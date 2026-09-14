from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from areal_rsi.api.research import TaskBrief, VerificationContract
from areal_rsi.base.storage import write_json
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.verification import read_trusted_result
from areal_rsi.execution import JobRecord, JobSpec


@pytest.fixture
def adapter() -> Any:
    path = Path(__file__).parents[1] / "examples/kernelbench_artifacts/verify_existing.py"
    spec = importlib.util.spec_from_file_location("existing_gpuq", path)
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def formal(adapter: Any, workspace: Path, identifier: str, order: int = 1) -> dict[str, Any]:
    return {
        "id": identifier,
        "kind": "eval",
        "state": "succeeded",
        "created_at": order,
        "source_sha256": adapter.sha(workspace / "model_new.py"),
        "evaluation": {
            "correct": True,
            "compiled": True,
            "numerical_correct": True,
            "source_sha256": adapter.sha(workspace / "model_new.py"),
            "evaluation_policy": "strict-fp32-v1",
            "reference_precision": adapter.PRECISION,
            "seed": 42,
            "num_correct_trials": 5,
            "num_perf_trials": 100,
            "metadata": {"correctness_trials": "(5 / 5)"},
            "speedup": 2.0,
            "runtime_ms": 5.0,
            "ref_ms": 10.0,
        },
    }


def test_final_needs_distinct_formal_repeats_and_uses_latest_result(
    adapter: Any, tmp_path: Path
) -> None:
    (tmp_path / "model_new.py").write_text("raise RuntimeError('Do not import')")
    first = formal(adapter, tmp_path, "first")
    manifest = {"seed": 42}
    assert adapter.score(manifest, tmp_path, [first], "search")[:2] == (2.0, True)
    assert adapter.score(manifest, tmp_path, [first], "final")[:2] == (0.0, False)
    diagnostic = {**first, "id": "diagnostic", "kind": "run", "created_at": 2}
    assert adapter.score(manifest, tmp_path, [first, diagnostic], "final")[:2] == (0.0, False)
    repeat = formal(adapter, tmp_path, "repeat", 3)
    assert adapter.score(manifest, tmp_path, [repeat, first], "final")[:2] == (2.0, True)
    with pytest.raises(ValueError, match="distinct"):
        adapter.score(manifest, tmp_path, [first, first], "final")
    failed = {**repeat, "id": "failed", "state": "failed", "created_at": 4}
    assert adapter.score(manifest, tmp_path, [first, repeat, failed], "final")[:2] == (0.0, False)
    assert adapter.score(manifest, tmp_path, [first, repeat, failed], "search")[:2] == (2.0, True)
    (tmp_path / "model_new.py").write_text("new untested implementation")
    assert adapter.score(manifest, tmp_path, [first, repeat], "final")[:2] == (0.0, False)


@pytest.mark.parametrize(
    "change",
    [
        {"reference_precision": {}},
        {"evaluation_policy": "smoke"},
        {"source_sha256": "wrong"},
        {"seed": 7},
        {"num_correct_trials": 1},
        {"num_perf_trials": 1},
        {"metadata": {"correctness_trials": "(4 / 5)"}},
        {"speedup": float("nan")},
        {"speedup": True},
        {"runtime_ms": 0},
        {"speedup": 30},
        {"correct": False},
    ],
)
def test_invalid_or_incomparable_results_are_not_scores(
    adapter: Any, tmp_path: Path, change: dict[str, Any]
) -> None:
    (tmp_path / "model_new.py").write_text("candidate")
    job = formal(adapter, tmp_path, "one")
    job["evaluation"].update(change)
    assert adapter.score({"seed": 42}, tmp_path, [job], "search")[:2] == (0.0, False)


@pytest.mark.parametrize("speedup,passed", [(0.9, False), (1.0, False), (1.01, True)])
def test_final_enforces_strict_fast1_even_when_brief_target_is_null(
    adapter: Any, tmp_path: Path, speedup: float, passed: bool
) -> None:
    (tmp_path / "model_new.py").write_text("candidate")
    jobs = [formal(adapter, tmp_path, str(i), i) for i in range(2)]
    for job in jobs:
        job["evaluation"].update(speedup=speedup, ref_ms=speedup * 5)
    assert adapter.score({"seed": 42}, tmp_path, jobs, "final")[:2] == (speedup, passed)


def test_queue_reader_only_gets_existing_jobs_and_checks_frozen_dispatch(
    adapter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "job/workspace"
    workspace.mkdir(parents=True)
    (workspace / "model_new.py").write_text("candidate")
    worker = tmp_path / "kb_worker.py"
    worker.write_text("never execute the worker")
    token = tmp_path / "token"
    token.write_text("test-only")
    manifest = {
        "kb_root": str(tmp_path),
        "worker_hashes": {"kb_worker.py": adapter.sha(worker)},
        "token_file": str(token),
        "gpuq_url": "http://example",
        "task_id": "task-original",
        "budget": 200,
        "python": "/python",
        "level": 3,
        "problem": 10,
        "seed": 42,
    }
    spec_path = workspace.parent / "evaluation-spec.json"
    output = workspace.parent / "evaluation.json"
    spec = {
        "level": 3,
        "problem": 10,
        "seed": 42,
        "mode": "eval",
        "candidate": str(workspace / "model_new.py"),
        "output": str(output),
    }
    spec_path.write_text(json.dumps(spec))
    job = formal(adapter, workspace, "formal")
    job.update(
        task_id="task-original",
        budget=200,
        budget_used=55,
        cwd=str(workspace),
        argv=["/python", "-u", str(worker), str(spec_path)],
    )
    output.write_text(json.dumps(job["evaluation"]))
    calls = []

    def request(path: str, **kwargs: Any) -> Any:
        calls.append(path)
        return [copy.deepcopy(job)]

    monkeypatch.setitem(sys.modules, "common", SimpleNamespace(request=request))
    actual = adapter.read_queue(manifest)
    assert calls == ["/jobs"]  # No body, registration, submission or eval execution.
    assert actual[0]["source_sha256"] == adapter.sha(workspace / "model_new.py")
    assert actual[0]["evaluation"]["reference_precision"] == adapter.PRECISION
    job["argv"] = ["/python", "custom.py"]
    with pytest.raises(ValueError, match="pinned worker"):
        adapter.read_queue(manifest)
    job["task_id"] = "another-task"
    with pytest.raises(ValueError, match="ownership"):
        adapter.read_queue(manifest)
    worker.write_text("changed protocol")
    with pytest.raises(ValueError, match="worker changed"):
        adapter.read_queue(manifest)


def test_adapter_output_passes_real_workbench_identity_and_input_checks(
    adapter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        c = Coordinator.create(
            tmp_path,
            TaskBrief("Improve", "Require fast1", verification=VerificationContract("speedup")),
        )
        source = tmp_path / "source"
        source.mkdir()
        (source / "model_new.py").write_text("raise RuntimeError('Never import me')")
        candidate = await c.store.snapshot(source, agent_id="operator", description="candidate")
        workspace = c.store.root / "execution-inputs/job-existing/workspace"
        write_json(workspace.parent / "snapshot.json", candidate.content)
        await c.store.content.materialize(candidate.content, workspace)
        metadata = {
            "contract_digest": c.store.contract_digest(),
            "candidate_id": candidate.candidate_id,
            "candidate_digest": candidate.content.digest,
            "phase": "final",
        }
        job = JobRecord(
            "job-existing",
            JobSpec(("read-only-verifier",), cwd=str(workspace), metadata=metadata),
            "succeeded",
            1,
            2,
        )
        write_json(c.store.root / "jobs/job-existing/job.json", job)
        jobs = [formal(adapter, workspace, str(i), i) for i in range(2)]
        monkeypatch.setattr(adapter, "query", lambda _: jobs)
        identity = {"protocol_id": "fixed-strict-worker", "manifest_id": "existing-task"}
        manifest = {**identity, "seed": 42, "metric": "speedup"}
        output = c.store.root / "execution-outputs/job-existing"
        adapter.verify(manifest, workspace, output, "final")
        verified = read_trusted_result(
            c.store,
            job,
            candidate,
            "final",
            identity={"adapter": "content", "phases": {"final": identity}},
        )
        assert verified.score == 2 and verified.passed and not verified.simulated
        assert verified.candidate_id == candidate.candidate_id
        assert (
            hashlib.sha256((workspace / "model_new.py").read_bytes()).hexdigest()
            == jobs[0]["source_sha256"]
        )

    asyncio.run(run())
