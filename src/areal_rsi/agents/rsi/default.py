"""RSI Agent：对话、实验计划与研究线剪枝。"""

from __future__ import annotations

import json
from collections.abc import Sequence

from areal_rsi.agents.common import ask_json
from areal_rsi.api.base import (
    AgentSpec,
    AgentState,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    JsonObject,
    ModelVersion,
)
from areal_rsi.api.modules import AgentSession, ConfiguredAgentRuntime
from areal_rsi.base.runs import FileRunStore
from areal_rsi.prompts import load_prompt
from areal_rsi.workflows.records import evolution_history


class DefaultRsiAgent:
    def __init__(
        self,
        runtime: ConfiguredAgentRuntime,
        model: ModelVersion,
        baseline: str,
        benchmark: str,
        *,
        benchmark_description: str = "用户配置的 Benchmark",
        research_agent_count: int = 1,
        workflow_name: str = "harness-evolution",
        evolution_target: EvolutionTarget = EvolutionTarget.HARNESS,
        target_model_name: str | None = None,
        model_plan_config: JsonObject | None = None,
        run_store: FileRunStore | None = None,
    ):
        self.runtime = runtime
        self.run_store = run_store
        self.model = model
        self.target_model_name = target_model_name
        self.model_plan_config = model_plan_config or {
            "rsi_model_name": model.name,
            "target_model_name": target_model_name,
        }
        self.baseline = baseline
        self.benchmark = benchmark
        self.benchmark_description = benchmark_description
        self.research_agent_count = research_agent_count
        self.workflow_name = workflow_name
        self.evolution_target = evolution_target
        self.session: AgentSession | None = None

    async def chat(self, message: str) -> str | ExperimentPlan:
        if self.session is None:
            target_label = self.evolution_target.value
            prompt = await self.runtime.content.put_text(
                load_prompt(
                    "agents/rsi/evolution_intake.md",
                    target_label=str(target_label),
                    benchmark=str(self.benchmark),
                    model=str(self.model.name),
                    target_model=str(self.target_model_name or "由实验基线确定"),
                    research_agent_count=str(self.research_agent_count),
                    benchmark_description=str(self.benchmark_description),
                )
            )
            self.session = await self.runtime.open(
                AgentSpec(
                    "rsi-agent",
                    "areal-agent-runtime@v1",
                    self.model,
                    (prompt,),
                )
            )
        data = await ask_json(self.session, message)
        if "plan" in data:
            plan = data["plan"]
            iterations = plan["max_iterations"]
            if type(iterations) is not int or not 1 <= iterations <= 20:
                raise ValueError("MVP 每次实验支持 1 到 20 轮")
            return ExperimentPlan(
                str(plan["goal"]),
                self.workflow_name,
                self.benchmark,
                self.baseline,
                self.evolution_target,
                iterations,
                research_agent_count=self.research_agent_count,
                stop_when_goal_reached=False,
                config={
                    **self.model_plan_config,
                },
            )
        return str(data.get("reply", "请描述你想优化的目标。"))

    async def prune(self, run: ExperimentRun) -> Sequence[str]:
        active = [a for a in run.research_agents if a.state == AgentState.RUNNING]
        if len(active) < 2:
            return ()
        if self.run_store is None:
            raise ValueError("RSI 剪枝需要所属 Run 的记录存储")
        prompt = await self.runtime.content.put_text(load_prompt("agents/rsi/evolution_prune.md"))
        session = await self.runtime.open(
            AgentSpec("rsi-agent", "areal-agent-runtime@v1", self.model, (prompt,))
        )
        try:
            answer = await ask_json(
                session,
                json.dumps(
                    {
                        "goal": run.plan.goal,
                        "agents": [
                            {
                                "agent_id": a.agent_id,
                                "iterations": [
                                    {
                                        "iteration": i.iteration,
                                        "score": i.validation_report.score,
                                        "reason": i.change.reason,
                                    }
                                    for i in evolution_history(self.run_store, a)[-3:]
                                ],
                            }
                            for a in active
                        ],
                    },
                    ensure_ascii=False,
                ),
            )
        finally:
            await session.finish()
        stopped = answer.get("stop_agent_ids")
        if not isinstance(stopped, list) or any(
            not isinstance(x, str) or x not in {a.agent_id for a in active} for x in stopped
        ):
            raise ValueError("RSI Agent 返回非法剪枝对象")
        return tuple(stopped)

    async def close(self) -> None:
        if self.session:
            await self.session.finish()
