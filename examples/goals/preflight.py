"""Real DSH Skill loading and directory-resource integration probe."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from areal_rsi.api.base import AgentSpec
from areal_rsi.app import LocalApp
from areal_rsi.base.storage import write_json
from areal_rsi.config import load_user_config
from areal_rsi.modules.skills import SkillLoader


async def probe(root: Path) -> None:
    load_user_config()
    app = await LocalApp.create(root, lambda _: None)
    source = root / "probe-skill"
    (source / "references").mkdir(parents=True)
    (source / "scripts").mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: skill-loading-probe\nversion: 1.0.0\n"
        "description: Verify real resource access\nscope: Runtime integration testing\n"
        "dependencies: [python]\n---\nLoad this Skill. Read references/probe.txt and run "
        "scripts/probe.py, relative to this Skill directory. Report the exact script output.\n"
    )
    (source / "references/probe.txt").write_text("RSI_SKILL_REFERENCE_OK\n")
    (source / "scripts/probe.py").write_text('print("RSI_SKILL_RESOURCE_OK")\n')
    skill = await SkillLoader(app.content).freeze(source)
    workspace = root / "probe-workspace"
    workspace.mkdir()
    _, ref = await SkillLoader(app.content).materialize(skill, workspace)
    try:
        session = await app.agent_runtimes["research-agent-1"].open(
            AgentSpec(
                "research-agent-1",
                "areal-agent-runtime@v1",
                app.agent_models["research-agent-1"],
                (
                    await app.content.put_text(
                        "Load the configured Skill using the skill tool. Then read its reference "
                        "and run its Python script using workspace tools. Report exact output."
                    ),
                ),
                (ref,),
                ("bash@v1",),
            ),
            workspace_root=str(workspace),
        )

        async def monitor() -> None:
            while True:
                await asyncio.sleep(10)
                proxies = set(app.agent_proxies.values())
                print(
                    json.dumps(
                        {
                            "timing": True,
                            "committed_events": len(session.events),
                            "upstream": [
                                {
                                    "bytes": item.get("received_bytes"),
                                    "completed": "response" in item,
                                }
                                for proxy in proxies
                                for item in proxy.requests
                            ],
                        }
                    ),
                    flush=True,
                )

        monitoring = asyncio.create_task(monitor())
        try:
            response = await session.send("Use skill-loading-probe.")
        finally:
            monitoring.cancel()
            await asyncio.gather(monitoring, return_exceptions=True)
            trajectory = await session.finish()
        record = await app.trajectories.get(trajectory.trajectory_id)
        assert record
        payload = json.loads(await app.content.read_text(record.content))
        evidence = payload["research_skills"]
        result = {
            "response": response,
            "trajectory_id": trajectory.trajectory_id,
            "loaded": len(evidence["loaded_events"]),
            "resources": len(evidence["resource_events"]),
        }
        result["passed"] = bool(
            result["loaded"] and result["resources"] and "RSI_SKILL_RESOURCE_OK" in response
        )
        write_json(root / "skill-integration-report.json", result)
        print(json.dumps(result), flush=True)
        if not result["passed"]:
            raise RuntimeError("Real Skill integration evidence is incomplete")
    finally:
        await app.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(probe(arguments.root.resolve()))
