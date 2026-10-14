from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from runtime_fakes import ActionRuntime

from areal_rsi.api.interfaces import ToolCall
from areal_rsi.api.records import RecordRef
from areal_rsi.api.research import (
    CandidateSnapshot,
    ResearchLimits,
    TaskBrief,
    VerificationContract,
    VerifiedResult,
)
from areal_rsi.infra.coordination.config import ResearchConfig, config_from_dict
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.coordination.evaluation.jobs import ResearchExecution
from areal_rsi.infra.coordination.evaluation.verification import read_trusted_result
from areal_rsi.infra.coordination.store import ResearchStore
from areal_rsi.infra.coordination.tools.actions import ResearchActions
from areal_rsi.infra.execution import ApprovedCommand, JobRecord, JobSpec
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.infra.storage.files import encode, now, write_json


def test_command_parameter_help_survives_config_and_preserves_execution(tmp_path: Path) -> None:
    async def scenario() -> None:
        command = ApprovedCommand(
            "produce",
            (sys.executable, "-c", "import pathlib,sys; "
             "pathlib.Path(sys.argv[3], sys.argv[2]).write_text(sys.argv[1])",
             "{dataset}", "{artifact}", "{output}"),
            ("workspace", "output", "dataset", "artifact"),
        )
        raw = ResearchConfig(
            TaskBrief("Research", "Produce a document"), commands=(command,),
        ).public()
        # Old persisted configurations need no migration or new required fields.
        raw["commands"][0].pop("parameter_descriptions", None)
        legacy = config_from_dict(raw).commands[0]
        descriptions = {
            "dataset": "Dataset identifier, for example survey-2026; not a file path.",
            "artifact": "Output filename, including extension; spaces are allowed.",
            "workspace": "Internal input mount",
            "output": "Internal output mount",
        }
        raw["commands"][0]["parameter_descriptions"] = descriptions
        config = config_from_dict(json.loads(json.dumps(raw)))
        assert config.public()["commands"][0]["parameter_descriptions"] == descriptions
        restored = config.commands[0]
        values = dict(workspace="input", output="output", dataset="survey", artifact="report.txt")
        assert legacy.bind(values, cwd="input") == restored.bind(values, cwd="input")
        c = Coordinator.create(tmp_path, config.brief)
        c.start()
        owner = c.spawn(
            key="owner", direction="Documents", instruction="Produce a report",
        )["agent_id"]
        runner = ResearchExecution(ResearchActions(c), commands={restored.name: restored})
        try:
            listed = await runner.handler(owner)(ToolCall("get_commands", {}, "help"))
            public = listed.output["commands"][0]
            assert public["parameters"] == ["dataset", "artifact"]
            assert public["parameter_descriptions"] == {
                key: descriptions[key] for key in ("dataset", "artifact")
            }
            assert "Internal" not in json.dumps(listed.output)
            dataset = "survey $(literal identifier)"
            artifact = "report with spaces.txt"
            result = await runner.submit(owner, ToolCall("run_job", {
                "command_name": "produce",
                "parameters_json": json.dumps({"dataset": dataset, "artifact": artifact}),
            }, "produce"))
            async with asyncio.timeout(10):
                while await runner.poll():
                    await asyncio.sleep(0.05)
            job_id = result.output["job_id"]
            assert runner.manager.get(job_id).state == "succeeded"
            assert Path(runner.jobs[job_id].output, artifact).read_text() == dataset
        finally:
            runner.close(cancel=True)

    asyncio.run(scenario())


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
    output = store.root / "execution-outputs" / job.job_id / "evaluation.json"
    write_json(output, result)
    return store, job, candidate, identity, result, output


