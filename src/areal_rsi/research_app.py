"""Assemble the research workbench without requiring a Task Agent or Solution."""

from __future__ import annotations

import asyncio
import fcntl
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from areal_rsi.api.base import ExperimentRun
from areal_rsi.api.research import (
    CandidateSnapshot,
    EvidenceRecord,
    ResearchMessage,
    TaskBrief,
    VerifiedResult,
)
from areal_rsi.base.runs import FileRunStore
from areal_rsi.base.storage import decode, encode, new_id, now, write_json
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.agent_tools import AgentTools
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ResearchEngine, ToolSet
from areal_rsi.coordination.intake import ResearchIntake, validate_execution
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.coordination.presets import example_config
from areal_rsi.coordination.sources import register_sources
from areal_rsi.coordination.store import ResearchStore
from areal_rsi.coordination.verification import read_trusted_result
from areal_rsi.execution import JobManager, JobRecord
from areal_rsi.modules.responses import ResponsesRuntime


@dataclass
class ActiveResearch:
    coordinator: Coordinator
    config: ResearchConfig
    actions: ResearchActions
    execution: ResearchExecution
    tools: AgentTools
    engine: ResearchEngine
    task: asyncio.Task[None]


class ResearchApp:
    def __init__(
        self,
        root: Path,
        *,
        repository: Path | None = None,
        config: ResearchConfig | None = None,
        runtime_factory: Callable[[ResearchConfig], ResponsesRuntime] | None = None,
        allow_simulation: bool = False,
    ):
        self.root = root.resolve()
        self.repository = repository or Path(__file__).resolve().parents[2]
        self.default_config = config
        self.runtime_factory = runtime_factory or (lambda cfg: ResponsesRuntime(cfg.runtime()))
        self.allow_simulation = allow_simulation
        self.active: dict[str, ActiveResearch] = {}

    def worker_elsewhere(self, c: Coordinator) -> bool:
        local = self.active.get(c.store.run_id)
        if local is not None and not local.task.done():
            return False
        path = c.store.paths.execution_lock
        if not path.exists():
            return False
        with path.open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
        return False

    def coordinator(self, run_id: str) -> Coordinator:
        if run_id in self.active:
            return self.active[run_id].coordinator
        return Coordinator(ResearchStore(self.root, run_id))

    def config(self, c: Coordinator) -> ResearchConfig:
        config: ResearchConfig = decode(
            ResearchConfig, json.loads((c.store.root / "coordination" / "config.json").read_text())
        )
        return config

    async def create(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation = bool(
            params.get("conversation", self.default_config is None and not self.allow_simulation)
        )
        simulated = False if conversation else bool(params.get("simulated", self.allow_simulation))
        message = str(params.get("message", "")).strip()
        if len(message) > 12000:
            raise ValueError("消息不能超过 12000 字")
        if simulated and not self.allow_simulation:
            raise ValueError("Restart with simulation enabled to create simulated runs")
        if self.default_config is None:
            config = (
                ResearchConfig(TaskBrief("新的研究", ""))
                if conversation
                else example_config(
                    params.get("template", "kernelbench"), repository=self.repository
                )
            )
        else:
            config = decode(ResearchConfig, encode(self.default_config))
        if params.get("objective"):
            config.brief.objective = str(params["objective"])
        if params.get("instructions"):
            config.brief.instructions += "\nUser instructions:\n" + str(params["instructions"])
        if "max_agents" in params:
            config.brief.limits.max_agents = int(params["max_agents"])
            config.brief.limits.__post_init__()
        if config.metadata.get("simulation_only") and not simulated:
            raise ValueError(
                "This template is for workflow simulation; configure real runners first"
            )
        if not conversation and config.seed_dir and not Path(config.seed_dir).is_dir():
            raise FileNotFoundError(f"Seed directory does not exist: {config.seed_dir}")
        if (
            not conversation
            and not simulated
            and config.verifier
            and not config.metadata.get("verification_identity")
        ):
            raise ValueError("Real verification requires a frozen verification_identity")
        c = Coordinator.create(self.root, config.brief, simulated=simulated)
        write_json(c.store.root / "coordination" / "config.json", config.public(), immutable=True)
        if conversation:
            c.state["intake"] = {"status": "discussing", "summary": "", "environment": ""}
            c.save()
        else:
            await self._seed(c, config)
        if simulated and config.metadata.get("template") == "kernelbench":
            ref = c.store.put(
                "materials",
                "development_trajectory",
                {
                    "agent_id": "rsi",
                    "simulated": True,
                    "shared": True,
                    "description": "Synthetic development trajectory for workflow testing",
                    "attempts": [
                        {
                            "failure": "Tail tile writes past output bounds",
                            "observation": "The generated kernel omitted masks on a tail tile",
                        },
                        {
                            "failure": "Candidate was timed before a correctness check",
                            "observation": "The harness did not require correctness before timing",
                        },
                    ],
                },
            )
            c.store.put(
                "evidence",
                "development_trajectory",
                EvidenceRecord(
                    "development_trajectory",
                    "operator",
                    "Development trajectory observations",
                    "Two synthetic failures concern tail masks and correctness-before-timing.",
                    now(),
                    references=(ref,),
                    shared=True,
                    limitations="Simulated task traces; no real task-model performance measured",
                ),
            )
        if message:
            await self.call(
                "chat",
                {
                    "run_id": c.store.run_id,
                    "text": message,
                    "message_id": params.get("message_id") or new_id("user"),
                },
            )
        return await self.detail(c.store.run_id)

    async def _seed(self, c: Coordinator, config: ResearchConfig) -> None:
        if config.seed_dir and not c.state.get("seed_candidate_id"):
            candidate = await c.store.snapshot(
                Path(config.seed_dir), agent_id="operator", description="研究起点"
            )
            c.state["seed_candidate_id"] = candidate.candidate_id
            if config.metadata.get("evaluate_seed_as_baseline"):
                c.state["baseline_candidate_id"] = candidate.candidate_id
            c.save()

    async def start(self, c: Coordinator, confirmation_message_id: str | None = None) -> None:
        if c.state["status"] != "planned":
            raise ValueError("研究已经开始")
        if "intake" in c.state and c.state["intake"].get("status") != "ready":
            raise ValueError("请先与 Interaction 确认研究目标和执行条件")
        config = self.config(c)
        config.brief = c.store.brief()
        if "intake" in c.state:
            issues = validate_execution(config)
            if issues:
                raise ValueError("；".join(issues))
        active = await self.attach(c)
        # Configuration may have changed over several Interaction turns. Bind its
        # final revision before RSI can submit any jobs.
        active.config = config
        active.execution.commands = {item.name: item for item in config.commands}
        active.execution.verifier = config.verifier
        active.execution.execution_env = config.execution_env
        active.execution.read_verification = self._verification_reader(c, config)
        active.execution.instructions = str(config.metadata.get("execution_instructions", ""))
        active.engine.max_concurrent_turns = config.brief.limits.max_agents + 2
        await self._seed(c, config)
        await active.actions.initialize_workspace("rsi")
        write_json(
            c.store.root / "coordination" / "started-config.json", config.public(), immutable=True
        )
        if "intake" in c.state:
            c.state["intake"]["confirmation_message_id"] = confirmation_message_id
            c.save()
        c.start()
        active.engine.wake()

    def _verification_reader(
        self, c: Coordinator, config: ResearchConfig
    ) -> Callable[[JobRecord, CandidateSnapshot, str], VerifiedResult]:
        def read(record: JobRecord, candidate: CandidateSnapshot, phase: str) -> VerifiedResult:
            kwargs: dict[str, Any] = {
                "result_id": record.job_id,
                "candidate_id": candidate.candidate_id,
                "contract_digest": c.store.contract_digest(),
                "created_at": now(),
                "phase": phase,
                "simulated": c.state["simulated"],
            }
            if record.state != "succeeded":
                return VerifiedResult(
                    **kwargs, status="infra_error", error=record.error or record.state
                )
            if c.state["simulated"]:
                path = c.store.root / "simulation" / "scores.json"
                scores = json.loads(path.read_text()) if path.exists() else {}
                identity = candidate.content.digest
                if identity not in scores:
                    index = min(len(scores), len(config.simulation_scores) - 1)
                    scores[identity] = config.simulation_scores[index]
                    write_json(path, scores)
                score = scores[identity]
                return VerifiedResult(
                    **kwargs,
                    status="succeeded",
                    passed=True,
                    score=score,
                    metrics={"simulated_score": score},
                )
            return read_trusted_result(
                c.store,
                record,
                candidate,
                phase,
                identity=config.metadata["verification_identity"],
            )

        return read

    async def attach(self, c: Coordinator) -> ActiveResearch:
        existing = self.active.get(c.store.run_id)
        if existing and not existing.task.done():
            return existing
        if existing:
            existing.execution.close()
            await existing.tools.close()
        config = self.config(c)
        runtime = self.runtime_factory(config)
        actions = ResearchActions(c)
        await actions.initialize_workspace("rsi")
        register_sources(actions)
        agent_tools = AgentTools(
            actions,
            self.root,
            environment=lambda: self.config(c).execution_env,
            credential_names=(config.api_key_env,),
        )
        execution = ResearchExecution(
            actions,
            commands={item.name: item for item in config.commands},
            verifier=config.verifier,
            read_verification=self._verification_reader(c, config),
            simulated=c.state["simulated"],
            mock_duration=0.2,
            execution_env=config.execution_env,
        )
        execution.instructions = str(config.metadata.get("execution_instructions", ""))
        ResearchIntake(
            actions, repository=self.repository, start=lambda message: self.start(c, message)
        )

        async def poll() -> bool:
            if c.state["status"] == "stopped":
                execution.close(cancel=True)
                return False
            return await execution.poll()

        engine = ResearchEngine(
            c,
            runtime,
            lambda agent_id: ToolSet(
                actions.definitions(agent_id),
                actions.handler(agent_id),
                agent_tools.instructions(agent_id),
            ),
            tick_hook=poll,
        )

        async def serve() -> None:
            try:
                await engine.serve()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                c.store.event("worker_error", error=str(exc))
            finally:
                execution.close(cancel=c.state["status"] == "stopped")
                await agent_tools.close()

        task = asyncio.create_task(serve(), name="research:" + c.store.run_id)
        active = ActiveResearch(c, config, actions, execution, agent_tools, engine, task)
        self.active[c.store.run_id] = active
        return active

    async def detail(self, run_id: str, after: int = 0) -> dict[str, Any]:
        c = self.coordinator(run_id)
        summary = c.summary()
        nodes, edges = [], []
        for actor in c.store.run().research_agents:
            for node in actor.iterations:
                assignment = c.store.public_record(node.assignment_ref)
                report = c.store.public_record(node.report_ref) if node.report_ref else None
                evidence = [
                    encode(item)
                    for item in c.store.records("evidence", EvidenceRecord)
                    if item.iteration_id == node.iteration_id
                ]
                nodes.append(
                    {
                        **encode(node),
                        "assignment": assignment,
                        "report": report,
                        "evidence": evidence,
                        "verification": None,
                    }
                )
                if node.parent_iteration_id:
                    edges.append(
                        {
                            "source": node.parent_iteration_id,
                            "target": node.iteration_id,
                            "kind": "parent",
                        }
                    )
                for ref in node.references:
                    try:
                        source = c.store.public_record(ref)
                    except (ValueError, FileNotFoundError):
                        continue
                    source_node = source.get("iteration_id")
                    if source_node and source_node != node.iteration_id:
                        edges.append(
                            {
                                "source": source_node,
                                "target": node.iteration_id,
                                "kind": "reference",
                            }
                        )
        messages = [
            encode(m)
            for m in c.store.records("messages", ResearchMessage)
            if m.sender == "user" or m.recipient == "user"
        ]
        messages.sort(key=lambda m: m["created_at"])
        jobs_path = c.store.root / "coordination" / "jobs.json"
        jobs = []
        if jobs_path.exists():
            intents = json.loads(jobs_path.read_text())
            for job_id, info in intents.items():
                path = c.store.root / "jobs" / job_id / "job.json"
                if not path.exists():
                    continue
                record = json.loads(path.read_text())
                jobs.append(
                    {
                        "job_id": job_id,
                        "title": (
                            "验证 · " + info["phase"]
                            if info["verification"]
                            else "执行作业 · " + info["agent_id"]
                        ),
                        "agent_id": info["agent_id"],
                        "status": record["state"],
                        "gpu_ids": record["assigned_gpu_ids"],
                        "started_at": record["started_at"],
                        "finished_at": record["finished_at"],
                        "exit_code": record["exit_code"],
                        "error": record["error"],
                        "simulated": c.state["simulated"],
                        "result": info.get("result"),
                    }
                )
                if info.get("verification") and info.get("result"):
                    for public_node in nodes:
                        if public_node["iteration_id"] == info.get("iteration_id"):
                            public_node["verification"] = info["result"]
        run = c.store.run()
        events = c.store.events(after)
        return {
            **summary,
            "objective": c.store.brief().objective,
            "nodes": nodes,
            "edges": edges,
            "messages": messages,
            "jobs": jobs,
            "evidence": encode(c.store.records("evidence", EvidenceRecord)),
            "final": c.store.public_record(run.final_result_ref) if run.final_result_ref else None,
            "events": encode(events),
            "event_cursor": events[-1].sequence if events else after,
            "worker_alive": (run_id in self.active and not self.active[run_id].task.done())
            or self.worker_elsewhere(c),
            "active_agents": list(self.active[run_id].engine.active_agents)
            if run_id in self.active
            else [],
            "pending_agents": c.pending(),
            "started_at": c.state.get("started_at"),
            "finished_at": run.finished_at,
        }

    async def call(self, action: str, params: dict[str, Any]) -> Any:
        if action == "bootstrap":
            return {
                "allow_simulation": self.allow_simulation,
                "default_simulated": self.allow_simulation and self.default_config is None,
                "has_config": self.default_config is not None,
            }
        if action == "list":
            runs = FileRunStore(self.root).list(ExperimentRun)
            return {
                "runs": [
                    {
                        "run_id": r.run_id,
                        "objective": r.plan.goal,
                        "status": self.coordinator(r.run_id).state["status"],
                        "simulated": self.coordinator(r.run_id).state["simulated"],
                    }
                    for r in runs
                    if r.plan.workflow == "research"
                    and (self.allow_simulation or not self.coordinator(r.run_id).state["simulated"])
                ]
            }
        if action == "create":
            return await self.create(params)
        c = self.coordinator(params["run_id"])
        if c.state["simulated"] and not self.allow_simulation:
            raise ValueError("该记录属于开发测试，请在独立的测试工作台查看")
        if action == "detail":
            return await self.detail(c.store.run_id, int(params.get("after", 0)))
        if action != "export" and self.worker_elsewhere(c):
            raise ValueError(
                "This run is controlled by another live process. Close that worker "
                "with Ctrl+C, then resume it here; viewing and export remain available."
            )
        if action == "chat":
            message_id = c.user_message(
                params["text"],
                key=params.get("message_id") or new_id("user"),
                reply_to=params.get("reply_to"),
                recipient=params.get("recipient"),
            )
            active = await self.attach(c)
            active.engine.wake()
            return {"run_id": c.store.run_id, "message_id": message_id, "status": c.state["status"]}
        elif action == "start":
            await self.start(c)
        elif action == "pause":
            c.pause()
            if c.store.run_id in self.active:
                await self.active[c.store.run_id].engine.tick()
        elif action == "resume":
            if c.state["status"] == "paused":
                c.resume()
            elif c.state["status"] not in {"planned", "running"}:
                raise ValueError("This run cannot be resumed")
            active = await self.attach(c)
            active.engine.wake()
        elif action == "retry":
            c.retry_agent(params["agent_id"])
            active = await self.attach(c)
            active.engine.wake()
        elif action == "stop":
            c.stop("Stopped by user")
            if c.store.run_id in self.active:
                active = self.active[c.store.run_id]
                await active.engine.close()
                active.execution.close(cancel=True)
                active.task.cancel()
                await asyncio.gather(active.task, return_exceptions=True)
            else:
                manager = JobManager(c.store.root, auto_monitor=False, scheduling_enabled=False)
                manager.close(preserve_running=False)
        elif action == "export":
            from areal_rsi.ui.workbench import WorkbenchDownload

            result_ref = c.store.run().final_result_ref or c.store.run().best_result_ref
            candidate_id = params.get("candidate_id") or c.state.get("selected_candidate_id")
            if not candidate_id and result_ref is not None:
                candidate_id = c.store.public_record(result_ref)["candidate_id"]
            if not candidate_id:
                raise ValueError("No candidate available for export")
            verification = c.store.public_record(result_ref) if result_ref else None
            if verification and verification["candidate_id"] != candidate_id:
                matching = [
                    r
                    for r in c.store.records("verifications", VerifiedResult)
                    if r.candidate_id == candidate_id
                    and r.status == "succeeded"
                    and r.passed
                    and r.contract_digest == c.store.contract_digest()
                    and r.simulated == c.state["simulated"]
                ]
                matching.sort(key=lambda r: (r.phase == "final", r.created_at), reverse=True)
                verification = encode(matching[0]) if matching else None
            folder = c.store.root / "exports" / new_id("export")
            await c.store.export(candidate_id, folder / "candidate")
            write_json(
                folder / "research-result.json",
                {
                    "run_id": c.store.run_id,
                    "candidate_id": candidate_id,
                    "simulated": c.state["simulated"],
                    "verification": verification,
                },
            )
            archive = shutil.make_archive(str(folder), "zip", folder)
            return WorkbenchDownload(Path(archive), candidate_id + ".zip")
        else:
            raise ValueError("Unknown workbench action")
        return await self.detail(c.store.run_id)

    async def close(self) -> None:
        for active in self.active.values():
            active.task.cancel()
        await asyncio.gather(*(a.task for a in self.active.values()), return_exceptions=True)
        self.active.clear()
