"""Core demo contract: HTTP launch/reuse, autonomous closeout, and no duplicate GPU finals."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import runpy
import socket
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from areal_rsi.api.research import TaskBrief, VerificationContract
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.responses import ResponsesConfig, ResponsesRuntime
from areal_rsi.research_app import ResearchApp
from areal_rsi.ui.launch import ensure_workbench
from areal_rsi.ui.workbench import WorkbenchServer


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


@pytest.mark.parametrize(
    "retry_verifier,deadline,withdraw_final",
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (True, True, False),
        (False, False, True),
    ],
)
def test_configured_http_run_finishes_without_a_monitor_and_reuses_service(
    tmp_path: Path, retry_verifier: bool, deadline: bool, withdraw_final: bool
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
    if deadline:
        config.brief.limits.max_wall_seconds = 0.2
        config.metadata["time_limit_scope"] = "exploration"
    app = ResearchApp(
        tmp_path,
        runtime_factory=lambda _: ResponsesRuntime(
            ResponsesConfig(base_url="https://example.test", api_key="test"),
            StopTransport("wait" if deadline else "stop_run"),
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


@pytest.mark.parametrize("withdraw_fastest", [False, True])
def test_remote_final_confirmation_survives_lost_reply_without_new_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, withdraw_fastest: bool
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
            "evaluation": {"correct": True, "speedup": 2, "evaluation_policy": "strict-fp32-v1"},
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
                "evaluation": {**jobs[0]["evaluation"], "speedup": 5},
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
    output = tmp_path / "output"
    with pytest.raises(TimeoutError):
        finish(manifest, output)
    finish(manifest, output)
    result = json.loads((output / "final-selection.json").read_text())
    assert result["status"] == "completed"
    assert len(submissions) == 1 and submissions[0]["seed"] == 137
    assert (tmp_path / "final/model_new.py").read_text() == "best passing code"
    assert (control / "exploration-closed").exists()


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
            **body, "id": "diagnostic", "task_id": "task", "budget": None,
            "budget_used": 1, "state": "queued", "cwd": str(workspace),
        }
        jobs.append(job)
        return job

    monkeypatch.setitem(handle.__globals__, "task_request", request)
    payload = {
        "operation": "submit_diagnostic", "task": "L1P1", "key": "same-experiment",
        "source": "", "script": base64.b64encode(b"print('probe')").decode(),
    }
    first = handle(manifest, payload)
    second = handle(manifest, payload)
    assert first["id"] == second["id"] and len(jobs) == 1
    assert jobs[0]["argv"] == [gpu_python or "/opt/.venv/bin/python", "-u", "_rsi_diagnostic.py"]
