"""Mode-specific, structured intake parameters and their server-side validation."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.coordination.policies.collaboration import selected_modules
from areal_rsi.infra.storage.files import decode, encode

PASSK_HIDDEN_LIMITS = (
    "max_model_calls",
    "max_nodes",
    "max_total_tokens",
    "max_verify_calls",
)


def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def brief_tool(mode: str) -> dict[str, Any]:
    selected_modules(mode, ())
    limits: dict[str, Any] = {}
    descriptions = {
        "max_agents": "同时存活的 AI 研究员上限，等待中的研究员也计入；不是实验并发数。",
        "max_wall_seconds": "研究总时限，单位秒，例如 3 小时为 10800；null 表示不限。",
        "max_effective_seconds": "pass@k 每名研究员的有效时间上限，单位秒；null 表示不限。",
        "max_jobs": "并发实验作业上限，与 AI 研究员数量独立；null 表示不限。",
    }
    for name, description in descriptions.items():
        limits[name] = {
            "type": "integer"
            if name == "max_agents"
            else [
                "number" if name in {"max_wall_seconds", "max_effective_seconds"} else "integer",
                "null",
            ],
            "description": description,
            "exclusiveMinimum": 0,
        }
    for name, description in {
        "gpu_ids": "允许使用的 GPU 标识，保留当前约定。",
        "allowed_paths": "允许修改的相对路径模式，保留当前约定；* 表示全部。",
    }.items():
        limits[name] = {"type": "array", "items": {"type": "string"}, "description": description}
    verification = object_schema(
        {
            "metric": {"type": "string", "description": "评测指标名称。"},
            "direction": {"type": "string", "enum": ["maximize", "minimize"]},
            "target": {"type": ["number", "null"], "description": "目标值；没有目标值时为 null。"},
            "description": {"type": "string", "description": "具体评测条件。"},
        }
    )
    description = (
        "启动前更新需求。先读取 get_state，填写结构化参数并保留未修改字段。"
        "用户未指定时限时保持不限，不得自行添加墙钟或有效时间截止；"
        "仅按用户明确要求设置或修改时限。"
        "验证 identity 由后端保留，不在此工具修改。"
        "不支持模型调用次数上限；若用户要求此限制，请说明并确认可用的预算约束。"
    )
    description += (
        "当前为 pass@k：仅提供研究时限、研究员数量和执行资源限制；"
        "token 总量、验证次数及累计节点数由系统保持不限。"
        "若用户要求这些上限，先说明当前不支持并明确预算；"
        "不要擅自忽略用户要求，也不要把冲突上限写进 instructions。"
    )
    return {
        "type": "function",
        "name": "update_brief",
        "description": description,
        "parameters": object_schema(
            {
                "objective": {"type": "string", "description": "研究目标。"},
                "instructions": {"type": "string", "description": "已确认的任务要求与必要原话。"},
                "revision": {"type": "integer", "minimum": 0,
                             "description": "当前 brief 的版本号，原样保留，不得自行增加。"},
                "limits": object_schema(limits),
                "verification": {"anyOf": [verification, {"type": "null"}]},
            }
        ),
        "strict": True,
    }


def updated_brief(mode: str, current: TaskBrief, arguments: dict[str, Any]) -> TaskBrief:
    errors = sorted(
        Draft202012Validator(brief_tool(mode)["parameters"]).iter_errors(arguments),
        key=lambda error: str(list(error.path)),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.path) or "arguments"
        raise ValueError(f"update_brief ({mode}) {location}: {error.message}")
    if arguments["revision"] != current.revision:
        raise ValueError("目标版本已变化，请重新读取当前 brief")
    value = encode(current)
    value.update(arguments)
    value["limits"] = dict(arguments["limits"])
    value["limits"].update(dict.fromkeys(PASSK_HIDDEN_LIMITS))
    if arguments["verification"] is not None:
        value["verification"] = {
            **arguments["verification"],
            "identity": encode(current.verification.identity) if current.verification else {},
        }
    brief: TaskBrief = decode(TaskBrief, value)
    return brief
