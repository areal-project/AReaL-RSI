"""入口和 ResearchAgent：通过真实 DSH Session 生成计划、修改与选版。"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from areal_rsi.api.base import (
    AgentSpec,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    HarnessChange,
    MemoryRecord,
    ModelVersion,
    ResearchChange,
    RunState,
    SolutionVersion,
)
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileVersionManager,
    decode,
    encode,
    new_id,
    write_json,
)
from areal_rsi.modules.harness import editable
from areal_rsi.modules.runtime import DshAgentRuntime, DshSession


def parse_object(text: str) -> dict[str, Any]:
    """接收模型最终 JSON 或 Markdown JSON 块，不修改其中的业务结论。"""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型未返回 JSON 对象")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型返回值必须为对象")
    return value


async def ask_json(session: DshSession, prompt: str) -> dict[str, Any]:
    reply = await session.send(prompt)
    try:
        return parse_object(reply)
    except (ValueError, json.JSONDecodeError):
        # 格式重试不补充答案、修改建议或评测数据。
        return parse_object(
            await session.send("请只返回前述要求的合法 JSON 对象，无代码块或额外文本。")
        )


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


class DefaultResearchAgent:
    def __init__(
        self,
        agent_id: str,
        runtime: DshAgentRuntime,
        model: ModelVersion,
        content: FileContentStore,
        memory: FileMemory,
        versions: FileVersionManager,
        root: Path,
        *,
        direction: str = "通用 Harness 改进",
        editable_path: Callable[[str], bool] = editable,
        harness_description: str = "DeepSeek Harness；保持原始输出格式及工具契约",
    ):
        self.agent_id = agent_id
        self.runtime = runtime
        self.model = model
        self.content = content
        self.memory = memory
        self.versions = versions
        self.root = root
        self.direction = direction
        self.editable_path = editable_path
        self.harness_description = harness_description
        self.last_trajectory: str | None = None

    async def _session(self, system: str, workspace: Path, *, tools: bool = True) -> DshSession:
        prompt = await self.content.put_text(system)
        return await self.runtime.open(
            AgentSpec(
                self.agent_id,
                "deepseek-harness@v1",
                self.model,
                (prompt,),
                tools=("bash@v1",) if tools else (),
            ),
            workspace_root=str(workspace),
        )

    async def propose(
        self,
        goal: str,
        target: EvolutionTarget,
        current: SolutionVersion,
        report: EvaluationReport,
        trajectories: ContentRef,
    ) -> ResearchChange:
        if target != EvolutionTarget.HARNESS or report.solution_id != current.solution_id:
            raise ValueError("当前实现只支持同一 Solution 的 Harness 提案")
        if report.split.value != "train":
            raise ValueError("ResearchAgent 只接收 TRAIN 证据")
        workspace = self.root / new_id("research")
        workspace.mkdir(parents=True)
        await self.content.materialize(current.harness.content, workspace / "harness")
        manifest = json.loads(await self.content.read_text(trajectories))
        train = workspace / "train"
        train.mkdir()
        write_json(train / "report.json", report)
        summaries = []
        for index, entry in enumerate(manifest["trajectories"]):
            data = await self.content.read_text(decode(ContentRef, entry["content"]))
            (train / f"trajectory_{index}.json").write_text(data)
            trace = json.loads(data)
            summaries.append(
                {
                    "trajectory_id": entry["trajectory_id"],
                    "raw_file": f"trajectory_{index}.json",
                    "turns": [
                        {"role": t.get("role"), "content": str(t.get("content", ""))[:2500]}
                        for t in trace.get("turns", [])[-8:]
                        if t.get("role") in ("user", "assistant")
                    ],
                }
            )
        write_json(train / "index.json", summaries)
        # Memory 工具按需检索的输入只是已有研究经验，不包含 VALIDATE 明细。
        write_json(workspace / "memory.json", self.memory.records.list(MemoryRecord))
        (workspace / "query.py").write_text(
            "import json,sys\n"
            "items=json.load(open('memory.json'))\n"
            "q=' '.join(sys.argv[1:]).lower()\n"
            "print(json.dumps([x for x in items if any(t in x['text'].lower() "
            "for t in q.split())][-5:],ensure_ascii=False)[:6000])\n"
        )
        system = (
            "You are a ResearchAgent improving a reusable user Harness. "
            "Read harness/prompt.md and train/index.json in one Bash call before proposing. "
            "The index already contains the real task questions and final responses; "
            "read raw event files only if the index omits necessary information. "
            "Use at most three short tool calls, then return the proposed JSON. "
            "Keep reasoning and the proposed instructions concise. "
            "Read bounded portions (head/sed); do not dump whole trajectories. "
            "If needed, query previous experience with `python query.py <keywords>`. "
            "Only improve general reasoning, verification workflow, skill, tool, prompt, or MCP. "
            "Never insert benchmark-specific questions, IDs, numeric answers, or answer tables. "
            "Do not add any new worked example with numeric literals. "
            "Use symbolic examples such as a - b * c if an example is needed. "
            "Do not read external benchmarks or change scoring or model. "
            "Return exactly one logical change as JSON: "
            '{"reason":"brief rationale", "files":{"prompt.md":"complete new file text"}}. '
            "Only include changed files, paths relative to harness/. "
            "The allowed files are prompt.md, skills/*.md, tools/*.py and mcp.json. "
            "Preserve the original task output contract. "
            "Even if accuracy is already perfect, propose a justified general improvement; "
            "do not deliberately weaken the baseline. No guaranteed score increase is expected."
        )
        session = await self._session(system, workspace)
        try:
            proposal = await ask_json(
                session,
                json.dumps(
                    {
                        "goal": goal,
                        "research_direction": self.direction,
                        "harness_description": self.harness_description,
                        "report": encode(report),
                        "harness": "harness/",
                        "evidence": "train/",
                        "memory_tool": "query.py",
                    },
                    ensure_ascii=False,
                ),
            )
        finally:
            self.last_trajectory = (await session.finish()).trajectory_id
        changes = proposal.get("files")
        if not isinstance(changes, dict) or not 1 <= len(changes) <= 8:
            raise ValueError("提案必须包含 1 到 8 个文件编辑")
        patches: list[str] = []
        for name, new_text in changes.items():
            if (
                not self.editable_path(name)
                or not isinstance(new_text, str)
                or len(new_text) > 100_000
            ):
                raise ValueError("非法 Harness 编辑")
            # 从存储快照读取旧文本，防止模型调用 Shell 意外改了工作副本。
            snapshot = json.loads(await self.content.read_text(current.harness.content))["files"]
            old_text = (
                await self.content.read_text(decode(ContentRef, snapshot[name]))
                if name in snapshot
                else ""
            )
            if name == "prompt.md" or name.startswith("skills/"):
                numeric_examples = re.findall(r"\d+\s*[-+*/]\s*\d+|####\s*[-+]?\d+", new_text)
                if any(example not in old_text for example in numeric_examples):
                    raise ValueError("拒绝新增题目数值示例或固定答案；请使用通用符号表达")
            if new_text and not new_text.endswith("\n"):
                new_text += "\n"
            patches.extend(
                difflib.unified_diff(
                    old_text.splitlines(keepends=True),
                    new_text.splitlines(keepends=True),
                    fromfile=f"a/{name}" if name in snapshot else "/dev/null",
                    tofile=f"b/{name}",
                )
            )
        if not patches:
            raise ValueError("ResearchAgent 没有提出实际修改")
        reason = str(proposal.get("reason", ""))[:4000]
        await self.memory.add(
            reason, source=f"trajectory:{self.last_trajectory}", tags=("harness",)
        )
        return HarnessChange(await self.content.put_text("".join(patches)), reason)

    async def select(
        self, current_solution_id: str, candidates: Sequence[EvaluationReport]
    ) -> str | None:
        if not candidates or any(r.split.value != "validate" for r in candidates):
            raise ValueError("选版只接收历史 VALIDATE 聚合结果")
        workspace = self.root / new_id("selection")
        workspace.mkdir(parents=True)
        versions = [encode(await self.versions.get_solution(r.solution_id)) for r in candidates]
        write_json(workspace / "versions.json", versions)
        session = await self._session(
            "Choose the next version for a continuing research experiment. "
            "Prefer the highest observed validation score; ties may explore the latest candidate. "
            "All inputs are aggregate results. No case details are available. "
            "Versions can be read from versions.json if useful. "
            'Return JSON {"candidate_index": integer} using the explicit zero-based index. '
            'Use {"candidate_index": null} only to end this research line. '
            "Do not copy long version IDs into the reply; "
            "the runtime maps the chosen index exactly. "
            "The workflow enforces iteration limits; continue exploration when a candidate exists.",
            workspace,
        )
        try:
            selected = await ask_json(
                session,
                json.dumps(
                    {
                        "current_solution_id": current_solution_id,
                        "candidates": [
                            {"candidate_index": index, "report": encode(report)}
                            for index, report in enumerate(candidates)
                        ],
                    }
                ),
            )

            def valid(value: dict[str, Any]) -> bool:
                index = value.get("candidate_index")
                return "candidate_index" in value and (
                    index is None or (type(index) is int and 0 <= index < len(candidates))
                )

            if not valid(selected):
                selected = await ask_json(
                    session,
                    json.dumps(
                        {
                            "error": "Invalid candidate_index; choose an allowed index or null.",
                            "allowed_indices": list(range(len(candidates))),
                            "output_format": {"candidate_index": "integer or null"},
                        }
                    ),
                )
            if not valid(selected):
                raise ValueError("选版返回了无效候选编号")
            index = selected["candidate_index"]
            identifier = None if index is None else candidates[index].solution_id
        finally:
            self.last_trajectory = (await session.finish()).trajectory_id
        return identifier
