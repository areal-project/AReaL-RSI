"""Connect background execution to research messages and trusted verification."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from areal_rsi.api.base import AgentState, ContentRef, IterationState, RecordRef
from areal_rsi.api.research import CandidateSnapshot, ResearchMessage, VerifiedResult
from areal_rsi.base.storage import decode, encode, now, write_json
from areal_rsi.coordination.actions import STRING, ResearchActions, definition
from areal_rsi.coordination.coordinator import effect_id
from areal_rsi.execution import ApprovedCommand, JobManager, JobRecord, JobSpec
from areal_rsi.modules.responses import ToolCall, ToolHandler, ToolResult

VerificationReader = Callable[[JobRecord, CandidateSnapshot, str], VerifiedResult]
INLINE_INSTRUCTIONS_CHARS = 8000


class ResearchExecution:
    """Operator-configured commands, never agent-selected evaluator code.

    Commands receive fixed application-owned {workspace}/{output} paths and any
    explicitly declared data arguments. Operators must supply an isolated command
    backend for untrusted code; arbitrary local JobSpec is not exposed to agents.
    """

    def __init__(
        self,
        actions: ResearchActions,
        *,
        commands: dict[str, ApprovedCommand] | None = None,
        verifier: ApprovedCommand | None = None,
        read_verification: VerificationReader | None = None,
        execution_env: dict[str, str] | None = None,
        simulated: bool = False,
        mock_duration: float = 0.1,
    ):
        self.actions, self.c = actions, actions.c
        self.commands = commands or {}
        self.verifier, self.read_verification = verifier, read_verification
        self.execution_env = execution_env or {}
        self.instructions = ""
        self.simulated, self.mock_duration = simulated, mock_duration
        limits = self.c.store.brief().limits
        self.manager = JobManager(
            self.c.store.root,
            gpu_ids=limits.gpu_ids,
            max_jobs=limits.max_jobs,
            auto_monitor=False,
            scheduling_enabled=False,
        )
        self.path = self.c.store.root / "coordination" / "jobs.json"
        self.jobs: dict[str, Any] = json.loads(self.path.read_text()) if self.path.exists() else {}
        for tool in (
            definition(
                "run_job",
                "按已配置命令运行工作区的固定副本。candidate_id 可以为空。先查看get_commands。",
                command_name=STRING,
                candidate_id=STRING,
                parameters_json=STRING,
            ),
            definition(
                "get_commands",
                "查看可用执行命令、参数、输出位置及环境用法。"
                "若 instructions_truncated 为 true，"
                "执行前用 read_record 读取 instructions_ref 的完整说明。",
            ),
            definition(
                "job_status",
                "查看作业的命令、原始任务参数、公开状态与已发布结果。"
                "任务归属以 parameters 和实际反馈为准，不凭 Job ID 猜测。",
                job_id=STRING,
            ),
            definition(
                "read_job_log",
                "读取普通执行作业的最近日志。私有验证日志不公开。",
                job_id=STRING,
            ),
            definition(
                "collect_job_output",
                "将已完成作业的输出复制到工作区，以便检查或保存版本。destination 为相对路径。",
                job_id=STRING,
                destination=STRING,
            ),
            definition(
                "read_job_output_file",
                "直接查看已完成作业的公开输出，不复制到工作区。path 为空时列出文件；offset 从 0"
                "开始，分页文本会归档并返回记录引用。",
                job_id=STRING,
                path=STRING,
                offset={"type": "integer", "minimum": 0},
            ),
            definition(
                "wait_for_job",
                "结束本回合，等作业完成后在同一任务中继续。作业已经完成时直接返回结果。",
                job_id=STRING,
            ),
            definition(
                "verify_candidate",
                "按 candidate_id 验证已保存版本，无需切换工作区。"
                "phase：search 用于研究反馈，final 用于最终确认，diagnostic 用于诊断。",
                candidate_id=STRING,
                phase=STRING,
            ),
        ):
            actions.register(tool, self.handler, roles={"rsi", "research"})

    def save(self) -> None:
        write_json(self.path, self.jobs)

    def find_job(self, job_id: str) -> JobRecord | None:
        try:
            return self.manager.get(job_id)
        except KeyError:
            return None

    def handler(self, agent_id: str) -> ToolHandler:
        async def handle(call: ToolCall) -> ToolResult:
            args = call.arguments
            if call.name == "get_commands":
                guide = None
                if self.instructions:
                    guide = self.c.store.put(
                        "materials",
                        "execution_guide_"
                        + hashlib.sha256(self.instructions.encode()).hexdigest()[:16],
                        {
                            "title": "执行环境使用说明",
                            "content": self.instructions,
                            "shared": True,
                        },
                    )
                return ToolResult(
                    {
                        "commands": [
                            {
                                "name": cmd.name,
                                "parameters": [
                                    p for p in cmd.parameters if p not in {"workspace", "output"}
                                ],
                                "output_subdir": cmd.output_subdir,
                            }
                            for cmd in self.commands.values()
                        ],
                        "verification_available": self.verifier is not None,
                        "instructions": self.instructions[:INLINE_INSTRUCTIONS_CHARS],
                        "instructions_ref": guide.uri if guide else None,
                        "instructions_truncated": len(self.instructions)
                        > INLINE_INSTRUCTIONS_CHARS,
                        "instructions_notice": (
                            "这里只展示部分说明，后文可能包含执行和验证限制。"
                            "执行前请用 read_record 读取 instructions_ref 指向的完整说明。"
                            if len(self.instructions) > INLINE_INSTRUCTIONS_CHARS
                            else ""
                        ),
                        "verification_feedback_available": self.verifier is not None
                        and self.verifier.public_output_subdir is not None,
                        "simulated": self.simulated,
                        "execution_setup": self.c.state.get("execution_setup", "ready"),
                    }
                )
            if call.name in {"run_job", "verify_candidate"}:
                return await self.submit(agent_id, call)
            job_id = args["job_id"]
            info = self.jobs[job_id]
            # Status and completion subscriptions are run-wide. File access below
            # still observes ownership/sharing and the verifier's publication boundary.
            if (
                call.name in {"read_job_log", "collect_job_output", "read_job_output_file"}
                and not info["verification"]
                and agent_id not in {"rsi", info["agent_id"]}
            ):
                self.actions.read_record(
                    agent_id,
                    self.c.store.paths.reference(
                        self.c.store.root / "executions" / f"{job_id}.json"
                    ).uri,
                )
            record = self.manager.get(job_id)
            assert record is not None
            if call.name == "read_job_log":
                if info["verification"]:
                    raise ValueError("Private verifier logs are not public feedback")
                with open(record.log_path, "rb") as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell() - 16000))
                    return ToolResult({"text": stream.read().decode(errors="replace")})
            if call.name in {"collect_job_output", "read_job_output_file"}:
                if record.state != "succeeded":
                    raise ValueError("Job outputs are available after successful completion")
                subdir = (
                    info.get("public_output_subdir")
                    if info["verification"]
                    else info.get("output_subdir", ".")
                )
                if subdir is None:
                    raise ValueError(
                        "The verifier has not published file feedback; use its result record"
                    )
                output_root = Path(info["output"])
                source = output_root / subdir
                if not source.resolve().is_relative_to(output_root.resolve()):
                    raise ValueError("Configured artifact path escapes job output")
                if source.is_symlink() or any(p.is_symlink() for p in source.rglob("*")):
                    raise ValueError("Job output must not contain symbolic links")
                if call.name == "read_job_output_file":
                    from areal_rsi.base.storage import safe_relative

                    if not args["path"]:
                        files = sorted(
                            p.relative_to(source).as_posix()
                            for p in source.rglob("*")
                            if p.is_file()
                        )
                        return ToolResult({"files": files[:500], "truncated": len(files) > 500})
                    path = source / safe_relative(args["path"])
                    if not path.resolve().is_relative_to(source.resolve()):
                        raise ValueError("File is outside the published execution output")
                    offset = args["offset"]
                    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                        raise ValueError("Offset must be a nonnegative integer")
                    if path.stat().st_size > 8_000_000:
                        raise ValueError("Output is too large; publish a bounded summary")
                    raw = path.read_text()
                    value = {
                        "agent_id": agent_id,
                        "job_id": job_id,
                        "path": args["path"],
                        "offset": offset,
                        "content": raw[offset : offset + 16000],
                        "next_offset": offset + 16000 if offset + 16000 < len(raw) else None,
                        "simulated": self.simulated,
                    }
                    ref = self.c.store.put("materials", effect_id("output", call.call_id), value)
                    self.c.link_reference(agent_id, ref)
                    return ToolResult({**value, "uri": ref.uri})
                destination = self.actions.workspace_path(agent_id, args["destination"])
                if destination.exists() and any(p.is_symlink() for p in destination.rglob("*")):
                    raise ValueError("Collection destination contains symbolic links")
                shutil.copytree(source, destination, dirs_exist_ok=True)
                return ToolResult({"destination": args["destination"]})
            if call.name == "wait_for_job" and not record.terminal:
                self.waiters(info).setdefault(
                    agent_id,
                    {
                        "iteration_id": self.c.actor(agent_id).current_iteration_id,
                        "notified": False,
                    },
                )
                info["waiting"] = True
                self.save()
                self.c.wait(agent_id, "job:" + job_id)
                return ToolResult(self.public(record), stop=True, reason="job")
            await self.poll()
            record = self.manager.get(job_id)
            assert record is not None
            return ToolResult(self.public(record))

        return handle

    async def submit(self, agent_id: str, call: ToolCall) -> ToolResult:
        if self.c.state.get("execution_setup") == "pending":
            raise ValueError("执行接入尚未冻结；RSI 调用 freeze_execution 后才能运行作业")
        key = effect_id("job", agent_id + ":" + call.call_id).replace("job_", "job-", 1)
        if key in self.jobs:
            record = self.find_job(key)
            if record is None:
                record = self.manager.submit(decode(JobSpec, self.jobs[key]["spec"]), job_id=key)
            return ToolResult(self.public(record))
        args = call.arguments
        verification = call.name == "verify_candidate"
        phase = args.get("phase", "diagnostic")
        if phase not in {"search", "final", "diagnostic"}:
            raise ValueError("Invalid verification phase")
        if verification:
            command = self.verifier
            if command is None or self.read_verification is None:
                raise ValueError("No trusted verifier configured for this run")
            max_verify = self.c.store.brief().limits.max_verify_calls
            if (
                max_verify is not None
                and sum(bool(info["verification"]) for info in self.jobs.values()) >= max_verify
            ):
                raise ValueError("Verification budget exhausted")
        else:
            command = self.commands[args["command_name"]]
        candidate_id = args.get("candidate_id") or None
        if verification and candidate_id is None:
            raise ValueError("Verification requires an immutable candidate")
        if verification and phase == "final" and not self.simulated:
            confirmed = {
                item["candidate_id"]
                for item in self.jobs.values()
                if item["verification"] and item["phase"] == "final"
            }
            if confirmed and candidate_id not in confirmed:
                raise ValueError(
                    "This final pool has already been queried for another candidate; "
                    "use a new run with a fresh final pool for independent confirmation"
                )
        input_root = self.c.store.root / "execution-inputs" / key
        workspace = input_root / "workspace"
        output = self.c.store.root / "execution-outputs" / key
        output.mkdir(parents=True, exist_ok=True)
        # Persist which snapshot is used before materialization, so a restart
        # cannot silently switch to a subsequently edited live workspace.
        snapshot_record = input_root / "snapshot.json"
        if snapshot_record.exists():
            content = decode(ContentRef, json.loads(snapshot_record.read_text()))
        else:
            if candidate_id is not None:
                content = self.c.store.get("candidates", candidate_id, CandidateSnapshot).content
            else:
                content = await self.c.store.content.snapshot(self.actions.workspace(agent_id))
            write_json(snapshot_record, content, immutable=True)
        ready = input_root / "ready.json"
        if not ready.exists():
            # Only application-created incomplete input copies can be removed;
            # a job cannot have launched before this completion marker exists.
            if workspace.exists():
                shutil.rmtree(workspace)
            await self.c.store.content.materialize(content, workspace)
            write_json(ready, {"content": encode(content)}, immutable=True)
        supplied = {} if verification else json.loads(args["parameters_json"])
        if not isinstance(supplied, dict) or any(not isinstance(v, str) for v in supplied.values()):
            raise ValueError("Command parameters must be a JSON object of strings")
        if set(supplied) & {"workspace", "output"}:
            raise ValueError("Execution paths are assigned by the application")
        reserved = {"workspace", "output", "phase"} if verification else {"workspace", "output"}
        if set(supplied) != set(command.parameters) - reserved:
            raise ValueError("Supplied parameters do not match the configured command")
        values = {
            **supplied,
            "workspace": str(workspace.resolve()),
            "output": str(output.resolve()),
        }
        if verification:
            values["phase"] = phase
        values = {name: values[name] for name in command.parameters}
        spec = command.bind(
            values, cwd=str(workspace), agent_id=agent_id, verification=str(verification).lower()
        )
        if self.execution_env:
            from dataclasses import replace

            spec = replace(spec, env=dict(self.execution_env))
        if verification:
            from dataclasses import replace

            assert candidate_id is not None
            candidate = self.c.store.get("candidates", candidate_id, CandidateSnapshot)
            spec = replace(
                spec,
                metadata={
                    **spec.metadata,
                    "contract_digest": self.c.store.contract_digest(),
                    "candidate_id": candidate_id,
                    "candidate_digest": candidate.content.digest,
                    "phase": phase,
                },
            )
        if self.simulated:
            spec = JobSpec(
                command=spec.command,
                cwd=spec.cwd,
                backend="mock",
                mock_duration_seconds=self.mock_duration,
                metadata=spec.metadata,
                timeout_seconds=spec.timeout_seconds,
            )
        self.jobs[key] = {
            "agent_id": agent_id,
            "iteration_id": self.c.actor(agent_id).current_iteration_id,
            "parameters": supplied,
            "candidate_id": candidate_id,
            "verification": verification,
            "phase": phase,
            "waiting": False,
            "processed": False,
            "output": str(output),
            "output_subdir": command.output_subdir,
            "public_output_subdir": command.public_output_subdir,
            "created_at": now(),
            "spec": encode(spec),
        }
        self.save()
        if verification:
            self.c.state["verify_calls"] = sum(
                bool(info["verification"]) for info in self.jobs.values()
            )
            self.c.save()
        self.manager.scheduling_enabled = self.c.state["status"] == "running"
        record = self.manager.submit(spec, job_id=key)
        self.c.store.event("job_submitted", agent_id=agent_id, job=self.public(record))
        return ToolResult(self.public(record))

    def public(self, record: JobRecord) -> dict[str, Any]:
        info = self.jobs[record.job_id]
        return {
            "job_id": record.job_id,
            "agent_id": info["agent_id"],
            "iteration_id": info.get("iteration_id"),
            "command_name": record.spec.metadata.get("command_template"),
            # Only caller-supplied data arguments are public. Never publish the
            # operator's argv, environment, frozen paths or private verifier inputs.
            # Legacy records have no parameters; do not infer them from argv.
            "parameters": info.get("parameters"),
            "candidate_id": info.get("candidate_id"),
            "phase": info.get("phase") if info["verification"] else None,
            "state": record.state,
            "gpu_ids": list(record.assigned_gpu_ids),
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "exit_code": record.exit_code,
            "error": record.error,
            "simulated": record.spec.backend == "mock",
            "verification": info["verification"],
            "public_files_available": record.state == "succeeded"
            and (not info["verification"] or info.get("public_output_subdir") is not None),
            "result": info.get("result"),
        }

    @staticmethod
    def waiters(info: dict[str, Any]) -> dict[str, Any]:
        # Existing runs recorded only the owner's subscription. Preserve it on upgrade.
        if "waiters" not in info:
            info["waiters"] = (
                {info["agent_id"]: {"iteration_id": info["iteration_id"], "notified": False}}
                if info.get("waiting")
                else {}
            )
        return info["waiters"]  # type: ignore[no-any-return]

    def notify_completion(self, record: JobRecord) -> None:
        info = self.jobs[record.job_id]
        for agent_id, subscription in self.waiters(info).items():
            if subscription["notified"]:
                continue
            actor = self.c.actor(agent_id)
            node_active = agent_id == "rsi" or any(
                n.iteration_id == subscription["iteration_id"] and n.state == IterationState.RUNNING
                for n in actor.iterations
            )
            if actor.state in {AgentState.STOPPED, AgentState.FAILED} or not node_active:
                continue
            # A later message/turn can change wait_reason without cancelling a subscription.
            # enqueue is idempotent, including a crash before notified is saved.
            key = "job:" + record.job_id
            if agent_id != info["agent_id"]:
                key += ":" + agent_id
            text = "你订阅的作业已完成。请读取结果，在原任务和会话中继续推进。\n" + json.dumps(
                self.public(record), ensure_ascii=False
            )
            with suppress(FileNotFoundError):
                # Older notifications can have a different JSON field order after reload.
                text = self.c.store.get("messages", effect_id("message", key), ResearchMessage).text
            if info.get("result", {}).get("uri"):
                self.c.link_reference(
                    agent_id,
                    RecordRef(info["result"]["uri"]),
                    iteration_id=subscription["iteration_id"],
                )
            self.c.enqueue(
                "system",
                agent_id,
                text,
                key=key,
            )
            subscription["notified"] = True
            self.save()

    async def poll(self) -> bool:
        # A crash after the intent is stored but before manager.submit is safe:
        # the job has a stable identity and immutable input snapshot.
        verify_calls = sum(bool(info["verification"]) for info in self.jobs.values())
        if self.c.state["verify_calls"] != verify_calls:
            self.c.state["verify_calls"] = verify_calls
            self.c.save()
        limits = self.c.store.brief().limits
        self.manager.gpu_ids, self.manager.max_jobs = limits.gpu_ids, limits.max_jobs
        self.manager.scheduling_enabled = False
        # This loop does not yield: one fresh actor snapshot is sufficient. Reading
        # each job's owner separately reloads every archived research node per job.
        actors = {actor.agent_id: actor for actor in self.c.actors()} if self.jobs else {}
        for key, info in self.jobs.items():
            actor_stopped = actors[info["agent_id"]].state == AgentState.STOPPED
            if self.find_job(key) is None:
                self.manager.submit(decode(JobSpec, info["spec"]), job_id=key)
            must_cancel = (
                actor_stopped
                or self.c.state["status"] in {"stopped", "succeeded", "failed"}
                or self.c.state.get("budget_stop_reason") == "Run wall-time budget exhausted"
            )
            if must_cancel and not self.manager.get(key).terminal:
                self.manager.cancel(key)
        self.manager.scheduling_enabled = self.c.state["status"] == "running"
        records = self.manager.poll()
        for record in records:
            info = self.jobs.get(record.job_id)
            if info is None or not record.terminal:
                continue
            if info["processed"]:
                # Result persistence and message delivery are separate durable effects.
                self.notify_completion(record)
                continue
            if info["verification"]:
                assert self.read_verification is not None
                candidate = self.c.store.get("candidates", info["candidate_id"], CandidateSnapshot)
                result_path = self.c.store.root / "verifications" / f"{record.job_id}.json"
                if result_path.exists():
                    result = self.c.store.get("verifications", record.job_id, VerifiedResult)
                else:
                    try:
                        result = self.read_verification(record, candidate, info["phase"])
                    except Exception as exc:
                        result = VerifiedResult(
                            record.job_id,
                            candidate.candidate_id,
                            self.c.store.contract_digest(),
                            now(),
                            "infra_error",
                            phase=info["phase"],
                            simulated=self.simulated,
                            error=(
                                f"Trusted verifier could not read result: "
                                f"{type(exc).__name__}: {exc}"
                            ),
                        )
                    if (
                        result.result_id != record.job_id
                        or result.candidate_id != candidate.candidate_id
                    ):
                        raise ValueError("Verifier returned the wrong job or candidate identity")
                    if result.simulated != self.simulated or result.phase != info["phase"]:
                        raise ValueError("Verifier returned the wrong simulation mode or phase")
                    if record.state != "succeeded" and result.status == "succeeded":
                        raise ValueError(
                            "Failed execution cannot produce a successful verification"
                        )
                    self.c.store.record_verification(result)
                ref = self.c.store.paths.reference(result_path)
                run = self.c.store.run()
                baseline_id = self.c.state.get("baseline_candidate_id")
                baseline = (
                    self.c.store.get("candidates", baseline_id, CandidateSnapshot)
                    if baseline_id
                    else None
                )
                if (
                    baseline is not None
                    and candidate.content == baseline.content
                    and result.phase == "search"
                    and result.status == "succeeded"
                    and result.passed
                    and result.score is not None
                ):
                    run.baseline_result_ref = ref
                if result.phase == "final" and result.status == "succeeded" and result.passed:
                    run.final_result_ref = ref
                self.c.store.save_run(run)
                self.c.store.consider_best(result.result_id, simulated_run=self.simulated)
                info["result"] = {**encode(result), "uri": ref.uri}
            else:
                # Job exit state is evidence. Training stdout is never a score.
                info["result"] = {
                    "agent_id": info["agent_id"],
                    "iteration_id": info["iteration_id"],
                    "job_id": record.job_id,
                    "simulated": self.simulated,
                    "exit_code": record.exit_code,
                    "state": record.state,
                    "output_available": record.state == "succeeded",
                }
                if self.simulated and record.state == "succeeded":
                    # A visible fixture, never a fabricated checkpoint or real score.
                    output = (
                        self.c.store.root
                        / "execution-outputs"
                        / record.job_id
                        / info.get("output_subdir", ".")
                    )
                    write_json(
                        output / "simulation.json",
                        {
                            "simulated": True,
                            "job_id": record.job_id,
                            "description": "Execution fixture: no training or GPU work performed",
                        },
                    )
                result_path = self.c.store.root / "executions" / f"{record.job_id}.json"
                if not result_path.exists():
                    self.c.store.put("executions", record.job_id, info["result"])
                info["result"]["uri"] = self.c.store.paths.reference(result_path).uri
            self.c.link_reference(
                info["agent_id"],
                RecordRef(info["result"]["uri"]),
                iteration_id=info["iteration_id"],
            )
            self.notify_completion(record)
            info["processed"] = True
            self.save()
            self.c.store.event("job_completed", agent_id=info["agent_id"], job=self.public(record))
        return any(not record.terminal for record in records)

    def close(self, *, cancel: bool = False) -> None:
        self.manager.close(preserve_running=not cancel)
