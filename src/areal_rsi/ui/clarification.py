"""CLI Interaction Agent: natural-language answers, deterministic Goal authorization."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from areal_rsi.agents.common import ask_json
from areal_rsi.api.base import AgentSpec
from areal_rsi.api.modules import AgentSession
from areal_rsi.base.catalog import LocalModelCatalog
from areal_rsi.base.storage import FileTrajectoryStore, FileVersionManager, encode
from areal_rsi.config import AgentModelSettings
from areal_rsi.goals import GoalService
from areal_rsi.modules.gateway import GatewayProxy
from areal_rsi.modules.runtime_package import PackageAgentRuntime, RuntimeSettings


class GoalClarifier:
    """Only drafts answers. This Agent has no lifecycle or filesystem tools."""

    def __init__(self, service: GoalService):
        self.service = service
        self.runtime: PackageAgentRuntime | None = None
        self.proxy: GatewayProxy | None = None
        self.session: AgentSession | None = None

    async def respond(self, goal_id: str, message: str) -> dict[str, Any]:
        if self.session is None:
            connection = AgentModelSettings.from_environment().resolve("interaction-agent")
            self.proxy = GatewayProxy(
                connection.base_url, connection.api_key, model=connection.model
            )
            content = self.service.content
            versions = FileVersionManager(self.service.root / "versions")
            model = await LocalModelCatalog(self.service.root, versions).model(
                connection.model, await content.put_json(connection.public_identity())
            )
            root = (
                Path(os.environ["RSI_SESSION_ROOT"]).expanduser()
                / hashlib.sha256(str(self.service.root).encode()).hexdigest()[:16]
                if os.environ.get("RSI_SESSION_ROOT")
                else self.service.root / "sessions"
            )
            self.runtime = PackageAgentRuntime(
                root,
                content,
                FileTrajectoryStore(self.service.root / "trajectories", content),
                settings=RuntimeSettings.from_environment(),
                role="rsi-agent",
                base_url=self.proxy.url,
                api_key="local-proxy",
                dsh_root=Path(os.environ.get("RSI_DSH_ROOT", str(self.service.root))),
                max_tokens=4096,
            )
            prompt = await content.put_text(
                "你是 CLI 的 RSI Interaction Agent，使用自然中文澄清生产目标。"
                "将用户自然语言回答转换为 GoalSpec 字段；只返回 JSON "
                '{"questions":["简短问题"],"answers":{已明确的字段}}。每次最多问两个关键问题。'
                "不要要求用户编写 JSON，不臆造质量阈值或预算，完整请求不重复追问。"
                "必须明确 description、target、allowed_paths、invariants、acceptance、budget。"
                "budget 包含 max_iterations、max_seconds、max_retries；"
                "acceptance 包含 metrics 数组（metric、threshold、direction=gte/lte）、"
                "required_checks 数组、verifier_version、final_protocol=independent-holdout-v1。"
                "Harness 固定模型、任务与验收器；Artifact 固定模型、Harness 与其他产物。"
                "现有 benchmark/verifier：arithmetic-v1/arithmetic-v1-verifier，"
                "structured-arithmetic-v1/structured-arithmetic-v1-verifier，"
                "ledger-arithmetic-v1/ledger-arithmetic-v1-verifier，"
                "task-web-v1/task-web-v1-verifier，bookmark-web-v1/bookmark-web-v1-verifier。"
                "Artifact 还需 artifact_path，可讨论适配现有静态网页浏览器契约。"
                "你不能确认或启动 Goal；执行授权由用户确认和确定性服务处理。"
            )
            self.session = await self.runtime.open(
                AgentSpec("interaction-agent", "areal-agent-runtime@v1", model, (prompt,))
            )
        goal = self.service.get(goal_id)
        answer = await ask_json(
            self.session,
            json.dumps(
                {"draft": encode(goal.request), "missing": goal.questions, "message": message},
                ensure_ascii=False,
            ),
        )
        if not isinstance(answer.get("answers", {}), dict) or not isinstance(
            answer.get("questions", []), list
        ):
            raise ValueError("Interaction Agent returned an invalid clarification response")
        self.service.event(
            goal,
            "clarification",
            user_message=re.sub(r"sk-[A-Za-z0-9]{20,}", "[redacted]", message),
            questions=answer.get("questions", []),
            answers=answer.get("answers", {}),
        )
        return answer

    async def close(self) -> None:
        if self.runtime:
            await self.runtime.close()
        if self.proxy:
            import asyncio

            await asyncio.to_thread(self.proxy.close)
