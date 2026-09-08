"""ResearchAgent：分析 TRAIN 证据、修改 Harness 与选版。"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from areal_rsi.agents.common import ask_json
from areal_rsi.api.base import (
    AgentSpec,
    ContentRef,
    EvaluationReport,
    EvolutionTarget,
    HarnessChange,
    MemoryRecord,
    ModelVersion,
    ResearchChange,
    SolutionVersion,
)
from areal_rsi.api.modules import AgentSession, ConfiguredAgentRuntime
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


class DefaultResearchAgent:
    def __init__(
        self,
        agent_id: str,
        runtime: ConfiguredAgentRuntime,
        model: ModelVersion,
        content: FileContentStore,
        memory: FileMemory,
        versions: FileVersionManager,
        root: Path,
        *,
        editable_path: Callable[[str], bool] = editable,
        harness_description: str = (
            "由 ARealAgentRuntime 配置选择的 Harness；保持原始输出格式及工具契约"
        ),
    ):
        self.agent_id = agent_id
        self.runtime = runtime
        self.model = model
        self.content = content
        self.memory = memory
        self.versions = versions
        self.root = root
        self.editable_path = editable_path
        self.harness_description = harness_description
        self.last_trajectory: str | None = None

    async def _session(self, system: str, workspace: Path, *, tools: bool = True) -> AgentSession:
        prompt = await self.content.put_text(system)
        return await self.runtime.open(
            AgentSpec(
                self.agent_id,
                "areal-agent-runtime@v1",
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
