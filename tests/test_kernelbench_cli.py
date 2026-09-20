"""Native shell job lifecycle, without external model or GPU requests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import runpy
from pathlib import Path

import pytest
from test_passk import setup
from test_research_runtime_package import ResponsesModel

from areal_rsi.api.research import TaskBrief
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.research_runtime import ResearchRuntime
from areal_rsi.modules.runtime_storage import read_journal

EXAMPLE = Path(__file__).parents[1] / "examples/kernelbench_artifacts"


def test_native_cli_yields_freezes_and_finishes_same_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RSI_API_KEY", "fixture")
    model = ResponsesModel()
    model.force_tool = True
    model.code_tool_input = """
let p = await tools.exec_command({cmd:"kb eval model_new.py",yield_time_ms:1000});
text(p);
const id = p.session_id;
if (!id) throw new Error("Expected a background process session");
while (!(p.output || "").includes("Submitted") && p.session_id) {
  p = await tools.write_stdin({session_id:id,chars:"",yield_time_ms:1000}); text(p);
}
text(await tools.exec_command({cmd:"echo changed > model_new.py",yield_time_ms:1000}));
while (p.session_id) {
  p = await tools.write_stdin({session_id:id,chars:"",yield_time_ms:1000}); text(p);
}
"""

    async def scenario() -> None:
        c, actions, agents = await setup(tmp_path / "study", 1)
        agent = agents[0]
        c.begin_turn(agent)
        workspace = actions.workspace(agent)
        (workspace / "tasks/L2P71").mkdir(parents=True)
        (workspace / "model_new.py").write_text("original")
        execution = ResearchExecution(
            actions, commands={
                "evaluate_task": ApprovedCommand(
                    "evaluate_task", ("echo", "{task}", "{candidate}"), ("task", "candidate"),
                ),
                "diagnose_task": ApprovedCommand(
                    "diagnose_task", ("echo", "{task}", "{script}", "{candidate}", "{arguments}"),
                    ("task", "script", "candidate", "arguments"),
                ),
            }, simulated=True, mock_duration=5,
        )
        runtime = ResearchRuntime(ResearchConfig(
            TaskBrief("test", "test"), backend="codex", base_url=model.url,
            model_settings_pinned=True, metadata={
                "execution_interface": "cli",
                "shell_commands": {name: str(EXAMPLE / "agent_cli.py") for name in ("kb", "gpuq")},
            },
        ))
        events = []

        async def sink(event):
            events.append(event)

        try:
            await asyncio.wait_for(runtime.turn(
                tmp_path / "session", system_prompt="Run eval, then edit after Submitted",
                user_message="Test", tools=actions.definitions(agent),
                handler=actions.handler(agent), workspace=workspace,
                recovery_state={"agent_id": agent}, event_sink=sink,
                incoming_messages=lambda: [{
                    "message_id": "notice", "sender": "system", "message": "Continue exploring",
                }],
            ), 90)
            assert len(execution.jobs) == 1
            job_id = next(iter(execution.jobs))
            record = execution.manager.get(job_id)
            assert record.terminal and record.state == "succeeded"
            assert (Path(record.spec.cwd) / "model_new.py").read_text() == "original"
            assert (workspace / "model_new.py").read_text().strip() == "changed"
            inputs = json.dumps(model.requests[-1]["input"])
            assert job_id in inputs and "Submitted" in inputs and "Continue exploring" in inputs
            assert not any(e["type"] == "message_injected" for e in events)
            journal, _ = read_journal(tmp_path / "session/runtime/data/state")
            calls = [str(e.data.get("name", "")) for e in journal if e.type == "tool.started"]
            assert any("exec_command" in name for name in calls)
            # Runtime projects the whole process as one command event, including
            # later write_stdin calls. The independent edit must finish first.
            completed = [e for e in journal if e.type == "tool.completed"]
            edit = next(e for e in completed if "echo changed" in json.dumps(e.data))
            evaluation = next(e for e in completed if "kb eval" in json.dumps(e.data))
            assert edit.time < evaluation.time
            assert not any("mcp__rsi__run_job" in name for name in calls)
            bridge = json.loads((tmp_path / "session/runtime/research-tools.json").read_text())
            assert "run_job" in bridge["shell_only_tools"]
            execution.mock_duration = 0.1
            (workspace / "repro.py").write_text("import sys; print(sys.argv)")
            process = await asyncio.create_subprocess_exec(
                "/usr/bin/sandbox-exec", "-f", str(tmp_path / "session/runtime/sandbox.sb"),
                str(tmp_path / "session/runtime/bin/gpuq"), "run", "--", "python", "repro.py",
                "two words", cwd=workspace,
                env={"PATH": str(tmp_path / "session/runtime/bin") + ":/usr/bin:/bin"},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            assert process.returncode == 0, (stdout, stderr)
            assert len(execution.jobs) == 2
            diagnostic = list(execution.jobs.values())[-1]
            assert json.loads(diagnostic["parameters"]["arguments"]) == ["two words"]
            # Deadline/stop remains enforced behind the CLI, with no new job.
            c.stop("test stop")
            process = await asyncio.create_subprocess_exec(
                "/usr/bin/sandbox-exec", "-f", str(tmp_path / "session/runtime/sandbox.sb"),
                str(tmp_path / "session/runtime/bin/kb"), "eval", "model_new.py",
                cwd=workspace, env={
                    "PATH": str(tmp_path / "session/runtime/bin") + ":/usr/bin:/bin",
                }, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            assert process.returncode == 2, (stdout, stderr)
            assert "stopped" in stdout.decode().lower()
            assert len(execution.jobs) == 2
        finally:
            await runtime.close()
            execution.close(cancel=True)

    try:
        asyncio.run(scenario())
    finally:
        model.close()


def test_prepare_freezes_cli_and_preserves_tools_choice(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RSI_BACKEND", "codex")
    namespace = runpy.run_path(str(EXAMPLE / "demo.py"))
    prepare = namespace["prepare"]
    source = b"class Model: pass\n"
    prepared = {
        "manifest": {
            "protocol_id": "fixture", "manifest_id": "fixture",
            "job_isolation": "filesystem-network-v1", "tasks": {"L2P71": {
                "reference_sha256": hashlib.sha256(source).hexdigest(),
                "reference_baseline": {"ref_ms": 1.0},
            }},
        },
        "references": {"L2P71": {"source": base64.b64encode(source).decode()}},
    }
    prepare.__globals__["control_request"] = lambda *args: prepared
    environment = tmp_path / "env.json"
    environment.write_text("{}")
    for interface in ("cli", "tools"):
        config = prepare(
            tmp_path, environment, interface, task="L2P71", check_credentials=False,
            collaboration_modules=(), execution_interface=interface,
        )
        metadata = config["metadata"]
        seed = Path(config["seed_dir"])
        assert metadata["execution_interface"] == interface
        if interface == "cli":
            assert (seed / "reference.py").read_bytes() == source
            assert "kb eval" in metadata["execution_instructions"]
            assert "run_job(wait=true)" not in (seed / "RULES.md").read_text()
            assert Path(metadata["shell_commands"]["kb"]).read_bytes() == (
                EXAMPLE / "agent_cli.py"
            ).read_bytes()
        else:
            assert "shell_commands" not in metadata
            assert "run_job(wait=true)" in metadata["execution_instructions"]


def test_remote_failure_is_not_hidden_by_successful_transport() -> None:
    state = runpy.run_path(str(EXAMPLE / "agent_cli.py"))["job_state"]
    assert state({"state": "succeeded", "feedback": {"data": {"state": "failed"}}}) == "failed"
    assert state({"state": "failed", "feedback": {"data": {"state": "succeeded"}}}) == "failed"
