"""Versioned collaboration recipes, independent of model and execution backends.

Modules are code registered here, never arbitrary imports from run configuration.
An installed module transforms the actions, scheduler and prompts together.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from areal_rsi.coordination.actions import ResearchActions
    from areal_rsi.coordination.engine import ResearchEngine


@dataclass(frozen=True)
class CollaborationPlan:
    actions: type[ResearchActions]
    engine: type[ResearchEngine]
    research_prompt: str
    rsi_prompt: str
    supervisor_only: bool = False


@dataclass(frozen=True)
class CollaborationModule:
    id: str
    label: str
    description: str
    version: str
    apply: Callable[[CollaborationPlan], CollaborationPlan]
    modes: tuple[str, ...] = ("passk",)
    requires: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


MODES = {
    "passk": {
        "id": "passk",
        "label": "纯 pass@k",
        "version": "2",
        "description": "每人独立完成整题；RSI 只启动和督促继续，不指导、不共享。"
        "使用时间或目标停止，不限制模型调用、Token 和验证次数。",
    },
    "collaborative": {
        "id": "collaborative",
        "label": "旧版协作流程",
        "version": "1",
        "description": "沿用原有 leader 分工、消息交流、报告与共享证据的完整流程。"
        "这是旧流程对照，不是 pass@k 的单模块消融。",
    },
}


def apply_leader_guidance(plan: CollaborationPlan) -> CollaborationPlan:
    from areal_rsi.coordination.leader_guidance import apply

    return apply(plan)


MODULES: dict[str, CollaborationModule] = {
    "leader_guidance": CollaborationModule(
        "leader_guidance",
        "RSI 协调",
        "RSI 综合进展并定向指导；同一 run 按需查阅研究资料，各自完成整题并验证。",
        "3",
        apply_leader_guidance,
    ),
}


def presets() -> list[dict[str, Any]]:
    choices = [{**mode, "mode": mode["id"], "modules": []} for mode in MODES.values()]
    choices.insert(
        1,
        {
            "id": "passk_guided",
            "label": "pass@k + RSI 协调",
            "mode": "passk",
            "modules": ["leader_guidance"],
            "description": MODULES["leader_guidance"].description,
        },
    )
    return choices


def selected_modules(mode: str, modules: tuple[str, ...]) -> tuple[str, ...]:
    if mode not in MODES:
        raise ValueError("Research mode must be collaborative or passk")
    if not isinstance(modules, (tuple, list)) or any(not isinstance(m, str) for m in modules):
        raise ValueError("协作模块须为名称列表")
    if len(set(modules)) != len(modules):
        raise ValueError("协作模块不能重复")
    selected = set(modules)
    for name in selected:
        if name not in MODULES:
            raise ValueError(f"协作模块尚未实现或未安装：{name}")
        module = MODULES[name]
        if mode not in module.modes:
            raise ValueError(f"协作模块 {name} 不支持 {mode}")
        if missing := set(module.requires) - selected:
            raise ValueError(f"协作模块 {name} 需要：{', '.join(sorted(missing))}")
        if conflicts := set(module.conflicts) & selected:
            raise ValueError(f"协作模块 {name} 与 {', '.join(sorted(conflicts))} 冲突")
    # Stable dependency order makes equivalent UI/API selections reproducible.
    ordered: list[str] = []
    while remaining := selected - set(ordered):
        ready = sorted(m for m in remaining if set(MODULES[m].requires) <= set(ordered))
        if not ready:
            raise ValueError("协作模块之间存在循环依赖")
        ordered.extend(ready)
    return tuple(ordered)


def build_plan(
    mode: str, modules: tuple[str, ...] = (), research_prompt_variant: str = "simple"
) -> CollaborationPlan:
    from areal_rsi.agents.research.deprecated.prompt import PROMPT_VARIANTS
    from areal_rsi.agents.research.passk_prompt import PROMPT as PASSK_RESEARCH_PROMPT
    from areal_rsi.agents.rsi.deprecated.prompt import PROMPT as RSI_PROMPT
    from areal_rsi.agents.rsi.passk_prompt import PROMPT as PASSK_RSI_PROMPT
    from areal_rsi.coordination.actions import ResearchActions
    from areal_rsi.coordination.engine import ResearchEngine
    from areal_rsi.coordination.passk import PassKActions, PassKEngine

    ordered = selected_modules(mode, modules)
    plan = (
        CollaborationPlan(PassKActions, PassKEngine, PASSK_RESEARCH_PROMPT, PASSK_RSI_PROMPT, True)
        if mode == "passk"
        else CollaborationPlan(
            ResearchActions, ResearchEngine, PROMPT_VARIANTS[research_prompt_variant], RSI_PROMPT
        )
    )
    for name in ordered:
        plan = MODULES[name].apply(plan)
    return plan


def manifest(
    mode: str, modules: tuple[str, ...] = (), research_prompt_variant: str = "simple"
) -> dict[str, Any]:
    ordered = selected_modules(mode, modules)
    plan = build_plan(mode, ordered, research_prompt_variant)
    value: dict[str, Any] = {
        "schema_version": 1,
        "mode": mode,
        "mode_version": MODES[mode]["version"],
        "modules": [{"id": name, "version": MODULES[name].version} for name in ordered],
        "prompt_sha256": {
            role: hashlib.sha256(prompt.encode()).hexdigest()
            for role, prompt in (("rsi", plan.rsi_prompt), ("research", plan.research_prompt))
        },
    }
    value["fingerprint"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value


def catalog() -> dict[str, Any]:
    return {
        "modes": list(MODES.values()),
        "presets": presets(),
        "modules": [
            {
                "id": module.id,
                "label": module.label,
                "description": module.description,
                "version": module.version,
                "modes": list(module.modes),
                "requires": list(module.requires),
                "conflicts": list(module.conflicts),
            }
            for module in MODULES.values()
        ],
    }
