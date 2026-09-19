"""Core demo contract: HTTP launch/reuse, autonomous closeout, and no duplicate GPU finals."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import runpy
import socket
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from areal_rsi.api.research import TaskBrief, VerificationContract
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.responses import ResponsesConfig, ResponsesRuntime
from areal_rsi.research_app import ResearchApp
from areal_rsi.ui.launch import ensure_workbench
from areal_rsi.ui.workbench import WorkbenchServer


@pytest.fixture(autouse=True)
def adapter_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path("examples/kernelbench_artifacts").resolve()))


class StopTransport:
    def __init__(self, action: str = "stop_run"):
        self.action = action

    async def request(self, config: Any, payload: Any, sink: Any) -> dict[str, Any]:
        return {
            "id": "stop",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": self.action,
                    "call_id": "finished",
                    "arguments": '{"reason":"探索结束"}',
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


@pytest.mark.parametrize("limit", [None, 1800])
def test_single_task_preparation_requires_time_budget_and_unlimited_submissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int | None
) -> None:
    prepare = runpy.run_path("examples/kernelbench_artifacts/demo.py")["prepare"]
    source = b"# Public reference fixture\n"
    reference = {"method": "fixed-reference-v1", "ref_ms": 10.0, "hardware": {}}
    manifest = {
        "tasks": {
            "L3P10": {
                "reference_sha256": hashlib.sha256(source).hexdigest(),
                "reference_calibration": reference,
            }
        },
        "protocol_id": "fixed",
        "manifest_id": "tasks",
        "search_scoring": {"method": "fixed-reference-v1"},
    }

    def remote(environment: Any, payload: Any) -> dict[str, Any]:
        if payload["operation"] == "calibrate":
            return {"status": "completed", "manifest": manifest}
        assert payload["demo"]["exploration_submissions"] is None
        return {
            "manifest": manifest,
            "references": {"L3P10": {"source": base64.b64encode(source).decode()}},
        }

    monkeypatch.setitem(prepare.__globals__, "control_request", remote)
    environment = tmp_path / "environment.json"
    environment.write_text("{}")
    if limit is None:
        with pytest.raises(ValueError, match="wall limit"):
            prepare(
                tmp_path,
                environment,
                "unlimited",
                task="L3P10",
                max_wall_seconds=None,
                exploration_submissions=None,
                check_credentials=False,
            )
        return
    config = prepare(
        tmp_path,
        environment,
        "unlimited",
        task="L3P10",
        max_agents=6,
        max_wall_seconds=limit,
        exploration_submissions=None,
        check_credentials=False,
        collaboration_modules=(),
    )
    limits = config["brief"]["limits"]
    assert limits["max_wall_seconds"] == limit
    assert limits["max_jobs"] == 6
    assert limits["max_model_calls"] is None
    assert limits["max_agents"] == 6
    assert config["metadata"]["finalization_reserve_seconds"] == 0
    assert ("本次不设时间上限" in config["brief"]["instructions"]) == (limit is None)
    assert "None 秒" not in config["brief"]["instructions"]
    assert "GPU 提交次数不限" in config["brief"]["instructions"]
    frozen = json.loads((tmp_path / "runs/unlimited/setup/research.json").read_text())
    assert frozen == config
    with pytest.raises(ValueError, match="协作模块"):
        prepare(
            tmp_path,
            environment,
            "unlimited",
            task="L3P10",
            max_agents=6,
            max_wall_seconds=limit,
            check_credentials=False,
            collaboration_modules=("leader_guidance",),
        )
    assert (
        json.loads(Path(config["seed_dir"]).joinpath("tasks/L3P10/baseline.json").read_text())
        == reference
    )
    assert config["mode"] == "passk"
    assert "collect_bundle" not in {c["name"] for c in config["commands"]}
    rules = Path(config["seed_dir"]).joinpath("RULES.md").read_text()
    prompt = (
        config["brief"]["instructions"] + rules + json.dumps(config["metadata"], ensure_ascii=False)
    )
    assert "阶段目标仍由 RSI 决定" not in prompt and "团队研究计划" not in prompt
    assert "独立完成整题" in rules and "不等待技术指导" in rules
    assert "不使用 triton.autotune" in prompt
    assert "TF32" in rules


@pytest.mark.parametrize("strict", [True, False])
def test_diagnostic_matches_formal_precision_without_changing_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, strict: bool
) -> None:
    diagnose = runpy.run_path("examples/kernelbench_artifacts/client.py")["diagnose"]
    torch = ModuleType("torch")
    torch.backends = SimpleNamespace(  # type: ignore[attr-defined]
        cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=True)),
        cudnn=SimpleNamespace(allow_tf32=True),
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    candidate = b"# original candidate\n"
    script = (
        "from __future__ import annotations\nimport torch\n"
        f"assert torch.backends.cuda.matmul.allow_tf32 is {not strict}\n"
        f"assert torch.backends.cudnn.allow_tf32 is {not strict}\n"
    ).encode()
    (tmp_path / "candidate.py").write_bytes(candidate)
    (tmp_path / "diagnose.py").write_bytes(script)
    captured = []

    def submit(*args: Any, **sources: str) -> None:
        captured.append(sources)
        assert base64.b64decode(sources["source"]) == candidate
        exec(compile(base64.b64decode(sources["script"]), "diagnostic.py", "exec"), {})
        if not strict:
            assert base64.b64decode(sources["script"]) == script

    monkeypatch.setitem(diagnose.__globals__, "submit_and_wait", submit)
    manifest = {"diagnostic_precision": "strict-fp32-v1"} if strict else {}
    diagnose(manifest, tmp_path, tmp_path / "output", "L3P10", "diagnose.py", "candidate.py")
    assert len(captured) == 1
    assert (tmp_path / "candidate.py").read_bytes() == candidate


@pytest.mark.parametrize(
    "retry_verifier,withdraw_final",
    [(False, False), (True, False), (False, True)],
)
def test_configured_http_run_finishes_without_a_monitor_and_reuses_service(
    tmp_path: Path, retry_verifier: bool, withdraw_final: bool
) -> None:
    script = tmp_path / "cpu_runner.py"
    script.write_text("""import json,sys,os
