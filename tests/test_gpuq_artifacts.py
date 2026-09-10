from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def module(name: str) -> Any:
    path = Path(__file__).parents[1] / "examples/kernelbench_artifacts" / (name + ".py")
    spec = importlib.util.spec_from_file_location("gpuq_" + name, path)
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_bundle_scores_only_matching_real_code_and_requires_final_repeat(tmp_path: Path) -> None:
    client = module("client")
    workspace = tmp_path / "L1P1"
    workspace.mkdir()
    source = workspace / "model_new.py"
    source.write_text("candidate")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    job = {
        "kind": "eval",
        "state": "succeeded",
        "correct": True,
        "speedup": 2.0,
        "source_sha256": digest,
        "created_at": 1,
        "evaluation": {"evaluation_policy": "strict-fp32-v1"},
    }
    manifest: dict[str, Any] = {"tasks": {"L1P1": {}, "L2P14": {}}}
    status: dict[str, Any] = {"L1P1": {"used": 1, "jobs": [job]}, "L2P14": {"used": 0, "jobs": []}}
    assert client.score_bundle(manifest, tmp_path, status, "search")[0] == 0.5
    assert client.score_bundle(manifest, tmp_path, status, "final")[0] == 0
    status["L1P1"]["jobs"].append({**job, "created_at": 2})
    assert client.score_bundle(manifest, tmp_path, status, "final")[0] == 0.5
    status["L1P1"]["jobs"].append({**job, "created_at": 3, "correct": False, "state": "failed"})
    assert client.score_bundle(manifest, tmp_path, status, "final")[0] == 0
    source.write_text("different code")
    assert client.score_bundle(manifest, tmp_path, status, "search")[0] == 0


def test_remote_submission_recovery_does_not_consume_budget_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = module("remote")
    workspace = tmp_path / "task/workspace"
    workspace.mkdir(parents=True)
    (workspace.parent / "gpuq-token").write_text("test-only")
    jobs: list[dict[str, Any]] = []

    def request(path: str, body: Any = None, **kwargs: Any) -> Any:
        if body is None:
            return jobs
        value = {
            "id": "job1",
            "task_id": "task1",
            "kind": "eval",
            "state": "queued",
            "budget_used": 1,
            "budget": 11,
            "cwd": str(workspace),
            "idempotency_key": body["idempotency_key"],
        }
        jobs.append(value)
        return value

    monkeypatch.setitem(sys.modules, "common", SimpleNamespace(request=request))
    manifest = {
        "kb_root": str(tmp_path),
        "gpuq_url": "http://example",
        "tasks": {"L1P1": {"workspace": str(workspace), "task_id": "task1"}},
    }
    payload = {
        "operation": "submit",
        "task": "L1P1",
        "key": "one-intent",
        "source": base64.b64encode(b"candidate").decode(),
    }
    first = bridge.handle(manifest, payload)
    assert bridge.handle(manifest, payload) == first
    assert len(jobs) == 1
    with pytest.raises(ValueError, match="different code"):
        bridge.handle(manifest, {**payload, "source": base64.b64encode(b"changed").decode()})
    with pytest.raises(ValueError, match="outside"):
        bridge.handle(manifest, {**payload, "task": "L3P10"})


def test_exhausted_submission_returns_feedback_instead_of_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = module("client")
    (tmp_path / "model_new.py").write_text("candidate")
    calls = []

    def request(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        raise RuntimeError("GPUQ HTTP 400: GPU feedback budget exhausted (11/11)")

    monkeypatch.setattr(client, "remote", request)
    output = tmp_path / "output"
    client.evaluate({"tasks": {"L1P1": {}}}, tmp_path, output, "L1P1", "model_new.py")
    assert len(calls) == 1
    assert json.loads((output / "feedback.json").read_text())["budget_exhausted"] is True


@pytest.mark.parametrize("task", ["L3P10", "L3P36"])
def test_evaluation_feedback_identifies_the_submitted_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str
) -> None:
    client = module("client")
    (tmp_path / "model_new.py").write_text("candidate")
    job = {"id": "remote-job", "state": "failed", "budget_used": 2, "correct": False}

    def request(manifest: Any, operation: str, **kwargs: Any) -> Any:
        assert operation == "submit"
        assert kwargs["task"] == task
        return job

    monkeypatch.setattr(client, "remote", request)
    output = tmp_path / "output"
    client.evaluate({"tasks": {task: {}}}, tmp_path, output, task, "model_new.py")
    feedback = json.loads((output / "feedback.json").read_text())
    assert feedback == {**job, "task": task}
    assert json.loads((output / "remote-job.json").read_text()) == job


