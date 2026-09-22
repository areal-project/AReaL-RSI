"""Add task-supplied skills to selected identities without replacing their prompts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from areal_rsi.base.storage import safe_relative
from areal_rsi.prompts import load_prompt


def prepare_skills(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for profile in profiles:
        resources = profile.get("resources")
        if not resources:
            result.append(dict(profile))
            continue
        for name, body in resources.items():
            safe_relative(name)
            if not name.startswith("skills/") or not isinstance(body, str):
                raise ValueError("研究员技能须为 skills/ 下的文本文件")
        entries = sorted(name for name in resources if name.endswith("/SKILL.md"))
        if not entries:
            raise ValueError("研究员技能缺少 SKILL.md")
        result.append(
            {
                **profile,
                "prompt": profile["prompt"]
                + load_prompt(
                    "agents/research/skills.md",
                    index="\n".join(f"- researcher/{name}" for name in sorted(resources)),
                    reading="\n\n".join(
                        f"### researcher/{name}\n\n{resources[name]}" for name in entries
                    ),
                ),
                "resource_digest": hashlib.sha256(
                    json.dumps(resources, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest(),
            }
        )
    return result
