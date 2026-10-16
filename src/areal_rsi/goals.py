"""Shared deterministic Goal service used by CLI and the native TUI MCP bridge."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from areal_rsi.api.base import (
    BenchmarkSplit,
    EvaluationReport,
    EvolutionTarget,
    ExperimentPlan,
    ExperimentRun,
    RunState,
)
from areal_rsi.api.goal import (
    GoalOutcome,
    GoalRevision,
    GoalSpec,
    GoalState,
    ResearcherSpec,
    accepts,
)
from areal_rsi.api.goal import (
    comparison_metrics as final_metrics,
)
from areal_rsi.base.goal_records import execution_projection
from areal_rsi.base.storage import (
    FileContentStore,
    FileRecords,
    decode,
    encode,
    json_bytes,
    new_id,
    now,
    safe_name,
    write_json,
)
from areal_rsi.config import ModelConfigurationError
from areal_rsi.modules.skills import SkillLoader, skill_configuration
from areal_rsi.ui.experiments import _lock_held, _locked, _process_identity

EXIT_CODES = {
    GoalState.ACHIEVED: 0,
    GoalState.NEEDS_INPUT: 3,
    GoalState.READY: 3,
    GoalState.BUDGET_EXHAUSTED: 4,
    GoalState.BLOCKED: 5,
    GoalState.FAILED: 6,
    GoalState.PAUSED: 130,
    GoalState.CANCELLED: 130,
}
REQUIRED = ("description", "target", "acceptance", "budget", "allowed_paths", "invariants")


def missing_fields(request: dict[str, Any]) -> tuple[str, ...]:
    questions = [f"请补充 {key}" for key in REQUIRED if not request.get(key)]
    for section, fields in (
        ("acceptance", ("metrics", "verifier_version")),
        ("budget", ("max_iterations", "max_seconds")),
    ):
        value = request.get(section)
        if isinstance(value, dict) and value:
            for name in fields:
                if name not in value or value[name] is None or value[name] in ("", []):
                    questions.append(f"请补充 {section}.{name}")
    acceptance = request.get("acceptance")
    if isinstance(acceptance, dict) and isinstance(acceptance.get("metrics"), list):
        for index, metric in enumerate(acceptance["metrics"]):
            if isinstance(metric, dict):
                for name in ("metric", "threshold"):
                    if name not in metric or metric[name] is None or metric[name] == "":
                        questions.append(f"请补充 acceptance.metrics[{index}].{name}")
    if request.get("target") == "artifact" and not request.get("artifact_path"):
        questions.append("请补充 artifact_path（待改进产物目录）")
    return tuple(questions)


def merge_answers(request: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    merged = dict(request)
    for key, value in answers.items():
        old = merged.get(key)
        merged[key] = (
            merge_answers(old, value)
            if isinstance(old, dict) and isinstance(value, dict)
            else value
        )
    return merged


def implementation_identity() -> dict[str, str]:
    package = Path(__file__).parent
    files = (
        "goals.py",
        "api/goal.py",
        "api/workflow.py",
        "app.py",
        "base/storage.py",
        "agents/research/default.py",
        "agents/research/artifact.py",
        "modules/runtime_package.py",
        "modules/runtime_worker.py",
        "modules/runtime_dsh.py",
        "modules/gateway.py",
        "modules/linux_sandbox.py",
        "modules/harness.py",
        "modules/artifact.py",
        "modules/browser.py",
        "modules/benchmark.py",
        "modules/structured_arithmetic.py",
        "modules/ledger.py",
        "framework/verifier.py",
        "workflows/service.py",
        "workflows/evolution.py",
        "workflows/artifact.py",
    )
    return {name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in files}


class GoalService:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.records = FileRecords(self.root / "goals")
        self.control = self.root / "goal-workers"
        self.control.mkdir(parents=True, exist_ok=True)
        self.content = FileContentStore(self.root / "content")

    def get(self, goal_id: str) -> GoalRevision:
        goal = self.records.get(safe_name(goal_id), GoalRevision)
        if goal is None:
            raise KeyError(goal_id)
        return goal

    def key(self, goal: GoalRevision) -> str:
        return f"{goal.goal_id}-r{goal.revision}"

    def event(self, goal: GoalRevision, event: str, **data: Any) -> dict[str, Any]:
        value = {
            "schema_version": 1,
            "goal_id": goal.goal_id,
            "revision": goal.revision,
            "run_id": goal.run_id,
            "type": event,
            "at": now(),
            **encode(data),
        }
        directory = self.root / "goal-events" / self.key(goal)
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / f"{time.time_ns()}-{new_id('event')}.json", value, immutable=True)
        return value

    def save(self, goal: GoalRevision) -> None:
        self.records.save(goal.goal_id, goal, immutable=False)

    def draft(self, request: dict[str, Any], *, goal_id: str | None = None) -> GoalRevision:
        # Goal files are task contracts, never authentication containers.
        def check(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if any(
                        word in key.lower()
                        for word in ("api_key", "apikey", "authorization", "password")
                    ):
                        raise ValueError("Credentials belong in local deployment configuration")
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
            elif isinstance(value, str) and re.search(r"sk-[A-Za-z0-9]{20,}", value):
                raise ValueError("Credentials belong in local deployment configuration")

        check(request)
        previous = self.get(goal_id) if goal_id else None
        if previous and self.active(previous):
            raise ValueError("Pause the active Goal before creating a new revision")
        questions = missing_fields(request)
        spec = None if questions else decode(GoalSpec, request)
        goal = GoalRevision(
            previous.goal_id if previous else new_id("goal"),
            previous.revision + 1 if previous else 1,
            GoalState.NEEDS_INPUT if questions else GoalState.READY,
            request,
            now(),
            questions,
            (*previous.clarification, request) if previous else (request,),
            spec,
        )
        if previous:
            write_json(
                self.root / "goal-revisions" / f"{self.key(previous)}.json",
                previous,
                immutable=True,
            )
        self.save(goal)
        self.event(goal, "draft", questions=questions)
        return goal

    def answer(self, goal_id: str, answers: dict[str, Any]) -> GoalRevision:
        with _locked(self.control / "control.lock"):
            previous = self.get(goal_id)
            return self.draft(merge_answers(previous.request, answers), goal_id=goal_id)

    def validate_fresh_final(self, goal: GoalRevision, tasks: list[dict[str, Any]]) -> None:
        proposed = {json_bytes(task["input"]) for task in tasks}
        for path in (self.root / "goal-revisions").glob(f"{goal.goal_id}-r*.json"):
            previous = decode(GoalRevision, json.loads(path.read_text()))
            if (
                not previous.run_id
                or not (self.root / "runs" / previous.run_id / "final/selection.json").exists()
            ):
                continue
            frozen = previous.frozen_identity.get("tasks")
            if not isinstance(frozen, dict) or not isinstance(frozen.get("final"), list):
                raise ValueError("Previous final identity is unavailable; cannot prove fresh cases")
            exposed = {
                json_bytes(task["input"]) for task in frozen["final"] if isinstance(task, dict)
            }
            if proposed & exposed:
                raise ValueError(
                    "Final cases were already exposed in an earlier revision; "
                    "provide fresh task inputs. "
                    "Changing only task IDs or an unused benchmark seed is insufficient."
                )

    async def confirm(self, goal_id: str, *, source: str) -> GoalRevision:
        from areal_rsi.app import LocalApp

        with _locked(self.control / "control.lock"):
            goal = self.get(goal_id)
            if goal.confirmed_at:
                return goal
            if goal.questions or goal.spec is None:
                raise ValueError("Cannot confirm an incomplete Goal")
            spec = goal.spec
            if spec.target == EvolutionTarget.MODEL:
                raise ValueError(
                    "Model training backend is not installed; no fake checkpoint is produced"
                )
            config = skill_configuration()
            paths = spec.skill_paths if spec.skill_paths is not None else config.get("paths", [])
            skills = tuple([await SkillLoader(self.content).freeze(Path(p)) for p in paths])
            app = await LocalApp.create(self.root, lambda _: None, goal=spec, skills=skills)
            try:
                app.runtime_settings.validate_artifacts()
                if skills and app.agent_runtimes["research-agent-1"].selection["kind"] != "dsh":
                    raise ValueError("Research Skills currently require the verified DSH adapter")
                ctx = app.service.context
                if spec.acceptance.verifier_version != ctx.verifier_spec.verifier_id:
                    raise ValueError(
                        "Acceptance verifier_version does not match the assembled verifier"
                    )
                tasks = {
                    split.value: encode(await ctx.benchmark.list_tasks(split))
                    for split in BenchmarkSplit
                }
                if any(not rows for rows in tasks.values()):
                    raise ValueError(
                        "Goal requires nonempty TRAIN, VALIDATE and independent FINAL tasks"
                    )
                ids = [t["task_id"] for rows in tasks.values() for t in rows]
                if len(ids) != len(set(ids)):
                    raise ValueError("Goal benchmark partitions must be disjoint")
                self.validate_fresh_final(goal, tasks["final"])
                researcher = ResearcherSpec(
                    app.model_plan_config(), app.runtime_settings.identity, skills
                )
                frozen: dict[str, Any] = {
                    "baseline_solution_id": app.baseline.solution_id,
                    "baseline": encode(app.baseline),
                    "harness": encode(app.baseline.harness.content),
                    "tasks": tasks,
                    "verifier": encode(ctx.verifier_spec),
                    "researcher": encode(researcher),
                    "allowed_paths": list(spec.allowed_paths),
                    "invariants": list(spec.invariants),
                    "implementation": implementation_identity(),
                }
                if app.baseline.artifacts:
                    frozen["artifact"] = encode(app.baseline.artifacts[0].content)
                frozen["digest"] = hashlib.sha256(json_bytes(frozen)).hexdigest()
                plan = ExperimentPlan(
                    spec.description,
                    app.rsi.workflow_name,
                    ctx.benchmark.name,
                    app.baseline.solution_id,
                    spec.target,
                    spec.budget.max_iterations,
                    spec.research_agent_count,
                    True,
                    {
                        **app.model_plan_config(),
                        "goal_id": goal.goal_id,
                        "goal_revision": goal.revision,
                        "artifact_name": spec.artifact_name,
                        "acceptance": encode(spec.acceptance),
                        "researcher": encode(researcher),
                        "allowed_paths": list(spec.allowed_paths),
                    },
                )
                app.service.validate(plan)
                goal.run_id = new_id("run")
                run = ExperimentRun(goal.run_id, plan, RunState.PLANNED, app.baseline.solution_id)
                app.service.records.save(run.run_id, run)
                goal.researcher, goal.frozen_identity = researcher, frozen
                goal.confirmed_at, goal.confirmation_source = now(), source
                self.save(goal)
                write_json(
                    self.root / "goal-contracts" / f"{self.key(goal)}.json", goal, immutable=True
                )
                self.event(
                    goal, "confirmed", identity=frozen["digest"], skills=skills, source=source
                )
            finally:
                await app.close()
        return goal

    def active(self, goal: GoalRevision) -> bool:
        return _lock_held(self.control / f"{self.key(goal)}.lock")

    def launch(self, goal_id: str, *, resume: bool = False) -> GoalRevision:
        with _locked(self.control / "control.lock"):
            goal = self.get(goal_id)
            if not goal.confirmed_at or not goal.run_id:
                raise ValueError("Goal must be complete and confirmed before execution")
            if self.active(goal) or goal.state == GoalState.ACHIEVED:
                return goal
            allowed = {GoalState.READY, GoalState.RUNNING}
            if resume:
                allowed |= {GoalState.PAUSED, GoalState.BLOCKED}
            if goal.state not in allowed:
                raise ValueError("This Goal requires a new revision, not another evaluation")
            lock_path = self.control / f"{self.key(goal)}.lock"
            with lock_path.open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                (self.control / f"{self.key(goal)}-request.json").unlink(missing_ok=True)
                with (self.control / f"{self.key(goal)}.log").open("ab", buffering=0) as log:
                    child = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "areal_rsi",
                            "_goal-worker",
                            "--root",
                            str(self.root),
                            "--goal-id",
                            goal.goal_id,
                            "--revision",
                            str(goal.revision),
                            "--lock-fd",
                            str(lock.fileno()),
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        pass_fds=(lock.fileno(),),
                    )
                write_json(
                    self.control / f"{self.key(goal)}-process.json",
                    {"pid": child.pid, "identity": _process_identity(child.pid)},
                )
            goal.state = GoalState.RUNNING
            self.save(goal)
            self.event(goal, "started", pid=child.pid)
            return goal

    def request(self, goal_id: str, action: str) -> GoalRevision:
        if action not in {"pause", "cancel"}:
            raise ValueError("Unknown Goal control action")
        with _locked(self.control / "control.lock"):
            goal = self.get(goal_id)
            if goal.state in {GoalState.ACHIEVED, GoalState.FAILED, GoalState.BUDGET_EXHAUSTED}:
                return goal
            write_json(
                self.control / f"{self.key(goal)}-request.json", {"action": action, "at": now()}
            )
            if not self.active(goal):
                goal.state = GoalState.PAUSED if action == "pause" else GoalState.CANCELLED
                self.save(goal)
            self.event(goal, action + "_requested")
            return goal

    def status(self, goal_id: str) -> dict[str, Any]:
        goal = self.get(goal_id)
        return {
            "schema_version": 1,
            **encode(goal),
            **execution_projection(
                self.root, self.key(goal), goal.state.value, goal.run_id, self.active(goal)
            ),
            "log_path": str(self.control / f"{self.key(goal)}.log"),
        }

    async def production_usage(self, goal: GoalRevision) -> dict[str, Any] | None:
        from areal_rsi.base.storage import FileTrajectoryStore

        ids: set[str] = set()
        folder = self.root / "runs" / (goal.run_id or "missing")
        for path in folder.rglob("task-*.json"):
            value = json.loads(path.read_text())
            ids.add(value["result"]["trajectory"]["trajectory_id"])
        for path in folder.rglob("completed.json"):
            value = json.loads(path.read_text())
            ids.update(
                value[key]
                for key in ("proposal_trajectory_id", "selection_trajectory_id")
                if value.get(key)
            )
        trajectories = FileTrajectoryStore(self.root / "trajectories", self.content)
        totals: dict[str, Any] = {}
        covered = 0
        for identifier in ids:
            trace = await trajectories.get(identifier)
            if trace and trace.usage.get("aggregation") == "dedicated_usage_notifications":
                covered += 1
                for key, value in trace.usage.items():
                    if key.endswith("Tokens") and type(value) in (int, float):
                        totals[key] = totals.get(key, 0) + value
        if not totals:
            return None
        return {
            "observed": totals,
            "covered_trajectories": covered,
            "referenced_trajectories": len(ids),
            "complete": False,
            "limitation": "Uncommitted or interrupted sessions may have unreported usage",
        }

    async def execute(self, goal_id: str, revision: int) -> GoalOutcome:
        from areal_rsi.app import LocalApp
        from areal_rsi.workflows.artifact import ArtifactEvolutionWorkflow
        from areal_rsi.workflows.evolution import HarnessEvolutionWorkflow

        goal = self.get(goal_id)
        if (
            goal.revision != revision
            or goal.spec is None
            or goal.researcher is None
            or not goal.run_id
        ):
            raise ValueError("Goal revision is unconfirmed or mismatched")
        spec = goal.spec
        frozen_tasks = goal.frozen_identity["tasks"]
        assert isinstance(frozen_tasks, dict)
        ledger_path = self.control / f"{self.key(goal)}-ledger.json"
        ledger = (
            json.loads(ledger_path.read_text())
            if ledger_path.exists()
            else {"elapsed": 0.0, "attempts": 0}
        )
        ledger.setdefault("retries", 0)
        elapsed_before = float(ledger["elapsed"])
        started = time.monotonic()
        app = None
        state, reason = GoalState.FAILED, "execution_failed"
        solution_id, report_ref = None, None
        remaining = spec.budget.max_seconds - elapsed_before
        current = asyncio.current_task()
        assert current is not None
        requested: str | None = None
        cancellation_started: float | None = None

        async def watch() -> None:
            nonlocal requested, cancellation_started
            while True:
                request = self.control / f"{self.key(goal)}-request.json"
                if request.exists():
                    requested = json.loads(request.read_text())["action"]
                    cancellation_started = time.monotonic()
                    current.cancel()
                    return
                write_json(
                    ledger_path, {**ledger, "elapsed": elapsed_before + time.monotonic() - started}
                )
                await asyncio.sleep(0.2)

        watcher = asyncio.create_task(watch())
        try:
            if remaining <= 0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                app = await LocalApp.create(
                    self.root,
                    lambda message: print(message, flush=True),
                    goal=spec,
                    frozen=goal.frozen_identity,
                    skills=goal.researcher.skills,
                )
                recorded_code = goal.frozen_identity.get("implementation")
                if recorded_code is not None and recorded_code != implementation_identity():
                    raise ValueError("Frozen implementation changed; create a new revision")
                run = app.service.records.get(goal.run_id, ExperimentRun)
                assert run is not None
                await app.bind_plan(run.plan)
                ctx = app.service.context
                ctx.stop_requested = lambda: (
                    self.control / f"{self.key(goal)}-request.json"
                ).exists()
                ctx.remaining_seconds = lambda: remaining - (time.monotonic() - started)
                for split in BenchmarkSplit:
                    if encode(await ctx.benchmark.list_tasks(split)) != frozen_tasks[split.value]:
                        raise ValueError("Frozen benchmark changed; create a new Goal revision")
                if encode(ctx.verifier_spec) != goal.frozen_identity["verifier"]:
                    raise ValueError("Frozen verifier changed")
                while True:
                    ledger["attempts"] += 1
                    self.event(goal, "attempt", attempt=ledger["attempts"])
                    await app.service.resume(goal.run_id)
                    task = app.service.tasks.get(goal.run_id)
                    run = (
                        await task if task else app.service.records.get(goal.run_id, ExperimentRun)
                    )
                    assert run is not None
                    request = self.control / f"{self.key(goal)}-request.json"
                    if request.exists():
                        requested = json.loads(request.read_text())["action"]
                        raise asyncio.CancelledError
                    if current.cancelling():
                        raise asyncio.CancelledError
                    if time.monotonic() - started >= remaining:
                        raise TimeoutError
                    if run.state == RunState.SUCCEEDED:
                        break
                    self.event(
                        goal,
                        "attempt_failed",
                        attempt=ledger["attempts"],
                        run_state=run.state.value,
                        error=re.sub(r"sk-[A-Za-z0-9]{20,}", "[redacted]", run.error or "")[:2000],
                    )
                    if ledger["retries"] >= spec.budget.max_retries:
                        raise RuntimeError(
                            "Execution failed after finite retries; see run checkpoints"
                        )
                    ledger["retries"] += 1
                    self.event(goal, "retry", retry=ledger["retries"])
                solution_id = run.best_solution_id
                workflow_type = (
                    ArtifactEvolutionWorkflow
                    if spec.target == EvolutionTarget.ARTIFACT
                    else HarnessEvolutionWorkflow
                )
                workflow = workflow_type(
                    self.root / "runs", app.service.records, run, lambda _: None
                )
                folder = self.root / "runs" / run.run_id
                reports = [
                    json.loads((folder / "baseline/validate/evaluation.json").read_text())["report"]
                ]
                reports += [
                    encode(i.validation_report) for a in run.research_agents for i in a.iterations
                ]
                development = next(r for r in reports if r["solution_id"] == solution_id)
                if not workflow.development_passed(decode(EvaluationReport, development)):
                    state, reason = GoalState.BUDGET_EXHAUSTED, "iteration_budget_exhausted"
                else:
                    # Commit the chosen version before touching any independent final case.
                    write_json(
                        folder / "final/selection.json",
                        {
                            "solution_id": solution_id,
                            "goal_revision": revision,
                            "identity": goal.frozen_identity["digest"],
                        },
                        immutable=True,
                    )
                    baseline = await app.versions.get_solution(run.plan.baseline_solution_id)
                    selected = await app.versions.get_solution(solution_id)
                    assert baseline is not None and selected is not None
                    before, _ = await workflow.evaluate(
                        ctx,
                        baseline,
                        BenchmarkSplit.FINAL,
                        folder / "final/baseline",
                        agent_id="final",
                    )
                    after, _ = await workflow.evaluate(
                        ctx,
                        selected,
                        BenchmarkSplit.FINAL,
                        folder / "final/candidate",
                        agent_id="final",
                    )
                    metrics = final_metrics(before, after)
                    passed = accepts(spec.acceptance, metrics, final=True)
                    final_report = {
                        "goal_id": goal_id,
                        "revision": revision,
                        "run_id": run.run_id,
                        "identity": goal.frozen_identity["digest"],
                        "baseline": encode(before),
                        "candidate": encode(after),
                        "metrics": metrics,
                        "passed": passed,
                        "search_closed": True,
                        "at": now(),
                    }
                    write_json(folder / "final/report.json", final_report, immutable=True)
                    report_ref = await self.content.put_json(final_report)
                    state = GoalState.ACHIEVED if passed else GoalState.FAILED
                    reason = (
                        "independent_acceptance_passed"
                        if passed
                        else "independent_acceptance_failed"
                    )
        except TimeoutError:
            state, reason = GoalState.BUDGET_EXHAUSTED, "time_budget_exhausted"
        except asyncio.CancelledError:
            state = GoalState.CANCELLED if requested == "cancel" else GoalState.PAUSED
            reason = "user_" + (requested or "interruption")
        except (FileNotFoundError, PermissionError, ModelConfigurationError) as exc:
            detail = re.sub(r"sk-[A-Za-z0-9]{20,}", "[redacted]", str(exc))[:1000]
            state, reason = (
                GoalState.BLOCKED,
                f"{type(exc).__name__}: dependency unavailable ({detail}); restore it and resume",
            )
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            state, reason = (
                GoalState.FAILED,
                type(exc).__name__ + ": execution_failed; see worker log",
            )
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            if app is not None:
                if goal.run_id in app.service.tasks:
                    await app.service.stop(goal.run_id)
                await app.close()
            elapsed = elapsed_before + time.monotonic() - started
            write_json(ledger_path, {**ledger, "elapsed": elapsed})
        goal = self.get(goal_id)
        if goal.revision != revision:
            raise ValueError("Goal revision changed while worker was active")
        goal.state = state
        self.save(goal)
        outcome = GoalOutcome(
            goal_id,
            revision,
            state,
            state == GoalState.ACHIEVED,
            reason,
            solution_id,
            report_ref,
            elapsed,
            int(ledger["attempts"]),
            token_usage=await self.production_usage(goal),
            cancellation_grace_seconds=max(
                0.0,
                elapsed - spec.budget.max_seconds,
                time.monotonic() - cancellation_started if cancellation_started else 0.0,
            ),
        )
        write_json(self.root / "goal-outcomes" / f"{self.key(goal)}.json", outcome)
        self.event(goal, "outcome", outcome=outcome)
        if (
            spec.target == EvolutionTarget.ARTIFACT
            and state in {GoalState.ACHIEVED, GoalState.BUDGET_EXHAUSTED, GoalState.FAILED}
            and spec.assets.get(
                "capture_candidates", skill_configuration().get("capture_candidates", True)
            )
        ):
            from areal_rsi.skill_capture import capture_skill

            try:
                await capture_skill(self.root, goal_id)
            except Exception as exc:
                self.event(
                    goal,
                    "skill_capture_failed",
                    error=type(exc).__name__,
                    production_outcome_unchanged=True,
                )
        return outcome


async def run_goal_worker(root: Path, goal_id: str, revision: int, lock_fd: int) -> int:
    service = GoalService(root)
    goal = service.get(goal_id)
    if revision != goal.revision:
        raise ValueError("Goal worker revision mismatch")
    expected = (service.control / f"{service.key(goal)}.lock").stat()
    actual = os.fstat(lock_fd)
    if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
        raise ValueError("Goal worker lock identity mismatch")
    os.set_inheritable(lock_fd, False)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # The launcher holds the control lock until the running state is committed.
    with _locked(service.control / "control.lock"):
        pass
    current = asyncio.current_task()
    assert current is not None
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, current.cancel)
    try:
        outcome = await service.execute(goal_id, revision)
        return EXIT_CODES[outcome.state]
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        with suppress(OSError):
            os.close(lock_fd)