@pytest.fixture
def diagnostic_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    bridge = module("remote")
    workspace = tmp_path / "task/workspace"
    workspace.mkdir(parents=True)
    (workspace.parent / "gpuq-token").write_text("test-only")
    (workspace / "reference.py").write_text("public reference")
    jobs: list[dict[str, Any]] = []

    def request(path: str, body: Any = None, **kwargs: Any) -> Any:
        assert kwargs["token"] == "test-only"
        if body is None:
            if path == "/jobs":
                return jobs
            return next(j for j in jobs if path == "/jobs/" + j["id"])
        if len(jobs) >= 11:
            raise ValueError("GPU feedback budget exhausted (11/11)")
        folder = tmp_path / "gpu-jobs" / str(len(jobs) + 1)
        shutil.copytree(workspace, folder / "workspace")
        value = {
            "id": "gpu-" + str(len(jobs) + 1),
            "task_id": "task1",
            "kind": body["kind"],
            "state": "queued",
            "budget_used": len(jobs) + 1,
            "budget": 11,
            "created_at": len(jobs) + 1,
            "cwd": str(folder / "workspace"),
            "artifacts": str(folder),
            "stdout_path": str(folder / "stdout.log"),
            "stderr_path": str(folder / "stderr.log"),
            "idempotency_key": body["idempotency_key"],
            "submitted_body": body,
        }
        jobs.append(value)
        return value

    monkeypatch.setitem(sys.modules, "common", SimpleNamespace(request=request))
    manifest = {
        "kb_root": str(tmp_path),
        "gpuq_url": "http://example",
        "tasks": {"L1P1": {"workspace": str(workspace), "task_id": "task1"}},
    }
    return SimpleNamespace(bridge=bridge, manifest=manifest, workspace=workspace, jobs=jobs)


def diagnostic_payload(key: str = "diagnosis") -> dict[str, str]:
    return {
        "operation": "submit_diagnostic",
        "task": "L1P1",
        "key": key,
        "source": base64.b64encode(b"class ModelNew: pass").decode(),
        "script": base64.b64encode(b"print('layer comparison')").decode(),
    }


@pytest.mark.parametrize("external_count", [0, 1])
def test_old_job_receipt_exposes_current_task_budget_without_resubmitting(
    diagnostic_queue: Any, external_count: int
) -> None:
    q = diagnostic_queue
    first = q.bridge.handle(q.manifest, diagnostic_payload("first"))
    q.bridge.handle(q.manifest, diagnostic_payload("second"))
    if external_count:
        ledger = q.workspace.parent / "external-submissions"
        ledger.mkdir()
        (ledger / "outside.json").write_text(
            json.dumps({"event_id": "outside", "task_id": "task1", "count": 1})
        )
    receipt = q.bridge.handle(
        q.manifest, {"operation": "job", "task": "L1P1", "job_id": first["id"]}
    )
    assert receipt["queue_budget_used"] == 1
    assert receipt["budget_used"] == 1 + external_count
    assert receipt["task_budget_used"] == 2 + external_count
    assert receipt["task_budget_remaining"] == 9 - external_count
    recovered = q.bridge.handle(q.manifest, diagnostic_payload("first"))
    assert recovered["id"] == first["id"]
    assert recovered["task_budget_used"] == receipt["task_budget_used"]
    status = q.bridge.handle(q.manifest, {"operation": "status"})["L1P1"]
    assert all(j["task_budget_used"] == status["used"] for j in status["jobs"])
    assert len(q.jobs) == 2


def test_client_progress_uses_current_task_count_instead_of_old_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = module("client")
    (tmp_path / "model_new.py").write_text("candidate")
    job = {
        "id": "old-job",
        "state": "succeeded",
        "budget_used": 3,
        "task_budget_used": 4,
        "task_budget_remaining": 7,
    }
    monkeypatch.setattr(client, "remote", lambda *args, **kwargs: job)
    output = tmp_path / "output"
    client.evaluate({"tasks": {"L1P1": {}}}, tmp_path, output, "L1P1", "model_new.py")
    progress = json.loads((output / "progress.json").read_text())
    assert "本题已用 4/11" in progress["message"]
    assert "剩余 7 次" in progress["message"]
    assert "3/11" not in progress["message"]
    assert json.loads((output / "feedback.json").read_text())["budget_used"] == 3


