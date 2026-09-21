"""Frozen, task-supplied persona libraries; no benchmark knowledge in the scheduler."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from areal_rsi.base.storage import safe_relative, write_json
from areal_rsi.coordination.problem_sharing import ProblemSharingActions
from areal_rsi.prompts import load_prompt

if TYPE_CHECKING:
    from areal_rsi.coordination.collaboration import CollaborationPlan


def load_resources(directory: Path) -> dict[str, Any]:
    files = {
        path.relative_to(directory).as_posix(): path.read_text()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.suffix in {".md", ".py"}
    }
    return {
        "resources": files,
        "required_reading": [
            "AGENTS.md",
            "knowledge/core.md",
            *sorted(
                name for name in files if name.startswith("skills/") and name.endswith("/SKILL.md")
            ),
        ],
    }


def prepare_profiles(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = []
    for profile in profiles:
        resources = profile.get("resources") or {
            "AGENTS.md": profile["prompt"],
            "knowledge/core.md": load_prompt("agents/research/resources/general/knowledge/core.md"),
        }
        required = profile.get("required_reading", ["AGENTS.md", "knowledge/core.md"])
        for name, body in resources.items():
            safe_relative(name)
            if not isinstance(body, str):
                raise ValueError("研究员资料必须是文本文件")
        if "AGENTS.md" not in required or any(name not in resources for name in required):
            raise ValueError("必读资料必须包含 AGENTS.md，且全部存在于身份资料包中")
        digest = hashlib.sha256(
            json.dumps(resources, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        prompt = (
            resources["AGENTS.md"]
            + "\n\n"
            + load_prompt(
                "agents/research/profile_resources.md",
                index="\n".join(f"- researcher/{name}" for name in sorted(resources)),
                reading="\n\n".join(
                    f"### researcher/{name}\n\n{resources[name]}"
                    for name in required
                    if name != "AGENTS.md"
                ),
            )
        )
        prepared.append(
            {
                **profile,
                "prompt": prompt,
                "resources": dict(resources),
                "required_reading": list(required),
                "resource_digest": digest,
            }
        )
    return prepared


class ProfileResourceActions(ProblemSharingActions):
    async def initialize_workspace(self, agent_id: str) -> None:
        await super().initialize_workspace(agent_id)
        profile = self.c.state.get("researcher_profiles", {}).get(agent_id, {})
        resources = profile.get("resources")
        marker = self.c.store.paths.agent(agent_id) / "profile_resources_initialized.json"
        if not resources or marker.exists():
            return
        root = self.workspace(agent_id) / "researcher"
        for name, body in resources.items():
            path = root / safe_relative(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_text() != body:
                raise ValueError(f"研究员资料与已有工作区文件冲突：{name}")
            path.write_text(body)
        write_json(marker, {"digest": profile["resource_digest"]}, immutable=True)


def apply(plan: CollaborationPlan) -> CollaborationPlan:
    return replace(
        plan,
        actions=type("ResourceGuidanceActions", (ProfileResourceActions, plan.actions), {}),
    )
