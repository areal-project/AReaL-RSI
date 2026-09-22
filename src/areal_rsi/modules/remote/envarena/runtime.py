"""Batch Job execution, durable receipts, normalized evidence, and remote grading."""

from __future__ import annotations

import asyncio
import fcntl
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from areal_rsi.api.base import (
    BenchmarkSplit,
    ContentRef,
    EvaluationReport,
    JsonObject,
    SolutionVersion,
    Task,
    TaskResult,
    Trajectory,
    TrajectoryRef,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
)
from areal_rsi.base.storage import (
    FileContentStore,
    FileTrajectoryStore,
    decode,
    encode,
    now,
    safe_relative,
    write_json,
)
from areal_rsi.modules.remote.envarena.benchmark import ArenaBenchmark
from areal_rsi.modules.remote.envarena.client import (
    ArenaClient,
    ArenaError,
    fingerprint,
    redact,
    reference,
    segment,
)
from areal_rsi.modules.remote.envarena.config import ArenaConfig
from areal_rsi.modules.remote.envarena.harness import REMOTE_HARNESS_TYPE

TERMINAL = {"DONE", "FAILED", "CANCELLED", "TIMEOUT", "STOPPED"}


class AmbiguousSubmission(RuntimeError):
    """The request may have succeeded. Reconcile; never retry POST blindly."""


