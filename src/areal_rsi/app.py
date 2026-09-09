"""本地应用装配，所有 provider 细节止于这一层。"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from areal_rsi.agents.research import DefaultResearchAgent
from areal_rsi.agents.rsi import DefaultRsiAgent
from areal_rsi.api.base import (
    ExperimentPlan,
    JsonObject,
    ModelVersion,
    SolutionVersion,
    VerifierSpec,
)
from areal_rsi.api.modules import Benchmark, Verifier
from areal_rsi.api.workflow import WorkflowContext
from areal_rsi.base.catalog import LocalModelCatalog
from areal_rsi.base.storage import (
    DIRECTORY_TYPE,
    FileContentStore,
    FileMemory,
    FileTrajectoryStore,
    FileVersionManager,
    new_id,
    write_json,
)
from areal_rsi.config import (
    AgentModelSettings,
    ModelConfig,
    ModelConfigurationError,
    research_agent_count_from_environment,
)
from areal_rsi.modules.benchmark import AccuracyEvaluation, ArithmeticBenchmark, ArithmeticVerifier
from areal_rsi.modules.gateway import GatewayProxy
from areal_rsi.modules.gsm8k import Gsm8kBenchmark, Gsm8kVerifier
from areal_rsi.modules.harness import LocalHarnessBuilder, LocalHarnessRuntime
from areal_rsi.modules.local.config import LocalConfig
from areal_rsi.modules.runtime_package import PackageAgentRuntime, RuntimeSettings
from areal_rsi.workflows.service import LocalExperimentService, WorkflowFactory


class LocalApp:
    root: Path
    content: FileContentStore
    versions: FileVersionManager
    trajectories: FileTrajectoryStore
    memory: FileMemory
    baseline: SolutionVersion
    target_model: ModelVersion
    agent_models: dict[str, ModelVersion]
    agent_proxies: dict[str, GatewayProxy]
    agent_runtimes: dict[str, PackageAgentRuntime]
    runtime_settings: RuntimeSettings
    interaction_model_name: str | None
    rsi: DefaultRsiAgent
    service: LocalExperimentService

    @classmethod
    async def create(
        cls,
        root: Path,
        emit: Callable[[str], None],
        *,
        workflows: Mapping[str, WorkflowFactory] | None = None,
        workflow_name: str = "harness-evolution",
    ) -> LocalApp:
        if workflow_name not in (
            workflows if workflows is not None else {"harness-evolution": None}
        ):
            raise ValueError("选中的 Python Workflow 尚未注册")
        if any((root / "remote" / name).exists() for name in ("backend.json", "config.json")):
            raise ValueError("数据目录属于 remote mode，请设置独立的 RSI_DATA_ROOT")
        baseline_file = root / "baseline.json"
        if baseline_file.exists():
            baseline = json.loads(baseline_file.read_text())
            if baseline["harness"]["content"].get("media_type") != DIRECTORY_TYPE:
                raise ValueError("数据目录属于 remote mode，请设置独立的 RSI_DATA_ROOT")
        runtime_settings = RuntimeSettings.from_environment()
        model_settings = AgentModelSettings.from_environment()
        research_agent_count = research_agent_count_from_environment()
        agent_ids = [
            "rsi-agent",
            *(f"research-agent-{index}" for index in range(1, research_agent_count + 1)),
            "task-agent",
        ]
        model_settings.validate_agents()
        research_connection = model_settings.resolve("research-agent")
        connections = {
            "rsi-agent": model_settings.resolve("rsi-agent"),
            **{
                agent_id: research_connection
                for agent_id in agent_ids
                if agent_id.startswith("research-agent-")
            },
        }
        local_config = LocalConfig.from_environment()
        target_connection = local_config.target_connection()
        runtime_connections = {**connections, "task-agent": target_connection}
        for role in runtime_connections:
            runtime_settings.select(role)
        self = cls()
        self.runtime_settings = runtime_settings
        self.interaction_model_name = model_settings.interaction_name()
        self.root = root.resolve()
        self.content = FileContentStore(self.root / "content")
        self.versions = FileVersionManager(self.root / "versions")
        self.trajectories = FileTrajectoryStore(self.root / "trajectories", self.content)
        self.memory = FileMemory(self.root / "memory")
        benchmark: Benchmark
        verifier: Verifier
        benchmark_kind = local_config.benchmark
        if benchmark_kind == "gsm8k":
            benchmark = Gsm8kBenchmark(
                local_config.dataset,
                train_limit=local_config.train_limit,
                validate_limit=local_config.validate_limit,
            )
            verifier = Gsm8kVerifier(self.content, benchmark)
            description = "GSM8K 官方 train 的固定训练/留出验证分区，不是官方 test 成绩。"
        elif benchmark_kind == "arithmetic-v1":
            benchmark = ArithmeticBenchmark()
            verifier = ArithmeticVerifier(self.content)
            description = "原创固定小型算术题，用于验收闭环，不是 GSM8K 正式结果。"
        else:
            raise ValueError(f"未知 Benchmark：{benchmark_kind}")
        catalog = LocalModelCatalog(self.root, self.versions)
        self.agent_models = {
            agent_id: await catalog.model(
                connection.model, await self.content.put_json(connection.public_identity())
            )
            for agent_id, connection in connections.items()
        }
        self.target_model = await catalog.model(
            target_connection.model,
            await self.content.put_json(target_connection.public_identity()),
        )
        self.baseline = await catalog.baseline(
            self.target_model,
            await self.content.snapshot(local_config.harness),
        )
        self.agent_proxies, self.agent_runtimes = {}, {}
        proxies: dict[ModelConfig, GatewayProxy] = {}
        runtimes: dict[tuple[ModelConfig, int, str], PackageAgentRuntime] = {}
        for agent_id, connection in runtime_connections.items():
            if connection not in proxies:
                proxies[connection] = GatewayProxy(connection.base_url, connection.api_key)
            proxy = proxies[connection]
            budget = 4096 if agent_id == "task-agent" else 12288
            selection = runtime_settings.select(agent_id)
            key = (connection, budget, json.dumps(selection, sort_keys=True))
            if key not in runtimes:
                runtimes[key] = PackageAgentRuntime(
                    self.root / "sessions",
                    self.content,
                    self.trajectories,
                    settings=runtime_settings,
                    role=agent_id,
                    base_url=proxy.url,
                    api_key="local-proxy",
                    timeout=float(os.environ.get("RSI_TURN_TIMEOUT", "1200")),
                    max_tokens=budget,
                    dsh_root=self.root,
                    credential_env_names=model_settings.credential_env_names,
                )
            self.agent_proxies[agent_id] = proxy
            self.agent_runtimes[agent_id] = runtimes[key]
        self.rsi = DefaultRsiAgent(
            self.agent_runtimes["rsi-agent"],
            self.agent_models["rsi-agent"],
            self.baseline.solution_id,
            benchmark.name,
            benchmark_description=description,
            research_agent_count=research_agent_count,
            workflow_name=workflow_name,
            target_model_name=self.baseline.model.name,
            model_plan_config=self.model_plan_config(),
        )
        researchers = tuple(
            DefaultResearchAgent(
                f"research-agent-{index}",
                self.agent_runtimes[f"research-agent-{index}"],
                self.agent_models[f"research-agent-{index}"],
                self.content,
                self.memory,
                self.versions,
                self.root / "research-workspaces",
            )
            for index in range(1, research_agent_count + 1)
        )
        harness_runtime = LocalHarnessRuntime(
            self.agent_runtimes["task-agent"], self.content, self.root / "task-workspaces"
        )
        builder = LocalHarnessBuilder(self.content, self.root / "builds")
        evaluation = AccuracyEvaluation()
        context = WorkflowContext(
            self.content,
            self.memory,
            self.trajectories,
            self.versions,
            harness_runtime,
            self.rsi,
            researchers,
            benchmark,
            verifier,
            VerifierSpec(
                verifier_id=f"{benchmark.name}-verifier",
                provider="injected",
            ),
            evaluation,
            builder,
        )
        self.service = LocalExperimentService(self.root, context, emit, workflows=workflows)
        return self

    def model_plan_config(self) -> JsonObject:
        models: JsonObject = {
            agent_id: {"model": model.name, "version_id": model.version_id}
            for agent_id, model in self.agent_models.items()
        }
        return {
            "mode": "local",
            "runtime_config_digest": self.runtime_settings.identity,
            "research_harness": self.runtime_settings.select("research-agent")["kind"],
            "agent_models": models,
            "interaction_model_name": self.interaction_model_name,
            "target_model_name": self.target_model.name,
            "target_model_version_id": self.target_model.version_id,
        }

    async def bind_plan(self, plan: ExperimentPlan) -> None:
        """按 Run 校验配置并恢复它的基线；目录本身不绑定模型。"""
        mode = self.model_plan_config()["mode"]
        if plan.config.get("mode", mode) != mode:
            raise ModelConfigurationError("实验模式与已确认计划不一致。")
        recorded_runtime = plan.config.get("runtime_config_digest")
        if recorded_runtime != self.runtime_settings.identity:
            raise ModelConfigurationError(
                "Run 的 Runtime 配置与当前配置不一致；旧 DSH 实验请使用原版本恢复。"
            )
        baseline = await self.versions.get_solution(plan.baseline_solution_id)
        if baseline is None:
            raise ModelConfigurationError("计划引用的目标基线不存在，请检查实验数据目录。")
        if (
            baseline.model.name != self.baseline.model.name
            or baseline.model.content != self.baseline.model.content
        ):
            raise ModelConfigurationError(
                "当前 Target Model 与此 Run 的目标模型不一致；请核对 RSI_TARGET 配置。"
            )
        recorded = plan.config.get("agent_models")
        if isinstance(recorded, dict):
            # 兼容旧计划：当时把同一个 Target Model 重复记录为 task-agent 模型。
            recorded_agents = dict(recorded)
            legacy_target = recorded_agents.pop("task-agent", None)
            if set(recorded_agents) != set(self.agent_models):
                raise ModelConfigurationError(
                    "计划的 Agent 集合与当前装配不一致，请核对研究线配置。"
                )
            for agent_id, model in self.agent_models.items():
                entry = recorded_agents[agent_id]
                if not isinstance(entry, dict):
                    raise ModelConfigurationError("计划缺少有效的 Agent 模型版本记录。")
                version_id = entry.get("version_id")
                if not isinstance(version_id, str):
                    raise ModelConfigurationError("计划缺少有效的 Agent 模型版本记录。")
                expected = await self.versions.get_model(version_id)
                if (
                    expected is None
                    or expected.name != model.name
                    or expected.content != model.content
                    or entry.get("model") != model.name
                ):
                    raise ModelConfigurationError(
                        f"{agent_id} 的模型配置与此 Run 不一致，请恢复对应配置或创建新计划。"
                    )
            if isinstance(legacy_target, dict) and (
                legacy_target.get("model") != baseline.model.name
                or legacy_target.get("version_id") != baseline.model.version_id
            ):
                raise ModelConfigurationError("旧计划记录的 Target Model 与基线不一致。")
            target_version_id = plan.config.get("target_model_version_id")
            if (
                isinstance(target_version_id, str)
                and target_version_id != baseline.model.version_id
            ):
                raise ModelConfigurationError("计划记录的 Target Model 与基线不一致。")
        else:
            # 旧计划记录的是所有研究 Agent 共用的 teacher，只按当时已记录的信息核验。
            teacher_id = plan.config.get("teacher_model_version_id")
            teacher_name = plan.config.get("teacher_model_name")
            teacher = (
                await self.versions.get_model(teacher_id) if isinstance(teacher_id, str) else None
            )
            for agent_id, model in self.agent_models.items():
                if (
                    isinstance(teacher_id, str)
                    and (
                        teacher is None
                        or teacher.name != model.name
                        or teacher.content != model.content
                    )
                ) or (isinstance(teacher_name, str) and teacher_name != model.name):
                    raise ModelConfigurationError(
                        f"{agent_id} 与旧计划的 teacher 配置不一致，请恢复对应配置或创建新计划。"
                    )
        self.service.validate(plan)
        self.baseline = baseline
        self.rsi.baseline = baseline.solution_id

    async def close(self) -> None:
        try:
            for run_id, task in self.service.tasks.items():
                if not task.done():
                    await self.service.stop(run_id)
            await self.rsi.close()
        finally:
            unique = list(dict.fromkeys(self.agent_proxies.values()))
            try:
                await asyncio.gather(
                    *(runtime.close() for runtime in set(self.agent_runtimes.values()))
                )
            finally:
                await self._close_proxies(unique)

    async def _close_proxies(self, unique: list[GatewayProxy]) -> None:
        try:
            for index, proxy in enumerate(unique):
                write_json(
                    self.root / "gateway" / f"{new_id('process')}-connection-{index}.json",
                    proxy.requests,
                )
        finally:
            await asyncio.gather(*(asyncio.to_thread(proxy.close) for proxy in unique))


async def create_app(root: Path, emit: Callable[[str], None]) -> LocalApp:
    mode = os.environ.get("RSI_MODE", "local")
    if mode == "local":
        return await LocalApp.create(root, emit)
    if mode == "remote":
        from areal_rsi.remote_app import RemoteApp

        return await RemoteApp.create(root, emit)
    raise ValueError("RSI_MODE 只支持 local 或 remote")
