from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.control import json_bytes
from areal_rsi.api.execution import (
    ArenaDatasetResources,
    ArenaJobLaunch,
    ArenaResourceKind,
    ArenaResourcePin,
)
from areal_rsi.examples.tir.arena import (
    boxed_fraction,
    collect,
    reward,
    select_calibrated,
    synthetic_candidates,
)
from areal_rsi.infra.coordination import arena
from areal_rsi.infra.coordination.arena import ArenaError, publish_resource, resource_version


def test_publication_keeps_old_resources_and_refuses_published_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    versions: dict[str, dict[str, Any]] = {}
    mutations: list[str] = []
    entity = False

    def cli(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        nonlocal entity
        assert "--token" not in arguments
        method, path = arguments[2:4]
        body = json.loads(kwargs["input"]) if kwargs["input"] else None
        data: dict[str, Any] = {}
        if method == "get":
            if "/versions/" in path:
                data = versions.get(path, {})
            elif entity:
                data = {"key": "test"}
            if not data:
                return subprocess.CompletedProcess(
                    arguments,
                    1,
                    json_bytes(
                        {
                            "ok": False,
                            "error": {"code": "NotFound", "message": "missing"},
                        }
                    ),
                )
        else:
            mutations.append(path)
            if path.endswith("/publish"):
                versions[path.removesuffix("/publish")]["status"] = "PUBLISHED"
            elif path.endswith("/versions"):
                versions[path + "/" + body["version"]] = {
                    "status": "DRAFT",
                    "spec": body["spec"],
                    "content_hash": "a" * 64,
                }
            else:
                entity = True
        return subprocess.CompletedProcess(arguments, 0, json_bytes({"ok": True, "data": data}))

    monkeypatch.setattr(subprocess, "run", cli)
    old = {"model_name": "m0", "env": {"OPENAI_BASE_URL": "https://example.invalid/v1"}}
    old_version = resource_version(old)
    pin = publish_resource(ArenaResourceKind.MODEL, "test", old_version, old, description="test")
    count = len(mutations)
    assert (
        publish_resource(ArenaResourceKind.MODEL, "test", old_version, old, description="test")
        == pin
    )
    assert len(mutations) == count
    changed = {**old, "model_name": "m1"}
    with pytest.raises(ValueError, match="choose a new version"):
        publish_resource(ArenaResourceKind.MODEL, "test", old_version, changed, description="test")
    assert len(mutations) == count
    new = publish_resource(
        ArenaResourceKind.MODEL, "test", resource_version(changed), changed, description="test"
    )
    assert new.version != pin.version
    assert versions[f"/models/test/versions/{pin.version}"]["spec"] == old


def test_authentication_failure_does_not_create_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def cli(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        return subprocess.CompletedProcess(
            arguments,
            1,
            json_bytes(
                {
                    "ok": False,
                    "error": {"code": "Forbidden", "message": "access denied"},
                }
            ),
        )

    monkeypatch.setattr(subprocess, "run", cli)
    with pytest.raises(ArenaError, match="access denied"):
        publish_resource(ArenaResourceKind.MODEL, "test", "1.0.0", {}, description="test")
    assert len(calls) == 1 and calls[0][2] == "get"


def test_live_gateway_defaults_do_not_break_idempotent_deployment() -> None:
    expected = {"endpoints": [{"base_url": "http://local-model:8000/v1", "enabled": True}]}
    actual = {
        "endpoints": [
            {
                **expected["endpoints"][0],
                "api_key": "****",
                "health": {"state": "healthy"},
            }
        ]
    }
    assert arena._contains(actual, expected)
    actual["endpoints"][0]["base_url"] = "http://other-model:8000/v1"
    assert not arena._contains(actual, expected)


def test_calibration_excludes_failed_and_twice_solved_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = synthetic_candidates(seed=20261002, count=32)

    def pin(kind: ArenaResourceKind, key: str) -> ArenaResourcePin:
        return ArenaResourcePin(kind, key, "1.0.0", "a" * 64)

    cases = tuple(
        pin(ArenaResourceKind.CASE, "rsi-tir-" + candidate.case.task_id[:24])
        for candidate in candidates
    )
    resources = ArenaDatasetResources(
        "probe",
        pin(ArenaResourceKind.HARNESS, "harness"),
        pin(ArenaResourceKind.MODEL, "model"),
        pin(ArenaResourceKind.ENV, "env"),
        pin(ArenaResourceKind.REWARD, "reward"),
        cases,
        pin(ArenaResourceKind.BENCHMARK, "benchmark"),
    )
    report = {
        "data": {
            "job": {
                "job_id": "probe-job",
                "status": "DONE",
                "sample_numbers": 2,
                "benchmark_ref": resources.benchmark.ref(),
                "model_ref": resources.model.ref(),
                "harness_ref": resources.harness.ref(),
            },
            "result": {
                "per_env": [
                    {
                        "case_ref": case.ref(),
                        "task_count": 2,
                        "sample_numbers": 2,
                        "task_ids": [f"{case.key}:0", f"{case.key}:1"],
                        "ok_count": 1 if i == 0 else 2,
                        "failed_count": int(i == 0),
                        "score": 1 if i == 1 else 0.5,
                    }
                    for i, case in enumerate(cases)
                ]
            },
        }
    }
    source = tmp_path / "probe.json"
    source.write_bytes(json_bytes(report))
    published = []

    def publish(kind: ArenaResourceKind, key: str, version: str, spec: Any, **kw: Any) -> Any:
        published.append(spec)
        return pin(kind, key)

    monkeypatch.setattr(arena, "publish_resource", publish)
    saved = select_calibrated(
        resources, source, tmp_path / "selected", run_id="train", seed=20261002, count=16
    )
    assert len(saved.cases) == 16
    assert not set(cases[:2]) & set(saved.cases)
    summary = json.loads((tmp_path / "selected/selection.json").read_bytes())
    assert summary["incomplete_cases"] == summary["twice_solved_cases"] == 1
    assert summary["one_pass"] == 16
    report["data"]["job"]["model_ref"] = {"key": "other-model", "version": "1.0.0"}
    source.write_bytes(json_bytes(report))
    with pytest.raises(ValueError, match="frozen refs"):
        select_calibrated(
            resources, source, tmp_path / "wrong-model", run_id="train", seed=20261002, count=16
        )
    assert len(published) == 1


def test_collect_and_reward_use_saved_response_and_isolated_oracle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trajectory, oracle = tmp_path / "trajectory.jsonl", tmp_path / "oracle.json"
    agent, result = tmp_path / "agent", tmp_path / "result/reward.json"
    trajectory.write_bytes(
        json_bytes(
            {
                "type": "result",
                "is_error": False,
                "result": r"A distracting 20. Final: \boxed{\frac{3}{2}}",
                "usage": {"input_tokens": 40, "output_tokens": 12},
            }
        )
    )
    oracle.write_bytes(json_bytes({"answer": "3/2"}))
    for key, value in {
        "ARENA_TRAJECTORY_PATH": trajectory,
        "ARENA_AGENT_OUTPUT_DIR": agent,
        "ARENA_ORACLE_PATH": oracle,
        "ARENA_REWARD_OUTPUT_FILE": result,
        "ARENA_TASK_ID": "task-one",
    }.items():
        monkeypatch.setenv(key, str(value))
    collect()
    reward()
    scored = json.loads(result.read_bytes())
    assert scored["status"] == "OK" and scored["score"] == 1
    assert scored["raw"]["parsed_answer"] == "3/2"
    assert (result.parent / "answer.json").read_bytes() == (agent / "answer.json").read_bytes()
    mount = tmp_path / "oracle-mount"
    mount.mkdir()
    (mount / "oracle.json").write_bytes(oracle.read_bytes())
    monkeypatch.setenv("ARENA_ORACLE_PATH", str(mount))
    reward()
    assert json.loads(result.read_bytes())["score"] == 1
    (mount / "ambiguous.json").write_bytes(oracle.read_bytes())
    with pytest.raises(ValueError, match="exactly one"):
        reward()
    monkeypatch.setenv("ARENA_ORACLE_PATH", str(oracle))
    trajectory.write_bytes(
        json_bytes(
            {
                "type": "result",
                "is_error": False,
                "result": "Only incidental numbers: 3/2",
                "usage": {},
            }
        )
    )
    collect()
    reward()
    assert json.loads(result.read_bytes())["score"] == 0
    trajectory.write_bytes(
        json_bytes({"type": "result", "is_error": True, "result": "upstream timed out"})
    )
    with pytest.raises(ValueError, match="finish successfully"):
        collect()
    assert boxed_fraction(r"\boxed{2} then \boxed{3/0}") is None
    assert boxed_fraction(r"\boxed{__import__('os').system('false')}") is None


def test_upload_receipt_rejects_source_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, receipt = tmp_path / "source", tmp_path / "upload.json"
    source.mkdir()
    (source / "main.py").write_text("one")
    uploads: list[str] = []

    def upload(*args: str, **kwargs: Any) -> dict[str, Any]:
        uploads.append(args[1])
        return {"data": {"uri": "uploads/frozen/"}}

    monkeypatch.setattr(arena, "arena_json", upload)
    assert arena.upload_asset(source, receipt=receipt) == "uploads/frozen/"
    assert arena.upload_asset(source, receipt=receipt) == "uploads/frozen/"
    assert len(uploads) == 1
    (source / "main.py").write_text("two")
    with pytest.raises(ValueError, match="source changed"):
        arena.upload_asset(source, receipt=receipt)
    assert len(uploads) == 1


def test_heldout_synthesis_is_disjoint_and_keeps_family_balance() -> None:
    probe = synthetic_candidates(seed=20261002, count=1024)
    excluded = frozenset(item.case.task_id for item in probe)
    heldout = synthetic_candidates(seed=20261003, count=256, exclude_task_ids=excluded)
    assert len(heldout) == 256
    assert not excluded.intersection(item.case.task_id for item in heldout)
    assert all(
        sum(item.family == family for item in heldout) == 32
        for family in {item.family for item in heldout}
    )


def test_uncertain_launch_requires_matching_job_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pins = tuple(
        ArenaResourcePin(kind, "test", "1.0.0", "a" * 64)
        for kind in (
            ArenaResourceKind.BENCHMARK,
            ArenaResourceKind.HARNESS,
            ArenaResourceKind.MODEL,
        )
    )
    launch = ArenaJobLaunch("test-run", *pins, 2, 4)
    receipt = tmp_path / "job.json"
    calls: list[str] = []
    monkeypatch.setattr(
        arena,
        "published_pin",
        lambda kind, key, version: next(pin for pin in pins if pin.kind == kind),
    )

    def uncertain(*args: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(args[0])
        raise ArenaError("Timeout", "response lost after submission")

    monkeypatch.setattr(arena, "arena_json", uncertain)
    with pytest.raises(ArenaError):
        arena.launch_job(launch, receipt)
    with pytest.raises(ValueError, match="uncertain"):
        arena.launch_job(launch, receipt)
    assert calls == ["job"]
    job = {
        "benchmark_ref": pins[0].ref(),
        "harness_ref": pins[1].ref(),
        "model_ref": pins[2].ref(),
        "sample_numbers": 1,
    }
    monkeypatch.setattr(arena, "arena_get", lambda path: {"job": job})
    with pytest.raises(ValueError, match="differs"):
        arena.launch_job(launch, receipt, adopt_job="existing")
    job["sample_numbers"] = 2
    saved = arena.launch_job(launch, receipt, adopt_job="existing")
    assert saved.job_id == "existing"
    assert arena.launch_job(launch, receipt) == saved
    assert calls == ["job"]
