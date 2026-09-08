"""供 DSH 对话入口调用的 MCP stdio 桥；实验由独立 worker 管理。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, StrictBool

from areal_rsi.config import ModelConfigurationError
from areal_rsi.ui.experiments import ExperimentController

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
        for key in ("teacher_model_name", "target_model_name", "interaction_model_name"):
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


def create_server(controller: ExperimentController) -> FastMCP:
    server = FastMCP(
        "areal-rsi",
        instructions=(
            "先用 rsi_plan 生成固定配置的实验计划并展示给用户。"
            "只有用户确认该计划后才能以 confirmed=true 调用 rsi_start；"
            "恢复实验同样需要用户确认。rsi_status 返回聚合状态，实验在后台独立运行。"
        ),
        log_level="WARNING",
    )

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
    )
    async def rsi_plan(goal: Goal, max_iterations: Iterations) -> str:
        """生成待确认计划，不启动实验。只接受目标和轮数；各 Agent 的有效模型、评测集和研究线数量
        来自启动配置，不能通过 goal 改变。teacher/target 只是默认值。
        RSI_AGENT_MODELS 可按 Interaction Agent、RSI Agent 和 ResearchAgent 组覆盖；
        Task Agent 直接使用
        Target Model，不单独配置。所有 ResearchAgent 共用一个模型。请展示返回的完整模型映射；
        不符合用户要求时
        需修改本机配置并重启 TUI。此工具没有实验模板名参数。
        """
        return await _invoke(lambda: controller.plan(goal, max_iterations))

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )
    async def rsi_start(plan_id: Identifier, confirmed: StrictBool) -> str:
        """启动已经生成并向用户展示的计划；confirmed 必须为用户明确确认后的 true。"""
        if confirmed is not True:
            raise ToolError("请先展示计划并取得用户确认，再设置 confirmed=true。")
        return await _invoke(lambda: controller.start(plan_id, confirmed=confirmed))

    @server.tool(
        structured_output=False, annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    )
    async def rsi_status(run_id: Identifier | None = None) -> str:
        """查看指定实验或最近实验的有界聚合状态，不提供完整轨迹或隐藏评测明细。"""
        return await _invoke(lambda: controller.status(run_id))

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
        ),
    )
    async def rsi_stop(run_id: Identifier) -> str:
        """按用户要求停止指定实验 worker，保留已保存的实验状态。"""
        return await _invoke(lambda: controller.stop(run_id))

    @server.tool(
        structured_output=False,
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True),
    )
    async def rsi_resume(run_id: Identifier, confirmed: StrictBool) -> str:
        """恢复指定实验；confirmed 必须为用户明确确认后的 true。"""
        if confirmed is not True:
            raise ToolError("请先取得用户恢复实验的确认，再设置 confirmed=true。")
        return await _invoke(lambda: controller.resume(run_id, confirmed=confirmed))

    return server


def serve(root: Path) -> None:
    """只管理 MCP 连接生命周期；退出时不停止已启动的后台实验。"""
    create_server(ExperimentController(root)).run(transport="stdio")