def test_collection_excludes_reviewed_code_but_keeps_history(diagnostic_queue: Any) -> None:
    q = diagnostic_queue
    for index, (source, speedup) in enumerate(((b"valid candidate", 1.2), (b"shortcut", 9.0))):
        q.bridge.handle(
            q.manifest,
            {
                "operation": "submit",
                "task": "L1P1",
                "key": str(index),
                "source": base64.b64encode(source).decode(),
            },
        )
        q.jobs[-1].update(state="succeeded", evaluation={"correct": True, "speedup": speedup})
    digest = hashlib.sha256(b"shortcut").hexdigest()
    assert q.bridge.handle(q.manifest, {"operation": "collect"})["L1P1"]["best"]["speedup"] == 9
    # Working files may now belong to another research direction.
    (q.workspace / "model_new.py").write_text("another task entirely")
    collected = q.bridge.handle(q.manifest, {"operation": "collect", "exclude_sha256": [digest]})[
        "L1P1"
    ]
    assert base64.b64decode(collected["source"]) == b"valid candidate"
    assert collected["best"]["speedup"] == 1.2
    assert collected["excluded_sha256"] == [digest]
    assert collected["used"] == 2 and len(collected["jobs"]) == 2
    assert collected["jobs"][1]["speedup"] == 9
    assert len(q.jobs) == 2
    with pytest.raises(ValueError, match="full lowercase SHA256"):
        q.bridge.handle(q.manifest, {"operation": "collect", "exclude_sha256": ["short"]})


@pytest.mark.parametrize("failure", ["excluded", "corrupt", "stale"])
def test_collection_checks_sources_before_changing_any_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    client = module("client")
    source = b"frozen source"
    digest = hashlib.sha256(source).hexdigest()
    value = {"source": base64.b64encode(source).decode(), "best": {"source_sha256": digest}}
    status = {"L1P1": dict(value), "L2P14": dict(value)}
    excluded = []
    if failure == "excluded":
        # An older deployed bridge may ignore the new selection parameter.
        excluded = [digest]
    elif failure == "corrupt":
        status["L2P14"]["source"] = base64.b64encode(b"different source").decode()
    else:
        status["L2P14"] = {}
    for task in status:
        (tmp_path / task).mkdir()
        (tmp_path / task / "model_new.py").write_text("preserve previous files")
    monkeypatch.setattr(client, "remote", lambda *args, **kwargs: status)
    with pytest.raises(ValueError, match=r"selected snapshot|fresh directory"):
        client.collect_bundle({"tasks": {name: {} for name in status}}, tmp_path, excluded)
    for task in status:
        assert (tmp_path / task / "model_new.py").read_text() == "preserve previous files"
    assert not (tmp_path / "evaluation-sources.json").exists()


def test_client_collects_selected_snapshot_and_archives_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = module("client")
    source = b"selected frozen source"
    digest = hashlib.sha256(source).hexdigest()
    rejected = hashlib.sha256(b"rejected source").hexdigest()

    def request(manifest: Any, operation: str, **kwargs: Any) -> Any:
        assert operation == "collect" and kwargs == {"exclude_sha256": [rejected]}
        return {
            "L1P1": {
                "source": base64.b64encode(source).decode(),
                "best": {"source_sha256": digest},
                "excluded_sha256": [rejected],
            },
            "L2P14": {"excluded_sha256": [rejected]},
        }

    monkeypatch.setattr(client, "remote", request)
    result = client.collect_bundle({"tasks": {"L1P1": {}, "L2P14": {}}}, tmp_path, [rejected])
    assert (tmp_path / "L1P1/model_new.py").read_bytes() == source
    assert not (tmp_path / "L2P14/model_new.py").exists()
    assert json.loads((tmp_path / "evaluation-sources.json").read_text()) == result
    assert "source" not in result["L1P1"]


