"""DSH frontend tools forwarding to the unified workbench HTTP API."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from areal_rsi.adapters.harness_cases import prepare
from areal_rsi.api.research import ResearchLimits, TaskBrief
from areal_rsi.base.storage import new_id
from areal_rsi.config import ModelConfig
from areal_rsi.coordination.config import ResearchConfig, load_config
from areal_rsi.ui.launch import WorkbenchClient


def public_summary(detail: dict[str, Any]) -> dict[str, Any]:
    """The bridge only exposes the workbench's public data, with bounded history."""
    result: dict[str, Any] = {
        k: detail[k]
        for k in (
            "run_id",
            "objective",
            "status",
            "worker_alive",
            "brief",
            "baseline",
            "best",
            "final",
            "budget",
            "waiting_for_user",
            "model_settings",
        )
        if k in detail
    }
    result["messages"] = detail.get("messages", [])[-8:]
    result["agents"] = detail.get("agents", [])[:12]
    result["jobs"] = [
        {k: j.get(k) for k in ("job_id", "status", "phase", "result", "error")}
        for j in detail.get("jobs", [])[-12:]
    ]
    return result


class ResearchBridge:
    def __init__(self, client: WorkbenchClient, root: Path, run_id: str | None = None):
        self.client, self.root, self.run_id = client, root.resolve(), run_id

    async def request(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self.client.request, path, body)

    def selected(self, run_id: str | None) -> str:
        selected = run_id or self.run_id
        if not selected:
            raise ValueError("尚未选择研究，请先准备研究或指定 run_id")
        from areal_rsi.base.storage import safe_name

        return safe_name(selected)

    async def create_config(self, config: ResearchConfig) -> dict[str, Any]:
        config.pin_model_settings()
        # Use one identity for transport retries. Never include secrets in returned summaries.
        run_id = new_id("research")
        result = await self.request(
            "/api/runs",
            {
                "run_id": run_id,
                "config": config.public(),
                "model_settings": {**config.model_settings(), "api_key": config.api_key()},
            },
        )
        self.run_id = result["run_id"]
        return await self.status()

    def summary(self, detail: dict[str, Any]) -> dict[str, Any]:
        return {
            **public_summary(detail),
            "workbench_url": self.client.url + "/?run=" + detail["run_id"],
        }

    async def plan(
        self,
        goal: str,
        *,
        case: str | None = None,
        config_path: str | None = None,
        dataset: str | None = None,
        limit: int = 2,
        max_research_agents: int | None = None,
        instructions: str = "",
    ) -> dict[str, Any]:
        if not goal.strip() or len(goal) > 4000:
            raise ValueError("请提供不超过 4000 字的研究目标")
        if case and config_path:
            raise ValueError("case 和 config_path 二选一")
        if config_path:
            config = load_config(Path(config_path).expanduser().resolve())
            config.brief.objective = goal
            if max_research_agents is not None:
                config.brief.limits.max_research_agents = max_research_agents
                config.brief.limits.__post_init__()
        elif case:
            if case not in {"arithmetic", "gsm8k", "lcb"}:
                raise ValueError("case 支持 arithmetic、gsm8k、lcb")
            config = await prepare(
                case,
                self.root / "setups" / new_id("setup") / "research.json",
                dataset=Path(dataset).expanduser()
                if dataset
                else Path.cwd() / ".rsi/datasets/gsm8k",
                limit=limit,
                max_research_agents=max_research_agents if max_research_agents is not None else 1,
                remote_config=Path(__file__).parents[1] / "resources/presets/livecodebench.yaml",
            )
            config.brief.objective = goal
        else:
            model = ModelConfig.from_environment()
            config = ResearchConfig(
                TaskBrief(
                    goal,
                    "",
                    ResearchLimits(
                        max_research_agents=max_research_agents
                        if max_research_agents is not None
                        else 1
                    ),
                ),
                model=model.model,
                base_url=model.base_url,
                api_key_env=ModelConfig.environment_prefix("teacher") + "API_KEY",
                model_settings_pinned=True,
            )
        if instructions:
            config.brief.instructions += "\n用户要求：\n" + instructions
        return await self.create_config(config)

    async def status(self, run_id: str | None = None) -> dict[str, Any]:
        if not (run_id or self.run_id):
            result = await self.request("/api/runs")
            return {"runs": result["runs"][:20]}
        return self.summary(await self.request("/api/runs/" + self.selected(run_id)))

    async def control(self, action: str, run_id: str | None = None) -> dict[str, Any]:
        if action not in {"start", "pause", "resume", "stop"}:
            raise ValueError("不支持这个操作")
        selected = self.selected(run_id)
        await self.request(f"/api/runs/{selected}/{action}", {})
        self.run_id = selected
        return await self.status()

    async def message(
        self,
        text: str,
        *,
        run_id: str | None = None,
        message_id: str | None = None,
        reply_to: str | None = None,
    ) -> dict[str, Any]:
        selected = self.selected(run_id)
        body: dict[str, Any] = {"text": text, "message_id": message_id or new_id("user")}
        if reply_to:
            body["reply_to"] = reply_to
        else:
            body["recipient"] = "rsi"
        return await self.request(f"/api/runs/{selected}/chat", body)


