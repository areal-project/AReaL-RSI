"""Run an operator's closeout command and publish its frozen, verified product.

No model participates. The command writes candidate/ under its output directory;
search/final verification uses the existing trusted verifier and CAS identities.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import shutil
from dataclasses import replace
from typing import Any

from areal_rsi.api.base import RunState
from areal_rsi.api.research import CandidateSnapshot, VerifiedResult
from areal_rsi.base.storage import encode, now, write_json
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.verification import read_trusted_result
from areal_rsi.execution import ApprovedCommand, JobManager, JobRecord


async def finalize(c: Coordinator, config: ResearchConfig) -> None:
    if c.state.get("finalization", {}).get("status") == "completed":
        return
    assert config.finalizer and config.verifier
    with c.store.paths.execution_lock.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with JobManager(c.store.root, max_jobs=None, auto_monitor=False) as manager:
            try:
                c.state["finalization"] = {"status": "running"}
                c.save()
                run = c.store.run()
                run.finished_at = None
                c.store.save_run(run)
                c.store.event("finalization_started", summary="探索已结束，正在自动复测和归档")
                await _finish(c, config, manager)
            except asyncio.CancelledError:
                c.state["finalization"] = {"status": "interrupted"}
                c.save()
                if c.state.get("user_stopped"):
                    manager.close(preserve_running=False)
                raise
            except Exception as exc:
                c.state["finalization"] = {"status": "failed", "error": str(exc)[:500]}
                c.save()
                c.store.event(
                    "finalization_failed",
                    summary="自动收尾失败，可恢复后继续",
                    error=str(exc)[:500],
                )


async def _finish(c: Coordinator, config: ResearchConfig, manager: JobManager) -> None:
    assert config.finalizer and config.verifier
    folder = c.store.root / "finalization"
    folder.mkdir(exist_ok=True)
    jobs_path = c.store.root / "coordination/jobs.json"
    jobs: dict[str, Any] = json.loads(jobs_path.read_text()) if jobs_path.exists() else {}

    async def execute(
        command: ApprovedCommand, phase: str, candidate: CandidateSnapshot | None = None
    ) -> JobRecord:
        # Failed local processes may be relaunched; the output/remote operation IDs stay fixed.
        attempt = 0
        while True:
            key = "job-" + hashlib.sha256(f"closeout:{phase}:{attempt}".encode()).hexdigest()
            path = c.store.root / "jobs" / key / "job.json"
            if not path.exists() or manager.get(key).state not in {
                "failed",
                "cancelled",
                "timed_out",
                "lost",
            }:
                break
            attempt += 1
        output = (
            folder / "output" if candidate is None else c.store.root / "execution-outputs" / key
        )
        workspace = folder
        values = {"output": str(output)}
        metadata = {"agent_id": "rsi", "verification": str(candidate is not None).lower()}
        if candidate:
            inputs = c.store.root / "execution-inputs" / key
            workspace = inputs / "workspace"
            write_json(inputs / "snapshot.json", candidate.content, immutable=True)
            ready = inputs / "ready.json"
            if not ready.exists():
                if workspace.exists():
                    shutil.rmtree(workspace)
                await c.store.content.materialize(candidate.content, workspace)
                write_json(ready, candidate.content, immutable=True)
            values.update(workspace=str(workspace), phase=phase)
            metadata.update(
                candidate_id=candidate.candidate_id,
                candidate_digest=candidate.content.digest,
                contract_digest=c.store.contract_digest(),
                phase=phase,
            )
        output.mkdir(parents=True, exist_ok=True)
        spec = replace(
            command.bind(values, cwd=str(workspace), **metadata), env=dict(config.execution_env)
        )
        jobs.setdefault(
            key,
            {
                "agent_id": "rsi",
                "iteration_id": None,
                "candidate_id": candidate.candidate_id if candidate else None,
                "verification": candidate is not None,
                "phase": phase,
                "output": str(output),
                "waiting": False,
                "processed": False,
                "spec": encode(spec),
                "created_at": now(),
            },
        )
        write_json(jobs_path, jobs)
        record = manager.submit(spec, job_id=key)
        while not record.terminal:
            await asyncio.sleep(0.5)
            manager.poll()
            record = manager.get(key)
        if record.state != "succeeded":
            raise RuntimeError(f"{command.name} 未完成（{record.state}），请查看作业 {key}")
        jobs[key]["processed"] = True
        write_json(jobs_path, jobs)
        return record

    await execute(config.finalizer, "prepare")
    candidate_id = "candidate-closeout"
    path = c.store.root / "candidates" / f"{candidate_id}.json"
    candidate = (
        c.store.get("candidates", candidate_id, CandidateSnapshot)
        if path.exists()
        else await c.store.snapshot(
            folder / "output/candidate",
            agent_id="operator",
            description="探索结束后冻结并独立复测的最终版本",
            candidate_id=candidate_id,
        )
    )
    final: VerifiedResult | None = None
    for phase in ("search", "final"):
        job = await execute(config.verifier, phase, candidate)
        path = c.store.root / "verifications" / f"{job.job_id}.json"
        result = (
            c.store.get("verifications", job.job_id, VerifiedResult)
            if path.exists()
            else read_trusted_result(
                c.store, job, candidate, phase, identity=config.metadata["verification_identity"]
            )
        )
        if result.status != "succeeded":
            raise RuntimeError(result.error or "可信验证未完成")
        ref = (
            c.store.record_verification(result)
            if not path.exists()
            else c.store.paths.reference(path)
        )
        jobs[job.job_id]["result"] = {**encode(result), "uri": ref.uri}
        write_json(jobs_path, jobs)
        if phase == "search":
            c.store.consider_best(result.result_id)
        else:
            final = result
            run = c.store.run()
            run.final_result_ref = ref
            c.store.save_run(run)
    assert final is not None
    c.state.update(
        selected_candidate_id=candidate_id,
        finalization={"status": "completed"},
        verify_calls=sum(bool(item["verification"]) for item in jobs.values()),
    )
    contract = c.store.brief().verification
    reached = (
        final.passed
        and final.score is not None
        and contract is not None
        and contract.target is not None
        and (
            final.score >= contract.target
            if contract.direction == "maximize"
            else final.score <= contract.target
        )
    )
    c.state["status"] = "succeeded" if reached else "stopped"
    run = c.store.run()
    run.state, run.finished_at = (RunState.SUCCEEDED if reached else RunState.CANCELLED), now()
    c.store.save_run(run)
    c.save()
    message = (
        f"自动收尾完成，最终 {contract.metric if contract else 'score'}={final.score}。"
        "冻结源码、逐题成绩和提交记录已归档。"
    )
    c.enqueue("runtime", "user", message, key="automatic-finalization-completed")
    c.store.event("finalization_completed", summary=message)
