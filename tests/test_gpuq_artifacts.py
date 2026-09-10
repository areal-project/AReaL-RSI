from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
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