def test_diagnostics_share_the_evaluation_cap_and_recover_the_same_intent(
    diagnostic_queue: Any,
) -> None:
    q = diagnostic_queue
    for i in range(10):
        q.bridge.handle(
            q.manifest,
            {
                "operation": "submit",
                "task": "L1P1",
                "key": str(i),
                "source": base64.b64encode(b"candidate").decode(),
            },
        )
    payload = diagnostic_payload()
    first = q.bridge.handle(q.manifest, payload)
    assert first["kind"] == "run" and first["budget_used"] == 11
    assert q.jobs[-1]["submitted_body"]["argv"] == [sys.executable, "-u", "_rsi_diagnostic.py"]
    assert q.jobs[-1]["submitted_body"]["gpus"] == 1
    assert q.bridge.handle(q.manifest, payload) == first
    assert len(q.jobs) == 11
    with pytest.raises(ValueError, match="different code"):
        q.bridge.handle(q.manifest, {**payload, "script": base64.b64encode(b"print(2)").decode()})
    with pytest.raises(ValueError, match="budget exhausted"):
        q.bridge.handle(q.manifest, diagnostic_payload("twelfth"))
    with pytest.raises(ValueError, match="budget exhausted"):
        q.bridge.handle(
            q.manifest,
            {
                "operation": "submit",
                "task": "L1P1",
                "key": "twelfth-eval",
                "source": payload["source"],
            },
        )
    assert len(q.jobs) == 11


def test_diagnostic_inputs_are_frozen_and_cannot_supply_scores(diagnostic_queue: Any) -> None:
    q = diagnostic_queue
    q.bridge.handle(q.manifest, diagnostic_payload())
    first = q.jobs[0]
    first.update(
        state="succeeded",
        evaluation={
            "correct": True,
            "speedup": 999,
            "evaluation_policy": "strict-fp32-v1",
        },
    )
    summary = q.bridge.summarize(first)
    assert summary["correct"] is False and summary["speedup"] == 0
    assert summary["diagnostic_sha256"] == hashlib.sha256(b"print('layer comparison')").hexdigest()
    assert "source" not in q.bridge.handle(q.manifest, {"operation": "collect"})["L1P1"]
    client = module("client")
    bundle = q.workspace.parent / "bundle"
    (bundle / "L1P1").mkdir(parents=True)
    (bundle / "L1P1/model_new.py").write_text("class ModelNew: pass")
    # Even forged diagnostic feedback with matching source cannot count as evaluation.
    forged = {**summary, "correct": True, "speedup": 999}
    assert (
        client.score_bundle(q.manifest, bundle, {"L1P1": {"used": 1, "jobs": [forged]}}, "search")[
            0
        ]
        == 0
    )
    # A diagnostic without a candidate must not silently reuse staging leftovers.
    q.bridge.handle(q.manifest, {**diagnostic_payload("reference-only"), "source": ""})
    assert not (Path(q.jobs[1]["cwd"]) / "model_new.py").exists()
    q.bridge.handle(
        q.manifest,
        {
            "operation": "submit",
            "task": "L1P1",
            "key": "evaluation",
            "source": base64.b64encode(b"new candidate").decode(),
        },
    )
    assert not (Path(q.jobs[2]["cwd"]) / "_rsi_diagnostic.py").exists()
    assert (Path(first["cwd"]) / "model_new.py").read_text() == "class ModelNew: pass"


def test_diagnostic_logs_include_the_start_and_mark_truncation(diagnostic_queue: Any) -> None:
    q = diagnostic_queue
    q.bridge.handle(q.manifest, diagnostic_payload())
    job = q.jobs[0]
    Path(job["stdout_path"]).write_text("first divergent layer\n" + "x" * 1_000_000)
    Path(job["stderr_path"]).write_text("compiler details")
    value = q.bridge.handle(q.manifest, {"operation": "logs", "task": "L1P1", "job_id": job["id"]})
    assert value["stdout"]["text"].startswith("first divergent layer")
    assert len(value["stdout"]["text"]) == 1_000_000 and value["stdout"]["truncated"]
    assert value["stderr"] == {"text": "compiler details", "truncated": False, "source": "file"}
    assert len(q.jobs) == 1


def test_diagnostic_log_lookup_rejects_another_task(diagnostic_queue: Any) -> None:
    q = diagnostic_queue
    q.bridge.handle(q.manifest, diagnostic_payload())
    job = q.jobs[0]
    job["task_id"] = "another-task"
    with pytest.raises(AssertionError):
        q.bridge.handle(q.manifest, {"operation": "logs", "task": "L1P1", "job_id": job["id"]})


