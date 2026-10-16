"""本地应用装配，所有 provider 细节止于这一层。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from areal_rsi.agents.research import DefaultResearchAgent
from areal_rsi.agents.research.artifact import ArtifactResearchAgent
from areal_rsi.agents.rsi import DefaultRsiAgent
from areal_rsi.api.base import (
    EvolutionTarget,
    ExperimentPlan,
    JsonObject,
    ModelVersion,
    SolutionVersion,
    VerifierSpec,
)
from areal_rsi.api.goal import GoalSpec, SkillVersion
from areal_rsi.api.modules import Benchmark, HarnessRuntime, Verifier
from areal_rsi.api.workflow import WorkflowContext
from areal_rsi.base.catalog import LocalModelCatalog
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileTrajectoryStore,
    FileVersionManager,
    decode,
    new_id,
    write_json,
)
from areal_rsi.config import (
    AgentModelSettings,
    ModelConfig,
    ModelConfigurationError,
    research_agent_count_from_environment,
)
from areal_rsi.modules.artifact import LocalArtifactBuilder
from areal_rsi.modules.benchmark import AccuracyEvaluation, ArithmeticBenchmark, ArithmeticVerifier
from areal_rsi.modules.gateway import GatewayProxy
from areal_rsi.modules.gsm8k import Gsm8kBenchmark, Gsm8kVerifier
from areal_rsi.modules.harness import LocalHarnessBuilder, LocalHarnessRuntime
from areal_rsi.modules.runtime_package import PackageAgentRuntime, RuntimeSettings
from areal_rsi.workflows.artifact import ArtifactEvolutionWorkflow
from areal_rsi.workflows.evolution import HarnessEvolutionWorkflow
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
        goal: GoalSpec | None = None,
        frozen: Mapping[str, object] | None = None,
        skills: tuple[SkillVersion, ...] = (),
    ) -> LocalApp:
        target = goal.target if goal else EvolutionTarget.HARNESS
        if target == EvolutionTarget.MODEL:
            raise ValueError(
                "Model training backend is not installed; real training is unsupported"
            )
        if target == EvolutionTarget.ARTIFACT:
            workflow_name = "artifact-evolution"
        if workflows is None:
            workflows = {
                "harness-evolution": HarnessEvolutionWorkflow,
                "artifact-evolution": ArtifactEvolutionWorkflow,
            }
        if workflow_name not in (
            workflows if workflows is not None else {"harness-evolution": None}
        ):
            raise ValueError("选中的 Python Workflow 尚未注册")
        runtime_settings = RuntimeSettings.from_environment()
        model_settings = AgentModelSettings.from_environment()
        research_agent_count = (
            goal.research_agent_count if goal else research_agent_count_from_environment()
        )
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
        target_connection = ModelConfig.from_environment("target")
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
        benchmark_kind = (
            goal.benchmark if goal else os.environ.get("RSI_BENCHMARK", "arithmetic-v1")
        )
        if target == EvolutionTarget.ARTIFACT:
            from areal_rsi.modules.browser import BrowserBenchmark, BrowserVerifier

            seed = goal.assets.get("benchmark_seed") if goal else None
            if seed is not None and type(seed) is not int:
                raise ValueError("benchmark_seed must be an integer")
            benchmark = BrowserBenchmark(benchmark_kind, seed=seed)
            verifier = BrowserVerifier(self.content)
            description = "Deterministic browser interaction and responsive layout checks."
        elif benchmark_kind == "ledger-arithmetic-v1":
            from areal_rsi.modules.ledger import LedgerBenchmark, LedgerVerifier

            seed = goal.assets.get("benchmark_seed", 20261019) if goal else 20261019
            if type(seed) is not int:
                raise ValueError("benchmark_seed must be an integer")
            benchmark = LedgerBenchmark(self.content, seed=seed)
            verifier = LedgerVerifier(self.content, benchmark)
            description = "Exact sequential ledger arithmetic, ordered fees and per-step rounding."
        elif benchmark_kind in {
            "structured-arithmetic-v1",
            "structured-arithmetic-v2",
            "structured-arithmetic-v3",
        }:
            from areal_rsi.modules.structured_arithmetic import (
                StructuredArithmeticBenchmark,
                StructuredArithmeticVerifier,
            )

            seed = goal.assets.get("benchmark_seed", 20261015) if goal else 20261015
            if type(seed) is not int:
                raise ValueError("benchmark_seed must be an integer")
            benchmark = StructuredArithmeticBenchmark(
                seed=seed,
                exact_division=benchmark_kind == "structured-arithmetic-v2",
                rational_terms=benchmark_kind == "structured-arithmetic-v3",
            )
            verifier = StructuredArithmeticVerifier(self.content, benchmark)
            description = "Frozen disjoint multi-step arithmetic and exact JSON output contract."
        elif benchmark_kind == "gsm8k":
            benchmark = Gsm8kBenchmark(
                Path(os.environ.get("RSI_GSM8K_DATA", ".rsi/datasets/gsm8k")),
                train_limit=int(os.environ.get("RSI_TRAIN_LIMIT", "16")),
                validate_limit=int(os.environ.get("RSI_VALIDATE_LIMIT", "16")),
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
            await self.content.snapshot(Path(__file__).parent / "default_harness"),
        )
        if goal and target == EvolutionTarget.ARTIFACT:
            import json

            if frozen and "artifact" in frozen:
                from areal_rsi.api.base import ContentRef

                artifact_content = decode(ContentRef, frozen["artifact"])
            else:
                artifact_content = await self.content.snapshot(
                    Path(goal.artifact_path or "").expanduser()
                )
            artifact = await self.versions.create_artifact(goal.artifact_name, artifact_content)
            self.baseline = await self.versions.create_solution(
                self.target_model, self.baseline.harness, artifacts=(artifact,)
            )
        if goal and target == EvolutionTarget.HARNESS and goal.assets.get("harness_path"):
            from areal_rsi.api.base import ContentRef

            if frozen and "harness" in frozen:
                harness_content = decode(ContentRef, frozen["harness"])
            else:
                source = goal.assets["harness_path"]
                if not isinstance(source, str):
                    raise ValueError("harness_path must be a directory path")
                harness_content = await self.content.snapshot(Path(source).expanduser())
            harness = await self.versions.create_harness("goal-baseline", harness_content)
            self.baseline = await self.versions.create_solution(self.target_model, harness)
        self.agent_proxies, self.agent_runtimes = {}, {}
        proxies: dict[ModelConfig, GatewayProxy] = {}
        runtimes: dict[tuple[ModelConfig, int, str], PackageAgentRuntime] = {}
        for agent_id, connection in runtime_connections.items():
            if connection not in proxies:
                proxies[connection] = GatewayProxy(
                    connection.base_url, connection.api_key, model=connection.model
                )
            proxy = proxies[connection]
            budget = 4096 if agent_id == "task-agent" else 12288
            selection = runtime_settings.select(agent_id)
            import json

            key = (connection, budget, json.dumps(selection, sort_keys=True))
            if key not in runtimes:
                runtimes[key] = PackageAgentRuntime(
                    (
                        Path(os.environ["RSI_SESSION_ROOT"]).expanduser()
                        / hashlib.sha256(str(self.root).encode()).hexdigest()[:16]
                        if os.environ.get("RSI_SESSION_ROOT")
                        else self.root / "sessions"
                    ),
                    self.content,
                    self.trajectories,
                    settings=runtime_settings,
                    role=agent_id,
                    base_url=proxy.url,
                    api_key="local-proxy",
                    timeout=float(os.environ.get("RSI_TURN_TIMEOUT", "1200")),
                    max_tokens=budget,
                    dsh_root=Path(os.environ.get("RSI_DSH_ROOT", str(self.root))).expanduser(),
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
        researchers_list: list[DefaultResearchAgent] = []
        for index in range(1, research_agent_count + 1):
            args = (
                f"research-agent-{index}",
                self.agent_runtimes[f"research-agent-{index}"],
                self.agent_models[f"research-agent-{index}"],
                self.content,
                self.memory,
                self.versions,
                self.root / "research-workspaces",
            )
            if goal and target == EvolutionTarget.ARTIFACT:
                researcher: DefaultResearchAgent = ArtifactResearchAgent(
                    *args, artifact_name=goal.artifact_name
                )
            else:
                researcher = DefaultResearchAgent(*args)
            researchers_list.append(researcher)
        researchers = tuple(researchers_list)
        for research_agent in researchers:
            research_agent.skills = skills
        self.rsi.evolution_target = target
        harness_runtime: HarnessRuntime = LocalHarnessRuntime(
            self.agent_runtimes["task-agent"], self.content, self.root / "task-workspaces"
        )
        if target == EvolutionTarget.ARTIFACT:
            from areal_rsi.modules.browser import BrowserArtifactRuntime

            harness_runtime = BrowserArtifactRuntime(
                self.content,
                self.trajectories,
                self.root / "browser-workspaces",
                artifact_name=goal.artifact_name if goal else "app",
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
                digest="sha256:"
                + hashlib.sha256(
                    Path(inspect.getfile(type(verifier))).read_bytes()
                    + Path(inspect.getfile(type(harness_runtime))).read_bytes()
                    + Path(inspect.getfile(type(evaluation))).read_bytes()
                ).hexdigest(),
            ),
            evaluation,
            builder,
        )
        if target == EvolutionTarget.ARTIFACT:
            context.artifact_builder = LocalArtifactBuilder(
                self.content, self.root / "artifact-builds"
            )
        if goal:
            concurrency = goal.assets.get("evaluation_concurrency", 1)
            if type(concurrency) is not int or not 1 <= concurrency <= 16:
                raise ValueError("evaluation_concurrency must be in [1,16]")
            context.evaluation_concurrency = concurrency
        self.service = LocalExperimentService(self.root, context, emit, workflows=workflows)
        return self

    def model_plan_config(self) -> JsonObject:
        models: JsonObject = {
            agent_id: {"model": model.name, "version_id": model.version_id}
            for agent_id, model in self.agent_models.items()
        }
        return {
            "runtime_config_digest": self.runtime_settings.identity,
            "agent_models": models,
            "interaction_model_name": self.interaction_model_name,
            "target_model_name": self.target_model.name,
            "target_model_version_id": self.target_model.version_id,
        }

    async def bind_plan(self, plan: ExperimentPlan) -> None:
        """按 Run 校验配置并恢复它的基线；目录本身不绑定模型。"""
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
        if plan.target == EvolutionTarget.ARTIFACT and not baseline.artifacts:
            raise ValueError("Artifact baseline is missing")
        self.service.validate(plan)
        self.baseline = baseline
        self.rsi.baseline = baseline.solution_id

    async def close(self) -> None:
        try:
            await self.rsi.close()
            for run_id, task in self.service.tasks.items():
                if not task.done():
                    await self.service.stop(run_id)
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
