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
