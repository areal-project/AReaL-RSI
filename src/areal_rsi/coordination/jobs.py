"""Connect background execution to research messages and trusted verification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from areal_rsi.api.base import AgentState, ContentRef, IterationState, RecordRef
from areal_rsi.api.research import CandidateSnapshot, ResearchMessage, VerifiedResult
from areal_rsi.base.storage import DIRECTORY_TYPE, decode, encode, now, safe_relative, write_json
from areal_rsi.coordination.actions import STRING, ResearchActions, definition
from areal_rsi.coordination.coordinator import effect_id
from areal_rsi.coordination.effective_time import EffectiveTime
from areal_rsi.execution import ApprovedCommand, JobManager, JobRecord, JobSpec
from areal_rsi.execution.progress import read_progress
from areal_rsi.modules.agent_runtime import ToolCall, ToolHandler, ToolResult

VerificationReader = Callable[[JobRecord, CandidateSnapshot, str], VerifiedResult]
INLINE_INSTRUCTIONS_CHARS = 8000
FEEDBACK_CHARS = 12000
WAIT = {
    "type": "boolean",
    "description": "通常为 true：完成后带回反馈；有独立工作要并行做时为 false。",
}


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
        self.inline_wait_seconds = 1.0
        # Independent attempts keep their native turn while GPU work runs.
        # A bounded wait may return a running job, but never ends that turn.
        self.continuous_wait_seconds = 240.0
        self._poll_lock = asyncio.Lock()
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
        config_path = self.c.store.root / "coordination" / "config.json"
        run_metadata = (
            json.loads(config_path.read_text()).get("metadata", {})
            if config_path.exists()
            else {}
        )
        self.require_diagnostic_before_search = bool(run_metadata.get("research_portfolios"))
        self.minimum_search_verifications_per_agent = run_metadata.get(
            "minimum_search_verifications_per_agent"
        )
        if (
            self.minimum_search_verifications_per_agent is not None
            and (
                type(self.minimum_search_verifications_per_agent) is not int
                or self.minimum_search_verifications_per_agent <= 0
            )
        ):
            raise ValueError(
                "minimum_search_verifications_per_agent must be a positive integer"
            )
        migrated = False
        if self.require_diagnostic_before_search:
            for job_id, info in self.jobs.items():
                if info.get("verification") and info.get("phase") == "search":
                    eligible = self._search_protocol_eligible(
                        info.get("agent_id"), info.get("candidate_id"), before_job_id=job_id
                    )
                    if info.get("protocol_eligible") != eligible:
                        info["protocol_eligible"] = eligible
                        migrated = True
                elif "protocol_eligible" not in info:
                    info["protocol_eligible"] = True
                    migrated = True
        if migrated:
            self.save()
        self.manager.schedule_filter = self._job_schedulable
        if self.require_diagnostic_before_search:
            for job_id, info in self.jobs.items():
                if (
                    info.get("verification")
                    and info.get("phase") == "search"
                    and not info.get("protocol_eligible", True)
                    and (record := self.find_job(job_id)) is not None
                    and record.state == "queued"
                ):
                    self.manager.cancel(job_id)
        for tool in (
            definition(
                "run_job",
                "按已配置命令运行固定输入。candidate_id 为空时使用当前工作区。"
                + (
                    "wait=true 在当前回合等待结果；若仍在运行，继续 wait_for_job 等原作业。"
                    if self.c.state.get("mode") == "passk"
                    else "wait=true 时短作业直接返回，长作业停止推理、完成后带回公开反馈。"
                )
                + "返回的记录引用在作业运行中即可交接；实时状态用 job_status 查询。",
                command_name=STRING,
                candidate_id=STRING,
                parameters_json={
                    "type": "string",
                    "description": "JSON 对象字符串；参数名及含义见 get_commands，所有值为字符串。",
                },
                wait=WAIT,
            ),
            definition(
                "rerun_job",
                "用原作业的冻结输入和相同命令重新执行一次，无需找文件或核对哈希。"
                "用于已选版本的独立复测；会消耗正常作业/评测预算。原作业须已结束。",
                job_id=STRING,
                wait=WAIT,
            ),
            definition(
                "get_commands",
                "查看可用执行命令、参数名称及含义、输出位置及环境用法。"
                "按执行说明确认实际携带的输入与依赖，不假定远端能访问整个本地工作区。"
                "若 instructions_truncated 为 true，"
                "执行前用 read_record 读取 instructions_ref 的完整说明。",
            ),
            definition(
                "job_status",
                "查看作业的命令、原始任务参数、公开状态、进度与已发布结果。"
                "普通作业失败且无公开反馈时，附上有界日志末尾帮助排错。"
                "任务归属以 parameters 和实际反馈为准，不凭 Job ID 猜测。"
                "重复观察时每次使用新的工具调用；复用同一 call id 会返回幂等缓存结果。",
                job_id=STRING,
            ),
            definition(
                "read_job_log",
                "读取普通执行作业的最近日志。私有验证日志不公开。",
                job_id=STRING,
            ),
            definition(
                "collect_job_output",
                "收集作业公开产物。destination 为工作区相对目录；"
                "as_candidate=true 同时保存为不可变候选，destination 为空时只保存候选。",
                job_id=STRING,
                destination=STRING,
                as_candidate={"type": "boolean"},
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
                (
                    "在当前回合等待原作业并返回反馈；仍在运行时继续等待，不重复提交。"
                    if self.c.state.get("mode") == "passk"
                    else "结束本回合，等作业完成后在同一任务中继续。作业已经完成时直接返回结果。"
                )
                + "不要用固定 call id 的自建轮询循环替代此工具。",
                job_id=STRING,
            ),
            definition(
                "verify_candidate",
                "按 candidate_id 验证已保存版本，无需切换工作区。"
                "phase：search 用于研究反馈，final 用于最终确认，diagnostic 用于诊断。",
                candidate_id=STRING,
                phase=STRING,
                wait=WAIT,
            ),
        ):
            actions.register(tool, self.handler, roles={"rsi", "research"})

    def _job_schedulable(self, record: JobRecord) -> bool:
        info = self.jobs.get(record.job_id)
        if (
            not self.require_diagnostic_before_search
            or info is None
            or not info.get("verification")
            or info.get("phase") != "search"
        ):
            return True
        agent_id = info.get("agent_id")
        if not info.get("protocol_eligible", True) or not self._diagnostic_ready(agent_id):
            return False
        target = getattr(self, "minimum_search_verifications_per_agent", None)
        if target is None:
            return True
        completed = sum(
            self._trusted_search_success(item) and item.get("agent_id") == agent_id
            for item in self.jobs.values()
        )
        active = sum(
            job_id != record.job_id
            and item.get("agent_id") == agent_id
            and item.get("verification")
            and item.get("phase") == "search"
            and item.get("protocol_eligible", True)
            and not item.get("processed", False)
            and (job := self.find_job(job_id)) is not None
            and job.state in {"starting", "running"}
            for job_id, item in self.jobs.items()
        )
        return completed + active < target

    @staticmethod
    def _trusted_search_success(info: dict[str, Any]) -> bool:
        result = info.get("result", {})
        metrics = result.get("metrics", {})
        return bool(
            info.get("verification")
            and info.get("phase") == "search"
            and info.get("protocol_eligible", True)
            and result.get("status") == "succeeded"
            and not result.get("simulated", False)
            and metrics.get("failed_count", 0) == 0
        )

    def _diagnostic_ready(self, agent_id: str | None) -> bool:
        return any(
            diagnostic.get("agent_id") == agent_id
            and diagnostic.get("verification")
            and diagnostic.get("phase") == "diagnostic"
            and diagnostic.get("processed")
            and diagnostic.get("result", {}).get("status") == "succeeded"
            for diagnostic in self.jobs.values()
        )

    def _diagnostic_blocks_resubmission(self, agent_id: str | None) -> bool:
        for job_id, diagnostic in self.jobs.items():
            if (
                diagnostic.get("agent_id") != agent_id
                or not diagnostic.get("verification")
                or diagnostic.get("phase") != "diagnostic"
            ):
                continue
            if diagnostic.get("processed") and diagnostic.get("result", {}).get(
                "status"
            ) == "succeeded":
                return True
            record = self.find_job(job_id)
            if record is not None and not record.terminal:
                return True
            if record is not None and record.state == "succeeded":
                return True
        return False

    def _candidate_digest(self, candidate_id: str | None) -> str | None:
        if not candidate_id:
            return None
        with suppress(FileNotFoundError, KeyError, ValueError):
            return self.c.store.get("candidates", candidate_id, CandidateSnapshot).content.digest
        return None

    def _candidate_semantic_digest(self, candidate_id: str | None) -> str | None:
        """Normalize JSON candidate files so formatting-only edits are not iterations."""
        if not candidate_id:
            return None
        with suppress(FileNotFoundError, KeyError, ValueError, TypeError):
            candidate = self.c.store.get("candidates", candidate_id, CandidateSnapshot)
            if candidate.content.media_type != DIRECTORY_TYPE:
                return candidate.content.digest
            root = self.c.store.content.root
            manifest_digest = candidate.content.digest.removeprefix("sha256:")
            manifest = json.loads((root / manifest_digest).read_text())
            identities = []
            for name, encoded_ref in sorted(manifest["files"].items()):
                ref = decode(ContentRef, encoded_ref)
                data = (root / ref.digest.removeprefix("sha256:")).read_bytes()
                if name.endswith(".json"):
                    normalized = json.dumps(
                        json.loads(data), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode()
                    digest = hashlib.sha256(normalized).hexdigest()
                else:
                    digest = hashlib.sha256(data).hexdigest()
                identities.append((name, digest))
            canonical = json.dumps(identities, ensure_ascii=False, separators=(",", ":")).encode()
            return "sha256:" + hashlib.sha256(canonical).hexdigest()
        return None

    def _search_protocol_eligible(
        self,
        agent_id: str | None,
        candidate_id: str | None,
        *,
        before_job_id: str | None = None,
    ) -> bool:
        """Require each counted DEV iteration to be a distinct post-diagnostic candidate."""
        if not self._diagnostic_ready(agent_id):
            return False
        candidate_digest = self._candidate_semantic_digest(candidate_id)
        if candidate_digest is None:
            return False
        diagnostic_digests = {
            self._candidate_semantic_digest(info.get("candidate_id"))
            for info in self.jobs.values()
            if info.get("agent_id") == agent_id
            and info.get("verification")
            and info.get("phase") == "diagnostic"
            and info.get("processed")
            and info.get("result", {}).get("status") == "succeeded"
        }
        if candidate_digest in diagnostic_digests:
            return False
        current_order: tuple[str, str] | None = None
        if before_job_id is not None and before_job_id in self.jobs:
            current = self.jobs[before_job_id]
            current_order = (str(current.get("created_at", "")), before_job_id)
        for job_id, info in self.jobs.items():
            if job_id == before_job_id:
                continue
            if current_order is not None and (
                str(info.get("created_at", "")), job_id
            ) >= current_order:
                continue
            if (
                info.get("verification")
                and info.get("phase") == "search"
                and info.get("protocol_eligible", True)
                and self._candidate_semantic_digest(info.get("candidate_id")) == candidate_digest
                and (
                    not info.get("processed", False)
                    or self._trusted_search_success(info)
                )
            ):
                return False
        return True

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
                                "parameter_descriptions": {
                                    p: cmd.parameter_descriptions[p]
                                    for p in cmd.parameters
                                    if p not in {"workspace", "output"}
                                    and p in cmd.parameter_descriptions
                                },
                                "output_subdir": cmd.output_subdir,
                                "feedback_file": cmd.feedback_file,
                                "auto_verify_output": cmd.auto_verify_output,
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
                result = await self.submit(agent_id, call)
                # Missing wait preserves the semantics of archived tool calls.
                if args.get("wait", False):
                    return await self.wait_for_result(agent_id, result.output["job_id"])
                return result
            job_id = args["job_id"]
            info = self.jobs[job_id]
            # Status and completion subscriptions are run-wide. File access below
            # still observes ownership/sharing and the verifier's publication boundary.
            if (
                call.name
                in {"read_job_log", "collect_job_output", "read_job_output_file", "rerun_job"}
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
            if call.name == "rerun_job":
                if not record.terminal:
                    raise ValueError("原作业尚未结束，请等待原作业，不要重复提交")
                if info["verification"]:
                    raise ValueError("验证作业请直接用 verify_candidate 和原 candidate_id")
                snapshot = self.c.store.root / "execution-inputs" / job_id / "snapshot.json"
                content = decode(ContentRef, json.loads(snapshot.read_text()))
                replay = ToolCall(
                    "run_job",
                    {
                        "command_name": record.spec.metadata["command_template"],
                        "candidate_id": info.get("candidate_id") or "",
                        "parameters_json": json.dumps(info["parameters"]),
                    },
                    call.call_id,
                    recovered=call.recovered,
                )
                result = await self.submit(
                    agent_id, replay, source_content=content, source_job_id=job_id
                )
                if args.get("wait", False):
                    return await self.wait_for_result(agent_id, result.output["job_id"])
                return result
            if call.name == "read_job_log":
                if info["verification"]:
                    raise ValueError("Private verifier logs are not public feedback")
                with open(record.log_path, "rb") as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell() - 16000))
                    return ToolResult({"text": stream.read().decode(errors="replace")})
            if call.name in {"collect_job_output", "read_job_output_file"}:
                if record.state != "succeeded" and not (
                    call.name == "read_job_output_file" and record.terminal
                ):
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
                    # Offsets remain character-based; never load a whole trajectory into memory.
                    with path.open() as stream:
                        remaining = offset
                        while remaining:
                            chunk = stream.read(min(remaining, 65536))
                            if not chunk:
                                break
                            remaining -= len(chunk)
                        raw = stream.read(16001)
                    value = {
                        "agent_id": agent_id,
                        "job_id": job_id,
                        "path": args["path"],
                        "offset": offset,
                        "content": raw[:16000],
                        "next_offset": offset + 16000 if len(raw) > 16000 else None,
                        "simulated": self.simulated,
                    }
                    output_id = effect_id("output", agent_id + ":" + call.call_id)
                    ref = self.c.store.put("materials", output_id, value)
                    self.c.link_reference(agent_id, ref)
                    return ToolResult({**value, "uri": ref.uri, "record_id": output_id})
                collected: dict[str, Any] = {}
                if args.get("as_candidate", False):
                    candidate_id = effect_id("candidate", agent_id + ":" + call.call_id)
                    candidate_path = self.c.store.root / "candidates" / f"{candidate_id}.json"
                    if candidate_path.exists():
                        candidate = self.c.store.get("candidates", candidate_id, CandidateSnapshot)
                    else:
                        candidate = await self.c.store.snapshot(
                            source,
                            agent_id=agent_id,
                            description="作业产物：" + job_id,
                            parent_candidate_id=info.get("candidate_id"),
                            candidate_id=candidate_id,
                            scope_base_candidate_id=self.c.state["seed_candidate_id"],
                        )
                    self.c.link_reference(agent_id, self.c.store.paths.reference(candidate_path))
                    collected["candidate_id"] = candidate.candidate_id
                    collected["content"] = encode(candidate.content)
                if args["destination"]:
                    destination = self.actions.workspace_path(agent_id, args["destination"])
                    if destination.exists() and any(p.is_symlink() for p in destination.rglob("*")):
                        raise ValueError("Collection destination contains symbolic links")
                    shutil.copytree(source, destination, dirs_exist_ok=True)
                    collected["destination"] = args["destination"]
                elif not collected:
                    raise ValueError("请指定收集目录或 as_candidate=true")
                return ToolResult(collected)
            if call.name == "wait_for_job" and not record.terminal:
                return await self.wait_for_result(agent_id, job_id, inline=False)
            await self.poll()
            record = self.manager.get(job_id)
            assert record is not None
            return ToolResult(self.for_agent(record, agent_id))

        return handle

    async def wait_for_result(
        self, agent_id: str, job_id: str, *, inline: bool = True
    ) -> ToolResult:
        continuous = self.c.state.get("mode") == "passk"
        wait_seconds = (
            self.continuous_wait_seconds
            if continuous
            else self.inline_wait_seconds
            if inline
            else 0
        )
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while True:
            await self.poll()
            record = self.manager.get(job_id)
            if record.terminal:
                return ToolResult(self.for_agent(record, agent_id))
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.25 if continuous else 0.05, remaining))
        if continuous:
            return ToolResult(
                {
                    **self.for_agent(record, agent_id),
                    "instruction": "作业仍在运行，用 wait_for_job 等待原作业；不要重复提交。",
                }
            )
        info = self.jobs[job_id]
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
        return ToolResult(self.for_agent(record, agent_id), stop=True, reason="job")

    async def submit(
        self,
        agent_id: str,
        call: ToolCall,
        *,
        source_content: ContentRef | None = None,
        source_job_id: str | None = None,
    ) -> ToolResult:
        if self.c.state.get("execution_setup") == "pending":
            raise ValueError("执行接入尚未冻结；RSI 调用 freeze_execution 后才能运行作业")
        key = effect_id("job", agent_id + ":" + call.call_id).replace("job_", "job-", 1)
        if key in self.jobs:
            record = self.find_job(key)
            if record is None:
                record = self.manager.submit(decode(JobSpec, self.jobs[key]["spec"]), job_id=key)
            if not self.jobs[key]["verification"]:
                self.publish_execution(record)
            return ToolResult(self.public(record))
        args = call.arguments
        verification = call.name == "verify_candidate"
        phase = args.get("phase", "diagnostic")
        if phase not in {"search", "final", "diagnostic", "baseline"}:
            raise ValueError("Invalid verification phase")
        if verification:
            command = self.verifier
            if command is None or self.read_verification is None:
                raise ValueError("No trusted verifier configured for this run")
            if phase == "diagnostic" and self._diagnostic_blocks_resubmission(agent_id):
                raise ValueError(
                    "This researcher already has a running or successful TRAIN diagnostic"
                )
            if phase == "search":
                if self.require_diagnostic_before_search and not self._diagnostic_ready(agent_id):
                    raise ValueError(
                        "Complete this researcher's successful TRAIN diagnostic before "
                        "submitting DEV search verification"
                    )
                config_path = self.c.store.root / "coordination/config.json"
                metadata = (
                    json.loads(config_path.read_text()).get("metadata", {})
                    if config_path.exists()
                    else {}
                )
                target = metadata.get("stop_after_search_verifications")
                if target is not None:
                    if type(target) is not int or target <= 0:
                        raise ValueError(
                            "stop_after_search_verifications must be a positive integer"
                        )
                    completed = sum(
                        item.status == "succeeded" and item.phase == "search"
                        for item in self.c.store.records("verifications", VerifiedResult)
                    )
                    pending = sum(
                        info["verification"]
                        and info["phase"] == "search"
                        and info.get("protocol_eligible", True)
                        and not info.get("processed", False)
                        for info in self.jobs.values()
                    )
                    if completed + pending >= target:
                        raise ValueError(
                            f"Search verification target ({target}) is already fully allocated"
                        )
                per_agent_target = metadata.get("minimum_search_verifications_per_agent")
                if per_agent_target is not None:
                    if type(per_agent_target) is not int or per_agent_target <= 0:
                        raise ValueError(
                            "minimum_search_verifications_per_agent must be a positive integer"
                        )
                    completed = sum(
                        self._trusted_search_success(item)
                        and item.get("agent_id") == agent_id
                        for item in self.jobs.values()
                    )
                    pending = sum(
                        item.get("agent_id") == agent_id
                        and item.get("verification")
                        and item.get("phase") == "search"
                        and item.get("protocol_eligible", True)
                        and not item.get("processed", False)
                        for item in self.jobs.values()
                    )
                    if completed + pending >= per_agent_target:
                        raise ValueError(
                            "This researcher's minimum search verification target "
                            f"({per_agent_target}) is already fully allocated"
                        )
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
        if (
            verification
            and phase == "search"
            and self.require_diagnostic_before_search
            and not self._search_protocol_eligible(agent_id, candidate_id)
        ):
            raise ValueError(
                "DEV search requires a new candidate whose content differs from this "
                "researcher's diagnostic baseline and prior counted iterations"
            )
        if self.c.state.get("full_benchmark_evaluation"):
            from areal_rsi.coordination.benchmark import baseline_pending

            if phase == "baseline":
                if not verification or candidate_id != self.c.state.get("baseline_candidate_id"):
                    raise ValueError("Full baseline must evaluate the immutable seed")
                if self.c.state.get("full_baseline_ready"):
                    raise ValueError("Full baseline is already complete")
            elif baseline_pending(self.c.state):
                raise ValueError("Complete the full benchmark baseline before optimization")
            frozen = self.c.state.get("full_final_candidate_id")
            if frozen and (not verification or phase != "final" or candidate_id != frozen):
                raise ValueError(
                    "Final candidate is frozen; no more optimization or candidate changes"
                )
            if verification and phase in {"search", "diagnostic"}:
                limit = self.c.store.brief().limits.max_verify_calls
                if (
                    limit is not None
                    and sum(bool(i["verification"]) for i in self.jobs.values()) >= limit - 1
                ):
                    raise ValueError("Last verification slot is reserved for full final evaluation")
            if phase == "final" and agent_id != "rsi":
                raise ValueError("RSI must select and freeze the final candidate")
            if phase == "final" and any(not self.manager.get(k).terminal for k in self.jobs):
                raise ValueError("Wait for running jobs before freezing the final candidate")
        if (
            verification
            and phase == "final"
            and candidate_id is not None
            and self.c.store.is_withdrawn(candidate_id)
        ):
            raise ValueError("Withdrawn candidates cannot be selected for final confirmation")
        if (
            verification
            and phase == "final"
            and not self.simulated
            and command.final_candidate_policy == "single"
        ):
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
        if verification and phase == "final" and self.c.state.get("full_benchmark_evaluation"):
            self.c.state["full_final_candidate_id"] = candidate_id
            self.c.save()
            for actor in self.c.store.run().research_agents:
                if actor.state != AgentState.STOPPED:
                    self.c.stop_agent(actor.agent_id, "Final candidate frozen for full evaluation")
        workspace = input_root / "workspace"
        output = self.c.store.root / "execution-outputs" / key
        output.mkdir(parents=True, exist_ok=True)
        # Persist which snapshot is used before materialization, so a restart
        # cannot silently switch to a subsequently edited live workspace.
        snapshot_record = input_root / "snapshot.json"
        if snapshot_record.exists():
            content = decode(ContentRef, json.loads(snapshot_record.read_text()))
        else:
            if source_content is not None:
                content = source_content
            elif candidate_id is not None:
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
        if source_job_id and source_content is not None:
            # Replaying an execution must retain its command. Verification of
            # a published artifact has provenance too, but is a different command.
            old = self.manager.get(source_job_id)
            old_values = dict(values)
            for name, value in {
                "workspace": old.spec.cwd,
                "output": self.jobs[source_job_id]["output"],
            }.items():
                if name in old_values:
                    old_values[name] = value
            if command.bind(old_values, cwd=old.spec.cwd).command != old.spec.command:
                raise ValueError("原作业的命令配置已变化，不能作为同一实验直接复测")
        if self.execution_env:
            from dataclasses import replace

            spec = replace(spec, env=dict(self.execution_env))
        if verification:
            from dataclasses import replace

            assert candidate_id is not None
            candidate = self.c.store.get("candidates", candidate_id, CandidateSnapshot)
            config_path = self.c.store.root / "coordination/config.json"
            run_metadata = (
                json.loads(config_path.read_text()).get("metadata", {})
                if config_path.exists()
                else {}
            )
            portfolio_metadata: dict[str, Any] = {}
            portfolios = run_metadata.get("research_portfolios")
            if isinstance(portfolios, list):
                researchers = [actor.agent_id for actor in self.c.store.run().research_agents]
                if agent_id in researchers:
                    portfolio = portfolios[researchers.index(agent_id)]
                    portfolio_metadata = {
                        "research_portfolio_id": portfolio["id"],
                        "research_portfolio_role": portfolio["role"],
                    }
            spec = replace(
                spec,
                metadata={
                    **spec.metadata,
                    "contract_digest": self.c.store.contract_digest(),
                    "candidate_id": candidate_id,
                    "candidate_digest": candidate.content.digest,
                    "phase": phase,
                    **portfolio_metadata,
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
            "source_job_id": source_job_id,
            "verification": verification,
            "phase": phase,
            "protocol_eligible": not verification
            or phase != "search"
            or not self.require_diagnostic_before_search
            or self._search_protocol_eligible(agent_id, candidate_id),
            "waiting": False,
            "processed": False,
            "output": str(output),
            "output_subdir": command.output_subdir,
            "public_output_subdir": command.public_output_subdir,
            "feedback_file": command.feedback_file,
            "queue_timing_file": command.queue_timing_file,
            "auto_verify_output": command.auto_verify_output if not verification else None,
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
        if not verification:
            self.publish_execution(record)
        self.c.store.event("job_submitted", agent_id=agent_id, job=self.public(record))
        return ToolResult(self.public(record))

    def publish_execution(self, record: JobRecord) -> dict[str, Any]:
        """Publish a shareable receipt now, then finalize the same reference once.

        Live status comes from job_status. The submission receipt is explicitly
        nonterminal; immutable job events preserve its history when finalized.
        Verifier results keep their separate trusted publication boundary.
        """
        info = self.jobs[record.job_id]
        assert not info["verification"]
        path = self.c.store.root / "executions" / f"{record.job_id}.json"
        value = json.loads(path.read_text()) if path.exists() else None
        # Legacy execution records were only published after completion.
        if value is None or (not value.get("terminal", True) and record.terminal):
            value = {
                "agent_id": info["agent_id"],
                "iteration_id": info["iteration_id"],
                "job_id": record.job_id,
                "created_at": info["created_at"],
                "observed_at": now(),
                "simulated": self.simulated,
                "exit_code": record.exit_code,
                "state": record.state,
                "terminal": record.terminal,
                "output_available": record.state == "succeeded",
            }
            self.c.store.put("executions", record.job_id, value, immutable=False)
        ref = self.c.store.paths.reference(path)
        self.c.link_reference(info["agent_id"], ref, iteration_id=info["iteration_id"])
        return {**value, "uri": ref.uri}

    def public(self, record: JobRecord) -> dict[str, Any]:
        info = self.jobs[record.job_id]
        path = self.c.store.root / "executions" / f"{record.job_id}.json"
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
            "source_job_id": info.get("source_job_id"),
            "published_candidate_id": info.get("published_candidate_id"),
            "verification_job_id": info.get("verification_job_id"),
            "publication_error": info.get("publication_error"),
            "phase": info.get("phase") if info["verification"] else None,
            "protocol_eligible": info.get("protocol_eligible", True),
            "state": record.state,
            "gpu_ids": list(record.assigned_gpu_ids),
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "exit_code": record.exit_code,
            "error": record.error,
            "simulated": record.spec.backend == "mock",
            "verification": info["verification"],
            "public_files_available": record.terminal
            and (not info["verification"] or info.get("public_output_subdir") is not None),
            "result": info.get("result"),
            **(
                {"uri": self.c.store.paths.reference(path).uri, "record_id": record.job_id}
                if not info["verification"] and path.is_file()
                else {}
            ),
        }

    def for_agent(self, record: JobRecord, agent_id: str) -> dict[str, Any]:
        result = self.public(record)
        info = self.jobs[record.job_id]
        # Run-wide status access does not grant access to another branch's files.
        if not info["verification"] and agent_id in {"rsi", info["agent_id"]}:
            progress = read_progress(Path(info["output"]))
            if progress is not None:
                result["progress"] = progress
            if record.state in {"failed", "timed_out", "lost"} and not info.get("feedback"):
                # Supply already-readable failure evidence with the status instead
                # of requiring another model/tool round trip to discover it.
                with suppress(OSError):
                    with open(record.log_path, "rb") as stream:
                        stream.seek(0, 2)
                        offset = max(0, stream.tell() - 16000)
                        stream.seek(offset)
                        raw = stream.read(16000)
                    if raw:
                        result["failure_log"] = {
                            "text": raw.decode(errors="replace"),
                            "truncated": offset > 0,
                        }
        if info.get("feedback") and (info["verification"] or agent_id in {"rsi", info["agent_id"]}):
            result["feedback"] = {
                **info["feedback"],
                "record_id": effect_id("feedback", record.job_id),
            }
        return result

    def publish_feedback(self, record: JobRecord) -> None:
        info = self.jobs[record.job_id]
        saved = self.c.store.root / "materials" / (effect_id("feedback", record.job_id) + ".json")
        if saved.exists():
            ref = self.c.store.paths.reference(saved)
            info["feedback"] = {**json.loads(saved.read_text()), "uri": ref.uri}
            self.c.link_reference(info["agent_id"], ref, iteration_id=info["iteration_id"])
            return
        name = info.get("feedback_file", "feedback.json")
        subdir = (
            info.get("public_output_subdir")
            if info["verification"]
            else info.get("output_subdir", ".")
        )
        if not name or subdir is None:
            return
        root = Path(info["output"])
        source = root / subdir
        try:
            path = source / safe_relative(name)
            if (
                not path.is_file()
                or not source.resolve().is_relative_to(root.resolve())
                or not path.resolve().is_relative_to(source.resolve())
                or any(
                    p.is_symlink()
                    for p in (path, *path.parents)
                    if p != root and p.is_relative_to(root)
                )
            ):
                return
            with path.open() as stream:
                text = stream.read(FEEDBACK_CHARS + 1)
            value = {
                "agent_id": info["agent_id"],
                "job_id": record.job_id,
                "path": name,
                "content": text[:FEEDBACK_CHARS],
                "truncated": len(text) > FEEDBACK_CHARS,
                "job_state": record.state,
            }
            ref = self.c.store.put("materials", effect_id("feedback", record.job_id), value)
            info["feedback"] = {**value, "uri": ref.uri}
            self.c.link_reference(info["agent_id"], ref, iteration_id=info["iteration_id"])
        except (OSError, ValueError, UnicodeError):
            # Missing/binary feedback must not hide the job's real terminal state.
            return

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
            text = (
                "作业已完成，以下是执行结果与已发布反馈。继续当前任务；需要细节时再读日志。\n"
                + json.dumps(self.for_agent(record, agent_id), ensure_ascii=False)
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

    async def publish_candidate(self, record: JobRecord) -> None:
        """Archive configured outputs and request trusted scoring without a model turn.

        Stable candidate/job IDs make recovery replayable. The command's receipt
        remains feedback only; it cannot supply a trusted score.
        """
        info = self.jobs[record.job_id]
        subdir = info.get("auto_verify_output")
        if subdir is None or record.state != "succeeded":
            return
        output = Path(info["output"])
        source = output if subdir == "." else output / safe_relative(subdir)
        if not source.resolve().is_relative_to(output.resolve()):
            raise ValueError("Configured artifact path escapes job output")
        if source.is_symlink() or any(p.is_symlink() for p in source.rglob("*")):
            raise ValueError("Job output must not contain symbolic links")
        if not source.is_dir() or not any(p.is_file() for p in source.rglob("*")):
            return  # A diagnostic or failed attempt need not produce a candidate.
        candidate_id = effect_id("candidate", "output:" + record.job_id)
        candidate_path = self.c.store.root / "candidates" / f"{candidate_id}.json"
        if not candidate_path.exists():
            await self.c.store.snapshot(
                source,
                agent_id=info["agent_id"],
                description="作业产物：" + record.job_id,
                parent_candidate_id=info.get("candidate_id"),
                candidate_id=candidate_id,
                scope_base_candidate_id=self.c.state["seed_candidate_id"],
            )
        self.c.link_reference(
            info["agent_id"],
            self.c.store.paths.reference(candidate_path),
            iteration_id=info["iteration_id"],
        )
        info["published_candidate_id"] = candidate_id
        self.save()
        if self.c.state["status"] != "running":
            return
        result = await self.submit(
            info["agent_id"],
            ToolCall(
                name="verify_candidate",
                arguments={"candidate_id": candidate_id, "phase": "search"},
                call_id="auto-verify:" + record.job_id,
            ),
            source_job_id=record.job_id,
        )
        job_id = result.output["job_id"]
        self.jobs[job_id]["iteration_id"] = info["iteration_id"]
        self.jobs[job_id]["automatic"] = True
        info["verification_job_id"] = job_id
        self.save()

    async def poll(self) -> bool:
        async with self._poll_lock:
            return await self._poll()

    async def ensure_full_baseline(self) -> bool:
        """Submit once per attempt; failures pause, resume explicitly permits a new attempt."""
        from areal_rsi.coordination.benchmark import baseline_pending

        if not baseline_pending(self.c.state):
            return False
        config_path = self.c.store.root / "coordination/config.json"
        metadata = (
            json.loads(config_path.read_text()).get("metadata", {}) if config_path.exists() else {}
        )
        if "full_baseline_source" in metadata:
            from areal_rsi.coordination.benchmark import imported_baseline, read_baseline_source

            archive = self.c.store.root / "coordination/imported-baseline.json"
            try:
                if archive.exists():
                    raw = json.loads(archive.read_text())
                else:
                    raw = await asyncio.to_thread(
                        read_baseline_source, metadata["full_baseline_source"]
                    )
                candidate = self.c.store.get(
                    "candidates", self.c.state["baseline_candidate_id"], CandidateSnapshot
                )
                manifest = json.loads(
                    (
                        self.c.store.content.root / candidate.content.digest.removeprefix("sha256:")
                    ).read_text()
                )
                files = {
                    name: ref["digest"].removeprefix("sha256:")
                    for name, ref in manifest["files"].items()
                }
                contract = self.c.store.brief().verification
                if contract is None:
                    raise ValueError("Full baseline requires a verification contract")
                result = imported_baseline(
                    raw,
                    candidate=candidate,
                    files=files,
                    metadata=metadata,
                    contract_digest=self.c.store.contract_digest(),
                    metric=contract.metric,
                )
                write_json(archive, raw, immutable=True)
                result.references = (self.c.store.paths.reference(archive),)
                previous_path = self.c.store.root / "verifications" / f"{result.result_id}.json"
                if previous_path.exists():
                    result.created_at = self.c.store.get(
                        "verifications", result.result_id, VerifiedResult
                    ).created_at
                self.c.store.record_verification(result)
                run = self.c.store.run()
                run.baseline_result_ref = self.c.store.paths.reference(
                    self.c.store.root / "verifications" / f"{result.result_id}.json"
                )
                self.c.store.save_run(run)
                self.c.state["full_baseline_ready"] = True
                self.c.state["baseline_origin"] = "imported"
                self.c.state.pop("baseline_error", None)
                self.c.save()
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.c.state["baseline_error"] = "Cannot import full baseline: " + str(exc)
                self.c.save()
                self.c.pause()
            return False
        attempts = [item for item in self.jobs.values() if item.get("phase") == "baseline"]
        if attempts and not attempts[-1].get("processed"):
            return True
        if attempts and self.c.state.get("baseline_failure_seen") != len(attempts):
            self.c.state["baseline_failure_seen"] = len(attempts)
            self.c.state["baseline_error"] = (
                attempts[-1].get("result", {}).get("error", "Incomplete baseline")
            )
            self.c.save()
            self.c.pause()
            return False
        try:
            await self.submit(
                "rsi",
                ToolCall(
                    name="verify_candidate",
                    arguments={
                        "candidate_id": self.c.state["baseline_candidate_id"],
                        "phase": "baseline",
                    },
                    call_id=f"full-baseline-{len(attempts)}",
                ),
            )
        except ValueError as exc:
            self.c.state["baseline_error"] = str(exc)
            self.c.save()
            self.c.pause()
            return False
        return True

    async def _poll(self) -> bool:
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
                (actor_stopped and not info.get("automatic"))
                or self.c.state["status"] in {"stopped", "succeeded", "failed"}
                or self.c.state.get("budget_stop_reason") == "Run wall-time budget exhausted"
            )
            if must_cancel and not self.manager.get(key).terminal:
                self.manager.cancel(key)
        self.manager.scheduling_enabled = self.c.state["status"] == "running"
        records = self.manager.poll()
        if limits.max_effective_seconds is not None:
            timer = EffectiveTime(self.c.state)
            for record in records:
                info = self.jobs.get(record.job_id)
                if info is None or info["verification"]:
                    continue
                timer.queue(
                    info["agent_id"],
                    "local:" + record.job_id,
                    record.created_at,
                    record.started_at or record.finished_at,
                )
                if name := info.get("queue_timing_file"):
                    receipt = Path(info["output"]) / safe_relative(name)
                    if receipt.is_file():
                        remote = json.loads(receipt.read_text())
                        end = remote.get("started_at") or remote.get("finished_at")
                        if record.finished_at is not None:
                            # A stale queued receipt cannot outlive its local execution.
                            end = min(end or record.finished_at, record.finished_at)
                        timer.queue(
                            info["agent_id"],
                            "remote:" + record.job_id,
                            remote["created_at"],
                            end,
                        )
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
                    and result.phase
                    == ("baseline" if self.c.state.get("full_benchmark_evaluation") else "search")
                    and result.status == "succeeded"
                    and result.score is not None
                ):
                    run.baseline_result_ref = ref
                    if self.c.state.get("full_benchmark_evaluation"):
                        self.c.state["full_baseline_ready"] = True
                        self.c.state["baseline_origin"] = "evaluated"
                        self.c.state.pop("baseline_error", None)
                        self.c.save()
                if (
                    result.phase == "final"
                    and result.status == "succeeded"
                    and (result.passed or self.c.state.get("full_benchmark_evaluation"))
                    and not self.c.store.is_withdrawn(candidate.candidate_id)
                ):
                    run.final_result_ref = ref
                    if self.c.state.get("full_benchmark_evaluation") and run.baseline_result_ref:
                        baseline_result = self.c.store.public_record(run.baseline_result_ref)
                        write_json(
                            self.c.store.root / "coordination/full-comparison.json",
                            {
                                "baseline": baseline_result,
                                "final": encode(result),
                                "score_delta": result.score - baseline_result["score"],
                                "metric_deltas": {
                                    name: value - baseline_result["metrics"][name]
                                    for name, value in result.metrics.items()
                                    if name in baseline_result["metrics"]
                                },
                                "scope": (
                                    "sealed-test generalization"
                                    if baseline_result.get("split") == "test"
                                    else "same-benchmark improvement, not held-out generalization"
                                ),
                            },
                        )
                self.c.store.save_run(run)
                info["result"] = {**encode(result), "uri": ref.uri}
                if self._trusted_search_success(info):
                    self.c.store.consider_best(result.result_id, simulated_run=self.simulated)
                if (
                    result.phase == "search"
                    and result.status == "succeeded"
                    and info.get("protocol_eligible", True)
                ):
                    config_path = self.c.store.root / "coordination/config.json"
                    metadata = (
                        json.loads(config_path.read_text()).get("metadata", {})
                        if config_path.exists()
                        else {}
                    )
                    target = metadata.get("stop_after_search_verifications")
                    if target is not None:
                        if type(target) is not int or target <= 0:
                            raise ValueError(
                                "stop_after_search_verifications must be a positive integer"
                            )
                        completed = sum(
                            self._trusted_search_success(job) for job in self.jobs.values()
                        )
                        self.c.state["completed_search_verifications"] = completed
                        self.c.save()
                        if completed >= target and self.c.state["status"] == "running":
                            self.c.stop(
                                f"Completed the configured {target} trusted search verifications"
                            )
                    per_agent_target = metadata.get("minimum_search_verifications_per_agent")
                    if per_agent_target is not None:
                        if type(per_agent_target) is not int or per_agent_target <= 0:
                            raise ValueError(
                                "minimum_search_verifications_per_agent must be a positive integer"
                            )
                        counts = {
                            actor.agent_id: sum(
                                job.get("agent_id") == actor.agent_id
                                and self._trusted_search_success(job)
                                for job in self.jobs.values()
                            )
                            for actor in self.c.store.run().research_agents
                        }
                        self.c.state["completed_search_verifications_by_agent"] = counts
                        self.c.save()
                        from areal_rsi.coordination.passk import record_search_targets

                        record_search_targets(self.c, counts, per_agent_target)
                        if (
                            counts
                            and all(count >= per_agent_target for count in counts.values())
                            and self.c.state["status"] == "running"
                        ):
                            self.c.stop(
                                "Every researcher completed at least "
                                f"{per_agent_target} trusted search verifications"
                            )
            else:
                # Job exit state is evidence. Training stdout is never a score.
                info["result"] = self.publish_execution(record)
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
            self.c.link_reference(
                info["agent_id"],
                RecordRef(info["result"]["uri"]),
                iteration_id=info["iteration_id"],
            )
            self.publish_feedback(record)
            try:
                await self.publish_candidate(record)
            except Exception as exc:
                # Scoring/publication failure must not discard useful experiment
                # feedback or strand its owner. It stays visible and can be retried.
                info["publication_error"] = f"{type(exc).__name__}: {exc}"
            self.notify_completion(record)
            info["processed"] = True
            self.save()
            self.c.store.event("job_completed", agent_id=info["agent_id"], job=self.public(record))
        return any(not self.manager.get(key).terminal for key in self.jobs)

    def close(self, *, cancel: bool = False) -> None:
        self.manager.close(preserve_running=not cancel)
