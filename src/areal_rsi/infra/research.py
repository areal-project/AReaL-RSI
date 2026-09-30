"""Research system lifecycle and control, independent of any UI or Task backend."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import shutil
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from copy import copy, deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, overload

from areal_rsi.api.control import ArtifactDownload, JsonAction, validate_record, validate_request
from areal_rsi.api.interfaces import ResearchAgentRuntime
from areal_rsi.api.records import AgentState, ResearchRun
from areal_rsi.api.research import (
    CandidateSnapshot,
    DevelopmentSetup,
    EvidenceRecord,
    GoalContext,
    GoalRevision,
    IntakeState,
    ResearchMessage,
    ResearchPreparation,
    TaskBrief,
    VerifiedResult,
)
from areal_rsi.infra import development
from areal_rsi.infra.agents.communication import tool_instructions
from areal_rsi.infra.coordination.config import (
    MODEL_EFFORTS,
    MODEL_FIELDS,
    ResearchConfig,
    archived_config,
    config_from_dict,
    git_head_commit,
    git_repository,
    validate_model_settings,
)
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.coordination.engine import ResearchEngine, ToolSet
from areal_rsi.infra.coordination.evaluation.jobs import ResearchExecution
from areal_rsi.infra.coordination.evaluation.verification import read_trusted_result
from areal_rsi.infra.coordination.intake import (
    ResearchIntake,
    missing_requirements,
    validate_execution,
)
from areal_rsi.infra.coordination.policies.collaboration import (
    DEFAULT_MODULES,
    MODES,
    build_plan,
    catalog,
    manifest,
    selected_modules,
)
from areal_rsi.infra.coordination.policies.passk import PassKEngine
from areal_rsi.infra.coordination.presets import example_config
from areal_rsi.infra.coordination.store import ResearchStore
from areal_rsi.infra.coordination.tools.actions import ResearchActions
from areal_rsi.infra.coordination.tools.web import register_sources
from areal_rsi.infra.coordination.views import agent_trace, research_detail, research_list
from areal_rsi.infra.execution import JobManager, JobRecord
from areal_rsi.infra.runtime.research_runtime import ResearchRuntime
from areal_rsi.infra.storage.files import (
    decode,
    encode,
    new_id,
    now,
    safe_relative,
    write_bytes,
    write_json,
)
from areal_rsi.infra.storage.runs import FileRunStore


@dataclass
class ActiveResearch:
    coordinator: Coordinator
    config: ResearchConfig
    actions: ResearchActions
    execution: ResearchExecution
    engine: ResearchEngine
    task: asyncio.Task[None]


class ResearchSystem:
    def __init__(
        self,
        root: Path,
        *,
        repository: Path | None = None,
        config: ResearchConfig | None = None,
        runtime_factory: Callable[[ResearchConfig], ResearchAgentRuntime] | None = None,
        allow_simulation: bool = False,
    ):
        self.root = root.resolve()
        self.repository = repository or Path(__file__).resolve().parents[3]
        self.default_config = config
        self.runtime_factory = runtime_factory or ResearchRuntime
        self.allow_simulation = allow_simulation
        self.active: dict[str, ActiveResearch] = {}
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._chat_followup_runs: set[str] = set()
        self._chat_wakeups: set[asyncio.Task[None]] = set()
        self._closing = False

    @asynccontextmanager
    async def control(self, run_id: str, action: str) -> AsyncIterator[Coordinator]:
        async with self._run_locks.setdefault(run_id, asyncio.Lock()):
            c = self._visible_coordinator(run_id)
            try:
                c.acquire()
            except BlockingIOError:
                raise ValueError("This run is controlled by another live process") from None
            try:
                if action != "stop" and action != "delete":
                    config = self.config(c)
                    selected_modules(config.mode, config.collaboration_modules)
                yield c
            finally:
                c.release()

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
    def _configure_tools(active: ActiveResearch, config: ResearchConfig) -> None:
        allow_web = config.metadata.get("allow_web_access", True)
        register_sources(active.actions, enabled=config.backend != "codex" and allow_web)
        for runtime in (active.engine.runtime, active.engine.interaction_runtime):
            if isinstance(runtime, ResearchRuntime):
                runtime.allow_web_access = allow_web

    def coordinator(self, run_id: str) -> Coordinator:
        if run_id in self.active:
            return self.active[run_id].coordinator
        return Coordinator(ResearchStore(self.root, run_id))

    def config(self, c: Coordinator) -> ResearchConfig:
        config = archived_config(
            json.loads((c.store.root / "coordination" / "config.json").read_text())
        )
        config.brief = c.store.brief()
        if not config.model_settings_pinned:
            # Legacy archives stored service defaults. Prefer the actual recorded
            # connection for a started run, without rewriting its history.
            recorded = sorted(
                c.store.root.glob("agents/*/sessions/*/configuration.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if c.state.started_at and recorded:
                settings = json.loads(recorded[0].read_text())
                for key in MODEL_FIELDS:
                    setattr(config, key, settings.get(key, getattr(config, key)))
                config.model_settings_pinned = True
            else:
                config.pin_model_settings()
        return config

    def model_settings(self, c: Coordinator) -> dict[str, Any]:
        config = self.config(c)
        return {
            **config.model_settings(),
            "has_credentials": bool(config.api_key()),
            "editable": config.mode in MODES
            and c.state.status == "planned"
            and not c.state.started_at,
        }

    def collaboration_settings(self, c: Coordinator) -> dict[str, Any]:
        config = self.config(c)
        path = c.store.root / "coordination/collaboration.json"
        frozen = json.loads(path.read_text()) if path.exists() else None
        return {
            "mode": config.mode,
            "max_agents": config.brief.limits.max_agents,
            "rsi_model_enabled": bool(config.collaboration_modules),
            "modules": list(config.collaboration_modules),
            "label": MODES.get(config.mode, {"label": "旧版协作（仅供查看）"})["label"],
            "supported": config.mode in MODES,
            "editable": config.mode in MODES
            and c.state.status == "planned"
            and not c.state.started_at
            and frozen is None,
            # Do not backfill current versions into historical experiments.
            "manifest": frozen,
        }

    @staticmethod
    def select_collaboration(config: ResearchConfig, values: dict[str, Any]) -> None:
        if (not isinstance(values, dict) or not {"mode", "modules"} <= set(values)
            or set(values) - {"mode", "modules", "max_agents"}):
            raise ValueError("协作设置需要 mode 和 modules")
        mode, modules = values["mode"], values["modules"]
        if not isinstance(mode, str):
            raise ValueError("协作方式须为名称")
        modules = selected_modules(mode, modules)
        config.mode, config.collaboration_modules = mode, modules
        if "max_agents" in values:
            validate_request("collaboration", {"run_id": "settings", **values})
            config.brief.limits.max_agents = values["max_agents"]
            config.brief.limits.__post_init__()
        config.__post_init__()

    async def set_collaboration(self, run_id: str, values: dict[str, Any]) -> None:
        async with self.control(run_id, "collaboration") as c:
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
                await active.engine.close()
                self.active.pop(c.store.run_id, None)
                config = self.config(c)
                config.brief = c.store.brief()
                self.select_collaboration(config, values)
            # Budget changes in the preset must also reach the displayed brief.
            run = c.store.run()
            run.brief_ref = c.store.put("briefs", new_id("collaboration"), config.brief)
            run.plan.max_iterations = config.brief.limits.max_nodes
            run.plan.goal = config.brief.objective
            run.plan.research_agent_count = config.brief.limits.max_agents
            c.store.save_run(run)
            previous = self.config(c)
            if (previous.mode, previous.collaboration_modules) != (
                config.mode,
                config.collaboration_modules,
            ):
                # The native session freezes its collaboration and tool configuration.
                self._reset_agent_session(c, "interaction")
            write_json(c.store.root / "coordination/config.json", config.public())
            c.state.mode = config.mode
            c.state.rsi_model_enabled = not build_plan(
                config.mode, config.collaboration_modules
            ).supervisor_only
            c.save()
            c.store.event(
                "collaboration_updated",
                mode=config.mode,
                modules=list(config.collaboration_modules),
            )
            if active:
                await self.attach(c)

    async def set_model_settings(self, run_id: str, values: dict[str, Any]) -> None:
        async with self.control(run_id, "model-settings") as c:
            settings, key = self.parse_model_settings({"backend": self.config(c).backend, **values})
            if not self.model_settings(c)["editable"]:
                raise ValueError("研究已经开始，模型设置已锁定；暂停或停止后也不能修改")
            active = self.active.get(c.store.run_id)
            if active:
                active.task.cancel()
                await asyncio.gather(active.task, return_exceptions=True)
                # Cancellation before serve() starts never reaches its finally.
                active.execution.close(cancel=False)
                await active.engine.close()
                self.active.pop(c.store.run_id, None)
            # An Interaction tool may have updated other config fields before
            # cancellation. Reload them instead of replacing the whole config.
            config = self.config(c)
            if any(settings[field] != getattr(config, field) for field in MODEL_FIELDS):
                self._reset_agent_session(c, "interaction")
            for field, value in settings.items():
                setattr(config, field, value)
            config.pin_model_settings()
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
    def _reset_agent_session(c: Coordinator, agent_id: str) -> None:
        """Keep intake dialogue when a frozen runtime or tool schema changes."""
        actor = c.actor(agent_id)
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
            c.store.paths.agent(agent_id) / "sessions" / actor.session_id / "backend-handoff.json",
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
        settings = validate_model_settings(values)
        return settings, "" if settings["auth_mode"] == "codex_login" else key.strip()

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
        validate_request("create", params)
        if params.get("use_workspace") and ("config" in params or "development" in params):
            raise ValueError("使用当前工作区时不能同时提交其他执行配置")
        if "development" in params and "config" in params:
            raise ValueError("研发配置不能与完整 config 同时提交")
        if "config" in params:
            config_from_dict(params["config"])
        run_id = params.get("run_id") or new_id("research")
        creation_digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        directory = self.root / "runs" / run_id
        lock = self.root / "runtime" / "creation-locks" / (run_id + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("该研究正在创建，请稍后重试同一请求") from None
            pending = directory / ".creating"
            if pending.exists():
                if pending.read_text() != creation_digest:
                    raise ValueError("该 run_id 已用于不同的创建请求")
                # No worker may attach until publication. Rebuild only this
                # uncommitted creation; shared content remains content-addressed.
                await asyncio.to_thread(shutil.rmtree, directory)
            if (directory / "run.json").exists():
                previous = self.coordinator(run_id)
                if previous.state.creation_digest != creation_digest:
                    raise ValueError("该 run_id 已用于不同的创建请求")
            else:
                await self._create(params, run_id, creation_digest)
            if ("development" not in params
                and (message := str(params.get("message", "")).strip())):
                previous = self.coordinator(run_id)
                if previous.state.status == "planned":
                    await self.send_message(
                        run_id,
                        message,
                        message_id=params.get("message_id") or "initial-" + creation_digest,
                    )
            return await self.detail(run_id)

    async def _create(self, params: dict[str, Any], run_id: str, creation_digest: str) -> None:
        supplied = params.get("config")
        conversation = bool(
            params.get(
                "conversation",
                supplied is None and self.default_config is None and not self.allow_simulation,
            )
        )
        simulated = False if conversation else bool(params.get("simulated", self.allow_simulation))
        if simulated and not self.allow_simulation:
            raise ValueError("Restart with simulation enabled to create simulated runs")
        if supplied is not None:
            config = config_from_dict(supplied)
            conversation = False
        elif self.default_config is None:
            config = (
                ResearchConfig(TaskBrief("新的研究", ""), collaboration_modules=DEFAULT_MODULES)
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
        setup = None
        if params.get("use_workspace"):
            objective = str(params.get("message", "")).strip()
            if not objective:
                raise ValueError("请描述你想完成的任务")
            selected = development.default_profile(self.root, config)
            if selected is None:
                raise ValueError("当前工作区尚未配置，请先说明要处理的项目")
            count = params.get("collaboration", {}).get("max_agents", 1)
            setup = replace(selected.defaults,
                            acceptance=objective,
                            parallelism=count,
                            wall_seconds=params.get("wall_seconds"))
        if "development" in params:
            validate_record(DevelopmentSetup, params["development"])
            setup = decode(DevelopmentSetup, params["development"])
            development.profile(self.root, setup.profile_id)
        if setup is not None:
            conversation = bool(params.get("use_workspace"))
            objective = str(params.get("message", "")).strip()
            if not objective:
                raise ValueError("请填写研发目标")
            config.brief.objective = objective
            config.brief.instructions = objective
            if setup.acceptance != objective:
                config.brief.instructions += "\n验收要求：\n" + setup.acceptance
            config.brief.limits.max_agents = setup.parallelism
            config.brief.limits.max_wall_seconds = setup.wall_seconds
            config.brief.limits.__post_init__()
            config.metadata["auto_resume"] = True
        if config.metadata.get("simulation_only") and not simulated:
            raise ValueError(
                "This template is for research simulation; configure real runners first"
            )
        if "collaboration" in params:
            self.select_collaboration(config, params["collaboration"])
        config.pin_model_settings()
        selected_modules(config.mode, config.collaboration_modules)
        if "model_settings" in params:
            settings, key = self.parse_model_settings(params["model_settings"])
            for field, value in settings.items():
                setattr(config, field, value)
        else:
            key = ""
        config.pin_model_settings()
        config.connection_settings()
        key = key or config.api_key()
        if not conversation and config.seed_dir and not Path(config.seed_dir).is_dir():
            raise FileNotFoundError(f"Seed directory does not exist: {config.seed_dir}")
        if config.workspace_dir:
            config.workspace_dir = str(git_repository(config.workspace_dir))
            git_head_commit(config.workspace_dir)
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
        directory = self.root / "runs" / run_id
        directory.mkdir(parents=True, exist_ok=True)
        write_bytes(directory / ".creating", creation_digest.encode())
        c = Coordinator.create(self.root, config.brief, simulated=simulated, run_id=run_id)
        c.state.creation_digest = creation_digest
        c.state.mode = config.mode
        c.state.workspace_dir = config.workspace_dir
        if setup is not None:
            c.state.preparation = ResearchPreparation(setup)
        c.save()
        if key:
            self.save_credentials(c, config, key)
        write_json(c.store.root / "coordination" / "config.json", config.public(), immutable=True)
        if conversation:
            c.state.intake = IntakeState(status="discussing")
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
                    "description": "Synthetic development trajectory for research testing",
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
        if (setup is None or conversation) and (message := str(params.get("message", "")).strip()):
            c.user_message(message, key=params.get("message_id") or "initial-" + creation_digest)
        (directory / ".creating").unlink()

    async def _seed(self, c: Coordinator, config: ResearchConfig) -> None:
        if config.seed_dir and not config.workspace_dir and not c.state.seed_candidate_id:
            candidate = await c.store.snapshot(
                Path(config.seed_dir), agent_id="operator", description="研究起点"
            )
            c.state.seed_candidate_id = candidate.candidate_id
            if config.metadata.get("evaluate_seed_as_baseline"):
                c.state.baseline_candidate_id = candidate.candidate_id
            c.save()

    async def start(self, run_id: str, confirmation_message_id: str | None = None) -> None:
        async with self.control(run_id, "start") as c:
            await self._start(c, confirmation_message_id)

    async def prepare(self, run_id: str, *, wait: bool = False) -> dict[str, Any]:
        async with self.control(run_id, "prepare") as c:
            active = self.active.get(run_id)
            manager = active.execution.manager if active and not active.execution.closed else None
            config = development.refresh(c, self.config(c), manager)
            if c.state.preparation is not None and c.state.intake.status is not None:
                if missing := missing_requirements(config.brief):
                    raise ValueError("准备前还需要明确：" + "、".join(missing))
                c.state.preparation.setup = replace(
                    c.state.preparation.setup,
                    acceptance="\n".join(config.brief.acceptance),
                    parallelism=config.brief.limits.max_agents,
                    wall_seconds=config.brief.limits.max_wall_seconds,
                )
                c.save()
            development.prepare(c, config, manager)
        while (wait and c.state.preparation
               and c.state.preparation.status in {"running", "cancelling"}):
            await asyncio.sleep(.1)
            async with self.control(run_id, "prepare") as c:
                if c.state.status != "planned":
                    raise ValueError("当前任务已结束或开始执行")
                active = self.active.get(run_id)
                manager = (active.execution.manager
                           if active and not active.execution.closed else None)
                development.refresh(c, self.config(c), manager)
        return await self.detail(run_id)

    async def _start(self, c: Coordinator, confirmation_message_id: str | None) -> None:
        if c.state.status != "planned":
            raise ValueError("研究已经开始")
        if c.state.intake.status is not None and c.state.intake.status != "ready":
            raise ValueError("请先与 Interaction 确认研究目标和执行条件")
        config = self.config(c)
        if c.state.intake.status is not None and (missing := missing_requirements(c.store.brief())):
            raise ValueError("启动前还需要明确：" + "、".join(missing))
        if c.state.preparation is not None:
            active = self.active.get(c.store.run_id)
            manager = active.execution.manager if active and not active.execution.closed else None
            config = development.refresh(c, config, manager)
            prepared = c.state.preparation
            if prepared.status != "ready" or prepared.config_digest != development.digest(config):
                raise ValueError("执行配置尚未通过预检，请先预检后再启动")
        selected_modules(config.mode, config.collaboration_modules)
        config.brief = c.store.brief()
        config.__post_init__()
        if config.workspace_dir:
            config.workspace_dir = str(git_repository(config.workspace_dir))
        config.validate_clock()
        config.pin_model_settings()
        config.connection_settings()
        write_json(c.store.root / "coordination" / "config.json", config.public())
        pending_setup = c.state.execution_setup == "pending"
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
        if c.state.intake.status is not None and not pending_setup:
            issues = validate_execution(config)
            if issues:
                raise ValueError("；".join(issues))
        active = await self.attach(c)
        # Configuration may have changed over several Interaction turns. Bind its
        # final revision before RSI can submit any jobs.
        active.config = config
        self._configure_tools(active, config)
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
            manifest(config.mode, config.collaboration_modules),
            immutable=True,
        )
        if c.state.intake.status is not None:
            c.state.intake.confirmation_message_id = confirmation_message_id
        c.state.workspace_dir = config.workspace_dir
        c.state.workspace_base_commit = (
            git_head_commit(config.workspace_dir) if config.workspace_dir else None
        )
        c.save()
        if config.workspace_dir and c.state.intake.status is not None:
            self._reset_agent_session(c, "interaction")
        c.start()
        active.engine.wake()

    async def freeze_execution(self, c: Coordinator) -> None:
        config = self.config(c)
        issues = validate_execution(config)
        if c.state.intake.execution_required and not (config.commands or config.verifier):
            issues.append("尚未接入实际执行命令")
        if issues:
            raise ValueError("；".join(issues))
        active = self.active[c.store.run_id]
        active.config = config
        self._configure_tools(active, config)
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
        c.state.execution_setup = "ready"
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

    async def attach(self, c: Coordinator, *, chat_only: bool = False) -> ActiveResearch:
        existing = self.active.get(c.store.run_id)
        if existing and not existing.task.done():
            return existing
        if existing:
            existing.execution.close()
            await existing.engine.close()
        if chat_only and c.state.status not in {"stopped", "succeeded", "completed", "failed"}:
            raise ValueError("Only a concluded research run can use chat-only execution")
        c.acquire()
        runtime: ResearchAgentRuntime | None = None
        execution: ResearchExecution | None = None
        engine: ResearchEngine | None = None
        attached = False
        try:
            config = self.config(c)
            if c.state.started_at:
                config.validate_clock()
            plan = build_plan(config.mode, config.collaboration_modules)
            frozen = c.store.root / "coordination/collaboration.json"
            if frozen.exists() and json.loads(frozen.read_text()) != manifest(
                config.mode, config.collaboration_modules
            ):
                raise ValueError("协作实现与启动时冻结的版本不同；请使用原版本恢复或新建研究")
            runtime = self.runtime_factory(config)
            if c.state.status == "planned" and config.model_settings_revision:
                actor = c.actor("interaction")
                session = (
                    c.store.paths.agent("interaction")
                    / "sessions"
                    / (actor.session_id or "interaction")
                )
                await runtime.reconfigure_session(session, config.model_settings_revision)
            from areal_rsi.infra.coordination.policies.passk import stop_on_target

            c.state.mode = config.mode
            c.state.rsi_model_enabled = not plan.supervisor_only
            c.save()
            actions = plan.actions(c)
            await actions.initialize_workspace("rsi")
            register_sources(
                actions,
                enabled=config.backend != "codex" and config.metadata.get("allow_web_access", True),
            )
            execution = ResearchExecution(
                actions,
                commands={item.name: item for item in config.commands},
                verifier=config.verifier,
                read_verification=self._verification_reader(c, config),
                simulated=c.state.simulated,
                mock_duration=0.2,
                execution_env=config.execution_env,
            )
            execution.instructions = str(config.metadata.get("execution_instructions", ""))
            ResearchIntake(
                actions,
                repository=self.repository,
                start=lambda message: self.start(c.store.run_id, message),
                freeze_execution=lambda: self.freeze_execution(c),
                prepare_workspace=lambda: self.prepare(c.store.run_id, wait=True),
            )

            async def poll() -> bool:
                current = self.active[c.store.run_id].config
                if (
                    (current.finalizer or current.mode == "passk")
                    and c.state.status == "paused"
                    and c.state.budget_stop_reason == "Run wall-time budget exhausted"
                ):
                    # The engine pauses at a hard budget. A configured closeout must
                    # still run without a user having to turn that pause into a stop.
                    c.stop("探索时间已到，停止研究并自动验证和归档已有产物")
                if c.state.status in {"stopped", "succeeded", "completed", "failed"}:
                    return False
                pending = await execution.poll()
                if current.mode == "passk":
                    stop_on_target(c)
                return pending

            engine = PassKEngine(
                c,
                runtime,
                lambda agent_id: ToolSet(
                    actions.definitions(agent_id, include_hidden=True),
                    actions.handler(agent_id),
                    tool_instructions(
                        str(actions.workspace(agent_id).resolve()),
                        backend=config.backend,
                        supervisor_only=agent_id == "rsi" and plan.supervisor_only,
                        extra=self.active[c.store.run_id].config.metadata.get(
                            "agent_tool_instructions"
                        ),
                    ),
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
                    async with AsyncExitStack() as sessions:
                        sessions.push_async_callback(runtime.close)
                        if engine.interaction_runtime is not runtime:
                            sessions.push_async_callback(engine.interaction_runtime.close)
                        await engine.serve()
                    # Native tool handlers are drained before completion takes over.
                    current = self.active[c.store.run_id].config
                    if (
                        not chat_only
                        and current.needs_finalization
                        and c.state.status in {"stopped", "succeeded"}
                        and not c.state.user_stopped
                    ):
                        await self._complete_result(c, current, execution)
                    else:
                        await execution.cancel_pending()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    c.store.event("worker_error", error=str(exc))
                finally:
                    try:
                        if c.state.user_stopped:
                            await execution.cancel_pending()
                    finally:
                        try:
                            execution.close()
                        finally:
                            await engine.close()

            engine._acquire()
            task = asyncio.create_task(serve(), name="research:" + c.store.run_id)
            active = ActiveResearch(c, config, actions, execution, engine, task)
            self.active[c.store.run_id] = active
            attached = True
            return active
        finally:
            try:
                if not attached:
                    async with AsyncExitStack() as cleanup:
                        if engine is not None:
                            cleanup.push_async_callback(engine.close)
                        if execution is not None:
                            cleanup.callback(execution.close)
                        if runtime is not None:
                            cleanup.push_async_callback(runtime.close)
                        if engine is not None and engine.interaction_runtime is not runtime:
                            cleanup.push_async_callback(engine.interaction_runtime.close)
            finally:
                c.release()

    async def _complete_result(
        self,
        c: Coordinator,
        config: ResearchConfig,
        execution: ResearchExecution,
    ) -> None:
        completion = c.begin_completion()
        if completion.status == "completed":
            return
        assert completion.exploration_finished_at is not None and config.verifier is not None
        remaining = None
        limit = config.brief.limits.max_wall_seconds
        if limit is not None and config.metadata.get("time_limit_scope", "total") == "total":
            started = c.state.started_at
            if started:
                remaining = max(
                    0,
                    limit
                    - (datetime.now().timestamp() - datetime.fromisoformat(started).timestamp()),
                )
        cutoff = (
            completion.exploration_finished_at
            if (config.metadata.get("time_limit_scope") == "exploration")
            else None
        )
        try:
            async with asyncio.timeout(remaining):
                if not c.goal_verification_current:
                    raise ValueError("新目标尚未确认现有验收器的覆盖范围，不能自动宣布完成")
                await execution.cancel_pending(exploration_only=True)
                if config.finalizer:
                    await c.store.export_selection(
                        c.execution_cutoffs(execution.jobs, completion.exploration_finished_at)
                    )
                    prepared = await execution.execute_completion(
                        config.finalizer, "prepare", cutoff=cutoff
                    )
                    candidate = await c.store.completion_candidate(
                        Path(prepared.output) / "candidate"
                    )
                    c.select_candidate(candidate.candidate_id)
                    await execution.execute_completion(
                        config.verifier, "search", candidate=candidate, cutoff=cutoff
                    )
                else:
                    selected = c.selected_candidate_id
                    if selected is None:
                        best = c.store.run().best_result_ref
                        if best is None:
                            raise ValueError("没有通过正式验证的候选，无法进行最终确认")
                        selected = decode(VerifiedResult, c.store.public_record(best)).candidate_id
                    c.select_candidate(selected)
                    candidate = c.store.get("candidates", selected, CandidateSnapshot)
                checked = await execution.execute_completion(
                    config.verifier, "final", candidate=candidate, cutoff=cutoff
                )
                assert checked.result is not None and checked.result_ref is not None
                c.complete_research(checked.result, checked.result_ref)
        except asyncio.CancelledError:
            c.interrupt_completion()
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                # Durable jobs retain any unconfirmed resource claims.
                with suppress(TimeoutError, RuntimeError):
                    await execution.cancel_pending()
                exc = TimeoutError("研究已到总时间上限；已验证的最佳版本仍保留")
            c.interrupt_completion(str(exc)[:500])

    async def trace(
        self, run_id: str, agent_id: str, before: int | None = None, limit: int = 50,
    ) -> dict[str, Any]:
        validate_request("trace", dict(run_id=run_id, agent_id=agent_id,
                                       before=before, limit=limit))
        result: dict[str, Any] = encode(await asyncio.to_thread(
            agent_trace, self._visible_coordinator(run_id), agent_id, before=before, limit=limit
        ))
        return result

    async def detail(
        self,
        run_id: str,
        after: int = 0,
        *,
        event_limit: int | None = None,
        goal_before: int | None = None,
        goal_limit: int = 20,
    ) -> dict[str, Any]:
        validate_request(
            "detail",
            {
                "run_id": run_id,
                "after": after,
                "event_limit": event_limit,
                "goal_before": goal_before,
                "goal_limit": goal_limit,
            },
        )
        c = self._visible_coordinator(run_id)
        if c.state.preparation is not None and c.state.status == "planned":
            try:
                c.acquire()
            except BlockingIOError:
                pass  # Another controller owns the transition; viewing stays read-only.
            else:
                try:
                    active = self.active.get(run_id)
                    manager = (active.execution.manager
                               if active and not active.execution.closed else None)
                    development.refresh(c, self.config(c), manager)
                finally:
                    c.release()
        active = self.active.get(run_id)
        # History reads can be slow on shared storage. Keep them off the engine's
        # event loop, with a private state snapshot so concurrent turns cannot
        # mutate dictionaries while the projection iterates them.
        snapshot = copy(c)
        snapshot.state = deepcopy(c.state)
        return await asyncio.to_thread(
            research_detail, snapshot,
            after=after,
            event_limit=event_limit,
            goal_before=goal_before,
            goal_limit=goal_limit,
            model_settings=self.model_settings(c),
            collaboration=self.collaboration_settings(c),
            worker_alive=bool(active and not active.task.done()) or self.worker_elsewhere(c),
            active_agents=active.engine.active_agents if active else (),
        )

    async def delete(self, run_id: str) -> dict[str, Any]:
        async with self.control(run_id, "delete") as c:
            if c.state.status in {"running", "paused"} or (
                c.completion is not None and c.completion.status == "running"
            ):
                raise ValueError("研究仍在运行或暂停中，请先停止研究再删除")
            active = self.active.get(c.store.run_id)
            if active:
                active.task.cancel()
                await asyncio.gather(active.task, return_exceptions=True)
                active.execution.close(cancel=True)
                await active.engine.close()
                self.active.pop(c.store.run_id, None)
            manager = JobManager(c.store.root, auto_monitor=False, scheduling_enabled=False)
            await asyncio.to_thread(manager.close, preserve_running=False)
            if any(not record.resources_released for record in manager.records()):
                raise ValueError("作业资源释放尚未确认，暂时不能删除研究")
            destination = self.root / ".deleting" / new_id("research")
            destination.parent.mkdir(parents=True, exist_ok=True)
            c.store.root.rename(destination)
            await asyncio.to_thread(shutil.rmtree, destination)
        return {"run_id": c.store.run_id, "status": "deleted"}

    async def send_message(
        self,
        run_id: str,
        text: str,
        *,
        message_id: str | None = None,
        reply_to: str | None = None,
        recipient: str | None = None,
    ) -> dict[str, Any]:
        async with self.control(run_id, "chat") as c:
            if (c.state.preparation is not None and c.state.status == "planned"
                and c.state.intake.status is None):
                raise ValueError("请先完成环境预检并启动研发；预检阶段不会启动对话 Agent")
            previous_active = self.active.get(run_id)
            message_id = c.user_message(
                text,
                key=message_id or new_id("user"),
                reply_to=reply_to,
                recipient=recipient,
            )
            actual_recipient = c.store.get("messages", message_id, ResearchMessage).recipient
            if (c.state.status == "paused"
                and c.actor(actual_recipient).wait_reason == "user_cancelled"):
                c.resume()
            config = self.config(c)
            closeout_pending = (
                config.needs_finalization
                and c.state.status in {"stopped", "succeeded"}
                and not c.state.user_stopped
                and (c.completion is None or c.completion.status not in {"completed", "failed"})
            )
            active = await self.attach(
                c,
                chat_only=c.state.status in {"stopped", "succeeded", "completed", "failed"}
                and not closeout_pending,
            )
            active.engine.wake()
            if (
                c.state.status in {"stopped", "succeeded", "completed", "failed"}
                and previous_active is active
                and not active.task.done()
                and run_id not in self._chat_followup_runs
            ):
                self._chat_followup_runs.add(run_id)

                def after_current_task(_: asyncio.Task[None]) -> None:
                    self._chat_followup_runs.discard(run_id)
                    if self._closing:
                        return
                    wakeup = asyncio.create_task(self._continue_terminal_chat(run_id))
                    self._chat_wakeups.add(wakeup)
                    wakeup.add_done_callback(self._chat_wakeups.discard)

                active.task.add_done_callback(after_current_task)
        return {"run_id": c.store.run_id, "message_id": message_id, "status": c.state.status}

    async def _continue_terminal_chat(self, run_id: str) -> None:
        try:
            async with self.control(run_id, "chat") as c:
                if (
                    c.state.status not in {"stopped", "succeeded", "completed", "failed"}
                    or "interaction" not in c.pending()
                ):
                    return
                active = await self.attach(c, chat_only=True)
                active.engine.wake()
        except Exception as exc:
            with suppress(OSError, ValueError):
                self.coordinator(run_id).store.event(
                    "chat_recovery_failed", error=type(exc).__name__
                )

    @staticmethod
    def _human_control(c: Coordinator) -> None:
        if (c.state.mode != "passk" or not c.state.started_at
            or c.state.status not in {"running", "paused"} or c.completion is not None):
            raise ValueError("人工指导仅适用于已启动且尚未结束的 pass@k 任务")

    async def set_agent_paused(self, run_id: str, agent_id: str, paused: bool) -> None:
        async with self.control(run_id, "agent-control") as c:
            self._human_control(c)
            if agent_id not in {a.agent_id for a in c.store.run().research_agents}:
                raise ValueError("请选择当前任务中的研究员")
            actor = c.actor(agent_id)
            if actor.state == AgentState.STOPPED:
                raise ValueError("该研究员已停止")
            held = c.state.human.paused_agents
            if paused:
                if agent_id not in held:
                    held.append(agent_id)
                actor.state, actor.wait_reason = AgentState.PAUSED, "human_paused"
            else:
                if agent_id not in held:
                    return
                held.remove(agent_id)
                if actor.state == AgentState.PAUSED:
                    actor.state, actor.wait_reason = AgentState.WAITING, "message"
                c.enqueue("system", agent_id, "人工暂停已解除，请处理最新用户指导后继续。",
                          key="human-resume:" + new_id("message"))
            c.save_actor(actor)
            c.save()
            active = await self.attach(c)
            await active.engine.tick()
            active.engine.wake()

    async def cancel_job(self, run_id: str, job_id: str) -> None:
        async with self.control(run_id, "cancel-job") as c:
            self._human_control(c)
            active = await self.attach(c)
            if job_id not in active.execution.jobs:
                raise ValueError("作业不属于当前任务")
            active.execution.manager.cancel(job_id)
            c.store.event("job_cancel_requested", job_id=job_id)
            active.engine.wake()

    def _apply_goal_change(self, c: Coordinator, change: GoalRevision) -> None:
        """Replay a durable intent while paused; all writes and deliveries are idempotent."""
        if change.applied:
            return
        if c.state.status != "paused":
            raise ValueError("目标切换必须在暂停状态下完成")
        run = c.store.run()
        run.brief_ref = c.store.put("briefs", "goal-" + change.request_id, change.brief)
        run.plan.goal = change.brief.objective
        run.best_result_ref = run.baseline_result_ref = run.final_result_ref = None
        c.store.save_run(run)
        config = self.config(c)
        write_json(c.store.root / "coordination/config.json", config.public())
        if active := self.active.get(c.store.run_id):
            active.config = config
        c.state.selected_candidate_id = None
        c.state.passk_target = None
        text = (f"全局目标已更新为 v{change.brief.revision + 1}。以此覆盖旧任务中冲突的要求，"
                "保留可复用代码；旧成绩仅作历史，需重新验证。先说明调整后的执行计划。\n\n"
                + change.brief.objective + "\n\n" + change.brief.instructions
                + "\n\n验收标准：\n" + "\n".join(change.brief.acceptance))
        for actor in c.store.run().research_agents:
            c.state.goals[actor.agent_id] = GoalContext(updated_at=now())
        recipients = list(c.store.run().research_agents)
        if c.state.rsi_model_enabled:
            recipients.append(c.actor("rsi"))
        for actor in recipients:
            c.enqueue("user", actor.agent_id, text,
                      key="goal:" + change.request_id + ":" + actor.agent_id)
        change.applied = True
        c.save()
        c.store.event("goal_revised", revision=change.brief.revision,
                      objective=change.brief.objective)

    async def update_goal(
        self, run_id: str, objective: str, instructions: str,
        request_id: str, expected_revision: int,
        verification_confirmed: bool = False, *, acceptance: list[str],
    ) -> None:
        validate_request("update-goal", dict(run_id=run_id, objective=objective,
                         instructions=instructions, request_id=request_id,
                         expected_revision=expected_revision,
                         verification_confirmed=verification_confirmed, acceptance=acceptance))
        async with self.control(run_id, "update-goal") as c:
            self._human_control(c)
            changes = c.state.human.goal_changes
            previous = next((x for x in changes if x.request_id == request_id), None)
            if previous:
                if (previous.brief.objective != objective.strip()
                    or previous.brief.instructions != instructions.strip()
                    or previous.brief.acceptance != acceptance
                    or previous.verification_confirmed != verification_confirmed
                    or previous.previous.revision != expected_revision):
                    raise ValueError("目标修改请求标识已用于不同内容")
                if previous.applied:
                    return
            elif any(not x.applied for x in changes):
                raise ValueError("上次目标修改尚未完成，请先恢复任务")
            brief = c.store.brief()
            if previous is None and brief.revision != expected_revision:
                raise ValueError("目标已被其他页面修改，请刷新后重试")
            revised = replace(brief, objective=objective.strip(), instructions=instructions.strip(),
                              revision=expected_revision + 1, acceptance=list(acceptance))
            running = c.state.status == "running"
            if running:
                c.pause()
            active = await self.attach(c)
            await active.engine.tick()  # Stop native turns before changing their task.
            change = previous or GoalRevision(request_id, brief, revised, now(),
                                              verification_confirmed=verification_confirmed)
            if previous is None:
                changes.append(change)
                c.save()
            # Stop writes from old jobs before the new goal reuses their workspaces.
            await active.execution.cancel_pending()
            self._apply_goal_change(c, change)
            if running:
                c.resume()
            active.engine.wake()

    async def cancel(self, run_id: str) -> dict[str, Any]:
        async with self.control(run_id, "cancel") as c:
            if c.state.preparation is not None and c.state.status == "planned":
                active = self.active.get(run_id)
                manager = (active.execution.manager
                           if active and not active.execution.closed else None)
                development.cancel(c, manager)
                return await self.detail(run_id)
            self._human_control(c)
            if c.state.status == "running":
                c.pause()
            active = self.active.get(c.store.run_id)
            if active:
                await active.engine.tick()
            for actor in c.store.run().research_agents:
                c.end_turn(actor.agent_id)
                actor = c.actor(actor.agent_id)
                actor.state, actor.wait_reason = AgentState.WAITING, "user_cancelled"
                c.save_actor(actor)
                goal = c.state.goals[actor.agent_id]
                goal.status, goal.blocker = "blocked", "用户已取消当前执行，等待新指令"
            c.save()
        return await self.detail(c.store.run_id)

    async def pause(self, run_id: str) -> dict[str, Any]:
        async with self.control(run_id, "pause") as c:
            c.pause()
            if c.store.run_id in self.active:
                await self.active[c.store.run_id].engine.tick()
        return await self.detail(c.store.run_id)

    async def resume(self, run_id: str) -> dict[str, Any]:
        async with self.control(run_id, "resume") as c:
            for change in c.state.human.goal_changes:
                if not change.applied:
                    active = await self.attach(c)
                    await active.execution.cancel_pending()
                    self._apply_goal_change(c, change)
            if c.state.preparation is not None and not c.state.started_at:
                raise ValueError("请先完成环境预检，再点击开始研究")
            if (
                self.config(c).mode == "passk"
                and c.state.status == "stopped"
                and c.completion is None
            ):
                raise ValueError(
                    "This passk experiment has ended; create a new run for another budget"
                )
            completion = c.completion
            if completion and completion.status == "completed":
                raise ValueError("最终复测已完成；继续探索请新建研究")
            if c.state.status not in {"planned", "running", "paused", "stopped"}:
                raise ValueError("This run cannot be resumed")
            c.state.user_stopped = False
            if c.state.status == "paused":
                for actor in c.store.run().research_agents:
                    if actor.wait_reason == "user_cancelled":
                        goal = c.state.goals[actor.agent_id]
                        goal.status, goal.blocker = "active", ""
                        actor.wait_reason = "message"
                        c.save_actor(actor)
                        c.enqueue("system", actor.agent_id, "用户已恢复执行，请继续最新目标。",
                                  key=new_id("resume"))
                c.resume()
            elif c.state.status == "stopped":
                c.reopen()
            elif c.state.status not in {"planned", "running"}:
                raise ValueError("This run cannot be resumed")
            active = await self.attach(c)
            active.engine.wake()
        return await self.detail(c.store.run_id)

    async def retry_agent(self, run_id: str, agent_id: str) -> dict[str, Any]:
        async with self.control(run_id, "retry") as c:
            if c.state.preparation is not None and not c.state.started_at:
                raise ValueError("请先完成环境预检，再点击开始研究")
            c.retry_agent(agent_id)
            if agent_id in c.state.goals:
                goal = c.state.goals[agent_id]
                goal.status, goal.blocker = "active", ""
                goal.updated_at, goal.revision = now(), goal.revision + 1
                c.save()
            active = await self.attach(c)
            active.engine.wake()
        return await self.detail(c.store.run_id)

    async def stop(self, run_id: str, *, reason: str = "Stopped by user") -> dict[str, Any]:
        async with self.control(run_id, "stop") as c:
            if c.state.status == "completed" or (
                c.completion is not None and c.completion.status == "completed"
            ):
                return await self.detail(c.store.run_id)
            c.state.user_stopped = True
            c.stop(reason)
            if c.store.run_id in self.active:
                active = self.active[c.store.run_id]
                active.task.cancel()
                await asyncio.gather(active.task, return_exceptions=True)
                await active.execution.cancel_pending()
                active.execution.close()
                await active.engine.close()
            else:
                manager = JobManager(c.store.root, auto_monitor=False, scheduling_enabled=False)
                await asyncio.to_thread(manager.close, preserve_running=False)
        return await self.detail(c.store.run_id)

    def _visible_coordinator(self, run_id: str) -> Coordinator:
        c = self.coordinator(run_id)
        if (c.store.root / ".creating").exists():
            raise ValueError("研究创建尚未完成，请重试原创建请求")
        if c.state.simulated and not self.allow_simulation:
            raise ValueError("该记录属于开发测试，请在独立的测试工作台查看")
        return c

    async def export(self, run_id: str, *, candidate_id: str | None = None) -> ArtifactDownload:
        c = self._visible_coordinator(run_id)
        result_ref = c.store.run().final_result_ref or c.store.run().best_result_ref
        candidate_id = candidate_id or c.selected_candidate_id
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
                and r.simulated == c.state.simulated
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
                "simulated": c.state.simulated,
                "verification": verification,
            },
        )
        archive = await asyncio.to_thread(shutil.make_archive, str(folder), "zip", folder)
        return ArtifactDownload(Path(archive), candidate_id + ".zip")

    async def export_report(self, run_id: str, job_id: str) -> ArtifactDownload:
        c = self._visible_coordinator(run_id)
        info = c.store.research_jobs().get(job_id)
        if info is None or not info.verification or info.public_output_subdir is None:
            raise ValueError("该作业没有公开验收报告")
        if not info.processed:
            raise ValueError("验收尚未结束")
        output = c.store.root / "execution-outputs" / job_id
        if Path(info.output).resolve() != output.resolve() or output.is_symlink():
            raise ValueError("Invalid report output")
        source = output / safe_relative(info.public_output_subdir)
        if not source.is_dir() or not source.resolve().is_relative_to(output.resolve()):
            raise ValueError("公开验收报告尚未生成")
        if (any(path.is_symlink() for path in (source, *source.parents)
                if path.is_relative_to(output))
            or any(path.is_symlink() for path in source.rglob("*"))):
            raise ValueError("Report must not contain symbolic links")
        if (source / "rsi-verification.json").exists():
            raise ValueError("Reserved verification manifest filename")
        folder = c.store.root / "exports" / new_id("report")
        folder.parent.mkdir(exist_ok=True)
        archive = await asyncio.to_thread(shutil.make_archive, str(folder), "zip", source)
        with zipfile.ZipFile(archive, "a") as report:
            report.writestr("rsi-verification.json", json.dumps({
                "run_id": run_id, "job_id": job_id, "verification": encode(info.result),
            }, ensure_ascii=False, indent=2))
        return ArtifactDownload(Path(archive), f"{c.store.run_id}-{job_id}-report.zip")

    async def list_runs(self) -> dict[str, Any]:
        return research_list(self.root, self.coordinator, allow_simulation=self.allow_simulation)

    async def recover(self) -> dict[str, Any]:
        for run in FileRunStore(self.root).list(ResearchRun):
            try:
                c = self.coordinator(run.run_id)
                config = self.config(c)
            except (OSError, ValueError, KeyError):
                continue
            completion = c.completion
            closeout_pending = (
                config.needs_finalization
                and c.state.status in {"stopped", "succeeded"}
                and not c.state.user_stopped
                and (completion is None or completion.status not in {"completed", "failed"})
            )
            resume_research = bool(
                config.metadata.get("auto_resume")
                and c.state.started_at
                and not c.state.user_stopped
                and (c.state.status == "running" or closeout_pending)
            )
            resume_chat = (
                c.state.status in {"stopped", "succeeded", "completed", "failed"}
                and "interaction" in c.pending()
            )
            if (
                config.mode in MODES
                and (resume_research or resume_chat)
                and not self.worker_elsewhere(c)
            ):
                try:
                    await self.attach(c, chat_only=resume_chat and not closeout_pending)
                except Exception as exc:
                    c.store.event("recovery_failed", error=type(exc).__name__)
        return {}

    async def bootstrap(self) -> dict[str, Any]:
        config = self.default_config or ResearchConfig(
            TaskBrief("新的研究", ""), collaboration_modules=DEFAULT_MODULES
        )
        return {
            "service": "areal-rsi",
            "api_version": 1,
            "capabilities": ["configured-runs", "automatic-finalization", "agent-trace"],
            "data_root": str(self.root),
            "default_model_settings": {
                **config.model_settings(),
                "has_credentials": bool(config.api_key()),
            },
            "collaboration_catalog": catalog(),
            "default_collaboration": {
                "mode": config.mode,
                "max_agents": config.brief.limits.max_agents,
                "modules": list(config.collaboration_modules),
            },
            "development_profiles": development.catalog(self.root),
            "workspace_ready": development.default_profile(self.root, config) is not None,
            "allow_simulation": self.allow_simulation,
            "default_simulated": self.allow_simulation and self.default_config is None,
            "has_config": self.default_config is not None,
            "models": [
                {"id": model, "efforts": list(efforts)} for model, efforts in MODEL_EFFORTS.items()
            ],
        }

    @overload
    async def call(
        self, action: Literal["export", "export-report"], params: dict[str, Any]
    ) -> ArtifactDownload: ...

    @overload
    async def call(self, action: JsonAction, params: dict[str, Any]) -> dict[str, Any]: ...

    @overload
    async def call(
        self, action: str, params: dict[str, Any]
    ) -> dict[str, Any] | ArtifactDownload: ...

    async def call(self, action: str, params: dict[str, Any]) -> dict[str, Any] | ArtifactDownload:
        if action == "create":
            return await self.create(params)
        validate_request(action, params)
        if action in {"model-settings", "collaboration"}:
            values = {key: value for key, value in params.items() if key != "run_id"}
            if action == "model-settings":
                await self.set_model_settings(params["run_id"], values)
            else:
                await self.set_collaboration(params["run_id"], values)
            return await self.detail(params["run_id"])
        handlers: dict[str, Callable[..., Awaitable[dict[str, Any] | ArtifactDownload | None]]] = {
            "recover": self.recover,
            "bootstrap": self.bootstrap,
            "list": self.list_runs,
            "detail": self.detail,
            "trace": self.trace,
            "delete": self.delete,
            "chat": self.send_message,
            "agent-control": self.set_agent_paused,
            "update-goal": self.update_goal,
            "cancel-job": self.cancel_job,
            "start": self.start,
            "prepare": self.prepare,
            "pause": self.pause,
            "cancel": self.cancel,
            "resume": self.resume,
            "retry": self.retry_agent,
            "stop": self.stop,
            "export": self.export,
            "export-report": self.export_report,
        }
        result = await handlers[action](**params)
        return await self.detail(params["run_id"]) if result is None else result

    async def close(self) -> None:
        self._closing = True
        wakeups = tuple(self._chat_wakeups)
        for task in wakeups:
            task.cancel()
        await asyncio.gather(*wakeups, return_exceptions=True)
        for active in self.active.values():
            active.task.cancel()
        await asyncio.gather(*(a.task for a in self.active.values()), return_exceptions=True)
        for active in self.active.values():
            active.execution.close(cancel=False)
            await active.engine.close()
        self.active.clear()