@pytest.mark.parametrize("adapter,phase", [("content", "search"), ("kernelbench", "final")])
def test_real_result_binds_frozen_candidate_contract_and_phase(
    tmp_path: Path, adapter: str, phase: str
) -> None:
    store, job, candidate, identity, _result, _output = fixture(tmp_path, adapter, phase)
    verified = read_trusted_result(store, job, candidate, phase, identity=identity)
    assert verified.score == 0.7 and verified.phase == phase
    assert verified.contract_digest == job.spec.metadata["contract_digest"]
    assert verified.candidate_id == candidate.candidate_id and not verified.simulated


def test_job_artifact_is_frozen_and_scored_automatically(tmp_path: Path) -> None:
    async def scenario() -> None:
        brief = TaskBrief("研究", "产物验证", verification=VerificationContract("score"))
        c = Coordinator.create(tmp_path, brief)
        c.start()
        agent = c.spawn(key="a", direction="数据", instruction="生成产物")["agent_id"]
        command = ApprovedCommand(
            "produce",
            (sys.executable, "-c", "import sys;from pathlib import Path; "
             "Path(sys.argv[1], 'value.txt').write_text('0.7')", "{output}"),
            ("output",), auto_verify_output=".",
        )
        runner = ResearchExecution(
            ResearchActions(c), commands={"produce": command},
            verifier=ApprovedCommand("verify", (sys.executable, "-c", "pass")),
            read_verification=lambda job, candidate, phase: VerifiedResult(
                job.job_id, candidate.candidate_id, c.store.contract_digest(), now(),
                "succeeded", float(Path(job.spec.cwd, "value.txt").read_text()), True,
                phase="search",
            ),
        )
        try:
            submitted = await runner.submit(
                agent,
                ToolCall("run_job", {"command_name": "produce", "parameters_json": "{}"}, "a"),
            )
            async with asyncio.timeout(10):
                while await runner.poll():
                    await asyncio.sleep(0.05)
            source = runner.jobs[submitted.output["job_id"]]
            assert source.verification_job_id is not None
            verified = runner.jobs[source.verification_job_id]
            assert verified.result is not None and verified.result_ref is not None
            assert verified.source_job_id == submitted.output["job_id"]
            assert verified.result.score == 0.7
            # Trusted verification keeps its existing result namespace; no second
            # public record with the same ID exposes private verifier execution.
            verification_path = (
                c.store.root / "executions" / f"{source.verification_job_id}.json"
            )
            assert not verification_path.exists()
            assert c.store.record_reference(RecordRef(source.verification_job_id)).uri == (
                verified.result_ref.uri
            )
            write_json(Path(verified.output) / "progress.json", {"message": "private verifier"})
            verification_job = runner.manager.get(source.verification_job_id)
            assert "progress" not in runner.for_agent(verification_job, agent)
            Path(verification_job.log_path).write_text("private verification details")
            for viewer in (agent, "rsi"):
                assert "failure_log" not in runner.for_agent(
                    replace(verification_job, state="failed"), viewer,
                )
            assert c.store.run().best_result_ref is not None
            await runner.poll()
            assert len(runner.jobs) == 2
        finally:
            runner.close(cancel=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("committed", [False, True])
def test_job_receipt_publication_recovers_without_resubmission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, committed: bool
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Research", ""))
        c.start()
        owner = c.spawn(key="owner", direction="owner", instruction="Investigate")["agent_id"]
        actions = ResearchActions(c)
        command = ApprovedCommand("produce", (sys.executable, "-c", "pass"))
        runner = ResearchExecution(
            actions, commands={"produce": command}, simulated=True, mock_duration=60,
        )
        call = ToolCall("run_job", {"command_name": "produce", "parameters_json": "{}"}, "job")
        original_put = c.store.put

        def interrupted_put(category: str, key: str, value: Any, **kwargs: Any) -> RecordRef:
            if category == "executions":
                if committed:
                    original_put(category, key, value, **kwargs)
                raise RuntimeError("publication interrupted")
            return original_put(category, key, value, **kwargs)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(c.store, "put", interrupted_put)
                with pytest.raises(RuntimeError, match="publication interrupted"):
                    await runner.submit(owner, call)
            job_id = next(iter(runner.jobs))
            runner.close()
            recovered = Coordinator(c.store)
            actions = ResearchActions(recovered)
            runner = ResearchExecution(
                actions, commands={"produce": command}, simulated=True, mock_duration=60,
            )
            replay = await runner.submit(owner, call)
            assert replay.output["job_id"] == job_id and len(runner.jobs) == 1
            ref = recovered.store.record_reference(RecordRef(job_id))
            assert actions.read_record(owner, ref.uri)["terminal"] is False
            assert ref in recovered.actor(owner).iterations[-1].references
        finally:
            runner.close(cancel=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("exit_code", [0, 7])
def test_running_job_receipt_and_private_logs_survive_recovery(
    tmp_path: Path, exit_code: int
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Research", "", ResearchLimits(max_agents=2)))
        c.start()
        owner, peer = [
            c.spawn(key=key, direction=key, instruction="Investigate")["agent_id"]
            for key in ("owner", "peer")
        ]
        c.begin_turn(owner)
        actions = ResearchActions(c)
        command = ApprovedCommand(
            "produce",
            (sys.executable, "-c", "import pathlib,sys,time; "
             "gate=pathlib.Path(sys.argv[1], 'gate'); "
             "exec('while not gate.exists(): time.sleep(0.01)'); "
             "print('verbose prefix:' + 'x' * 20000, flush=True); "
             "print('Missing configured input', file=sys.stderr, flush=True); "
             f"sys.exit({exit_code})", "{output}"),
            ("output",),
        )
        runner = ResearchExecution(
            actions, commands={"produce": command},
            execution_env={"PRIVATE_EXECUTION_SECRET": "operator-secret"},
        )
        call = ToolCall("run_job", {"command_name": "produce", "parameters_json": "{}"}, "job")
        try:
            submitted = await runner.submit(owner, call)
            job_id = submitted.output["job_id"]
            # The handle must already resolve while the process waits on its gate.
            ref = c.store.record_reference(RecordRef(job_id))
            initial = actions.read_record(owner, ref.uri)
            assert initial["job_id"] == job_id and not initial["terminal"]
            assert not initial["output_available"] and initial["exit_code"] is None
            assert submitted.output["uri"] == ref.uri
            assert submitted.output["record_id"] == job_id
            write_json(Path(runner.jobs[job_id].output) / "progress.json", {
                "stage": "transport", "message": "Retrying connection", "score": 9.0,
                "unpublished": "private operator field",
            })
            record = runner.manager.get(job_id)
            for viewer in (owner, "rsi"):
                assert runner.for_agent(record, viewer)["progress"] == {
                    "stage": "transport", "message": "Retrying connection",
                }
            assert "progress" not in runner.for_agent(record, peer)
            assert "failure_log" not in runner.for_agent(record, owner)
            assert "operator-secret" not in json.dumps(initial)
            assert not ({"spec", "env", "command", "cwd", "output"} & initial.keys())
            with pytest.raises(ValueError, match="not shared"):
                actions.read_record(peer, job_id)
            c.end_turn(owner)
            runner.close()
            recovered = Coordinator(c.store)
            actions = ResearchActions(recovered)
            runner = ResearchExecution(actions, commands={"produce": command})
            replay = await runner.submit(owner, call)
            assert replay.output["job_id"] == job_id and len(runner.jobs) == 1
            assert actions.read_record(owner, job_id) == initial
            Path(runner.jobs[job_id].output, "gate").touch()
            async with asyncio.timeout(10):
                while await runner.poll():
                    await asyncio.sleep(0.02)
            final = actions.read_record(owner, job_id)
            assert final["terminal"] and final["exit_code"] == exit_code
            assert final["state"] == ("succeeded" if exit_code == 0 else "failed")
            assert final["output_available"] == (exit_code == 0)
            assert final["created_at"] == initial["created_at"]
            assert recovered.store.record_reference(RecordRef(job_id)) == ref
            assert runner.jobs[job_id].result_ref == ref
            assert recovered.store.run().best_result_ref is None
            assert len(list((recovered.store.root / "executions").glob("*.json"))) == 1
            status = await runner.handler(owner)(
                ToolCall("job_status", {"job_id": job_id}, "status")
            )
            if exit_code:
                log = status.output["failure_log"]
                assert log["truncated"] and len(log["text"].encode()) <= 16000
                assert log["text"].endswith("Missing configured input\n")
                assert "verbose prefix:" not in log["text"]
                assert runner.for_agent(runner.manager.get(job_id), "rsi")["failure_log"] == log
                assert "failure_log" not in runner.for_agent(runner.manager.get(job_id), peer)
                runner.jobs[job_id].feedback = {"content": "Published failure details"}
                assert "failure_log" not in runner.for_agent(runner.manager.get(job_id), owner)
                runner.jobs[job_id].feedback = None
                Path(runner.manager.get(job_id).log_path).unlink()
                assert "failure_log" not in runner.for_agent(runner.manager.get(job_id), owner)
            else:
                assert "failure_log" not in status.output
        finally:
            runner.close(cancel=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("target", [None, 0.5])
def test_verifier_only_run_automatically_confirms_best_candidate(
    tmp_path: Path, target: float | None
) -> None:
    script = tmp_path / "verify.py"
    script.write_text("""import json, os, sys, time
from pathlib import Path
output, workspace, phase = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
assert phase == 'final' and (workspace/'answer').read_text() == 'best'
assert os.environ['CUDA_VISIBLE_DEVICES'] == '0'
time.sleep(0.15)
job = json.loads((workspace.parents[2]/'jobs'/output.name/'job.json').read_text())
meta = job['spec']['metadata']
(output/'evaluation.json').write_text(json.dumps(dict(
    status='completed', simulated=False, phase=phase, metric='quality', score=0.4,
    metrics={'quality':0.4}, protocol_id='fixed', manifest_id='final',
    candidate_id=meta['candidate_id'], candidate_digest=meta['candidate_digest'])))
""")
    config = ResearchConfig(
        TaskBrief(
            "Research",
            "Improve answer",
            verification=VerificationContract(
                "quality", target=target, identity={"require_final_confirmation": True}
            ),
        ),
        verifier=ApprovedCommand(
            "verify",
            (sys.executable, str(script), "{output}", "{workspace}", "{phase}"),
            ("output", "workspace", "phase"),
            gpu_count=1,
        ),
        metadata={
            "time_limit_scope": "exploration",
            "verification_identity": {
                "adapter": "content",
                "phases": {
                    p: {"protocol_id": "fixed", "manifest_id": p} for p in ("search", "final")
                },
            },
        },
    )
    config.brief.limits.gpu_ids = ("0",)

    async def scenario() -> None:
        config.mode = "legacy_passk"
        app = ResearchSystem(tmp_path, runtime_factory=lambda _: ActionRuntime())
        try:
            run_id = await app.create(config)
            c = app.coordinator(run_id)
            workspace = tmp_path / "candidate"
            workspace.mkdir()
            for text, score in (("fallback", 0.6), ("best", 0.9)):
                (workspace / "answer").write_text(text)
                candidate = await c.store.snapshot(workspace, agent_id="operator", description=text)
                result = VerifiedResult(
                    text,
                    candidate.candidate_id,
                    c.store.contract_digest(),
                    now(),
                    "succeeded",
                    score=score,
                    passed=True,
                )
                c.store.record_verification(result)
                c.store.consider_best(result.result_id)
            await app.start(c.store.run_id)
            c = app.coordinator(run_id)
            active = app.active[c.store.run_id]
            await active.engine.tick()
            if target is None:
                c.stop("Exploration budget ended")
            assert c.state.status == "stopped", "Search alone must not declare success"
            async with asyncio.timeout(3):
                while c.completion is None:
                    await asyncio.sleep(0.01)
            with (
                c.store.paths.execution_lock.open("a+") as lock,
                pytest.raises(BlockingIOError),
            ):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Native tool waiters may deliver a late poll during final verification.
            # Final jobs must survive both cancellation and scheduler reconciliation.
            async with asyncio.timeout(10):
                while not active.task.done():
                    await active.execution.poll()
                    await asyncio.sleep(0.01)
            await active.task
            assert c.state.finalization is not None
            assert c.state.finalization.status == "completed"
            final_ref = c.store.run().final_result_ref
            assert final_ref is not None
            final = c.store.public_record(final_ref)
            assert final["score"] == 0.4 and final["candidate_id"] == candidate.candidate_id
            assert c.state.status == "completed"
            assert c.store.run().state.value == "completed"
            assert c.completion is not None
            assert c.completion.target_reached is (False if target is not None else None)
            before = c.path.read_bytes()
            with pytest.raises(ValueError, match="Only planned"):
                await app.resume(c.store.run_id)
            assert c.path.read_bytes() == before
            await app.stop(c.store.run_id)
            assert c.path.read_bytes() == before
            jobs = json.loads((c.store.root / "coordination/jobs.json").read_text())
            assert len(jobs) == 1 and next(iter(jobs.values()))["phase"] == "final"
            await app.detail(run_id)
            assert len(list((c.store.root / "jobs").glob("*/job.json"))) == 1
        finally:
            await app.close()

    asyncio.run(scenario())


def test_completion_recovers_intent_without_retrying_an_unreleased_job(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Research", "Return a result"))
        c.start()
        c.stop("Exploration ended")
        c.begin_completion()
        output = c.store.root / "finalization/output"
        output.mkdir(parents=True)
        marker = output / "calls"
        command = ApprovedCommand(
            "prepare", (sys.executable, "-c", "import sys; from pathlib import Path; "
                        "p=Path(sys.argv[1]); p.write_text('once')", str(marker), "{output}"),
            ("output",),
        )
        spec = command.bind({"output": str(output)}, cwd=str(output.parent))
        key = "job-" + hashlib.sha256(b"closeout:prepare:0").hexdigest()
        runner = ResearchExecution(ResearchActions(c))
        old = runner.manager.submit(spec, job_id=key)
        record_path = Path(old.result_path).with_name("job.json")
        write_json(record_path, replace(old, state="lost", resources_released=False))
        index = c.store.root / "coordination/jobs.json"
        write_json(index, {key: {
            "agent_id": "rsi", "output": str(output), "spec": encode(spec), "created_at": now(),
            "verification": False, "phase": "prepare", "processed": False, "completion": True,
        }})
        before = index.read_bytes()
        runner.close()
        recovered = ResearchExecution(ResearchActions(Coordinator(c.store)))
        try:
            assert recovered.jobs[key].completion
            assert index.read_bytes() == before, "Reading old records must not migrate them on disk"
            await recovered.cancel_pending(exploration_only=True)
            with pytest.raises(RuntimeError, match="unresolved resource claim"):
                await recovered.execute_completion(command, "prepare")
            assert not marker.exists() and len(recovered.manager.records()) == 1
            # After an owner confirms release, the same closing operation can safely retry.
            write_json(record_path, replace(recovered.manager.get(key), resources_released=True))
            completed = await recovered.execute_completion(command, "prepare")
            assert completed.processed and marker.read_text() == "once"
            count = len(recovered.manager.records())
            await recovered.execute_completion(command, "prepare")
            assert len(recovered.manager.records()) == count and marker.read_text() == "once"
            assert all(job.completion for job in c.store.research_jobs().values())
        finally:
            recovered.close()

    asyncio.run(scenario())
