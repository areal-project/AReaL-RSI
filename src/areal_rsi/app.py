"""本地应用装配，所有 provider 细节止于这一层。"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from areal_rsi.agents.default import DefaultResearchAgent, DefaultRsiAgent
from areal_rsi.api.base import SolutionVersion
from areal_rsi.api.modules import Benchmark, Verifier
from areal_rsi.api.workflow import WorkflowContext
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileTrajectoryStore,
    FileVersionManager,
    decode,
    new_id,
    write_json,
)
from areal_rsi.config import ModelConfig
from areal_rsi.modules.benchmark import AccuracyEvaluation, ArithmeticBenchmark, ArithmeticVerifier
from areal_rsi.modules.gateway import GatewayProxy
from areal_rsi.modules.gsm8k import Gsm8kBenchmark, Gsm8kVerifier
from areal_rsi.modules.harness import LocalHarnessBuilder, LocalHarnessRuntime
from areal_rsi.modules.runtime import DshAgentRuntime
from areal_rsi.workflows.service import LocalExperimentService, WorkflowFactory


class LocalApp:
    root: Path
    content: FileContentStore
    versions: FileVersionManager
    trajectories: FileTrajectoryStore
    memory: FileMemory
    baseline: SolutionVersion
    proxy: GatewayProxy
    runtime: DshAgentRuntime
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
        model_config = ModelConfig.from_environment()
        self = cls()
        self.root = root.resolve()
        self.content = FileContentStore(self.root / "content")
        self.versions = FileVersionManager(self.root / "versions")
        self.trajectories = FileTrajectoryStore(self.root / "trajectories", self.content)
        self.memory = FileMemory(self.root / "memory")
        benchmark: Benchmark
        verifier: Verifier
        benchmark_kind = os.environ.get("RSI_BENCHMARK", "arithmetic-v1")
        if benchmark_kind == "gsm8k":
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
        model_name = model_config.model
        base_url = model_config.base_url
        baseline_file = self.root / "baseline.json"
        if baseline_file.exists():
            self.baseline = decode(SolutionVersion, json.loads(baseline_file.read_text()))
            if self.baseline.model.name != model_name:
                raise ValueError("该数据目录已绑定其他模型，请使用新的 RSI_DATA_ROOT")
        else:
            model = await self.versions.create_model(
                model_name, await self.content.put_json({"model": model_name, "base_url": base_url})
            )
            harness = await self.versions.create_harness(
                "dsh-arithmetic",
                await self.content.snapshot(Path(__file__).parent / "default_harness"),
            )
            self.baseline = await self.versions.create_solution(model, harness)
            write_json(baseline_file, self.baseline, immutable=True)
        self.proxy = GatewayProxy(base_url, model_config.api_key)
        self.runtime = DshAgentRuntime(
            self.root / "sessions",
            self.content,
            self.trajectories,
            base_url=self.proxy.url,
            api_key="local-proxy",
            timeout=float(os.environ.get("RSI_TURN_TIMEOUT", "240")),
            max_tokens=12288,
        )
        task_runtime = DshAgentRuntime(
            self.root / "sessions",
            self.content,
            self.trajectories,
            base_url=self.proxy.url,
            api_key="local-proxy",
            timeout=float(os.environ.get("RSI_TURN_TIMEOUT", "240")),
            max_tokens=4096,
        )
        configured_directions = json.loads(
            os.environ.get("RSI_RESEARCH_DIRECTIONS", '["通用 Harness 改进"]')
        )
        if not isinstance(configured_directions, list):
            raise ValueError("RSI_RESEARCH_DIRECTIONS 必须为 JSON 数组")
        directions = tuple(configured_directions)
        if not 1 <= len(directions) <= 8 or any(
            not isinstance(d, str) or not d.strip() for d in directions
        ):
            raise ValueError("RSI_RESEARCH_DIRECTIONS 必须为 1 到 8 个非空方向的 JSON 数组")
        self.rsi = DefaultRsiAgent(
            self.runtime,
            self.baseline.model,
            self.baseline.solution_id,
            benchmark.name,
            benchmark_description=description,
            research_directions=directions,
            workflow_name=workflow_name,
        )
        researchers = tuple(
            DefaultResearchAgent(
                f"research-agent-{index}",
                self.runtime,
                self.baseline.model,
                self.content,
                self.memory,
                self.versions,
                self.root / "research-workspaces",
                direction=direction,
            )
            for index, direction in enumerate(directions, 1)
        )
        harness_runtime = LocalHarnessRuntime(
            task_runtime, self.content, self.root / "task-workspaces"
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
            evaluation,
            builder,
        )
        self.service = LocalExperimentService(self.root, context, emit, workflows=workflows)
        return self

    async def close(self) -> None:
        await self.rsi.close()
        for run_id, task in self.service.tasks.items():
            if not task.done():
                await self.service.stop(run_id)
        # 请求记录用于审计实际模型、token usage 和返回值，模型工作区不能读此目录。
        write_json(self.root / "gateway" / f"{new_id('process')}.json", self.proxy.requests)
        self.proxy.close()
