"""RSI Agent：对话、实验计划与研究线剪枝。"""

from __future__ import annotations

import json
from collections.abc import Sequence

from areal_rsi.agents.common import ask_json
from areal_rsi.api.base import (
    AgentSpec,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    ModelVersion,
    RunState,
)
from areal_rsi.modules.runtime import DshAgentRuntime, DshSession


class DefaultRsiAgent:
    def __init__(
        self,
        runtime: DshAgentRuntime,
        model: ModelVersion,
        baseline: str,
        benchmark: str,
        *,
        benchmark_description: str = "用户配置的 Benchmark",
        research_directions: tuple[str, ...] = ("通用 Harness 改进",),
        workflow_name: str = "harness-evolution",
        evolution_target: EvolutionTarget = EvolutionTarget.HARNESS,
    ):
        self.runtime = runtime
        self.model = model
        self.baseline = baseline
        self.benchmark = benchmark
        self.benchmark_description = benchmark_description
        self.research_directions = research_directions
        self.workflow_name = workflow_name
        self.evolution_target = evolution_target
        self.session: DshSession | None = None

    async def chat(self, message: str) -> str | ExperimentPlan:
        if self.session is None:
            target_label = self.evolution_target.value
            prompt = await self.runtime.content.put_text(
                "你是 RSI 研发入口，使用中文自然对话。所有回复为 JSON。"
                '普通对话返回 {"reply":"回复"}；用户明确要求启动当前配置的自迭代实验且给出轮数时，'
                '返回 {"plan":{"goal":"用户目标","max_iterations":整数}}。'
                "不确定轮数时询问用户。不要声称已经执行实验；计划会由 TUI 展示确认。"
                f"当前配置的优化对象是 {target_label}，benchmark={self.benchmark}，"
                f"模型={self.model.name}。"
                f"配置了 {len(self.research_directions)} 条独立研究线。"
                f"Benchmark 说明：{self.benchmark_description}"
            )
            self.session = await self.runtime.open(
                AgentSpec(
                    "rsi-agent",
                    "deepseek-harness@v1",
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
                research_agent_count=len(self.research_directions),
                stop_when_goal_reached=False,
                config={"research_directions": list(self.research_directions)},
            )
        return str(data.get("reply", "请描述你想优化的目标。"))

    async def prune(self, run: ExperimentRun) -> Sequence[str]:
        active = [a for a in run.research_agents if a.state == RunState.RUNNING]
        if len(active) < 2:
            return ()
        prompt = await self.runtime.content.put_text(
            "你是 RSI Agent 的外层研究控制器。根据多条研究线的聚合进展，决定是否停止"
            "重复、缺乏依据的研究方向。分数持平不等于失败；证据不足时保留探索。"
            "只能停止输入中仍在运行的 agent_id；不要虚构提升，不要修改评分。"
            '只返回 JSON {"stop_agent_ids":[],"reason":"简短理由"}。'
        )
        session = await self.runtime.open(
            AgentSpec("rsi-controller", "deepseek-harness@v1", self.model, (prompt,))
        )
        try:
            answer = await ask_json(
                session,
                json.dumps(
                    {
                        "goal": run.plan.goal,
                        "directions": run.plan.config.get("research_directions", []),
                        "agents": [
                            {
                                "agent_id": a.agent_id,
                                "iterations": [
                                    {
                                        "iteration": i.iteration,
                                        "score": i.validation_report.score,
                                        "reason": i.change.reason,
                                    }
                                    for i in a.iterations[-3:]
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