from pathlib import Path
output=Path(sys.argv[2]);output.mkdir(parents=True,exist_ok=True)
if sys.argv[1]=='finish':
    selection=json.loads(Path(os.environ['RSI_CANDIDATE_SELECTION']).read_text())
    (output/'selection-seen.json').write_text(json.dumps(selection))
    candidate=output/'candidate';candidate.mkdir(exist_ok=True)
    (candidate/'model.py').write_text('candidate')
    with (output/'calls').open('a') as f:f.write('once\\n')
else:
    once=Path(__file__).with_name('failed-once')
    if os.environ.get('RSI_TEST_FAIL')=='1' and not once.exists():
        once.touch();sys.exit(1)
    workspace=Path(sys.argv[3]);phase=sys.argv[4]
    job=json.loads((workspace.parents[2]/'jobs'/output.name/'job.json').read_text())
    meta=job['spec']['metadata']
    result=dict(status='completed',simulated=False,phase=phase,metric='fast_1',score=0.5,
                metrics={'fast_1':0.5},protocol_id='fixed',manifest_id='tasks',
                candidate_id=meta['candidate_id'],candidate_digest=meta['candidate_digest'])
    (output/'evaluation.json').write_text(json.dumps(result))
""")
    config = ResearchConfig(
        TaskBrief(
            "Demo", "运行后自主结束", verification=VerificationContract("fast_1", target=1.0)
        ),
        finalizer=ApprovedCommand(
            "finish", (sys.executable, str(script), "finish", "{output}"), ("output",)
        ),
        verifier=ApprovedCommand(
            "verify",
            (sys.executable, str(script), "verify", "{output}", "{workspace}", "{phase}"),
            ("output", "workspace", "phase"),
        ),
        execution_env={"RSI_TEST_FAIL": "1" if retry_verifier else "0"},
        metadata={
            "verification_identity": {
                "adapter": "content",
                "phases": {
                    phase: {"protocol_id": "fixed", "manifest_id": "tasks"}
                    for phase in ("search", "final")
                },
            }
        },
    )
    config.brief.limits.max_wall_seconds = 0.2
    config.metadata["time_limit_scope"] = "exploration"
    app = ResearchApp(
        tmp_path,
        runtime_factory=lambda _: ResponsesRuntime(
            ResponsesConfig(base_url="https://example.test", api_key="test"),
            StopTransport("wait"),
        ),
    )
    server = WorkbenchServer(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client, info = ensure_workbench(server.server_port)
        assert info["data_root"] == str(tmp_path.resolve())
        params = {"run_id": "demo", "config": config.public()}
        first = client.request("/api/runs", params)
        assert first["run_id"] == client.request("/api/runs", params)["run_id"]
        if withdraw_final:
            c = app.coordinator("demo")
            workspace = tmp_path / "defective-product"
            workspace.mkdir()
            (workspace / "model.py").write_text("candidate")
            candidate = asyncio.run(
                c.store.snapshot(workspace, agent_id="operator", description="Known defect")
            )
            c.store.withdraw_candidate(candidate.candidate_id, "Known contract violation", ())
        client.request("/api/runs/demo/start", {})
        until = time.monotonic() + 15
        while time.monotonic() < until:
            detail = client.request("/api/runs/demo")
            if retry_verifier and (detail.get("finalization") or {}).get("status") == "failed":
                client.request("/api/runs/demo/resume", {})
                retry_verifier = False
                continue
            if (detail.get("finalization") or {}).get("status") in {"completed", "failed"}:
                break
            time.sleep(0.1)
        selection = json.loads(
            (tmp_path / "runs/demo/finalization/output/selection-seen.json").read_text()
        )
        assert selection["run_id"] == "demo"
        if withdraw_final:
            assert detail["finalization"]["status"] == "failed", detail
            assert "withdrawn candidate" in detail["finalization"]["error"]
            assert detail["final"] is None
            assert (
                selection["withdrawn_candidates"][0]["files"]["model.py"]
                == "sha256:" + hashlib.sha256(b"candidate").hexdigest()
            )
            return
        assert not selection["withdrawn_candidates"]
        assert detail["finalization"]["status"] == "completed", detail
        assert detail["final"]["score"] == 0.5
        assert (
            detail["status"] == "stopped"
        )  # Valid completion is not falsely called target success.
        with pytest.raises(ValueError, match="复测已完成"):
            client.request("/api/runs/demo/resume", {})
        assert (tmp_path / "runs/demo/finalization/output/calls").read_text() == "once\n"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_launcher_rejects_an_unrelated_listener() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(ValueError, match="占用"):
            ensure_workbench(listener.getsockname()[1])


def test_score_keeps_raw_speedup_and_exposes_measurement_noise(tmp_path: Path) -> None:
    score_bundle = runpy.run_path("examples/kernelbench_artifacts/client.py")["score_bundle"]
    candidate = tmp_path / "L1P1/model_new.py"
    candidate.parent.mkdir()
    candidate.write_text("candidate")
    job = {
        "kind": "eval",
        "state": "succeeded",
        "source_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "correct": True,
        "speedup": 13.45,
        "evaluation": {
            "evaluation_policy": "strict-fp32-v1",
            "runtime_ms": 0.426,
            "ref_ms": 5.73,
            "runtime_stats": {"std": 0.01},
            "ref_stats": {"std": 6.5},
        },
    }
    score, metrics, evidence = score_bundle(
        {"tasks": {"L1P1": {}}, "metric": "speedup"},
        tmp_path,
        {"L1P1": {"jobs": [job], "used": 1}},
        "search",
    )
    assert score == 13.45 and evidence["L1P1"]["job"] == job
    assert metrics["L1P1_runtime_ms"] == 0.426
    assert metrics["L1P1_ref_ms"] == 5.73 and metrics["L1P1_ref_std_ms"] == 6.5


@pytest.mark.parametrize("withdraw_fastest", [False, True])
@pytest.mark.parametrize("effective_cutoff", [False, True])
def test_remote_final_confirmation_survives_lost_reply_without_new_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, withdraw_fastest: bool, effective_cutoff: bool
) -> None:
    script = Path(__file__).resolve().parents[1] / "examples/kernelbench_artifacts/control.py"
    remote_finish = runpy.run_path(str(script))["finish"]
    demo = ModuleType("demo")
    demo.control_request = lambda manifest, payload: remote_finish(payload["manifest"])  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "demo", demo)
    finish = runpy.run_path(str(script.with_name("finish.py")))["finish"]
    workspace = tmp_path / "task/workspace"
    workspace.mkdir(parents=True)
    (workspace.parent / "gpuq-token").write_text("scoped")
    control = tmp_path / "control"
    control.mkdir()
    original = tmp_path / "original"
    original.mkdir()
    (original / "model_new.py").write_text("best passing code")
    (workspace / "model_new.py").write_text("later untested code")
    jobs: list[dict[str, Any]] = [
        {
            "id": "exploration",
            "task_id": "task",
            "budget": 11,
            "kind": "eval",
            "state": "succeeded",
            "cwd": str(original),
            "finished_at": 90,
            "evaluation": {
                "correct": True,
                "speedup": 2,
                "runtime_ms": 2,
                "evaluation_policy": "strict-fp32-v1",
            },
        }
    ]
    submissions = []

    def request(path: str, body: Any = None, **kwargs: Any) -> Any:
        if body is None:
            return list(jobs)
        submissions.append(body)
        final = tmp_path / "final"
        final.mkdir()
        (final / "model_new.py").write_bytes((workspace / "model_new.py").read_bytes())
        jobs.append(
            {
                "id": "final",
                "task_id": "task",
                "budget": 11,
                "kind": "eval",
                "state": "succeeded",
                "cwd": str(final),
                "idempotency_key": body["idempotency_key"],
            }
        )
        raise TimeoutError("Queue accepted the job, reply was lost")

    common = ModuleType("common")
    common.request = request  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "common", common)
    manifest = {
        "run_id": "repeat",
        "kb_root": str(tmp_path),
        "remote_manifest": str(control / "manifest.json"),
        "gpuq_url": "http://unused",
        "tasks": {"L1P1": {"task_id": "task", "workspace": str(workspace)}},
        "submission_budget_per_task": 11,
        "exploration_budget_per_task": 10,
        "final_seed": 137,
    }
    if withdraw_fastest:
        defective = tmp_path / "defective"
        defective.mkdir()
        (defective / "model_new.py").write_text("faster but known defective")
        jobs.append(
            {
                **jobs[0],
                "id": "withdrawn",
                "cwd": str(defective),
                "evaluation": {**jobs[0]["evaluation"], "speedup": 5, "runtime_ms": 0.5},
            }
        )
        selection = tmp_path / "candidate-selection.json"
        selection.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": "repeat",
                    "withdrawn_candidates": [
                        {
                            "files": {
                                "L1P1/model_new.py": "sha256:"
                                + hashlib.sha256(
                                    (defective / "model_new.py").read_bytes()
                                ).hexdigest()
                            }
                        }
                    ],
                }
            )
        )
        monkeypatch.setenv("RSI_CANDIDATE_SELECTION", str(selection))
    else:
        monkeypatch.delenv("RSI_CANDIDATE_SELECTION", raising=False)
    if effective_cutoff:
        selection_path = tmp_path / "finalization/candidate-selection.json"
        selection_path.parent.mkdir()
        previous_path = os.environ.get("RSI_CANDIDATE_SELECTION")
        cutoff_selection = (
            json.loads(Path(previous_path).read_text())
            if previous_path
            else {
                "schema_version": 1,
                "run_id": "repeat",
                "withdrawn_candidates": [],
            }
        )
        cutoff_selection["execution_cutoffs"] = {"job-a": 100, "job-b": 100}
        for job_id, remote_id in [("job-a", "exploration"), ("job-b", "late")]:
            receipt = tmp_path / "execution-outputs" / job_id / "remote-job.json"
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({"id": remote_id}))
        selection_path.write_text(json.dumps(cutoff_selection))
        monkeypatch.setenv("RSI_CANDIDATE_SELECTION", str(selection_path))
        # This would win globally, but completed after its owner's cutoff.
        jobs.append(
            {
                **jobs[0],
                "id": "late",
                "finished_at": 110,
                "evaluation": {**jobs[0]["evaluation"], "speedup": 10, "runtime_ms": 0.1},
            }
        )
        # Unattributed jobs must not bypass the per-researcher cutoff either.
        jobs.append(
            {
                **jobs[0],
                "id": "unattributed",
                "evaluation": {**jobs[0]["evaluation"], "speedup": 20, "runtime_ms": 0.05},
            }
        )
    output = tmp_path / "output"
    with pytest.raises(TimeoutError):
        finish(manifest, output)
    finish(manifest, output)
    result = json.loads((output / "final-selection.json").read_text())
    assert result["status"] == "completed"
    assert len(submissions) == 1 and submissions[0]["seed"] == 137
    assert (tmp_path / "final/model_new.py").read_text() == "best passing code"
    assert (control / "exploration-closed").exists()
    assert result["selected"]["L1P1"]["job_id"] == "exploration"


def test_fixed_reference_feedback_registration_and_final_selection_agree(
    tmp_path: Path,
) -> None:
    select = runpy.run_path("examples/kernelbench_artifacts/control.py")["select_candidate"]
    summarize = runpy.run_path("examples/kernelbench_artifacts/remote.py")["summarize"]
    score_bundle = runpy.run_path("examples/kernelbench_artifacts/client.py")["score_bundle"]
    scoring = runpy.run_path("examples/kernelbench_artifacts/scoring.py")
    HARDWARE_FIELDS, evaluation = scoring["HARDWARE_FIELDS"], scoring["evaluation"]
    hardware = dict.fromkeys(HARDWARE_FIELDS)
    manifest = {
        "exploration_seed": 42,
        "final_seed": 137,
        "metric": "speedup",
        "search_scoring": {"method": "fixed-reference-v1"},
        "tasks": {"L3P10": {"reference_calibration": {"ref_ms": 10.0, "hardware": hardware}}},
    }
    jobs: list[dict[str, Any]] = []
    for name, runtime, speedup in [("slow", 9.25, 1.4378), ("fast", 7.57, 1.2893)]:
        folder = tmp_path / name
        folder.mkdir()
        (folder / "model_new.py").write_text(name)
        jobs.append(
            {
                "id": name,
                "cwd": str(folder),
                "kind": "eval",
                "state": "succeeded",
                "budget_used": 1,
                "evaluation": {
                    "runtime_ms": runtime,
                    "speedup": speedup,
                    "ref_ms": runtime * speedup,
                    "correct": True,
                    "seed": 42,
                    "evaluation_policy": "strict-fp32-v1",
                },
            }
        )
    chosen = select(manifest, "L3P10", jobs, set())
    assert chosen["job_id"] == "fast" and chosen["search_score"] == 10 / 7.57
    feedback = [summarize(j, manifest=manifest, task="L3P10") for j in jobs]
    assert feedback[1]["speedup"] == chosen["search_score"]
    assert feedback[0]["evaluation"]["measured_speedup"] == 1.4378
    candidate = tmp_path / "L3P10/model_new.py"
    candidate.parent.mkdir()
    candidate.write_text("fast")
    score, metrics, _ = score_bundle(
        manifest, tmp_path, {"L3P10": {"jobs": feedback, "used": 2}}, "search"
    )
    assert score == chosen["search_score"] and metrics["L3P10_ref_ms"] == 10
    final = {**jobs[1]["evaluation"], "seed": 137, "speedup": 1.2, "ref_ms": 9.084}
    assert evaluation(manifest, "L3P10", final) == final
    # The same source is confirmed once; final scoring still uses the final measurement.
    final_job = {**feedback[1], "created_at": 2, "evaluation": final, "speedup": 1.2}
    feedback[1]["created_at"] = 1
    final_score, _, _ = score_bundle(
        manifest, tmp_path, {"L3P10": {"jobs": [feedback[1], final_job], "used": 3}}, "final"
    )
    assert final_score == 1.2
    excluded = {hashlib.sha256(b"fast").hexdigest()}
    assert select(manifest, "L3P10", jobs, excluded)["job_id"] == "slow"
    assert select({}, "L3P10", jobs, set())["job_id"] == "slow", "Historical scores stay unchanged"
    assert select(manifest, "L3P10", [], set()) is None
    with pytest.raises(ValueError, match="环境"):
        evaluation(manifest, "L3P10", jobs[0]["evaluation"] | {"hardware": {"torch": "different"}})


@pytest.mark.parametrize("noisy_batch", [False, True])
def test_reference_calibration_freezes_once_and_reuses_jobs_after_lost_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, noisy_batch: bool
) -> None:
    calibrate = runpy.run_path("examples/kernelbench_artifacts/control.py")["calibrate"]
    calibration = runpy.run_path("examples/kernelbench_artifacts/scoring.py")["calibration"]

    policy = {
        "method": "fixed-reference-v1",
        "calibration_runs": 3,
        "max_calibration_runs": 6,
        "max_relative_std": 0.05,
        "max_relative_spread": 0.05,
    }
    manifest = {
        "run_id": "study",
        "kb_root": str(tmp_path),
        "remote_manifest": str(tmp_path / "manifest.json"),
        "gpuq_url": "unused",
        "protocol_id": "raw-evaluator",
        "exploration_seed": 42,
        "search_scoring": policy,
        "tasks": {"L1P63": {"task_id": "task", "workspace": str(tmp_path)}},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "token").write_text("operator")
    (tmp_path / "queue-auth.json").write_text(json.dumps({"token_file": str(tmp_path / "token")}))
    (tmp_path / "scoring.py").write_text("frozen scorer")
    jobs: dict[str, dict[str, Any]] = {}
    lost_reply = True

    def request(path: str, body: Any = None, **kwargs: Any) -> Any:
        nonlocal lost_reply
        assert kwargs["token"] == "operator"
        if body is not None:
            key = body["idempotency_key"]
            job = jobs.setdefault(key, {"id": key, **body, "state": "queued"})
            if lost_reply:
                lost_reply = False
                raise TimeoutError("Accepted but reply lost")
            return job
        return jobs[path.removeprefix("/jobs/")]

    common = ModuleType("common")
    common.request = request  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "common", common)
    with pytest.raises(TimeoutError):
        calibrate(manifest)
    assert calibrate(manifest)["status"] == "waiting"
    assert len(jobs) == 3 and all(j["task_id"] == "task-reference" for j in jobs.values())
    for index, job in enumerate(jobs.values()):
        job.update(
            state="succeeded",
            evaluation={
                "correct": True,
                "ref_ms": 10 + index / 10,
                "ref_stats": {"std": 3 if noisy_batch and index == 0 else 0.02},
                "evaluation_policy": "strict-fp32-v1",
            },
        )
    result = calibrate(manifest)
    if noisy_batch:
        assert result["status"] == "waiting" and len(jobs) == 4
        list(jobs.values())[-1].update(
            state="succeeded",
            evaluation={
                "correct": True,
                "ref_ms": 10.1,
                "ref_stats": {"std": 0.02},
                "evaluation_policy": "strict-fp32-v1",
            },
        )
        result = calibrate(manifest)
    fixed = result["manifest"]["tasks"]["L1P63"]["reference_calibration"]
    assert result["status"] == "completed" and fixed["ref_ms"] == 10.1
    assert result["manifest"]["protocol_id"] != "raw-evaluator"
    frozen = (tmp_path / "manifest.json").read_bytes()
    assert calibrate(manifest) == result and len(jobs) == (4 if noisy_batch else 3)
    assert len(fixed["discarded_job_ids"]) == int(noisy_batch)
    assert (tmp_path / "manifest.json").read_bytes() == frozen
    samples = list(jobs.values())[:3]
    samples[0]["evaluation"]["ref_stats"]["std"] = 3
    assert calibration(samples, policy) is None
    samples[0]["evaluation"].update(ref_ms=20, ref_stats={"std": 0.02})
    assert calibration(samples, policy) is None


def test_invalid_scoring_environment_is_not_retried_as_a_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = runpy.run_path("examples/kernelbench_artifacts/client.py")["remote"]
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps(
                {"ok": False, "error": "ValueError: calibration environment changed"}
            ),
            returncode=1,
        ),
    )
    manifest = dict.fromkeys(
        ["continue_script", "slurm_job", "remote_script", "remote_manifest", "host"], "fixture"
    )
    with pytest.raises(ValueError, match="calibration environment"):
        remote(manifest, "status")


@pytest.mark.parametrize("gpu_python", [None, "/runtime/python"])
def test_diagnostic_uses_execution_interpreter_and_reuses_submitted_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gpu_python: str | None
) -> None:
    script = Path(__file__).resolve().parents[1] / "examples/kernelbench_artifacts/remote.py"
    handle = runpy.run_path(str(script))["handle"]
    workspace = tmp_path / "task/workspace"
    workspace.mkdir(parents=True)
    control = tmp_path / "control"
    control.mkdir()
    manifest = {
        "python": "/control-host/python",
        "submission_budget_per_task": None,
        "exploration_budget_per_task": None,
        "remote_manifest": str(control / "manifest.json"),
        "tasks": {"L1P1": {"task_id": "task", "workspace": str(workspace)}},
    }
    if gpu_python:
        manifest["gpu_python"] = gpu_python
    jobs: list[dict[str, Any]] = []

    def request(manifest: Any, name: str, path: str, body: Any = None) -> Any:
        if body is None:
            return list(jobs)
        job = {
            **body,
            "id": "diagnostic",
            "task_id": "task",
            "budget": None,
            "budget_used": 1,
            "state": "queued",
            "cwd": str(workspace),
        }
        jobs.append(job)
        return job

    monkeypatch.setitem(handle.__globals__, "task_request", request)
    payload = {
        "operation": "submit_diagnostic",
        "task": "L1P1",
        "key": "same-experiment",
        "source": "",
        "script": base64.b64encode(b"print('probe')").decode(),
    }
    first = handle(manifest, payload)
    second = handle(manifest, payload)
    assert first["id"] == second["id"] and len(jobs) == 1
    assert jobs[0]["argv"] == [gpu_python or "/opt/.venv/bin/python", "-u", "_rsi_diagnostic.py"]


def test_remote_preparation_rejects_device_only_isolation() -> None:
    require = runpy.run_path("examples/kernelbench_artifacts/control.py")["require_job_isolation"]
    with pytest.raises(ValueError, match="拒绝启动"):
        require({"ok": True, "isolate": True})
    require({"ok": True, "job_isolation": "filesystem-network-v1"})
    remote = runpy.run_path("examples/kernelbench_artifacts/client.py")["remote"]
    for operation in ("submit", "submit_diagnostic"):
        with pytest.raises(ValueError, match="拒绝提交"):
            remote({}, operation)


def test_harness_config_defaults_and_early_budget_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = runpy.run_path("scripts/configure_research.py")
    seed, prepared = tmp_path / "seed", tmp_path / "data"
    seed.mkdir()
    prepared.mkdir()
    operator = tmp_path / "operator.json"
    operator.write_text(
        json.dumps(
            {
                "prepared_data": str(prepared),
                "baseline_harness": str(seed),
                "gpu": {"kind": "local", "cuda_visible_devices": "0"},
            }
        )
    )
    argv = [
        "--example",
        "kernelbench",
        "--operator",
        str(operator),
        "--output",
        str(tmp_path / "research.json"),
        "--target-model",
        "fixture",
    ]
    args = module["parser"]().parse_args(argv)
    calls = []

    def describe(*values: Any) -> dict[str, str]:
        phase = values[4]
        calls.append(phase)
        return {
            "adapter": "kernelbench",
            "phase": phase,
            "split": "development" if phase == "diagnostic" else phase,
            "protocol_id": "a" * 64,
            "manifest_id": str(len(calls)) * 64,
            "target_model": "fixture",
        }

    monkeypatch.setitem(module["build_config"].__globals__, "describe", describe)
    config = module["build_config"](args)
    assert config.needs_finalization and config.finalizer is None
    assert config.brief.limits.max_model_calls is None
    assert config.brief.limits.max_total_tokens is None
    assert config.brief.limits.max_verify_calls is None
    args.max_model_calls = 200
    calls.clear()
    with pytest.raises(ValueError, match="不支持"):
        module["build_config"](args)
    assert not calls
