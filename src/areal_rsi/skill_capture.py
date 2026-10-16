"""Extract reusable candidate methods from development evidence, never final cases."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from areal_rsi.agents.common import ask_json
from areal_rsi.api.base import AgentSpec, ArtifactChange, ExperimentRun, SolutionVersion
from areal_rsi.app import LocalApp
from areal_rsi.base.storage import encode, new_id, safe_relative, write_json
from areal_rsi.goals import GoalService
from areal_rsi.modules.skills import SkillLibrary, SkillLoader, skill_configuration


def package_proposal(workspace: Path, proposal: dict[str, Any]) -> Path:
    """Package explicit model outputs, including committed workspace checkpoints."""
    files = proposal.get("files")
    names = proposal.get("workspace_files")
    if names is not None:
        if not isinstance(names, list) or not 2 <= len(names) <= 12:
            raise ValueError("Skill capture requires 2 to 12 explicit workspace files")
        files = {}
        for name in names:
            relative = safe_relative(name)
            source = workspace / relative
            if source.is_symlink() or not source.resolve().is_relative_to(workspace.resolve()):
                raise ValueError("Skill output must remain inside its workspace")
            files[name] = source.read_text()
    if not isinstance(files, dict) or not 2 <= len(files) <= 12:
        raise ValueError("Skill capture must include SKILL.md and reusable resources")
    package = workspace / new_id("candidate")
    package.mkdir()
    for name, content in files.items():
        relative = safe_relative(name)
        if not isinstance(content, str) or len(content) > 100000:
            raise ValueError("Invalid extracted resource")
        file = package / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
    return package


async def capture_skill(root: Path, goal_id: str, *, max_seconds: float = 600) -> dict[str, Any]:
    goals = GoalService(root)
    goal = goals.get(goal_id)
    if (root / "skill-capture-deferred.json").exists():
        deferred_result = {
            "goal_id": goal_id,
            "revision": goal.revision,
            "state": "deferred",
            "reason": "user_priority_main_demos_first",
            "elapsed_seconds": 0.0,
        }
        write_json(root / "skill-captures" / f"{goals.key(goal)}-deferred.json", deferred_result)
        goals.event(goal, "skill_capture_deferred", deferred_result=deferred_result)
        return deferred_result
    if goal.spec is None or goal.spec.target.value != "artifact" or goal.researcher is None:
        raise ValueError("Skill extraction requires a confirmed Artifact Goal")
    from areal_rsi.base.storage import FileRecords

    run = FileRecords(root / "run-states").get(goal.run_id or "missing", ExperimentRun)
    if run is None or not any(a.iterations for a in run.research_agents):
        raise ValueError("No completed development iteration to extract")
    workspace = root / "skill-capture-workspaces" / new_id("capture")
    workspace.mkdir(parents=True)
    # Only development evidence enters this input; final details remain outside.
    development = [
        {
            "iteration": i.iteration,
            "artifact_solution": i.candidate_solution_id,
            "reason": i.change.reason,
            "train": encode(i.train_report),
            "patch": await goals.content.read_text(i.change.patch),
            "validation_aggregate": encode(i.validation_report),
        }
        for agent in run.research_agents
        for i in agent.iterations
        if isinstance(i.change, ArtifactChange)
    ]
    version_records = FileRecords(root / "versions")
    for item in development:
        solution = version_records.get(item["artifact_solution"], SolutionVersion)
        item["artifact_versions"] = encode(solution.artifacts) if solution else []
    write_json(workspace / "development.json", development)
    started = time.monotonic()
    app = None
    result: dict[str, Any] = {
        "goal_id": goal_id,
        "revision": goal.revision,
        "budget_seconds": max_seconds,
        "state": "failed",
    }
    try:
        async with asyncio.timeout(max_seconds):
            app = await LocalApp.create(
                root, lambda _: None, goal=goal.spec, frozen=goal.frozen_identity
            )
            prompt = await goals.content.put_text(
                "Extract a reusable research Skill from development.json. "
                "Read that file using a workspace tool. Record transferable implementation steps, "
                "applicability, exclusions, dependencies, validation methods and failure modes. "
                "Do not copy the product source, business data, test answers, or credentials. "
                "Include references/checklist.md and scripts/validate.py, a scenario "
                "self-test runnable with python from a workspace with the package in package/. "
                "Use only Python standard library and generic synthetic fixtures. "
                "When supported by development evidence, extract a small business-neutral state "
                "or validation template/script with explicit parameters for future tasks. "
                "Do not save the completed application as a template. "
                "Do not claim cross-task benefit from a single production run. "
                "Prefer writing SKILL.md, references/checklist.md and scripts/validate.py "
                "directly in this workspace, then return only JSON "
                '{"workspace_files":["SKILL.md","references/checklist.md","scripts/validate.py"]}. '
                "Do not duplicate file contents in the final response after writing them. "
                "Alternatively return JSON {files: {SKILL.md: full markdown, ...}}. "
                "Keep the method concise and validate it with one scenario-script call. "
                "Frontmatter must include name, version "
                "(1.0.0), description, scope and dependencies (array of executable names). "
                "Only SKILL.md, scripts/, templates/, references/ may be packaged. "
                "Do not access anything outside this workspace."
            )
            session = await app.agent_runtimes["research-agent-1"].open(
                AgentSpec(
                    "skill-capture",
                    "areal-agent-runtime@v1",
                    app.agent_models["research-agent-1"],
                    (prompt,),
                    tools=("bash@v1",),
                ),
                workspace_root=str(workspace),
            )
            try:
                proposal = await ask_json(
                    session, "Extract a candidate Skill from the available development evidence."
                )
            finally:
                trajectory = await session.finish()
            package = package_proposal(workspace, proposal)
            skill = await SkillLoader(goals.content).freeze(package)
            config = skill_configuration()
            library = SkillLibrary(
                Path(config.get("library_root", "~/.areal_rsi/skills")), goals.content
            )
            record = await library.add(
                skill,
                {
                    "goal_id": goal_id,
                    "revision": goal.revision,
                    "data_root": str(root.resolve()),
                    "run_id": run.run_id,
                    "development": [
                        {k: v for k, v in item.items() if k != "patch"} for item in development
                    ],
                    "trajectory_id": trajectory.trajectory_id,
                    "final_evidence_access": False,
                },
            )
            result.update(
                state="candidate", skill=record["skill"], trajectory_id=trajectory.trajectory_id
            )
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": extraction_failed; production outcome unchanged"
    finally:
        if app:
            await app.close()
        result["elapsed_seconds"] = time.monotonic() - started
        result["token_usage"] = None
        result["cost"] = None
        write_json(root / "skill-captures" / f"{goals.key(goal)}-{workspace.name}.json", result)
        goals.event(goal, "skill_capture", result=result)
    return result
