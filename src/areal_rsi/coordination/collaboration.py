"""Versioned collaboration recipes, independent of model and execution backends.

Modules are code registered here, never arbitrary imports from run configuration.
An installed module transforms the actions, scheduler and prompts together.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from areal_rsi.prompts import load_prompt

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
}


def apply_leader_guidance(plan: CollaborationPlan) -> CollaborationPlan:
    from areal_rsi.coordination.leader_guidance import apply

    return apply(plan)


def apply_leader_guidance_v022(plan: CollaborationPlan) -> CollaborationPlan:
    return replace(plan, rsi_prompt=load_prompt("agents/rsi/leader_guidance_v0.2.2.md"))


def apply_problem_sharing(plan: CollaborationPlan) -> CollaborationPlan:
    from areal_rsi.coordination.problem_sharing import apply

    return apply(plan)


def apply_researcher_profiles(plan: CollaborationPlan) -> CollaborationPlan:
    return replace(
        plan, rsi_prompt=plan.rsi_prompt + load_prompt("agents/rsi/researcher_profiles.md")
    )


def apply_objective_sharing(plan: CollaborationPlan) -> CollaborationPlan:
    from areal_rsi.coordination.objective_sharing import apply

    return apply(plan)


def apply_team_review(plan: CollaborationPlan) -> CollaborationPlan:
    from areal_rsi.coordination.team_review import apply

    return apply(plan)


def apply_profile_resources(plan: CollaborationPlan) -> CollaborationPlan:
    from areal_rsi.coordination.profile_resources import apply

    return apply(plan)


def apply_leader_guidance_v023(plan: CollaborationPlan) -> CollaborationPlan:
    return replace(
        plan,
        rsi_prompt=load_prompt("agents/rsi/leader_guidance_v0.2.3.md"),
        research_prompt=load_prompt("agents/research/leader_guidance_v0.2.3.md"),
    )


def apply_leader_guidance_v024(plan: CollaborationPlan) -> CollaborationPlan:
    from areal_rsi.coordination.continuous_guidance import apply

    return apply(plan)


def apply_sharing_quota(plan: CollaborationPlan) -> CollaborationPlan:
    from areal_rsi.coordination.sharing_quota import apply

    return apply(plan)


def apply_intervention_review(plan: CollaborationPlan) -> CollaborationPlan:
    from areal_rsi.coordination.intervention_review import apply

    return apply(plan)


def apply_researcher_restart(plan: CollaborationPlan) -> CollaborationPlan:
    from areal_rsi.coordination.researcher_restart import apply

    return apply(plan)


DEFAULT_MODULES = ("leader_guidance", "leader_guidance_v024")


MODULES: dict[str, CollaborationModule] = {
    "researcher_skills": CollaborationModule(
        "researcher_skills",
        "按身份加载技能",
        "只为配置了技能的研究员追加技能入口与文件，保留原身份和协作方式。",
        "1",
        apply_profile_resources,
        requires=("researcher_profiles",),
        conflicts=("profile_resources",),
    ),
    "team_review": CollaborationModule(
        "team_review",
        "v0.2.1.4 · 定期观察与协调",
        "沿用 v0.2.1.2 的指导方式，约每十分钟观察各人的探索并按需要协调。",
        "1",
        apply_team_review,
        requires=("researcher_profiles",),
        conflicts=("objective_sharing",),
    ),
    "profile_resources": CollaborationModule(
        "profile_resources",
        "v0.2.1.4 · 人设资料",
        "冻结每人的 AGENTS.md、知识和可选技能，启动时加载必读正文并保留工作区文件。",
        "1",
        apply_profile_resources,
        requires=("researcher_profiles",),
        conflicts=("objective_sharing",),
    ),
    "objective_sharing": CollaborationModule(
        "objective_sharing",
        "v0.2.1.3 · 及时报告与客观分享",
        "重要发现先简要报告；RSI 分享事实、条件与来源身份，接收者结合自己的研究偏好独立判断。",
        "1",
        apply_objective_sharing,
        requires=("researcher_profiles",),
    ),
    "researcher_profiles": CollaborationModule(
        "researcher_profiles",
        "v0.2.1.2 · 不同研究偏好",
        "在 v0.2.1.1 上为独立研究员配置不同身份，保留完整探索与定向共享。",
        "1",
        apply_researcher_profiles,
        requires=("problem_sharing",),
    ),
    "leader_guidance": CollaborationModule(
        "leader_guidance",
        "RSI 协调",
        "RSI 综合进展并定向指导；同一 run 按需查阅研究资料，各自完成整题并验证。",
        "3",
        apply_leader_guidance,
    ),
    "leader_guidance_v022": CollaborationModule(
        "leader_guidance_v022",
        "v0.2.2 Leader Prompt",
        "关注未验证的思路，分享具体经验；保留独立探索，允许有依据地反驳建议。",
        "1",
        apply_leader_guidance_v022,
        requires=("leader_guidance",),
    ),
    "problem_sharing": CollaborationModule(
        "problem_sharing",
        "v0.2.1.1 · 区分问题与成果分享",
        "未解决的问题只增加一名接收者；已验证成果可定向分享给多人，研究员自主判断建议。",
        "1",
        apply_problem_sharing,
        requires=("leader_guidance",),
        conflicts=("leader_guidance_v022", "leader_guidance_v023", "leader_guidance_v024"),
    ),
    "leader_guidance_v023": CollaborationModule(
        "leader_guidance_v023",
        "v0.2.3 精简 Prompt",
        "精简 leader 与 researcher 提示，保留定向共享、自主判断和持续探索。",
        "1",
        apply_leader_guidance_v023,
        requires=("leader_guidance",),
        conflicts=("leader_guidance_v022",),
    ),
    "leader_guidance_v024": CollaborationModule(
        "leader_guidance_v024",
        "v0.2.4 持续协调",
        "RSI 持续观察研究进展和实验，定向指导并跟进；不依赖研究员主动报告才继续工作。",
        "1",
        apply_leader_guidance_v024,
        requires=("leader_guidance",),
        conflicts=("leader_guidance_v022", "leader_guidance_v023"),
    ),
    "sharing_quota": CollaborationModule(
        "sharing_quota",
        "限制经验传播",
        "同一经验在整个研究中只增加一名接收者；保留纠正与按需读取原始资料。",
        "1",
        apply_sharing_quota,
        requires=("leader_guidance_v024",),
    ),
    "researcher_restart": CollaborationModule(
        "researcher_restart",
        "回滚并重新探索",
        "保留最好结果，让少数停滞研究员从自己的历史候选开启新会话，沿用原预算。",
        "1",
        apply_researcher_restart,
        requires=("leader_guidance_v024",),
    ),
    "intervention_review": CollaborationModule(
        "intervention_review",
        "跟进指导结果",
        "根据连续证据选择介入对象和建议粒度，记录并跟进上次建议的实际结果。",
        "1",
        apply_intervention_review,
        requires=("leader_guidance_v024",),
    ),
}


def presets() -> list[dict[str, Any]]:
    choices = [{**mode, "mode": mode["id"], "modules": []} for mode in MODES.values()]
    choices.insert(
        1,
        {
            "id": "passk_guided",
            "label": "v0.2.1 · pass@k + RSI 协调",
            "mode": "passk",
            "modules": ["leader_guidance"],
            "description": MODULES["leader_guidance"].description,
        },
    )
    choices.append(
        {
            "id": "passk_guided_v022",
            "label": "v0.2.2 · pass@k + RSI 协调",
            "mode": "passk",
            "modules": ["leader_guidance", "leader_guidance_v022"],
            "description": MODULES["leader_guidance_v022"].description,
        }
    )
    choices.append(
        {
            "id": "passk_guided_v023",
            "label": "v0.2.3 · pass@k + RSI 协调",
            "mode": "passk",
            "modules": ["leader_guidance", "leader_guidance_v023"],
            "description": MODULES["leader_guidance_v023"].description,
        }
    )
    choices.append(
        {
            "id": "passk_guided_v024",
            "label": "v0.2.4 · pass@k + RSI 持续协调",
            "mode": "passk",
            "modules": list(DEFAULT_MODULES),
            "description": MODULES["leader_guidance_v024"].description,
        }
    )
    choices.append(
        {
            "id": "passk_guided_v025",
            "label": "v0.2.5 · 限制经验传播",
            "mode": "passk",
            "modules": ["leader_guidance", "leader_guidance_v024", "sharing_quota"],
            "description": MODULES["sharing_quota"].description,
        }
    )
    choices.append(
        {
            "id": "passk_guided_v026",
            "label": "v0.2.6 · 跟进指导结果",
            "mode": "passk",
            "modules": ["leader_guidance", "leader_guidance_v024", "intervention_review"],
            "description": MODULES["intervention_review"].description,
        }
    )
    choices.append(
        {
            "id": "passk_guided_v027",
            "label": "v0.2.7 · 回滚并重新探索",
            "mode": "passk",
            "modules": ["leader_guidance", "leader_guidance_v024", "researcher_restart"],
            "description": MODULES["researcher_restart"].description,
        }
    )
    choices.append(
        {
            "id": "passk_guided_v0211",
            "label": "v0.2.1.1 · 区分问题与成果分享",
            "mode": "passk",
            "modules": ["leader_guidance", "problem_sharing"],
            "description": MODULES["problem_sharing"].description,
        }
    )
    choices.append(
        {
            "id": "passk_guided_v0212",
            "label": "v0.2.1.2 · 不同研究偏好",
            "mode": "passk",
            "modules": ["leader_guidance", "problem_sharing", "researcher_profiles"],
            "description": MODULES["researcher_profiles"].description,
        }
    )
    choices.append(
        {
            "id": "passk_guided_v0213",
            "label": "v0.2.1.3 · 及时报告与客观分享",
            "mode": "passk",
            "modules": [
                "leader_guidance",
                "problem_sharing",
                "researcher_profiles",
                "objective_sharing",
            ],
            "description": MODULES["objective_sharing"].description,
        }
    )
    choices.append(
        {
            "id": "passk_guided_v0214",
            "label": "v0.2.1.4 · 团队观察与人设资料",
            "mode": "passk",
            "modules": [
                "leader_guidance",
                "problem_sharing",
                "researcher_profiles",
                "team_review",
                "profile_resources",
            ],
            "description": "基于 v0.2.1.2，定期协调探索，并加载任务配置的人设资料。",
        }
    )
    choices.append(
        {
            "id": "passk_guided_v0212_skills",
            "label": "v0.2.1.2 · 研究员技能",
            "mode": "passk",
            "modules": [
                "leader_guidance",
                "problem_sharing",
                "researcher_profiles",
                "researcher_skills",
            ],
            "description": MODULES["researcher_skills"].description,
        }
    )
    return choices


def selected_modules(mode: str, modules: tuple[str, ...]) -> tuple[str, ...]:
    if mode not in MODES:
        raise ValueError("旧版协作模式已移除；请新建 passk 研究，可选 leader_guidance 模块")
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


def build_plan(mode: str, modules: tuple[str, ...] = ()) -> CollaborationPlan:
    from areal_rsi.coordination.passk import PassKActions, PassKEngine

    ordered = selected_modules(mode, modules)
    plan = CollaborationPlan(
        PassKActions,
        PassKEngine,
        load_prompt("agents/research/passk.md"),
        load_prompt("agents/rsi/passk.md"),
        True,
    )
    for name in ordered:
        plan = MODULES[name].apply(plan)
    return plan


def manifest(mode: str, modules: tuple[str, ...] = ()) -> dict[str, Any]:
    ordered = selected_modules(mode, modules)
    plan = build_plan(mode, ordered)
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