def create_server(bridge: ResearchBridge) -> MCPServer:
    server = MCPServer(
        "areal-rsi",
        instructions=(
            "以自然对话理解用户需求，用 rsi_plan 准备研究；用户要求开始时 rsi_start。"
            "rsi_status 查询当前绑定研究，rsi_message 将用户要求交给后台 RSI。"
            "所有工具操作同一个 ResearchApp，不运行旧固定工作流。"
        ),
        log_level="WARNING",
    )

    async def invoke(operation: Any) -> str:
        try:
            result = await operation
            text = json.dumps(result, ensure_ascii=False)
            if len(text) > 24000:
                # Keep a valid JSON envelope even when a report is very long.
                result.pop("messages", None)
                result.pop("jobs", None)
                text = json.dumps(result, ensure_ascii=False)
            return text
        except (OSError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except Exception:
            raise ToolError("研究操作失败，请检查工作台日志；不会显示凭据或原始服务响应") from None

    @server.tool(structured_output=False, annotations=ToolAnnotations(read_only_hint=False))
    async def rsi_plan(
        goal: str,
        case: Literal["arithmetic", "gsm8k", "lcb"] | None = None,
        config_path: str | None = None,
        dataset: str | None = None,
        limit: int = 2,
        max_research_agents: int | None = None,
        instructions: str = "",
    ) -> str:
        """准备研究并返回 run_id、目标、范围、预算，不启动模型研究。case 或 config_path 二选一。"""
        return await invoke(
            bridge.plan(
                goal,
                case=case,
                config_path=config_path,
                dataset=dataset,
                limit=limit,
                max_research_agents=max_research_agents,
                instructions=instructions,
            )
        )

    @server.tool(structured_output=False, annotations=ToolAnnotations(read_only_hint=True))
    async def rsi_status(run_id: str | None = None) -> str:
        """查看绑定研究的真实状态、成绩、消息与工作台链接；没有绑定时列出研究。"""
        return await invoke(bridge.status(run_id))

    @server.tool(structured_output=False)
    async def rsi_start(run_id: str | None = None) -> str:
        """按用户开始研究的要求启动已准备的研究；省略 run_id 使用当前绑定研究。"""
        return await invoke(bridge.control("start", run_id))

    @server.tool(structured_output=False)
    async def rsi_control(
        action: Literal["pause", "resume", "stop"], run_id: str | None = None
    ) -> str:
        """按用户要求暂停、恢复或停止研究，保留记录。"""
        return await invoke(bridge.control(action, run_id))

    @server.tool(structured_output=False)
    async def rsi_message(
        text: str,
        run_id: str | None = None,
        message_id: str | None = None,
        reply_to: str | None = None,
    ) -> str:
        """将用户补充发到已启动研究的 RSI 会话；回复问题时指定 reply_to，重试保留 message_id。"""
        return await invoke(
            bridge.message(text, run_id=run_id, message_id=message_id, reply_to=reply_to)
        )

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--run")
    args = parser.parse_args()
    client = WorkbenchClient(args.port)
    info = client.bootstrap()
    if Path(info["data_root"]).resolve() != args.root.resolve():
        raise ValueError("MCP 与工作台数据目录不一致")
    create_server(ResearchBridge(client, args.root, args.run)).run(transport="stdio")


if __name__ == "__main__":
    main()
