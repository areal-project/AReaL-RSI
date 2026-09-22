"""Confirm the best verified candidate or an operator's prepared final artifact.

No model participates. Both paths reuse the trusted verifier and CAS identities;
an optional closeout command writes candidate/ under its output directory.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import shutil
from dataclasses import replace
from datetime import datetime
from typing import Any

from areal_rsi.api.base import ContentRef, RunState
from areal_rsi.api.research import CandidateSnapshot, VerifiedResult
from areal_rsi.base.storage import decode, encode, now, write_json
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.verification import read_trusted_result
from areal_rsi.execution import ApprovedCommand, JobManager, JobRecord


async def finalize(c: Coordinator, config: ResearchConfig) -> None:
    if c.state.get("finalization", {}).get("status") == "completed":
        return
    assert config.verifier
    with c.store.paths.execution_lock.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with JobManager(
            c.store.root, gpu_ids=config.brief.limits.gpu_ids, max_jobs=None, auto_monitor=False
        ) as manager:
            try:
                cutoff = c.state.get("finalization", {}).get("exploration_finished_at")
                if cutoff is None:
                    finished = c.store.run().finished_at
                    cutoff = (
                        datetime.fromisoformat(finished).timestamp()
                        if finished
                        else (datetime.now().timestamp())
                    )
                    if c.state.get("started_at") and config.brief.limits.max_wall_seconds:
                        cutoff = min(
                            cutoff,
                            datetime.fromisoformat(c.state["started_at"]).timestamp()
                            + config.brief.limits.max_wall_seconds,
                        )
                c.state["finalization"] = {
                    "status": "running",
                    "exploration_finished_at": cutoff,
                }
                c.save()
                run = c.store.run()
                run.finished_at = None
                c.store.save_run(run)
                c.store.event("finalization_started", summary="探索已结束，正在自动复测和归档")
                seconds = config.brief.limits.max_wall_seconds
                started = c.state.get("started_at")
                remaining = (
                    max(
                        0,
                        seconds
                        - (
                            datetime.now().timestamp() - datetime.fromisoformat(started).timestamp()
                        ),
                    )
                    if seconds is not None
                    and started
                    and config.metadata.get("time_limit_scope", "total") == "total"
                    else None
                )
                async with asyncio.timeout(remaining):
                    await _finish(c, config, manager)
            except asyncio.CancelledError:
                c.state["finalization"].update(status="interrupted")
                c.save()
                if c.state.get("user_stopped"):
                    manager.close(preserve_running=False)
                raise
            except Exception as exc:
                if isinstance(exc, TimeoutError):
                    manager.close(preserve_running=False)
                    exc = TimeoutError("研究已到总时间上限，停止最终复测；已验证的最佳版本仍保留")
                c.state["finalization"].update(status="failed", error=str(exc)[:500])
                c.save()
                c.store.event(
                    "finalization_failed",
                    summary="自动收尾失败，可恢复后继续",
                    error=str(exc)[:500],
                )


async def _finish(c: Coordinator, config: ResearchConfig, manager: JobManager) -> None:
    assert config.verifier
    folder = c.store.root / "finalization"
    folder.mkdir(exist_ok=True)
    jobs_path = c.store.root / "coordination/jobs.json"
    jobs: dict[str, Any] = json.loads(jobs_path.read_text()) if jobs_path.exists() else {}
    selection_path = folder / "candidate-selection.json"
    if config.finalizer and not selection_path.exists():
        withdrawn = []
        for item in c.store.withdrawals():
            candidate = c.store.get("candidates", item["candidate_id"], CandidateSnapshot)
            manifest = await c.store.content.read_json(candidate.content)
            assert isinstance(manifest, dict) and isinstance(manifest["files"], dict)
            withdrawn.append(
                {
                    **item,
                    "files": {
                        name: decode(ContentRef, value).digest
                        for name, value in manifest["files"].items()
                    },
                }
            )
        write_json(
            selection_path,
            {
                "schema_version": 1,
                "run_id": c.store.run_id,
                "withdrawn_candidates": withdrawn,
                **(
                    {
                        "execution_cutoffs": {
                            job_id: c.state["effective_time"]["agents"][info["agent_id"]][
                                "finished_at"
                            ]
                            or c.state["finalization"]["exploration_finished_at"]
                            for job_id, info in jobs.items()
                            if info["agent_id"] in c.state["effective_time"]["agents"]
                            and not info["verification"]
                        }
                    }
                    if config.brief.limits.max_effective_seconds is not None
                    else {}
                ),
            },
            immutable=True,
        )

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
        env = dict(config.execution_env)
        if candidate is None:
            env["RSI_CANDIDATE_SELECTION"] = str(selection_path)
        if config.metadata.get("time_limit_scope") == "exploration":
            env["RSI_EXPLORATION_FINISHED_AT"] = str(
                c.state["finalization"]["exploration_finished_at"]
            )
        spec = replace(command.bind(values, cwd=str(workspace), **metadata), env=env)
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

    phases: tuple[str, ...]
    if config.finalizer:
        await execute(config.finalizer, "prepare")
        # An adapter may choose a lower-scoring eligible artifact, but cannot ignore
        # a withdrawal and publish the same complete artifact under a new identifier.
        content = await c.store.content.snapshot(folder / "output/candidate")
        if any(item["content_digest"] == content.digest for item in c.store.withdrawals()):
            raise ValueError(
                "Finalizer returned a withdrawn candidate; honor RSI_CANDIDATE_SELECTION"
            )
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
        phases = ("search", "final")
    else:
        candidate_id = c.state.get("selected_candidate_id") or ""
        if not candidate_id:
            best = c.store.run().best_result_ref
            if best is None:
                raise ValueError("没有通过正式验证的候选，无法进行最终确认")
            candidate_id = c.store.public_record(best)["candidate_id"]
            c.state["selected_candidate_id"] = candidate_id
            c.save()
        candidate = c.store.get("candidates", candidate_id, CandidateSnapshot)
        if c.store.is_withdrawn(candidate_id):
            raise ValueError("最终候选已撤回，不能发布")
        phases = ("final",)
    final: VerifiedResult | None = None
    for phase in phases:
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
        finalization={**c.state["finalization"], "status": "completed"},
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
        "冻结产物、验证结果和执行记录已归档。"
    )
    c.enqueue("runtime", "user", message, key="automatic-finalization-completed")
    c.store.event("finalization_completed", summary=message)
