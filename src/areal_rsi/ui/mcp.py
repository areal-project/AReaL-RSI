"""供 DSH 对话入口调用的 MCP stdio 桥；实验由独立 worker 管理。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, StrictBool

from areal_rsi.config import ModelConfigurationError
from areal_rsi.ui.experiments import ExperimentController
from areal_rsi.ui.html_scores import create_html_report
from areal_rsi.ui.workspace import ExperimentWorkspace

Goal = Annotated[str, Field(strict=True, min_length=1, max_length=4000)]
Iterations = Annotated[int, Field(strict=True, ge=1, le=20)]
Identifier = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,128}$")]


def _agent_models(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    return {
        agent_id: {"model": entry["model"][:500]}
        for agent_id, entry in list(value.items())[:12]
        if isinstance(agent_id, str)
        and len(agent_id) <= 128
        and isinstance(entry, dict)
        and isinstance(entry.get("model"), str)
    }


def _summary(value: dict[str, Any]) -> dict[str, Any]:
    """只输出公开计划和聚合状态，新增控制器字段不会自动泄漏到对话。"""
    fields = {
        "plan_id",
        "confirmation_required",
        "run_id",
        "state",
        "goal",
        "workflow",
        "target",
        "benchmark",
        "mode",
        "research_harness",
        "teacher_model_name",
        "target_model_name",
        "interaction_model_name",
        "best_solution_id",
        "completed_iterations",
        "max_iterations",
        "research_agent_count",
        "started_at",
        "finished_at",
        "stop_requested",
        "error",
    }
    result: dict[str, Any] = {
        key: item[:1000] if isinstance(item, str) else item
        for key, item in value.items()
        if key in fields and not isinstance(item, (dict, list))
    }
    if "agent_models" in value:
        result["agent_models"] = _agent_models(value["agent_models"])
    if isinstance(value.get("plan"), dict):
        plan = value["plan"]
        result["plan"] = {
            key: plan[key]
            for key in (
                "goal",
                "workflow",
                "benchmark",
                "baseline_solution_id",
                "target",
                "max_iterations",
                "research_agent_count",
                "stop_when_goal_reached",
            )
            if key in plan
        }
        config = plan.get("config", {})
        for key in (
            "teacher_model_name",
            "target_model_name",
            "interaction_model_name",
            "research_harness",
            "mode",
            "remote_backend",
        ):
            model = config.get(key)
            if key == "target_model_name" and model is None:
                model = config.get("model_name")  # 兼容历史单模型计划。
            if isinstance(model, str):
                result["plan"].setdefault("config", {})[key] = model[:500]
        if "agent_models" in config:
            result["plan"].setdefault("config", {})["agent_models"] = _agent_models(
                config["agent_models"]
            )
    if isinstance(value.get("worker"), dict):
        result["worker"] = {
            key: item
            for key, item in value["worker"].items()
            if key in {"pid", "active", "status", "log_path"}
        }
    if isinstance(value.get("runs"), list):
        result["runs"] = [_summary(run) for run in value["runs"][:20] if isinstance(run, dict)]
    return result


async def _invoke(operation: Callable[[], Awaitable[dict[str, Any]]]) -> str:
    try:
        result = _summary(await operation())
        text = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(text) > 24_000:
            raise ToolError("摘要过长，请使用 rsi_status 并指定一个 run_id。")
        return text
    except ToolError:
        raise
    except KeyError:
        raise ToolError("找不到计划或实验，请先调用 rsi_plan 或 rsi_status。") from None
    except ModelConfigurationError as exc:
        # 此异常仅包含后端定义的配置提示，不回显地址、凭证或原始响应。
        raise ToolError(str(exc)) from None
    except ValueError:
        raise ToolError("参数或当前状态不允许此操作，请核对计划、实验状态及固定配置。") from None
    except Exception:
        # 不把底层 HTTP 响应、环境变量或原始异常内容转发给模型。
        raise ToolError("实验操作失败，请检查本地配置或实验日志。") from None


def create_server(controller: ExperimentController | ExperimentWorkspace) -> MCPServer:
    server = MCPServer(
        "areal-rsi",
        instructions=(
            "用户明确要求开始优化 PAST-Bench 时，直接用 rsi_research_start，"
            "这是已配置的新版研究入口，无需再次确认；仅询问或讨论时不要启动。"
            "其他旧版实验先用 rsi_plan 生成固定配置的实验计划并展示给用户。"
            "只有用户确认该计划后才能以 confirmed=true 调用 rsi_start；"
            "恢复实验同样需要用户确认。rsi_status 返回聚合状态，实验在后台独立运行。"
            "用户要求画图、可视化或刷新跑分时调用 rsi_visualize 生成 HTML 报告，"
            "只返回摘要和报告链接，不在聊天中画字符图。"
        ),
        log_level="WARNING",
    )

    from areal_rsi.ui.research_bridge import ResearchBridge

    research = ResearchBridge(controller.root)

    async def research_call(operation: Callable[[], dict[str, Any]]) -> str:
        try:
            return json.dumps(await asyncio.to_thread(operation), ensure_ascii=False)
        except Exception:
            # Upstream errors may include model credentials or private traces.
            raise ToolError(
                "研究操作未完成。检查 Docker、RSI_PAST_BENCH_CONFIG 指向的配置、协议锁和模型凭证；"
                "配置步骤见 examples/past_bench/README.md。启动重试必须复用原 request_id。"
            ) from None

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        ),
    )
    async def rsi_research_start(goal: Goal, request_id: Identifier) -> str:
        """用户明确要求开始优化 Task Agent 在 PAST-Bench 上的表现时直接调用。

        使用操作者预配置的小规模真实研究，优化 prompt.md，先 baseline 后候选搜索和 final。
        不需要再问轮数或确认。仅询问、解释或要计划时不要调用。
        每个新用户请求生成唯一 request_id（字母数字下划线）；同一请求重试必须复用它，
        防止重复启动。返回 run_id 仅表示提交，使用 rsi_research_status 查看真实进度。
        """
        return await research_call(lambda: research.start(goal, request_id))

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    async def rsi_research_status(run_id: Identifier) -> str:
        """查看新版 PAST-Bench 研究的真实 baseline、best、final 和作业状态。"""
        return await research_call(lambda: research.control(run_id))

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=True
        ),
    )
    async def rsi_research_control(
        run_id: Identifier, action: Literal["pause", "resume", "stop"]
    ) -> str:
        """用户要求时暂停、继续或停止新版研究。pause 不强杀运行中的评测，stop 会取消作业。"""
        return await research_call(lambda: research.control(run_id, action))

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=False
        ),
    )
    async def rsi_plan(
        goal: Goal,
        max_iterations: Iterations,
        preset: Literal["arithmetic", "lcb", "tb21"] | None = None,
        config_path: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
        | None = None,
    ) -> str:
        """生成待确认计划，不启动实验。preset=arithmetic 是 local 算术 demo，preset=lcb 是
        remote EnvArena LCB demo，preset=tb21 是固定 gpt-5-6-sol-4ipic@1.0.0 与
        terminus-2-tb21@1.0.10 的 EnvArena TB2.1 优化；省略时使用 arithmetic。也可用
        config_path 选择项目中的实验 YAML，与 preset 二选一；mode 从 YAML 读取。同一 TUI
        可创建多种模式的计划，无需重启。
        实验 YAML 与 Runtime 配置按计划保存快照。请展示 mode、研究 Harness、所有 Agent 模型、
        Benchmark、研究线数量和轮数，再等待用户确认。模型连接与凭证仍来自宿主环境。
        """
        if isinstance(controller, ExperimentWorkspace):
            return await _invoke(
                lambda: controller.plan(
                    goal, max_iterations, preset=preset, config_path=config_path
                )
            )
        if preset or config_path:
            raise ToolError("此旧控制器不支持选择实验配置，请使用 rsi tui。")
        return await _invoke(lambda: controller.plan(goal, max_iterations))

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        ),
    )
    async def rsi_start(plan_id: Identifier, confirmed: StrictBool) -> str:
        """启动已经生成并向用户展示的计划；confirmed 必须为用户明确确认后的 true。"""
        if confirmed is not True:
            raise ToolError("请先展示计划并取得用户确认，再设置 confirmed=true。")
        return await _invoke(lambda: controller.start(plan_id, confirmed=confirmed))

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    async def rsi_status(run_id: Identifier | None = None) -> str:
        """查看指定实验或最近实验的有界聚合状态，不提供完整轨迹或隐藏评测明细。"""
        return await _invoke(lambda: controller.status(run_id))

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=False
        ),
    )
    async def rsi_visualize(
        run_id: Identifier | None = None,
        query: Annotated[str, Field(strict=True, max_length=200)] = "",
        scope: Literal["recent", "all", "running"] = "recent",
        limit: Annotated[int, Field(strict=True, ge=1, le=6)] = 4,
        offset: Annotated[int, Field(strict=True, ge=0)] = 0,
        open_browser: StrictBool = True,
    ) -> str:
        """生成包含真实折线图的离线 HTML 报告，默认自动请求浏览器打开。

        只返回简短摘要和 report_url 链接，不在聊天框画字符图，也不要求启动 Web 服务。
        open_browser=false 只生成文件，适合无桌面环境；打开失败仍返回文件路径。
        相同筛选重新调用会更新同一报告文件。优先传入对话中的当前 run_id；
        无上下文时省略以查看最近实验。历史和当前实验一起看用 scope=all；
        仅看确认持有执行锁的 RSI 实验用 scope=running。query 可按目录、Benchmark、
        目标文本筛选（Terminal Bench 可用 tb）；offset 翻页。自动发现历史工作区，
        支持独立 TB2.1 campaign；不同评分口径分开展示。刷新时再次调用；不启动评测。
        """
        try:
            return create_html_report(
                controller.root,
                run_id,
                query=query,
                scope=scope,
                limit=limit,
                offset=offset,
                open_browser=open_browser,
            )
        except FileNotFoundError:
            raise ToolError("找不到实验，请用 rsi_status 查看可用的 run_id。") from None
        except Exception:
            raise ToolError("无法读取跑分，请检查本地实验记录。") from None

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
        ),
    )
    async def rsi_stop(run_id: Identifier) -> str:
        """按用户要求停止指定实验 worker，保留已保存的实验状态。"""
        return await _invoke(lambda: controller.stop(run_id))

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, open_world_hint=True
        ),
    )
    async def rsi_resume(run_id: Identifier, confirmed: StrictBool) -> str:
        """恢复指定实验；confirmed 必须为用户明确确认后的 true。"""
        if confirmed is not True:
            raise ToolError("请先取得用户恢复实验的确认，再设置 confirmed=true。")
        return await _invoke(lambda: controller.resume(run_id, confirmed=confirmed))

    return server


def serve(root: Path) -> None:
    """只管理 MCP 连接生命周期；退出时不停止已启动的后台实验。"""
    create_server(ExperimentWorkspace(root)).run(transport="stdio")
