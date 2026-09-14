"""Provider-neutral remote application; local research and remote evaluation."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from areal_rsi.agents.rsi import DefaultRsiAgent
from areal_rsi.api.base import ExperimentPlan, ExperimentRun, JsonObject
from areal_rsi.api.workflow import WorkflowContext
from areal_rsi.app import LocalApp
from areal_rsi.base.catalog import LocalModelCatalog
from areal_rsi.base.runs import FileRunStore, RunPaths
from areal_rsi.base.storage import (
    DIRECTORY_TYPE,
    FileContentStore,
    FileMemory,
    FileTrajectoryStore,
    FileVersionManager,
    write_json,
)
from areal_rsi.config import (
    AgentModelSettings,
    ModelConfig,
    ModelConfigurationError,
    research_agent_count_from_environment,
)
from areal_rsi.modules.gateway import GatewayProxy
from areal_rsi.modules.remote.base import RemoteContext
from areal_rsi.modules.remote.registry import RemoteSettings, get_provider, stored_backend
from areal_rsi.modules.runtime_package import PackageAgentRuntime, RuntimeSettings
from areal_rsi.workflows.service import LocalExperimentService, WorkflowFactory


class RemoteApp(LocalApp):
    remote_backend_name: str
    _bind_backend_run: Callable[[RunPaths], None] | None

    @classmethod
    async def create(
        cls,
        root: Path,
        emit: Callable[[str], None],
        *,
        workflows: Mapping[str, WorkflowFactory] | None = None,
        workflow_name: str = "harness-evolution",
    ) -> RemoteApp:
        if workflows is not None or workflow_name != "harness-evolution":
            raise ValueError("remote 首期只装配 harness-evolution")
        settings = RemoteSettings.from_environment()
        root = root.resolve()
        previous_backend = stored_backend(root)
        if previous_backend is not None and previous_backend != settings.backend:
            raise ValueError("数据目录已绑定其他 remote backend，请使用独立的 RSI_DATA_ROOT")
        if any((root / "baselines").glob("*.json")):
            raise ValueError("数据目录属于 local mode，请设置独立的 RSI_DATA_ROOT")
        baseline_file = root / "baseline.json"
        if previous_backend is None and baseline_file.exists():
            baseline = json.loads(baseline_file.read_text())
            if baseline["harness"]["content"].get("media_type") == DIRECTORY_TYPE:
                raise ValueError("数据目录属于 local mode，请设置独立的 RSI_DATA_ROOT")
        runtime_settings = RuntimeSettings.from_environment()
        model_settings = AgentModelSettings.from_environment()
        model_settings.validate_agents()
        research_agent_count = research_agent_count_from_environment()
        research_connection = model_settings.resolve("research-agent")
        connections = {
            "rsi-agent": model_settings.resolve("rsi-agent"),
            **{
                f"research-agent-{index}": research_connection
                for index in range(1, research_agent_count + 1)
            },
        }
        for agent_id in connections:
            runtime_settings.select(agent_id)
        research_max_tokens = int(os.environ.get("RSI_RESEARCH_MAX_TOKENS", "2048"))
        if not 256 <= research_max_tokens <= 32768:
            raise ValueError("RSI_RESEARCH_MAX_TOKENS 必须在 256 到 32768 之间")

        self = cls()
        self.root, self.runtime_settings = root, runtime_settings
        self.remote_backend_name = settings.backend
        self._run_id = None
        preparation = root / "runtime" / "preparation"
        self._audit_root = preparation / "gateway"
        self.interaction_model_name = model_settings.interaction_name()
        self.content = FileContentStore(root / "content")
        self.versions = FileVersionManager(root / "versions")
        self.trajectories = FileTrajectoryStore(preparation / "trajectories", self.content)
        self.memory = FileMemory(preparation / "memory")
        context = RemoteContext(
            root, self.content, self.memory, self.trajectories, self.versions, emit
        )
        # Reserve the directory before provider code can create backend-specific records.
        write_json(root / "remote" / "backend.json", {"backend": settings.backend}, immutable=True)
        backend = await get_provider(settings.backend).create(context, settings.config_path)
        self._bind_backend_run = backend.bind_run
        self.baseline = backend.baseline
        self.target_model = self.baseline.model
        catalog = LocalModelCatalog(self.root, self.versions)
        self.agent_models = {
            agent_id: await catalog.model(
                connection.model, await self.content.put_json(connection.public_identity())
            )
            for agent_id, connection in connections.items()
        }
        self.agent_proxies, self.agent_runtimes = {}, {}
        proxies: dict[ModelConfig, GatewayProxy] = {}
        runtimes: dict[tuple[ModelConfig, str], PackageAgentRuntime] = {}
        for agent_id, connection in connections.items():
            if connection not in proxies:
                proxies[connection] = GatewayProxy(connection.base_url, connection.api_key)
            selection = runtime_settings.select(agent_id)
            key = (connection, json.dumps(selection, sort_keys=True))
            if key not in runtimes:
                runtimes[key] = PackageAgentRuntime(
                    preparation / "agents",
                    self.content,
                    self.trajectories,
                    settings=runtime_settings,
                    role=agent_id,
                    base_url=proxies[connection].url,
                    api_key="local-proxy",
                    timeout=float(os.environ.get("RSI_TURN_TIMEOUT", "1200")),
                    max_tokens=research_max_tokens,
                    dsh_root=self.root,
                    credential_env_names=model_settings.credential_env_names,
                    agent_directories=True,
                )
            self.agent_proxies[agent_id] = proxies[connection]
            self.agent_runtimes[agent_id] = runtimes[key]
        self.rsi = DefaultRsiAgent(
            self.agent_runtimes["rsi-agent"],
            self.agent_models["rsi-agent"],
            self.baseline.solution_id,
            backend.benchmark.name,
            benchmark_description=backend.description,
            research_agent_count=research_agent_count,
            target_model_name=self.baseline.model.name,
            model_plan_config=self.model_plan_config(),
            run_store=FileRunStore(root),
        )
        researchers = tuple(
            backend.research_agent_factory(
                agent_id, self.agent_runtimes[agent_id], self.agent_models[agent_id]
            )
            for agent_id in connections
            if agent_id.startswith("research-agent-")
        )
        workflow_context = WorkflowContext(
            self.content,
            self.memory,
            self.trajectories,
            self.versions,
            backend.harness_runtime,
            self.rsi,
            researchers,
            backend.benchmark,
            backend.verifier,
            backend.verifier_spec,
            backend.evaluation,
            backend.harness_builder,
        )
        self.service = LocalExperimentService(
            root, workflow_context, emit, context_factory=self._bind_run
        )
        return self

    async def _bind_run(self, run: ExperimentRun) -> WorkflowContext:
        if self._run_id == run.run_id:
            return self.service.context
        context = await self._bind_run_storage(run)
        if self._bind_backend_run is not None:
            self._bind_backend_run(self.service.records.paths(run.run_id))
        return context

    def model_plan_config(self) -> JsonObject:
        return {
            **super().model_plan_config(),
            "mode": "remote",
            "remote_backend": self.remote_backend_name,
        }

    async def bind_plan(self, plan: ExperimentPlan) -> None:
        if plan.config.get("remote_backend", self.remote_backend_name) != self.remote_backend_name:
            raise ModelConfigurationError("计划的 remote backend 与当前配置不一致。")
        await super().bind_plan(plan)
