from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from areal_rsi.api.research import (
    CandidateSnapshot,
    TaskBrief,
    VerificationContract,
    VerifiedResult,
)
from areal_rsi.base.storage import now, write_json
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.coordination.store import ResearchStore
from areal_rsi.coordination.verification import read_trusted_result
from areal_rsi.execution import ApprovedCommand, JobRecord, JobSpec
from areal_rsi.modules.responses import ToolCall


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


@pytest.mark.parametrize("adapter", ["content", "kernelbench", "harness"])
@pytest.mark.parametrize("phase", ["search", "final"])
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
            verified = runner.jobs[source["verification_job_id"]]
            assert verified["source_job_id"] == submitted.output["job_id"]
            assert verified["result"]["score"] == 0.7
            assert c.store.run().best_result_ref is not None
            await runner.poll()
            assert len(runner.jobs) == 2
        finally:
            runner.close(cancel=True)

    asyncio.run(scenario())
