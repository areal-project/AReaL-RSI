"""Assemble the research workbench without requiring a Task Agent or Solution."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from areal_rsi.api.base import ExperimentRun
from areal_rsi.api.research import (
    CandidateSnapshot,
    EvidenceRecord,
    ResearchMessage,
    ResearchReport,
    TaskBrief,
    VerifiedResult,
)
from areal_rsi.base.runs import FileRunStore
from areal_rsi.base.storage import decode, encode, new_id, now, write_json
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.agent_tools import AgentTools
from areal_rsi.coordination.collaboration import MODES, build_plan, catalog, manifest
from areal_rsi.coordination.config import (
    MODEL_EFFORTS,
    MODEL_FIELDS,
    ResearchConfig,
    config_from_dict,
    validate_model_settings,
)
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ResearchEngine, ToolSet
from areal_rsi.coordination.intake import ResearchIntake, validate_execution
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.coordination.presets import example_config
from areal_rsi.coordination.sources import register_sources
from areal_rsi.coordination.store import ResearchStore
from areal_rsi.coordination.verification import read_trusted_result
from areal_rsi.execution import JobManager, JobRecord
from areal_rsi.modules.agent_runtime import AgentRuntime
from areal_rsi.modules.research_runtime import ResearchRuntime


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
        runtime_factory: Callable[[ResearchConfig], AgentRuntime] | None = None,
        allow_simulation: bool = False,
    ):
        self.root = root.resolve()
        self.repository = repository or Path(__file__).resolve().parents[2]
        self.default_config = config
        self.runtime_factory = runtime_factory or self._runtime
        self.allow_simulation = allow_simulation
        self.active: dict[str, ActiveResearch] = {}
        self._run_locks: dict[str, asyncio.Lock] = {}

    def run_lock(self, c: Coordinator) -> asyncio.Lock:
        return self._run_locks.setdefault(c.store.run_id, asyncio.Lock())

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

    @staticmethod
    def _runtime(config: ResearchConfig) -> AgentRuntime:
        if not config.api_key():
            raise ValueError(f"Set {config.api_key_env} before starting model agents")
        return ResearchRuntime(config)

    @staticmethod
    def _configure_agent_tools(active: ActiveResearch, config: ResearchConfig) -> None:
        active.tools.configure(config.metadata.get("agent_tool_instructions"))
        allow_web = config.metadata.get("allow_web_access", True)
        register_sources(active.actions, enabled=config.backend == "standard" and allow_web)
        for runtime in (active.engine.runtime, active.engine.interaction_runtime):
            if isinstance(runtime, ResearchRuntime):
                runtime.allow_web_access = allow_web

    def coordinator(self, run_id: str) -> Coordinator:
        if run_id in self.active:
            return self.active[run_id].coordinator
        return Coordinator(ResearchStore(self.root, run_id))

    def config(self, c: Coordinator) -> ResearchConfig:
        config: ResearchConfig = decode(
            ResearchConfig, json.loads((c.store.root / "coordination" / "config.json").read_text())
        )
        if not config.model_settings_pinned:
            # Legacy archives stored service defaults. Prefer the actual recorded
            # connection for a started run, without rewriting its history.
            recorded = sorted(
                c.store.root.glob("agents/*/sessions/*/configuration.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if c.state.get("started_at") and recorded:
                settings = json.loads(recorded[0].read_text())
                for key in MODEL_FIELDS:
                    setattr(config, key, settings.get(key, "standard"))
                config.model_settings_pinned = True
            else:
                config.pin_model_settings()
        return config

    def model_settings(self, c: Coordinator) -> dict[str, Any]:
        config = self.config(c)
        return {
            **config.model_settings(),
            "has_credentials": bool(config.api_key()),
            "editable": c.state["status"] == "planned" and not c.state.get("started_at"),
        }

    def collaboration_settings(self, c: Coordinator) -> dict[str, Any]:
        config = self.config(c)
        path = c.store.root / "coordination/collaboration.json"
        frozen = json.loads(path.read_text()) if path.exists() else None
        return {
            "mode": config.mode,
            "modules": list(config.collaboration_modules),
            "label": MODES[config.mode]["label"],
            "editable": c.state["status"] == "planned"
            and not c.state.get("started_at")
            and frozen is None,
            # Do not backfill current versions into historical experiments.
            "manifest": frozen,
        }

    @staticmethod
    def select_collaboration(config: ResearchConfig, values: dict[str, Any]) -> None:
        if not isinstance(values, dict) or set(values) != {"mode", "modules"}:
            raise ValueError("协作设置需要 mode 和 modules")
        mode, modules = values["mode"], values["modules"]
        if not isinstance(mode, str):
            raise ValueError("协作方式须为名称")
        from areal_rsi.coordination.collaboration import selected_modules

        modules = selected_modules(mode, modules)
        if mode == "passk" and config.mode != "passk":
            # This is part of the explicit pass@k preset, described in the UI.
            # Preserve the user's wall-time, target, k and execution resources.
            config.brief.limits.max_model_calls = None
            config.brief.limits.max_total_tokens = None
            config.brief.limits.max_verify_calls = None
            config.brief.limits.max_nodes = None
            config.max_model_calls_per_turn = None
        config.mode, config.collaboration_modules = mode, modules
        config.__post_init__()

    async def set_collaboration(self, c: Coordinator, values: dict[str, Any]) -> None:
        async with self.run_lock(c):
            c = self.coordinator(c.store.run_id)
            if not self.collaboration_settings(c)["editable"]:
                raise ValueError("研究已经开始，协作配置已锁定；请新建研究进行消融对比")
            config = self.config(c)
            config.brief = c.store.brief()
            self.select_collaboration(config, values)
            active = self.active.get(c.store.run_id)
            if active:
                active.task.cancel()
                await asyncio.gather(active.task, return_exceptions=True)
                active.execution.close(cancel=False)
                await active.tools.close()
                self.active.pop(c.store.run_id, None)
                config = self.config(c)
                config.brief = c.store.brief()
                self.select_collaboration(config, values)
            # Budget changes in the preset must also reach the displayed brief.
            run = c.store.run()
            run.brief_ref = c.store.put("briefs", new_id("collaboration"), config.brief)
            run.plan.max_iterations = config.brief.limits.max_nodes
            c.store.save_run(run)
            if c.state.get("mode", "collaborative") != config.mode:
                # update_brief has a mode-specific schema frozen in the native session.
                self._reset_interaction_session(c)
            write_json(c.store.root / "coordination/config.json", config.public())
            c.state["mode"] = config.mode
            c.save()
            c.store.event(
                "collaboration_updated",
                mode=config.mode,
                modules=list(config.collaboration_modules),
            )
            if active:
                await self.attach(c)

    async def set_model_settings(self, c: Coordinator, values: dict[str, Any]) -> None:
        settings, key = self.parse_model_settings({"backend": self.config(c).backend, **values})
        async with self.run_lock(c):
            c = self.coordinator(c.store.run_id)
            if not self.model_settings(c)["editable"]:
                raise ValueError("研究已经开始，模型设置已锁定；暂停或停止后也不能修改")
            active = self.active.get(c.store.run_id)
            if active:
                active.task.cancel()
                await asyncio.gather(active.task, return_exceptions=True)
                # Cancellation before serve() starts never reaches its finally.
                active.execution.close(cancel=False)
                await active.tools.close()
                self.active.pop(c.store.run_id, None)
            # An Interaction tool may have updated other config fields before
            # cancellation. Reload them instead of replacing the whole config.
            config = self.config(c)
            if any(settings[field] != getattr(config, field) for field in MODEL_FIELDS):
                self._reset_interaction_session(c)
            for field, value in settings.items():
                setattr(config, field, value)
            config.model_settings_pinned = True
            config.model_settings_revision = new_id("model")
            if key:
                self.save_credentials(c, config, key)
            directory = c.store.root / "coordination"
            write_json(
                directory / "configurations" / f"{config.model_settings_revision}.json",
                config.public(),
                immutable=True,
            )
            write_json(directory / "config.json", config.public())
            c.store.event("model_settings_updated", **settings)
            if active:
                # Resume the pending delivery using the selected session.
                await self.attach(c)

    @staticmethod
    def _reset_interaction_session(c: Coordinator) -> None:
        """Keep intake dialogue when a frozen runtime or tool schema changes."""
        actor = c.actor("interaction")
        actor.session_id = new_id("session")
        dialogue = sorted(
            (
                item
                for item in c.store.records("messages", ResearchMessage)
                if item.sender == "user" or item.recipient == "user"
            ),
            key=lambda item: item.created_at,
        )
        write_json(
            c.store.paths.agent("interaction")
            / "sessions"
            / actor.session_id
            / "backend-handoff.json",
            {
                "previous_dialogue": [
                    {"sender": item.sender, "text": item.text} for item in dialogue
                ]
            },
        )
        c.save_actor(actor)

    @staticmethod
    def parse_model_settings(values: dict[str, Any]) -> tuple[dict[str, str], str]:
        if not isinstance(values, dict):
            raise ValueError("模型设置须为对象")
        values = dict(values)
        key = values.pop("api_key", "")
        if not isinstance(key, str) or len(key) > 8192 or any(c.isspace() for c in key.strip()):
            raise ValueError("API Key 格式无效")
        return validate_model_settings(values), key.strip()

    @staticmethod
    def save_credentials(c: Coordinator, config: ResearchConfig, key: str) -> None:
        # Credentials belong to this run, never to event, prompt or public config payloads.
        path = c.store.root / "coordination" / "credentials.json"
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump({"api_key": key}, handle)
        temporary.replace(path)
        config.credential_file = str(path)

    async def create(self, params: dict[str, Any]) -> dict[str, Any]:
        supplied = params.get("config")
        run_id = params.get("run_id")
        creation_digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        if run_id:
            from areal_rsi.base.storage import safe_name

            safe_name(run_id)
            if (self.root / "runs" / run_id / "run.json").exists():
                previous = self.coordinator(run_id)
                if previous.state.get("creation_digest") != creation_digest:
                    raise ValueError("该 run_id 已用于不同的创建请求")
                return await self.detail(run_id)
        benchmark = params.get("benchmark")
        if benchmark is not None:
            if supplied is not None:
                raise ValueError("benchmark 模板不能与完整 config 同时使用")
            from areal_rsi.benchmarks.scale import prepare_config

            run_id = run_id or new_id(f"research_{benchmark}")
            supplied = encode(
                prepare_config(
                    self.root,
                    self.repository,
                    run_id,
                    str(benchmark),
                    max_agents=params.get("max_agents", 1),
                    max_jobs=params.get("max_jobs", 5),
                    hours=params.get("hours", 3),
                )
            )
        conversation = bool(
            params.get(
                "conversation",
                supplied is None and self.default_config is None and not self.allow_simulation,
            )
        )
        simulated = False if conversation else bool(params.get("simulated", self.allow_simulation))
        message = str(params.get("message", "")).strip()
        if len(message) > 12000:
            raise ValueError("消息不能超过 12000 字")
        if simulated and not self.allow_simulation:
            raise ValueError("Restart with simulation enabled to create simulated runs")
        if supplied is not None:
            config = config_from_dict(supplied)
            conversation = False
        elif self.default_config is None:
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
        if "collaboration" in params:
            self.select_collaboration(config, params["collaboration"])
        config.pin_model_settings()
        if "model_settings" in params:
            settings, key = self.parse_model_settings(params["model_settings"])
            for field, value in settings.items():
                setattr(config, field, value)
        else:
            key = ""
        key = key or config.api_key()
        if not conversation and config.seed_dir and not Path(config.seed_dir).is_dir():
            raise FileNotFoundError(f"Seed directory does not exist: {config.seed_dir}")
        if (
            not conversation
            and not simulated
            and config.verifier
            and not config.metadata.get("verification_identity")
        ):
            raise ValueError("Real verification requires a frozen verification_identity")
        if supplied is not None:
            issues = validate_execution(config)
            if issues:
                raise ValueError("；".join(issues))
        c = Coordinator.create(self.root, config.brief, simulated=simulated, run_id=run_id)
        c.state["creation_digest"] = creation_digest
        c.state["mode"] = config.mode
        c.state["shared_agent_workspace"] = config.metadata.get("shared_agent_workspace", False)
        c.save()
        if key:
            self.save_credentials(c, config, key)
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
        async with self.run_lock(c):
            await self._start(self.coordinator(c.store.run_id), confirmation_message_id)

    async def _start(self, c: Coordinator, confirmation_message_id: str | None) -> None:
        if c.state["status"] != "planned":
            raise ValueError("研究已经开始")
        if "intake" in c.state and c.state["intake"].get("status") != "ready":
            raise ValueError("请先与 Interaction 确认研究目标和执行条件")
        config = self.config(c)
        config.brief = c.store.brief()
        config.__post_init__()
        write_json(c.store.root / "coordination" / "config.json", config.public())
        pending_setup = c.state.get("execution_setup") == "pending"
        # A complete operator configuration imported during intake needs no
        # second model turn to approve the same commands after the clock starts.
        prepared_setup = (
            pending_setup
            and bool(config.commands or config.verifier)
            and not validate_execution(config)
        )
        if config.mode == "passk" and pending_setup and not prepared_setup:
            issues = validate_execution(config)
            raise ValueError(
                "passk requires the execution setup to be complete before starting: "
                + ("；".join(issues) if issues else "尚未登记实际执行命令")
            )
        if "intake" in c.state and not pending_setup:
            issues = validate_execution(config)
            if issues:
                raise ValueError("；".join(issues))
        active = await self.attach(c)
        # Configuration may have changed over several Interaction turns. Bind its
        # final revision before RSI can submit any jobs.
        active.config = config
        self._configure_agent_tools(active, config)
        active.execution.commands = {item.name: item for item in config.commands}
        active.execution.verifier = config.verifier
        active.execution.execution_env = config.execution_env
        active.execution.read_verification = self._verification_reader(c, config)
        active.execution.instructions = str(config.metadata.get("execution_instructions", ""))
        active.engine.max_concurrent_turns = config.brief.limits.max_agents + 2
        if prepared_setup:
            await self.freeze_execution(c)
            pending_setup = False
        if not pending_setup:
            await self._seed(c, config)
        await active.actions.initialize_workspace("rsi")
        write_json(
            c.store.root / "coordination" / "started-config.json", config.public(), immutable=True
        )
        write_json(
            c.store.root / "coordination/collaboration.json",
            manifest(config.mode, config.collaboration_modules, config.research_prompt_variant),
            immutable=True,
        )
        if "intake" in c.state:
            c.state["intake"]["confirmation_message_id"] = confirmation_message_id
            c.save()
        c.start()
        active.engine.wake()

    async def freeze_execution(self, c: Coordinator) -> None:
        config = self.config(c)
        issues = validate_execution(config)
        if c.state.get("intake", {}).get("execution_required") and not (
            config.commands or config.verifier
        ):
            issues.append("尚未接入实际执行命令")
        if issues:
            raise ValueError("；".join(issues))
        active = self.active[c.store.run_id]
        active.config = config
        self._configure_agent_tools(active, config)
        active.execution.commands = {item.name: item for item in config.commands}
        active.execution.verifier = config.verifier
        active.execution.execution_env = config.execution_env
        active.execution.read_verification = self._verification_reader(c, config)
        active.execution.instructions = str(config.metadata.get("execution_instructions", ""))
        await self._seed(c, config)
        await active.actions.initialize_workspace("rsi")
        write_json(
            c.store.root / "coordination" / "execution-config.json", config.public(), immutable=True
        )
        c.state["execution_setup"] = "ready"
        c.save()
        c.store.event("execution_ready")

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
                from areal_rsi.execution.diagnostics import failure_summary

                return VerifiedResult(
                    **kwargs,
                    status="infra_error",
                    error=failure_summary(
                        c.store.root / "execution-outputs" / record.job_id,
                        record.error or record.state,
                    ),
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
        plan = build_plan(config.mode, config.collaboration_modules, config.research_prompt_variant)
        frozen = c.store.root / "coordination/collaboration.json"
        if frozen.exists() and json.loads(frozen.read_text()) != manifest(
            config.mode, config.collaboration_modules, config.research_prompt_variant
        ):
            raise ValueError("协作实现与启动时冻结的版本不同；请使用原版本恢复或新建研究")
        runtime = self.runtime_factory(config)
        if c.state["status"] == "planned" and config.model_settings_revision:
            actor = c.actor("interaction")
            session = (
                c.store.paths.agent("interaction")
                / "sessions"
                / (actor.session_id or "interaction")
            )
            await runtime.reconfigure_session(session, config.model_settings_revision)
        from areal_rsi.coordination.passk import stop_on_target

        c.state["mode"] = config.mode
        c.save()
        actions = plan.actions(c)
        await actions.initialize_workspace("rsi")
        register_sources(
            actions,
            enabled=config.backend == "standard" and config.metadata.get("allow_web_access", True),
        )
        agent_tools = AgentTools(
            actions,
            self.root,
            environment=lambda: self.config(c).execution_env,
            credential_names=(config.api_key_env,),
            instructions=config.metadata.get("agent_tool_instructions"),
            backend=config.backend,
            supervisor_only=plan.supervisor_only,
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
            actions,
            repository=self.repository,
            start=lambda message: self.start(c, message),
            freeze_execution=lambda: self.freeze_execution(c),
        )

        async def poll() -> bool:
            current = self.active[c.store.run_id].config
            if (
                current.finalizer
                and c.state["status"] == "paused"
                and c.state.get("budget_stop_reason") == "Run wall-time budget exhausted"
            ):
                # The engine pauses at a hard budget. A configured closeout must
                # still run without a user having to turn that pause into a stop.
                c.stop("探索时间已到，停止研究并自动验证和归档已有产物")
            limit = current.brief.limits.max_wall_seconds
            reserve = current.metadata.get("finalization_reserve_seconds", 0)
            started = c.state.get("started_at")
            if (
                current.finalizer
                and current.mode != "passk"
                and limit
                and reserve
                and current.metadata.get("time_limit_scope", "total") == "total"
                and started
                and c.state["status"] == "running"
            ):
                elapsed = datetime.now().timestamp() - datetime.fromisoformat(started).timestamp()
                if elapsed >= limit - reserve:
                    c.stop("已到探索时间上限，预留剩余时间自动复测最佳版本")
            if c.state["status"] == "stopped":
                execution.close(cancel=True)
                return False
            pending = await execution.poll()
            if current.mode == "passk":
                stop_on_target(c)
            return pending

        engine = plan.engine(
            c,
            runtime,
            lambda agent_id: ToolSet(
                actions.definitions(agent_id, include_hidden=True),
                actions.handler(agent_id),
                agent_tools.instructions(agent_id),
                actions.definitions(agent_id),
                actions.workspace(agent_id),
                config.execution_env,
            ),
            research_prompt=plan.research_prompt,
            rsi_prompt=plan.rsi_prompt,
            tick_hook=poll,
        )

        async def serve() -> None:
            try:
                await engine.serve()
                execution.close(cancel=True)
                current = self.active[c.store.run_id].config
                if (
                    current.finalizer
                    and c.state["status"] in {"stopped", "succeeded"}
                    and not c.state.get("user_stopped")
                ):
                    from areal_rsi.coordination.finalization import finalize

                    await finalize(c, current)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                c.store.event("worker_error", error=str(exc))
            finally:
                execution.close(cancel=c.state["status"] in {"stopped", "succeeded", "failed"})
                await agent_tools.close()
                await runtime.close()
                await engine.interaction_runtime.close()

        task = asyncio.create_task(serve(), name="research:" + c.store.run_id)
        active = ActiveResearch(c, config, actions, execution, agent_tools, engine, task)
        self.active[c.store.run_id] = active
        return active

    async def detail(
        self, run_id: str, after: int = 0, *, event_limit: int | None = None
    ) -> dict[str, Any]:
        c = self.coordinator(run_id)
        summary = c.summary()
        summary["finalization"] = c.state.get("finalization")
        run = c.store.run()
        actors = {actor["agent_id"]: actor for actor in summary["agents"]}
        evidence = encode(c.store.records("evidence", EvidenceRecord))
        evidence_by_iteration: dict[str, list[dict[str, Any]]] = {}
        for item in evidence:
            if item["iteration_id"] is not None:
                evidence_by_iteration.setdefault(item["iteration_id"], []).append(item)
        all_messages = c.store.records("messages", ResearchMessage)
        report_messages = {
            ref.uri: message
            for message in all_messages
            if message.recipient == "rsi"
            for ref in message.references
            if "/reports/" in ref.uri
        }
        reports_by_iteration: dict[str, list[dict[str, Any]]] = {}
        reports_by_message = {}
        delivered = set(c.state["delivered"])
        for report_record in sorted(
            c.store.records("reports", ResearchReport),
            key=lambda item: (item.created_at, item.report_id),
        ):
            report_view = encode(report_record)
            ref = c.store.paths.reference(
                c.store.root / "reports" / f"{report_record.report_id}.json"
            )
            message = report_messages.get(ref.uri)
            report_view["delivery_status"] = (
                "delivered"
                if message and message.message_id in delivered
                else "pending"
                if message
                else "archived"
            )
            reports_by_iteration.setdefault(report_record.iteration_id, []).append(report_view)
            if message:
                reports_by_message[message.message_id] = (report_record, message.sender)
        nodes, edges = [], []
        for actor in run.research_agents:
            for node in actor.iterations:
                assignment = c.store.public_record(node.assignment_ref)
                report = c.store.public_record(node.report_ref) if node.report_ref else None
                nodes.append(
                    {
                        **encode(node),
                        "assignment": assignment,
                        "report": report,
                        "reports": reports_by_iteration.get(node.iteration_id, []),
                        "evidence": evidence_by_iteration.get(node.iteration_id, []),
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
        archived_chat = set(c.state.get("archived_chat_message_ids", []))
        messages = [
            encode(m)
            for m in all_messages
            if (m.sender == "user" or m.recipient == "user") and m.message_id not in archived_chat
        ]
        messages.sort(key=lambda m: m["created_at"])
        jobs_path = c.store.root / "coordination" / "jobs.json"
        jobs = []
        contract = c.store.brief().verification
        contract_digest = c.store.contract_digest() if contract else None
        withdrawn: dict[str, bool] = {}
        if jobs_path.exists():
            from areal_rsi.execution.progress import read_progress

            intents = json.loads(jobs_path.read_text())
            for job_id, info in intents.items():
                path = c.store.root / "jobs" / job_id / "job.json"
                if not path.exists():
                    continue
                record = json.loads(path.read_text())
                baseline = bool(
                    info.get("verification")
                    and info.get("candidate_id")
                    and info["candidate_id"] == c.state.get("baseline_candidate_id")
                    and info.get("phase") == "search"
                )
                phase = info.get("phase")
                title = (
                    "起点评测"
                    if baseline
                    else {"search": "迭代验证", "final": "最终验证", "diagnostic": "诊断检查"}.get(
                        phase, "验证作业"
                    )
                    if info.get("verification")
                    else "研究执行"
                )
                job_actor = actors[info["agent_id"]]
                jobs.append(
                    {
                        "job_id": job_id,
                        "title": title,
                        "phase": phase,
                        "verification": bool(info.get("verification")),
                        "is_baseline": baseline,
                        "iteration_id": info.get("iteration_id"),
                        "candidate_id": info.get("candidate_id"),
                        "agent_id": info["agent_id"],
                        "agent_name": job_actor["direction"]
                        or {"rsi": "RSI Agent", "interaction": "Interaction Agent"}.get(
                            info["agent_id"], "Research Agent"
                        ),
                        "status": record["state"],
                        "created_at": record["created_at"],
                        "gpu_ids": record["assigned_gpu_ids"],
                        "started_at": record["started_at"],
                        "finished_at": record["finished_at"],
                        "exit_code": record["exit_code"],
                        "error": record["error"],
                        "simulated": c.state["simulated"],
                        "result": info.get("result"),
                        "published_candidate_id": info.get("published_candidate_id"),
                        "verification_job_id": info.get("verification_job_id"),
                        "source_job_id": info.get("source_job_id"),
                        "publication_error": info.get("publication_error"),
                        "progress": read_progress(Path(info["output"])),
                    }
                )
                result = info.get("result")
                if (
                    contract
                    and info.get("verification")
                    and not baseline
                    and result
                    and result.get("status") == "succeeded"
                    and result.get("passed") is True
                    and result.get("score") is not None
                    and result.get("phase") == "search"
                    and result.get("contract_digest") == contract_digest
                    and result.get("simulated") == c.state["simulated"]
                ):
                    candidate_id = result["candidate_id"]
                    if candidate_id not in withdrawn:
                        withdrawn[candidate_id] = c.store.is_withdrawn(candidate_id)
                    if withdrawn[candidate_id]:
                        continue
                    for public_node in nodes:
                        if public_node["iteration_id"] == info.get("iteration_id"):
                            previous = public_node["verification"]
                            if previous is None or (
                                result["score"] > previous["score"]
                                if contract.direction == "maximize"
                                else result["score"] < previous["score"]
                            ):
                                public_node["verification"] = result
        events = c.store.events(after, limit=event_limit)
        if c.state.get("mode") == "passk":
            # Older runs have the durable report/message, but no named report event.
            # Reuse its original cursor and timestamp; never rewrite archived events.
            for index, event in enumerate(events):
                message_id = event.data.get("message_id")
                report_message = (
                    reports_by_message.get(message_id) if isinstance(message_id, str) else None
                )
                if event.kind == "message" and report_message:
                    report_record, sender = report_message
                    events[index] = replace(
                        event,
                        kind="report_submitted",
                        agent_id=sender,
                        iteration_id=report_record.iteration_id,
                        data={**event.data, "report_id": report_record.report_id},
                    )
        return {
            **summary,
            "objective": c.store.brief().objective,
            "nodes": nodes,
            "edges": edges,
            "messages": messages,
            "agent_activity": c.state.get("agent_activity", {}),
            "execution_setup": c.state.get("execution_setup", "ready"),
            "passk_closeout": c.state.get("passk_closeout"),
            "model_settings": self.model_settings(c),
            "collaboration": self.collaboration_settings(c),
            "jobs": jobs,
            "evidence": evidence,
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

    async def delete(self, c: Coordinator) -> dict[str, Any]:
        async with self.run_lock(c):
            c = self.coordinator(c.store.run_id)
            if (
                c.state["status"] in {"running", "paused"}
                or (c.state.get("finalization") or {}).get("status") == "running"
            ):
                raise ValueError("研究仍在运行或暂停中，请先停止研究再删除")
            if self.worker_elsewhere(c):
                raise ValueError("研究由其他进程运行，请先停止该进程再删除")
            active = self.active.get(c.store.run_id)
            if active:
                active.task.cancel()
                await asyncio.gather(active.task, return_exceptions=True)
                active.execution.close(cancel=True)
                await active.tools.close()
                self.active.pop(c.store.run_id, None)
            # Hold the same lock as CLI workers until the directory leaves the
            # run index. Reclaim files off the event loop after the atomic move.
            c.store.paths.execution_lock.parent.mkdir(parents=True, exist_ok=True)
            with c.store.paths.execution_lock.open("a+") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("研究由其他进程运行，请先停止该进程再删除") from None
                manager = JobManager(c.store.root, auto_monitor=False, scheduling_enabled=False)
                manager.close(preserve_running=False)
                destination = self.root / ".deleting" / new_id("research")
                destination.parent.mkdir(parents=True, exist_ok=True)
                c.store.paths.legacy_state.unlink(missing_ok=True)
                c.store.root.rename(destination)
            await asyncio.to_thread(shutil.rmtree, destination)
        return {"run_id": c.store.run_id, "status": "deleted"}

    async def call(self, action: str, params: dict[str, Any]) -> Any:
        if action == "recover":
            for run in FileRunStore(self.root).list(ExperimentRun):
                if run.plan.workflow != "research":
                    continue
                try:
                    c = self.coordinator(run.run_id)
                    config = self.config(c)
                except (OSError, ValueError, KeyError):
                    continue
                finalization = c.state.get("finalization") or {}
                if (
                    config.metadata.get("auto_resume")
                    and c.state.get("started_at")
                    and not c.state.get("user_stopped")
                    and (
                        c.state["status"] == "running"
                        or (
                            config.finalizer
                            and c.state["status"] in {"stopped", "succeeded"}
                            and finalization.get("status") not in {"completed", "failed"}
                        )
                    )
                    and not self.worker_elsewhere(c)
                ):
                    try:
                        await self.attach(c)
                    except Exception as exc:
                        c.store.event("recovery_failed", error=type(exc).__name__)
            return {}
        if action == "bootstrap":
            config = self.default_config or ResearchConfig(TaskBrief("新的研究", ""))
            from areal_rsi.benchmarks.scale import catalog as scale_catalog

            return {
                "service": "areal-rsi",
                "api_version": 1,
                "capabilities": ["configured-runs", "automatic-finalization"],
                "data_root": str(self.root),
                "default_model_settings": {
                    **config.model_settings(),
                    "has_credentials": bool(config.api_key()),
                },
                "collaboration_catalog": catalog(),
                "default_collaboration": {
                    "mode": config.mode,
                    "modules": list(config.collaboration_modules),
                },
                "allow_simulation": self.allow_simulation,
                "default_simulated": self.allow_simulation and self.default_config is None,
                "has_config": self.default_config is not None,
                "models": [
                    {"id": model, "efforts": list(efforts)}
                    for model, efforts in MODEL_EFFORTS.items()
                ],
                "benchmark_templates": scale_catalog(self.repository),
            }
        if action == "list":
            runs = FileRunStore(self.root).list(ExperimentRun)
            # Workbench organization is independent of frozen experiment records.
            group_file = self.root / "workbench-groups.json"
            groups = json.loads(group_file.read_text())["groups"] if group_file.exists() else []
            run_groups = {
                run_id: {key: group[key] for key in ("id", "name", "created_at")}
                for group in groups
                for run_id in group["run_ids"]
            }

            def created_at(run: ExperimentRun) -> str:
                c = self.coordinator(run.run_id)
                if c.state.get("created_at"):
                    return str(c.state["created_at"])
                # Older runs predate the timestamp field. Their first immutable
                # event records creation, unlike file modification time.
                event = c.store.root / "events" / "000000000001.json"
                return (
                    str(json.loads(event.read_text())["created_at"])
                    if event.exists()
                    else (run.started_at or "")
                )

            return {
                "runs": sorted(
                    [
                        {
                            "run_id": r.run_id,
                            "objective": r.plan.goal,
                            "status": self.coordinator(r.run_id).state["status"],
                            "simulated": self.coordinator(r.run_id).state["simulated"],
                            "created_at": created_at(r),
                            "group": run_groups.get(r.run_id),
                        }
                        for r in runs
                        if r.plan.workflow == "research"
                        and (
                            self.allow_simulation
                            or not self.coordinator(r.run_id).state["simulated"]
                        )
                    ],
                    key=lambda r: (r["created_at"], r["run_id"]),
                    reverse=True,
                )
            }
        if action == "create":
            return await self.create(params)
        c = self.coordinator(params["run_id"])
        if c.state["simulated"] and not self.allow_simulation:
            raise ValueError("该记录属于开发测试，请在独立的测试工作台查看")
        if action == "detail":
            return await self.detail(
                c.store.run_id, int(params.get("after", 0)), event_limit=params.get("event_limit")
            )
        if action == "delete":
            return await self.delete(c)
        if action != "export" and self.worker_elsewhere(c):
            raise ValueError(
                "This run is controlled by another live process. Close that worker "
                "with Ctrl+C, then resume it here; viewing and export remain available."
            )
        if action == "chat":
            if self.config(c).mode == "passk" and c.state.get("started_at"):
                raise ValueError(
                    "passk researchers cannot receive intervention after launch; "
                    "create a new run to change the task"
                )
            async with self.run_lock(c):
                c = self.coordinator(c.store.run_id)
                message_id = c.user_message(
                    params["text"],
                    key=params.get("message_id") or new_id("user"),
                    reply_to=params.get("reply_to"),
                    recipient=params.get("recipient"),
                )
                active = await self.attach(c)
                active.engine.wake()
            return {"run_id": c.store.run_id, "message_id": message_id, "status": c.state["status"]}
        elif action == "model-settings":
            await self.set_model_settings(c, {k: v for k, v in params.items() if k != "run_id"})
        elif action == "collaboration":
            await self.set_collaboration(c, {k: v for k, v in params.items() if k != "run_id"})
        elif action == "start":
            await self.start(c)
        elif action == "pause":
            async with self.run_lock(c):
                c = self.coordinator(c.store.run_id)
                c.pause()
                if c.store.run_id in self.active:
                    await self.active[c.store.run_id].engine.tick()
        elif action == "resume":
            async with self.run_lock(c):
                c = self.coordinator(c.store.run_id)
                if (
                    self.config(c).mode == "passk"
                    and c.state["status"] == "stopped"
                    and not c.state.get("finalization")
                ):
                    raise ValueError(
                        "This passk experiment has ended; create a new run for another budget"
                    )
                c.state.pop("user_stopped", None)
                c.save()
                if self.config(c).finalizer and c.state.get("finalization"):
                    if c.state["finalization"]["status"] == "completed":
                        raise ValueError("最终复测已完成；继续探索请新建研究")
                    active = await self.attach(c)
                    return await self.detail(c.store.run_id)
                if c.state["status"] == "paused":
                    c.resume()
                elif c.state["status"] == "stopped":
                    c.reopen()
                elif c.state["status"] not in {"planned", "running"}:
                    raise ValueError("This run cannot be resumed")
                active = await self.attach(c)
                active.engine.wake()
        elif action == "retry":
            async with self.run_lock(c):
                c = self.coordinator(c.store.run_id)
                c.retry_agent(params["agent_id"])
                active = await self.attach(c)
                active.engine.wake()
        elif action == "stop":
            async with self.run_lock(c):
                c = self.coordinator(c.store.run_id)
                c.state["user_stopped"] = True
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
        for active in self.active.values():
            active.execution.close(cancel=False)
            await active.tools.close()
        self.active.clear()
