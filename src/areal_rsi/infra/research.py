"""One run owner, shared by the TUI and headless CLI.

Policies own research behavior; Runtime owns model sessions; Execution owns jobs.
No HTTP dispatcher, UI settings, historical migrations or background web service.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from areal_rsi.api.interfaces import ResearchAgentRuntime
from areal_rsi.api.research import CandidateSnapshot, GoalContext, VerifiedResult
from areal_rsi.infra.agents.communication import tool_instructions
from areal_rsi.infra.coordination.config import ResearchConfig, config_from_dict, git_head_commit
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.coordination.engine import ResearchEngine, ToolSet
from areal_rsi.infra.coordination.evaluation.jobs import ResearchExecution
from areal_rsi.infra.coordination.evaluation.verification import read_trusted_result
from areal_rsi.infra.coordination.intake import (
    ResearchIntake,
    missing_requirements,
    validate_execution,
)
from areal_rsi.infra.coordination.policies.goal import GoalActions, GoalEngine
from areal_rsi.infra.coordination.policies.legacy_passk import (
    PassKActions,
    PassKEngine,
)
from areal_rsi.infra.coordination.policies.legacy_passk import (
    finish_run as finish_legacy_run,
)
from areal_rsi.infra.coordination.store import ResearchStore
from areal_rsi.infra.coordination.tools.actions import ResearchActions
from areal_rsi.infra.coordination.tools.web import register_sources
from areal_rsi.infra.execution import JobManager, JobRecord
from areal_rsi.infra.runtime.research_runtime import ResearchRuntime
from areal_rsi.infra.storage.files import encode, new_id, now, safe_name, write_json


@dataclass
class ActiveResearch:
    """Components and resource scopes owned by one local worker."""

    coordinator: Coordinator
    actions: ResearchActions
    execution: ResearchExecution
    engine: ResearchEngine
    resources: AsyncExitStack
    model_resources: AsyncExitStack
    task: asyncio.Task[None] = field(init=False)


def _toolset(actions: ResearchActions, config: ResearchConfig, agent_id: str) -> ToolSet:
    workspace = actions.workspace(agent_id)
    return ToolSet(
        actions.definitions(agent_id, include_hidden=True),
        actions.handler(agent_id),
        tool_instructions(
            str(workspace),
            backend=config.backend,
            supervisor_only=config.mode == "legacy_passk" and agent_id == "rsi",
        ),
        actions.definitions(agent_id),
        workspace,
        config.execution_env,
    )


class ResearchSystem:
    def __init__(
        self,
        root: Path,
        *,
        runtime_factory: Callable[[ResearchConfig], ResearchAgentRuntime] | None = None,
    ):
        self.root = root.resolve()
        self.runtime_factory = runtime_factory or ResearchRuntime
        self.active: dict[str, ActiveResearch] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def coordinator(self, run_id: str) -> Coordinator:
        safe_name(run_id)
        active = self.active.get(run_id)
        return active.coordinator if active else Coordinator(ResearchStore(self.root, run_id))

    @staticmethod
    def config(c: Coordinator) -> ResearchConfig:
        config = config_from_dict(
            json.loads((c.store.root / "coordination/config.json").read_text())
        )
        config.brief = c.store.brief()
        return config

    @asynccontextmanager
    async def control(self, run_id: str) -> AsyncIterator[Coordinator]:
        async with self._locks.setdefault(run_id, asyncio.Lock()):
            c = self.coordinator(run_id)
            try:
                c.acquire()
            except BlockingIOError:
                raise ValueError("Another live process owns this run") from None
            try:
                yield c
            finally:
                c.release()

    async def create(
        self,
        config: ResearchConfig,
        *,
        run_id: str | None = None,
        simulated: bool = False,
    ) -> str:
        """Validate config, pin model settings, save initial records, and return the run ID."""
        config = config_from_dict(config.public())
        config.pin_model_settings()
        if not simulated and (issues := validate_execution(config)):
            raise ValueError("; ".join(issues))
        # Check if there is an existing HEAD commit.
        if config.workspace_dir:
            git_head_commit(config.workspace_dir)

        c = Coordinator.create(self.root, config.brief, run_id=run_id, simulated=simulated)
        c.state.mode = config.mode
        c.state.workspace_dir = config.workspace_dir
        c.save()
        write_json(c.store.root / "coordination/config.json", config.public(), immutable=True)
        return c.store.run_id

    async def start(self, run_id: str) -> None:
        """Validate the run, prepare its workspace, and start background execution."""
        async with self.control(run_id) as c:
            if c.state.status != "planned":
                raise ValueError("Run has already started")
            config = self.config(c)
            if not config.brief.objective.strip() or not config.brief.instructions.strip():
                raise ValueError("Start requires a task objective and instructions")
            if c.state.intake.status is not None and (
                missing := missing_requirements(config.brief)
            ):
                raise ValueError("Start requires: " + ", ".join(missing))
            if not c.state.simulated and (issues := validate_execution(config)):
                raise ValueError("; ".join(issues))
            config.validate_clock()
            c.state.workspace_base_commit = (
                git_head_commit(config.workspace_dir) if config.workspace_dir else None
            )
            if config.mode == "goal":
                c.state.goal = GoalContext(
                    acceptance=list(config.brief.acceptance), updated_at=now()
                )
            conversation = c.state.intake.status is not None
            c.state.intake.status = "starting"
            c.save()
            try:
                await self._seed(c, config)
                active = await self.attach(c)
                active.execution.manager.gpu_ids = config.brief.limits.gpu_ids
                active.execution.manager.max_jobs = config.brief.limits.max_jobs
                await active.actions.initialize_workspace("rsi")
                write_json(
                    c.store.root / "coordination/started-config.json",
                    config.public(),
                    immutable=True,
                )
                c.state.intake.status = "ready" if conversation else None
                c.start()
                active.engine.wake()
            except BaseException:
                if c.state.status == "planned":
                    c.state.intake.status = "discussing" if conversation else None
                    c.save()
                raise

    async def attach(self, c: Coordinator) -> ActiveResearch:
        """Reuse an active run or assemble and launch its local execution instance."""
        previous = self.active.get(c.store.run_id)
        if previous and not previous.task.done():
            return previous
        resources = AsyncExitStack()
        try:
            c.acquire()
            resources.callback(c.release)
            # Models close before legacy final verification; jobs and ownership span it.
            model_resources = await resources.enter_async_context(AsyncExitStack())
            config = self.config(c)
            runtime = self.runtime_factory(config)
            model_resources.push_async_callback(runtime.close)
            legacy = config.mode == "legacy_passk"
            actions = PassKActions(c) if legacy else GoalActions(c)
            if isinstance(actions, GoalActions):
                actions.recover_acceptance()
            register_sources(
                actions,
                enabled=config.backend != "codex" and config.metadata.get("allow_web_access", True),
            )
            execution = ResearchExecution(
                actions,
                commands={cmd.name: cmd for cmd in config.commands},
                verifier=config.verifier,
                read_verification=self._verification_reader(c, config),
                execution_env=config.execution_env,
                simulated=c.state.simulated,
            )
            resources.callback(execution.close)
            execution.instructions = str(config.metadata.get("execution_instructions", ""))
            ResearchIntake(actions, partial(self.start, c.store.run_id))
            engine_type = PassKEngine if legacy else GoalEngine
            engine = engine_type(
                c, runtime, partial(_toolset, actions, config), tick_hook=execution.poll
            )
            resources.push_async_callback(engine.close)
            if engine.interaction_runtime is not runtime:
                model_resources.push_async_callback(engine.interaction_runtime.close)
            active = ActiveResearch(c, actions, execution, engine, resources, model_resources)
            active.task = asyncio.create_task(self._serve(active), name="rsi:" + c.store.run_id)
            self.active[c.store.run_id] = active
            return active
        except BaseException:
            await resources.aclose()
            raise

    async def _serve(self, active: ActiveResearch) -> None:
        """Run model turns, finish the run, and release its registered resources."""
        c = active.coordinator
        try:
            await active.engine.serve()
            await active.model_resources.aclose()
            if isinstance(active.engine, PassKEngine):
                await finish_legacy_run(c, self.config(c), active.execution)
            else:
                await active.execution.cancel_pending()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            c.state.status = "failed"
            c.save()
            c.store.event("worker_failed", error=str(exc))
        finally:
            await active.resources.aclose()

    async def send_message(self, run_id: str, text: str) -> None:
        async with self.control(run_id) as c:
            c.user_message(text, key=new_id("user"))
            active = await self.attach(c)
            active.engine.wake()

    async def pause(self, run_id: str) -> None:
        async with self.control(run_id) as c:
            c.pause()
            active = self.active.get(run_id)
            if active:
                await active.engine.tick()

    async def resume(self, run_id: str) -> None:
        async with self.control(run_id) as c:
            if c.state.status == "failed" and c.state.started_at:
                c.state.status = "running"
                c.save()
            if c.state.status == "paused":
                if c.state.budget_stop_reason:
                    raise ValueError("The run budget is exhausted; start a new run")
                c.resume()
            if c.state.status not in {"planned", "running"}:
                raise ValueError("Only planned, active or paused runs can resume")
            active = await self.attach(c)
            active.engine.wake()

    async def retry(self, run_id: str, agent_id: str = "rsi") -> None:
        async with self.control(run_id) as c:
            c.retry_agent(agent_id)
            active = await self.attach(c)
            active.engine.wake()

    async def stop(self, run_id: str) -> None:
        async with self.control(run_id) as c:
            if c.state.status in {"succeeded", "completed"}:
                return
            c.state.user_stopped = True
            c.stop("Stopped by user")
            active = self.active.get(run_id)
            if active:
                await self._stop_worker(active)
                # The worker closed its monitor; recover job ownership for cancellation.
            manager = JobManager(c.store.root, auto_monitor=False, scheduling_enabled=False)
            await asyncio.to_thread(manager.close, preserve_running=False)

    async def detail(self, run_id: str, *, after: int = 0) -> dict[str, Any]:
        c = self.coordinator(run_id)
        active = self.active.get(run_id)
        events = c.store.events(after=after)
        return {
            **c.summary(),
            "events": encode(events),
            "event_cursor": events[-1].sequence if events else after,
            "worker_alive": bool(active and not active.task.done()),
            "selected_candidate_id": c.selected_candidate_id,
            "finalization": encode(c.completion),
            "workspace": str(
                self.root / "worktrees" / run_id / "rsi"
                if c.state.workspace_dir
                else c.store.paths.agent("rsi") / "workspace"
            ),
        }

    async def export(self, run_id: str, destination: Path) -> None:
        async with self.control(run_id) as c:
            selected = c.selected_candidate_id
            if not selected or c.state.status not in {"succeeded", "completed"}:
                raise ValueError("This run has no accepted artifact")
            if destination.exists():
                raise FileExistsError(destination)
            candidate = c.store.get("candidates", selected, CandidateSnapshot)
            await c.store.content.materialize(candidate.content, destination)

    async def close(self) -> None:
        await asyncio.gather(*(self._stop_worker(a) for a in self.active.values()))
        self.active.clear()

    async def _stop_worker(self, active: ActiveResearch) -> None:
        active.task.cancel()
        try:
            await asyncio.gather(active.task, return_exceptions=True)
        finally:
            # A task cancelled before its first instruction never enters _serve's finally.
            await active.resources.aclose()

    async def _seed(self, c: Coordinator, config: ResearchConfig) -> None:
        if config.seed_dir and not config.workspace_dir and not c.state.seed_candidate_id:
            candidate = await c.store.snapshot(
                Path(config.seed_dir), agent_id="operator", description="研究起点"
            )
            c.state.seed_candidate_id = candidate.candidate_id
            if config.metadata.get("evaluate_seed_as_baseline"):
                c.state.baseline_candidate_id = candidate.candidate_id
            c.save()

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
                "simulated": c.state.simulated,
            }
            if record.state != "succeeded":
                from areal_rsi.infra.execution.diagnostics import failure_summary

                return VerifiedResult(
                    **kwargs,
                    status="infra_error",
                    error=failure_summary(
                        c.store.root / "execution-outputs" / record.job_id,
                        record.error or record.state,
                    ),
                )
            if c.state.simulated:
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
