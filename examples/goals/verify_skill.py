"""Independent native Skill resource capability check; no production effect claim."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from areal_rsi.api.base import AgentSpec
from areal_rsi.app import LocalApp
from areal_rsi.base.storage import encode, write_json
from areal_rsi.config import load_user_config
from areal_rsi.modules.runtime_package import skill_resource_events
from areal_rsi.modules.skills import SkillLoader


async def verify(root: Path, package: Path, seconds: float) -> int:
    started = time.monotonic()
    app = None
    result: dict[str, Any] = {
        "classification": "independent_real_runtime_resource_capability_check",
        "not_part_of_paired_production_timings": True,
        "budget_seconds": seconds,
        "passed": False,
        "fee": None,
        "human_seconds": None,
        "semantic_effect": "unknown",
    }
    try:
        async with asyncio.timeout(seconds):
            app = await LocalApp.create(root, lambda _: None)
            loader = SkillLoader(app.content)
            skill = await loader.freeze(package)
            workspace = root / "capability-workspace"
            directory, skill_prompt = await loader.materialize(skill, workspace)
            relative = directory.relative_to(workspace).as_posix()
            instructions = await app.content.put_text(
                "This is a bounded Skill resource integration test, not product research. "
                "Load the configured Skill using the native skill tool. Read "
                + relative
                + "/references/checklist.md with workspace.read, then execute "
                "python3 " + relative + "/scripts/validate.py with workspace.run. "
                "Do not edit any files, inspect any benchmarks or implement an artifact. "
                "After the tools finish, reply RESOURCE_CHECK_DONE only."
            )
            session = await app.agent_runtimes["research-agent-1"].open(
                AgentSpec(
                    "skill-resource-probe",
                    "areal-agent-runtime@v1",
                    app.agent_models["research-agent-1"],
                    (instructions,),
                    skills=(skill_prompt,),
                    tools=("bash@v1",),
                ),
                workspace_root=str(workspace.resolve()),
            )
            try:
                result["response"] = await session.send(
                    "Perform the three required resource checks."
                )
            finally:
                trajectory = await session.finish()
                result["trajectory_id"] = trajectory.trajectory_id
            record = await app.trajectories.get(trajectory.trajectory_id)
            assert record is not None
            payload = json.loads(await app.content.read_text(record.content))
            calls = skill_resource_events(payload["events"])
            loaded = len(payload["research_skills"]["loaded_events"])
            scenario_success = any(
                json.loads(item["completion"]["data"]["value"]["stdout"]).get("passed") is True
                for item in calls["commands"]
            )
            result.update(
                skill=encode(skill),
                counts={k: len(v) for k, v in calls.items()},
                loaded=loaded,
                scenario_success=scenario_success,
                passed=loaded > 0 and bool(calls["reads"]) and scenario_success,
                resource_evidence=calls,
            )
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": capability verification incomplete"
    finally:
        if app:
            await app.close()
        result["elapsed_seconds"] = time.monotonic() - started
        write_json(root / "resource-capability-report.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in ["resource_evidence", "skill"]}))
    return 0 if result["passed"] else 6


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=300)
    args = parser.parse_args()
    load_user_config()
    raise SystemExit(asyncio.run(verify(args.root.resolve(), args.package.resolve(), args.seconds)))