def test_diagnostic_logs_do_not_read_paths_outside_job_artifacts(diagnostic_queue: Any) -> None:
    q = diagnostic_queue
    q.bridge.handle(q.manifest, diagnostic_payload())
    job = q.jobs[0]
    outside = q.workspace.parent / "private.txt"
    outside.write_text("must not be exposed")
    Path(job["stdout_path"]).symlink_to(outside)
    job["stdout_tail"] = "queue output"
    value = q.bridge.diagnostic_logs(job)
    assert value["stdout"] == {"text": "queue output", "truncated": True, "source": "queue_tail"}


def test_diagnostic_restart_polls_existing_job_and_saves_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = module("client")
    (tmp_path / "compare.py").write_text("print('compare')")
    output = tmp_path / "output"
    output.mkdir()
    (output / "remote-job.json").write_text(json.dumps({"id": "existing"}))
    calls = []

    def request(manifest: Any, operation: str, **values: Any) -> Any:
        calls.append(operation)
        assert values == {"task": "L1P1", "job_id": "existing"}
        if operation == "job":
            return {"id": "existing", "kind": "run", "state": "failed", "budget_used": 4}
        assert operation == "logs"
        return {
            s: {"text": s + " details", "truncated": False, "source": "file"}
            for s in ("stdout", "stderr")
        }

    monkeypatch.setattr(client, "remote", request)
    client.diagnose({"tasks": {"L1P1": {}}}, tmp_path, output, "L1P1", "compare.py")
    assert calls == ["job", "logs"]
    assert json.loads((output / "feedback.json").read_text())["task"] == "L1P1"
    assert (output / "stdout.log").read_text() == "stdout details"
    assert json.loads((output / "diagnostic-logs.json").read_text())["stdout"]["truncated"] is False


def test_diagnostic_bad_inputs_do_not_reach_the_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = module("client")
    monkeypatch.setattr(client, "remote", lambda *a, **k: pytest.fail("Unexpected GPU submission"))
    (tmp_path / "invalid.py").write_text("def broken(")
    with pytest.raises(SyntaxError):
        client.diagnose(
            {"tasks": {"L1P1": {}}}, tmp_path, tmp_path / "output", "L1P1", "invalid.py"
        )
    with pytest.raises(ValueError, match="frozen"):
        client.diagnose(
            {"tasks": {"L1P1": {}}}, tmp_path, tmp_path / "output", "L1P1", "../outside.py"
        )


def test_audited_external_gpu_work_reduces_the_remaining_submission_allowance(
    diagnostic_queue: Any,
) -> None:
    q = diagnostic_queue
    ledger = q.workspace.parent / "external-submissions"
    ledger.mkdir()
    (ledger / "outside-check.json").write_text(
        json.dumps(
            {
                "event_id": "outside-check",
                "task_id": "task1",
                "count": 1,
                "note": "Operator confirmed one GPU execution outside the queue",
            }
        )
    )
    for i in range(10):
        q.bridge.handle(q.manifest, diagnostic_payload(str(i)))
    state = q.bridge.handle(q.manifest, {"operation": "status"})["L1P1"]
    assert (state["used"], state["remaining"], state["queue_used"], state["external_used"]) == (
        11,
        0,
        10,
        1,
    )
    assert state["jobs"][-1]["budget_used"] == 11
    assert q.bridge.handle(q.manifest, diagnostic_payload("9"))["id"] == q.jobs[-1]["id"]
    with pytest.raises(ValueError, match="budget exhausted"):
        q.bridge.handle(q.manifest, diagnostic_payload("eleventh-queued"))
    with pytest.raises(ValueError, match="budget exhausted"):
        q.bridge.handle(
            q.manifest,
            {
                "operation": "submit",
                "task": "L1P1",
                "key": "another-eval",
                "source": base64.b64encode(b"candidate").decode(),
            },
        )
    assert len(q.jobs) == 10


@pytest.mark.parametrize("patch", [{"task_id": "wrong-task"}, {"count": -1}, {"count": True}])
def test_external_accounting_rejects_invalid_records_before_submission(
    diagnostic_queue: Any,
    patch: dict[str, Any],
) -> None:
    q = diagnostic_queue
    ledger = q.workspace.parent / "external-submissions"
    ledger.mkdir()
    (ledger / "audit.json").write_text(
        json.dumps(
            {
                "event_id": "audit",
                "task_id": "task1",
                "count": 1,
                **patch,
            }
        )
    )
    with pytest.raises(ValueError, match="Invalid external"):
        q.bridge.handle(q.manifest, diagnostic_payload())
    assert not q.jobs