class ArenaHarnessRuntime:
    def __init__(
        self,
        client: ArenaClient,
        content: FileContentStore,
        trajectories: FileTrajectoryStore,
        benchmark: ArenaBenchmark,
        config: ArenaConfig,
        root: Path,
        emit: Callable[[str], None] = lambda _: None,
    ):
        self.client, self.content, self.trajectories = client, content, trajectories
        self.benchmark, self.config, self.root, self.emit = benchmark, config, root, emit
        self.locks: dict[str, asyncio.Lock] = {}

    async def run(
        self, solution: SolutionVersion, task: Task, *, config: JsonObject | None = None
    ) -> TaskResult:
        return (await self.run_many(solution, [task], config=config))[task.task_id]

    async def run_many(
        self, solution: SolutionVersion, tasks: Sequence[Task], *, config: JsonObject | None = None
    ) -> Mapping[str, TaskResult]:
        execution_key = (config or {}).get("execution_key")
        if not isinstance(execution_key, str) or not execution_key:
            raise ValueError("remote 执行需要稳定的 execution_key")
        key = fingerprint(execution_key)
        async with self.locks.setdefault(key, asyncio.Lock()):
            locks = self.root / "locks"
            locks.mkdir(parents=True, exist_ok=True)
            with (locks / f"{key}.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError("另一个执行器正在处理相同的 Arena execution_key") from None
                return await self._run(solution, tasks, config or {}, key)

    async def _run(
        self, solution: SolutionVersion, tasks: Sequence[Task], config: JsonObject, key: str
    ) -> Mapping[str, TaskResult]:
        if solution.artifacts:
            raise ValueError("remote Harness 实验暂不接受 Artifact 输入")
        if solution.harness.content.media_type != REMOTE_HARNESS_TYPE:
            raise ValueError("remote 执行需要 Arena Harness 配置引用")
        harness = json.loads(await self.content.read_text(solution.harness.content))
        model = json.loads(await self.content.read_text(solution.model.content))
        if reference(model["ref"]) != self.config.model:
            raise ValueError("remote Harness 实验禁止改变模型")
        if not tasks or len({t.task_id for t in tasks}) != len(tasks):
            raise ValueError("远端 Task 集合必须非空且无重复")
        signature = {
            "execution_key": config["execution_key"],
            "run_id": config.get("run_id"),
            "base_url": self.client.connection.base_url,
            "solution_id": solution.solution_id,
            "harness": harness,
            "model": model,
            "tasks": encode(tasks),
            "policy": {
                "max_parallel": self.config.max_parallel,
                "max_retries": self.config.max_retries,
            },
        }
        folder = self.root / "executions" / key
        receipt = folder / "receipt.json"
        if receipt.exists():
            record = json.loads(receipt.read_text())
            if record["signature"] != signature:
                raise ValueError("execution_key 已绑定不同配置，拒绝复用")
        else:
            record = {
                "signature": signature,
                "tag": f"rsi-execution:{key}",
                "job_id": None,
                "state": "prepared",
                "created_at": now(),
            }
            write_json(receipt, record)
        job_id = record.get("job_id")
        try:
            if not job_id:
                if record["state"] in {"submitting", "unknown", "stop_requested"}:
                    jobs = await self.client.items("/jobs", "jobs", tag=record["tag"])
                    matches = [j for j in jobs if record["tag"] in j.get("tags", [])]
                    if len(matches) != 1:
                        raise AmbiguousSubmission(
                            "Arena 提交结果待核对；按 receipt.json 的唯一 tag 查 Job，禁止盲目重发"
                        )
                    job = matches[0]
                    if job.get("metadata", {}).get("rsi_request_hash") != fingerprint(signature):
                        raise ValueError("找到的 Arena Job 与本地请求身份不符")
                    job_id = job["job_id"]
                else:
                    subset = await self.benchmark.ensure_subset(tasks)
                    body = {
                        "job_id": f"rsi-{self.config.namespace}-{key[:12]}",
                        "benchmark_ref": subset,
                        "harness_ref": reference(harness["ref"]),
                        "model_ref": reference(model["ref"]),
                        "sample_numbers": 1,
                        "concurrency_policy": {
                            "max_parallel": self.config.max_parallel,
                            "max_retries": self.config.max_retries,
                            "retry_backoff_seconds": 10,
                        },
                        "tags": ["source:areal-rsi", record["tag"]],
                        "metadata": {"rsi_request_hash": fingerprint(signature)},
                    }
                    record.update(state="submitting", request=body)
                    write_json(receipt, record)
                    # Persist a returned job ID even if stop arrives during the HTTP request.
                    submission = asyncio.create_task(self.client.post("/jobs", body))
                    try:
                        response = await asyncio.shield(submission)
                    except asyncio.CancelledError as cancellation:
                        try:
                            response = await submission
                            record.update(job_id=response["job_id"], state="submitted")
                        except ArenaError:
                            record["state"] = "unknown"
                        write_json(receipt, record)
                        raise cancellation
                    except ArenaError as exc:
                        record["state"] = (
                            "rejected" if exc.status in {400, 401, 403, 404, 422} else "unknown"
                        )
                        write_json(receipt, record)
                        raise
                    job_id = response["job_id"]
                record.update(job_id=job_id, state="submitted")
                write_json(receipt, record)
            self.emit(f"Arena job={job_id}")
            deadline = time.monotonic() + self.config.timeout_seconds
            previous = ""
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Arena 评测等待超时，已请求停止；可恢复原 Job")
                try:
                    summary = await self.client.job_summary(job_id)
                except ArenaError as exc:
                    if exc.status not in {0, 429, 500, 502, 503, 504}:
                        raise
                    await asyncio.sleep(self.config.poll_seconds)
                    continue
                status = summary["status"]
                generation = summary.get("execution_generation", 1)
                if "generation" in record and record["generation"] != generation:
                    raise ValueError("Arena Job execution generation 已改变，不能复用旧评测")
                record["generation"] = generation
                record.update(
                    state=status, task_counts=summary.get("task_counts", {}), updated_at=now()
                )
                write_json(receipt, record)
                if status != previous:
                    self.emit(f"Arena {job_id}: {status}")
                    previous = status
                if status == "STOPPED":
                    await self.client.post(f"/jobs/{segment(job_id)}/start")
                elif status in TERMINAL:
                    if status != "DONE":
                        raise RuntimeError(f"Arena Job 终止：{status}；保留 receipt 供排查")
                    break
                await asyncio.sleep(self.config.poll_seconds)
            remote_tasks = await self.client.items(
                f"/jobs/{segment(job_id)}/tasks", "tasks", status="ALL"
            )
            ref_field = self.benchmark.reference_field
            by_item: dict[str, dict[str, Any]] = {}
            for row in remote_tasks:
                ref = reference(row.get(ref_field))
                if ref["key"] in by_item or row.get("sample_index", 0) != 0:
                    raise ValueError("Arena 返回重复评测项或多样本，不能映射单个 TaskResult")
                by_item[ref["key"]] = row
            if set(by_item) != {t.task_id for t in tasks}:
                raise ValueError("Arena 返回的评测项集合与提交集合不一致")
            results = {}
            for task in tasks:
                row = by_item[task.task_id]
                for field, expected in (
                    (ref_field, task.metadata[ref_field]),
                    ("harness_ref", harness["ref"]),
                    ("model_ref", model["ref"]),
                    ("reward_ref", task.metadata["reward_ref"]),
                ):
                    if row.get(field) != expected:
                        raise ValueError(f"Arena Task {field} 与请求不一致")
                cache = folder / f"task-{fingerprint(task.task_id)}.json"
                if cache.exists():
                    saved = json.loads(cache.read_text())
                    if saved["attempt"] != row.get("attempts", 1):
                        raise ValueError("Arena Task attempt 已改变，不能复用旧证据")
                    result = decode(TaskResult, saved["result"])
                else:
                    result = await self._collect(job_id, task, solution, row)
                    write_json(
                        cache, {"attempt": row.get("attempts", 1), "result": result}, immutable=True
                    )
                results[task.task_id] = result
            aggregate = await self.client.get(f"/jobs/{segment(job_id)}/result")
            weights = [float(self.benchmark.entries[t.task_id].get("weight", 1)) for t in tasks]
            scores = []
            for task in tasks:
                output = json.loads(await self.content.read_text(results[task.task_id].output))
                scores.append(0 if output["arena"]["failed"] else output["arena"]["score"])
            expected_score = sum(s * w for s, w in zip(scores, weights, strict=True)) / sum(weights)
            remote_score = aggregate.get("aggregate_score")
            if (
                type(remote_score) not in (int, float)
                or not math.isfinite(remote_score)
                or abs(remote_score - expected_score) > 0.00011
            ):
                raise ValueError("Arena 聚合分与逐题分/权重不一致，拒绝完成评测")
            record.update(state="collected", aggregate=redact(aggregate), updated_at=now())
            write_json(receipt, record)
            return results
        except (asyncio.CancelledError, TimeoutError):
            if record.get("job_id") or record["state"] in {"submitting", "unknown"}:
                record["state"] = "stop_requested"
            write_json(receipt, record)
            if record.get("job_id"):
                try:
                    await asyncio.shield(
                        self.client.post(f"/jobs/{segment(record['job_id'])}/stop")
                    )
                except ArenaError as exc:
                    record["stop_error"] = str(exc)
                    write_json(receipt, record)
            raise

    async def _collect(
        self, job_id: str, task: Task, solution: SolutionVersion, row: dict[str, Any]
    ) -> TaskResult:
        remote_id = row["task_id"]
        attempt = row.get("attempts", 1)
        generation = row.get("generation", 1)
        result = await self.client.get(f"/tasks/{segment(remote_id)}/result")
        if result.get("task_id") != remote_id:
            raise ValueError("Arena Reward 返回了其他任务结果")
        score = result.get("score")
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Arena score 必须是 [0,1] 的有限数")
        try:
            trace = await self.client.get(
                f"/jobs/{segment(job_id)}/tasks/{segment(remote_id)}/trajectory?attempt={attempt}"
            )
        except ArenaError as exc:
            if exc.status != 404 or row["status"] == "DONE":
                raise
            trace = {"events": [], "missing": True}
        events = trace.get("events", [])
        turns = []
        usage = {}
        for event in events:
            message = event.get("message", {})
            if isinstance(message, dict) and message.get("role") in {
                "user",
                "assistant",
                "tool",
                "system",
            }:
                turns.append({"role": message["role"], "content": message.get("content", "")})
            elif event.get("type") == "system" and event.get("system_prompt"):
                turns.append({"role": "system", "content": event["system_prompt"]})
            if event.get("usage"):
                usage = event["usage"]
        artifacts = await self.client.get(f"/tasks/{segment(remote_id)}/artifacts")
        if isinstance(artifacts, dict):
            artifacts = artifacts.get("items", artifacts.get("artifacts"))
        if not isinstance(artifacts, list) or any(not isinstance(a, dict) for a in artifacts):
            raise ValueError("Arena 返回了无效的产物列表")
        public_artifacts = [
            a
            for a in artifacts
            if a.get("role") == "harness" and a.get("attempt", attempt) == attempt
        ]
        artifact_refs = []
        for artifact in public_artifacts:
            name = artifact.get("name", "")
            size = artifact.get("size_bytes")
            # Fetch bounded primary outputs, not arbitrary workspace directories or reward files.
            if (
                isinstance(name, str)
                and any(fnmatchcase(name, pattern) for pattern in self.config.artifact_globs)
                and type(size) is int
                and 0 < size <= 2 * 1024 * 1024
            ):
                safe_relative(name)
                data = await self.client.artifact(remote_id, name, attempt)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError("Arena artifact 大小与声明不符")
                ref = await self.content.put_bytes(data, media_type=artifact.get("type"))
                artifact_refs.append({"name": name, "content": encode(ref)})
        failed = row["status"] != "DONE"
        feedback = (
            result.get("raw", {}).get("error")
            or result.get("raw", {}).get("runner_status")
            or result.get("status", "")
        )
        ref_field = self.benchmark.reference_field
        evidence = {
            "job_id": job_id,
            "task_id": remote_id,
            ref_field: row[ref_field],
            "harness_ref": row["harness_ref"],
            "reward_ref": row["reward_ref"],
            "model_ref": row["model_ref"],
            "attempt": attempt,
            "generation": generation,
            "status": row["status"],
            "score": score,
            "failed": failed,
            "feedback": str(feedback)[:12000],
            "artifacts": public_artifacts,
            "artifact_refs": artifact_refs,
        }
        trace_id = "arena_" + fingerprint(
            {
                "base": self.client.connection.base_url,
                "task": remote_id,
                "generation": generation,
                "attempt": attempt,
            }
        )
        existing = await self.trajectories.get(trace_id)
        if existing is None:
            trace_content = await self.content.put_json(
                redact({"turns": turns, "events": events, "arena": evidence})
            )
            await self.trajectories.save(
                Trajectory(
                    trace_id,
                    "arena-task",
                    trace_content,
                    redact(usage),
                    result.get("computed_at") or row["updated_at"],
                    task_id=task.task_id,
                    solution_id=solution.solution_id,
                )
            )
        elif existing.solution_id != solution.solution_id or existing.task_id != task.task_id:
            raise ValueError("Arena 轨迹已绑定其他 Solution/Task")
        output = await self.content.put_json(
            redact(
                {
                    "task_id": task.task_id,
                    "solution_id": solution.solution_id,
                    "trajectory_id": trace_id,
                    "arena": evidence,
                    "response": next(
                        (t["content"] for t in reversed(turns) if t["role"] == "assistant"), ""
                    ),
                }
            )
        )
        return TaskResult(output, TrajectoryRef(trace_id))


class ArenaVerifier:
    def __init__(self, content: FileContentStore):
        self.content = content

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        task, solution, result = request.task, request.solution, request.result
        output = json.loads(await self.content.read_text(result.output))
        if (
            output.get("task_id") != task.task_id
            or output.get("solution_id") != solution.solution_id
            or output.get("trajectory_id") != result.trajectory.trajectory_id
        ):
            raise ValueError("远端结果与本地 Task/Solution/轨迹不匹配")
        arena = output["arena"]
        ref_field = task.metadata.get("arena_reference_field")
        if ref_field not in {"case_ref", "env_ref"} or ref_field not in task.metadata:
            raise ValueError("Task 必须绑定一个 Arena Case 或 Env 引用")
        if arena[ref_field] != task.metadata[ref_field] or (
            arena["reward_ref"] != task.metadata["reward_ref"]
        ):
            raise ValueError("远端评分身份与 Task 不匹配")
        score = float(arena["score"])
        failed = bool(arena["failed"])
        weight = task.metadata["weight"]
        if not isinstance(weight, (int, float)):
            raise ValueError("无效 Task weight")
        return VerificationResult(
            task.task_id,
            result.trajectory.trajectory_id,
            not failed and score == 1,
            0 if failed else score,
            {"execution_failed": float(failed), "weight": float(weight)},
            str(arena["feedback"]),
            # Publish collected outputs as files in the evaluation evidence.
            attachments=tuple(
                decode(ContentRef, artifact["content"])
                for artifact in arena.get("artifact_refs", [])
            ),
        )


class ArenaEvaluation:
    def __init__(self, target_score: float = 1):
        self.target_score = target_score

    async def evaluate(
        self,
        solution: SolutionVersion,
        split: BenchmarkSplit,
        results: Sequence[VerificationResult],
        *,
        baseline: EvaluationReport | None = None,
    ) -> EvaluationReport:
        if not results or len({r.task_id for r in results}) != len(results):
            raise ValueError("评测必须包含完整且无重复的结果")
        if any(r.status is not VerificationStatus.SUCCEEDED for r in results):
            raise ValueError("Verifier 未成功完成，不能聚合为任务分数")
        scores = [r.score for r in results if r.score is not None]
        if len(scores) != len(results) or any(
            not math.isfinite(score) or not 0 <= score <= 1 for score in scores
        ):
            raise ValueError("无效 Arena score")
        weights = [r.metrics.get("weight", 1) for r in results]
        if any(not math.isfinite(w) or w <= 0 for w in weights):
            raise ValueError("无效 Arena weight")
        score = sum(s * w for s, w in zip(scores, weights, strict=True)) / sum(weights)
        return EvaluationReport(
            solution.solution_id,
            split,
            score >= self.target_score,
            score,
            {
                "mean_reward": score,
                "task_count": float(len(results)),
                "failed_count": sum(r.metrics.get("execution_failed", 0) for r in results),
            },
            now(),
        )
