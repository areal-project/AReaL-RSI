"""Natural-language submission, clarification and NDJSON attachment for Goals."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from areal_rsi.api.goal import GoalState
from areal_rsi.base.storage import encode
from areal_rsi.goals import EXIT_CODES, GoalService
from areal_rsi.modules.skills import SkillLibrary, skill_configuration


def output(value: Any, *, stream: TextIO | None = None) -> None:
    value = encode(value)
    if isinstance(value, dict) and "schema_version" in value:
        value = {"goal_id": None, "revision": None, "run_id": None, **value}
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), file=stream, flush=True)


async def attach(service: GoalService, goal_id: str) -> int:
    seen: set[str] = set()
    try:
        while True:
            goal = service.get(goal_id)
            folder = service.root / "goal-events" / service.key(goal)
            for path in sorted(folder.glob("*.json")):
                if path.name not in seen:
                    output(json.loads(path.read_text()))
                    seen.add(path.name)
            if not service.active(goal):
                if goal.state == GoalState.RUNNING:
                    output(
                        {
                            "schema_version": 1,
                            "type": "worker_interrupted",
                            "goal_id": goal_id,
                            "run_id": goal.run_id,
                            "revision": goal.revision,
                            "resume_required": True,
                        }
                    )
                    return 6
                output({"schema_version": 1, "type": "result", **service.status(goal_id)})
                return EXIT_CODES.get(goal.state, 6)
            await asyncio.sleep(0.25)
    except KeyboardInterrupt:
        return 130


async def dispatch(args: Any, root: Path) -> int:
    service = GoalService(root)
    if args.command == "skill":
        if args.action == "capture":
            from areal_rsi.skill_capture import capture_skill

            result = await capture_skill(root, args.goal, max_seconds=args.max_seconds)
            output(result)
            return 0 if result["state"] == "candidate" else 6
        config = skill_configuration()
        library = SkillLibrary(
            Path(config.get("library_root", "~/.areal_rsi/skills")), service.content
        )
        if args.action == "list":
            output(library.list())
        elif args.action == "inspect":
            output(library.inspect(args.skill_id, args.version))
        elif args.action == "validate":
            from areal_rsi.base.storage import write_json

            record = await library.validate(args.skill_id, args.version)
            write_json(root / "skill-lifecycle" / f"{args.skill_id}-{args.version}.json", record)
            output(record)
        elif args.action == "deprecate":
            output(library.deprecate(args.skill_id, args.version, args.reason))
        else:
            from areal_rsi.base.storage import write_json

            record = library.activate(args.skill_id, args.version)
            write_json(root / "skill-lifecycle" / f"{args.skill_id}-{args.version}.json", record)
            output(record)
        return 0
    if args.command == "goal":
        if args.action == "export":
            from areal_rsi.api.base import SolutionVersion
            from areal_rsi.base.storage import FileRecords, write_json

            goal = service.get(args.goal_id)
            status = service.status(goal.goal_id)
            outcome = status.get("outcome")
            if not outcome or not outcome.get("solution_id"):
                raise ValueError("This Goal has no frozen delivery version")
            solution = FileRecords(root / "versions").get(outcome["solution_id"], SolutionVersion)
            if solution is None or goal.spec is None:
                raise ValueError("Delivery snapshot is unavailable")
            destination = args.destination.expanduser().resolve()
            if destination.exists():
                raise FileExistsError("Export destination must be a new directory")
            if goal.spec.target.value == "artifact":
                artifact = next(a for a in solution.artifacts if a.name == goal.spec.artifact_name)
                await service.content.materialize(artifact.content, destination / "artifact")
            else:
                await service.content.materialize(solution.harness.content, destination / "harness")
            write_json(
                destination / "manifest.json",
                {
                    "goal_id": goal.goal_id,
                    "revision": goal.revision,
                    "run_id": goal.run_id,
                    "solution_id": solution.solution_id,
                    "outcome": outcome,
                },
            )
            output(
                {
                    "schema_version": 1,
                    "type": "exported",
                    "goal_id": goal.goal_id,
                    "revision": goal.revision,
                    "run_id": goal.run_id,
                    "destination": str(destination),
                    "achieved": outcome["achieved"],
                }
            )
            return 0
        if args.action == "answer":
            goal = service.answer(args.goal_id, json.loads(Path(args.answers).read_text()))
            if args.yes and goal.spec:
                await service.confirm(goal.goal_id, source="cli-answer--yes")
                service.launch(goal.goal_id)
                return await attach(service, goal.goal_id)
        elif args.action == "resume":
            goal = service.launch(args.goal_id, resume=True)
            return await attach(service, goal.goal_id)
        elif args.action in {"pause", "cancel"}:
            service.request(args.goal_id, args.action)
            goal = service.get(args.goal_id)
        else:
            goal = service.get(args.goal_id)
        output({"schema_version": 1, "type": "result", **service.status(goal.goal_id)})
        return 0 if args.action in {"status", "pause", "cancel"} else EXIT_CODES.get(goal.state, 3)
    request = json.loads(Path(args.spec).expanduser().read_text()) if args.spec else {}
    if args.description:
        request["description"] = args.description
    if args.target:
        request["target"] = args.target
    if args.artifact:
        request["artifact_path"] = str(Path(args.artifact).expanduser().resolve())
    goal = service.draft(request)
    interactive = sys.stdin.isatty() and not args.json and not args.yes
    if interactive and goal.questions:
        from areal_rsi.ui.clarification import GoalClarifier

        clarifier = GoalClarifier(service)
        message = "请帮助澄清这个目标，并补齐执行与验收契约。"
        try:
            while goal.questions:
                response = await clarifier.respond(goal.goal_id, message)
                answers = response.get("answers", {})
                if answers:
                    goal = service.answer(goal.goal_id, answers)
                if goal.questions:
                    for question in response.get("questions", [])[:2]:
                        print(str(question), file=sys.stderr)
                    message = input("你的补充：")
        finally:
            await clarifier.close()
    if goal.questions:
        output({"schema_version": 1, "type": "needs_input", **service.status(goal.goal_id)})
        return 3
    confirmed = args.yes
    if interactive:
        output(
            {
                "schema_version": 1,
                "type": "confirmation",
                "spec": encode(goal.spec),
                "goal_id": goal.goal_id,
                "revision": goal.revision,
                "run_id": goal.run_id,
            }
        )
        print("确认此契约并开始执行？[y/N]", file=sys.stderr)
        confirmed = input().strip().lower() in {"y", "yes"}
    if not confirmed:
        output(
            {"schema_version": 1, "type": "confirmation_required", **service.status(goal.goal_id)}
        )
        return 3
    await service.confirm(goal.goal_id, source="cli--yes" if args.yes else "cli-interactive")
    goal = service.launch(goal.goal_id)
    if args.detach:
        output({"schema_version": 1, "type": "started", **service.status(goal.goal_id)})
        return 0
    return await attach(service, goal.goal_id)
