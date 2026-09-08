"""One real, same-task diagnostic using the standard RSI evolution workflow."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from adapter import ThreeModelBenchmark, ThreeModelEvaluation
from run_baseline import DATASET, EXAMPLE, GENERATION_ENVIRONMENT, prepare_public_workspace

from areal_rsi.agents.research.default import DefaultResearchAgent
from areal_rsi.agents.rsi.default import DefaultRsiAgent
from areal_rsi.api.base import (
    AgentSpec,
    BenchmarkSplit,
    EvolutionTarget,
    ExperimentPlan,
    ModelVersion,
    RunState,
    SolutionVersion,
    Task,
    TaskResult,
    VerificationResult,
    VerifierSpec,
)
from areal_rsi.api.workflow import WorkflowContext
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileTrajectoryStore,
    FileVersionManager,
    decode,
    encode,
    new_id,
    now,
    write_json,
)
from areal_rsi.config import AgentModelSettings, ModelConfig
from areal_rsi.modules.gateway import GatewayProxy
from areal_rsi.modules.harness import LocalHarnessBuilder
from areal_rsi.modules.runtime import DshAgentRuntime, sandbox_wrapper
from areal_rsi.workflows.service import LocalExperimentService

TASK_MESSAGE = "Read TARGET.md and implement src/model.js."
SCOPE = "MODEL-001 shared train/validate diagnostic; Contract V2 only, no visual or held-out score"


class DiagnosticBenchmark:
    """Explicit overlap for wiring ONE task, never a held-out benchmark result."""

    def __init__(self, task, snapshot):
        self.task = task
        identity = json.dumps(
            {"task": encode(task), "snapshot": snapshot, "scope": SCOPE}, sort_keys=True
        ).encode()
        self.name = "3d-model001-diagnostic-" + hashlib.sha256(identity).hexdigest()[:16]

    async def list_tasks(self, split):
        if split not in (BenchmarkSplit.TRAIN, BenchmarkSplit.VALIDATE):
            raise ValueError("Unsupported split")
        return (decode(type(self.task), encode(self.task)),)


class FileTaskRuntime:
    def __init__(self, root, content, runtime, template, emit, *, retry_incomplete=False):
        self.root, self.content, self.runtime = root, content, runtime
        self.template, self.emit = template, emit
        self.retry_incomplete = retry_incomplete

    async def run(self, solution, task, *, config=None):
        execution_key = (config or {})["execution_key"]
        folder = self.root / hashlib.sha256(execution_key.encode()).hexdigest()[:16]
        folder.mkdir(parents=True, exist_ok=True)
        identity = {"execution_key": execution_key, "solution": solution, "task": task}
        write_json(folder / "inputs.json", identity, immutable=True)
        if (folder / "task-result.json").exists():
            return decode(TaskResult, json.loads((folder / "task-result.json").read_text()))
        if (folder / "workspace").exists():
            if not self.retry_incomplete:
                raise ValueError("Incomplete generation exists; refusing an implicit paid retry")
            archived = self.root / "failed-attempts" / new_id(folder.name)
            archived.parent.mkdir(parents=True, exist_ok=True)
            folder.rename(archived)
            folder.mkdir()
            write_json(folder / "inputs.json", identity, immutable=True)
            write_json(
                folder / "retry.json",
                {
                    "previous_attempt": str(archived),
                    "execution_key": execution_key,
                    "reason": "Explicit incomplete-generation retry; earlier evidence retained",
                },
            )
            self.emit(f"Retry incomplete Task Agent; previous files retained at {archived}")
        workspace = folder / "workspace"
        prepare_public_workspace(self.template, workspace)
        (workspace / "TARGET.md").write_text(str(task.input))
        await self.content.materialize(solution.harness.content, folder / "harness")
        prompt = await self.content.put_text((folder / "harness/prompt.md").read_text())
        self.emit(f"Task Agent start: {execution_key}")
        session = await self.runtime.open(
            AgentSpec(
                "task-agent", "deepseek-harness@v1", solution.model, (prompt,), tools=("bash@v1",)
            ),
            workspace_root=str(workspace),
        )
        try:
            response = await session.send(TASK_MESSAGE)
            (folder / "response.txt").write_text(response)
        finally:
            trace = await session.finish()
        for name in ("package.json", "CONTRACT_V2.md", "TARGET.md"):
            path = workspace / name
            expected = (
                str(task.input).encode()
                if name == "TARGET.md"
                else (self.template / name).read_bytes()
            )
            if path.is_symlink() or path.read_bytes() != expected:
                raise ValueError(f"Task Agent modified fixed input: {name}")
        source = workspace / "src/model.js"
        if source.is_symlink() or source.parent.is_symlink() or not source.is_file():
            raise ValueError("Task output is missing or a symlink")
        result = TaskResult(
            await self.content.put_bytes(source.read_bytes(), media_type="text/javascript"), trace
        )
        write_json(folder / "task-result.json", result, immutable=True)
        self.emit(f"Task Agent finished: {execution_key}; output={folder}")
        return result


class IsolatedVerifier:
    def __init__(self, root, content, emit):
        self.root, self.content, self.emit = root, content, emit

    async def verify(self, request):
        result = request.result
        source = await self.content.read_bytes(result.output)
        if result.output.media_type != "text/javascript":
            raise ValueError("Expected model.js file contents")
        folder = self.root / result.trajectory.trajectory_id
        folder.mkdir(parents=True)
        evaluation = folder / "evaluation"
        prepared = await asyncio.to_thread(
            subprocess.run,
            ["bash", str(EXAMPLE / "run.sh"), "prepare", str(evaluation)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        (folder / "prepare.log").write_text(prepared.stdout + prepared.stderr)
        prepared.check_returncode()
        (evaluation / "src/model.js").write_bytes(source)
        sandbox = folder / "sandbox"
        sandbox.mkdir()
        node = Path(shutil.which("node")).resolve()
        sandbox_wrapper(sandbox, evaluation, node)
        profile = sandbox / "sandbox.sb"
        profile.write_text(profile.read_text() + "\n(deny network*)\n")
        checked = await asyncio.to_thread(
            subprocess.run,
            ["/usr/bin/sandbox-exec", "-f", str(profile), str(node), "scripts/check-model.mjs"],
            cwd=evaluation,
            env={"PATH": os.environ.get("PATH", ""), "GAMEAGENT_GDD_STYLE_ID": "piggy-island"},
            capture_output=True,
            text=True,
            timeout=120,
        )
        (folder / "validator.stdout").write_text(checked.stdout)
        (folder / "validator.stderr").write_text(checked.stderr)
        if checked.returncode not in (0, 1):
            raise RuntimeError(f"Validator process failed: exit {checked.returncode}")
        passed = checked.returncode == 0
        verification = VerificationResult(
            request.task.task_id,
            result.trajectory.trajectory_id,
            passed,
            float(passed),
            {"contract_v2": float(passed)},
            (checked.stdout + checked.stderr).strip(),
            attachments=(
                await self.content.put_json(
                    {
                        "source_sha256": hashlib.sha256(source).hexdigest(),
                        "returncode": checked.returncode,
                        "scope": SCOPE,
                    }
                ),
            ),
        )
        write_json(folder / "verification.json", verification)
        self.emit(f"Verifier: passed={passed}; trajectory={result.trajectory.trajectory_id}")
        return verification


def check_plan(plan, baseline_id, benchmark_name):
    if not isinstance(plan, ExperimentPlan) or (
        plan.max_iterations != 1
        or plan.research_agent_count != 1
        or plan.target != EvolutionTarget.HARNESS
        or plan.baseline_solution_id != baseline_id
        or plan.benchmark != benchmark_name
        or plan.workflow != "harness-evolution"
    ):
        raise ValueError("RSI plan exceeds the authorized one-round prompt experiment")


async def main(tasks, *, resume=None, retry_incomplete=False):
    if retry_incomplete and resume is None:
        raise ValueError("Incomplete retry requires an explicit resume directory")
    settings = AgentModelSettings.from_environment()
    settings.validate_agents()
    connections = {
        "task-agent": ModelConfig.from_environment("target"),
        "rsi-agent": settings.resolve("rsi-agent"),
        "research-agent": settings.resolve("research-agent"),
    }
    template = DATASET / "viewer-template"
    snapshot = json.loads((DATASET / "snapshot.json").read_text())
    for name, digest in snapshot["files"].items():
        if hashlib.sha256((template / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Fixed template changed: {name}")
    saved = None
    if resume is not None:
        root = resume.resolve()
        root.relative_to((DATASET / "outputs").resolve())
        saved = json.loads((root / "inputs.json").read_text())
        expected = {
            "snapshot": snapshot,
            "scope": SCOPE,
            "generation_environment": GENERATION_ENVIRONMENT,
            "research_feedback_delivery": "evidence-files-v1",
            "task_max_tokens_per_request": 4096,
            "request_timeout_seconds": 600,
            "task_message": TASK_MESSAGE,
        }
        if any(saved.get(key) != value for key, value in expected.items()):
            raise ValueError("Resume configuration differs from recorded experiment")
        task = decode(Task, saved["task"])
    else:
        task = (await ThreeModelBenchmark(tasks).list_tasks(BenchmarkSplit.TRAIN))[0]
        root = DATASET / "outputs" / new_id("model001_full")
        root.mkdir(parents=True)
    benchmark = DiagnosticBenchmark(task, snapshot)

    def emit(message):
        print(f"{now()} {message}", flush=True)
        with (root / "events.log").open("a") as stream:
            stream.write(f"{now()} {message}\n")

    emit(f"Full experiment: {root}")
    content = FileContentStore(root / "content")
    versions = FileVersionManager(root / "versions")
    traces = FileTrajectoryStore(root / "trajectories", content)
    memory = FileMemory(root / "memory")
    if saved:
        models = {name: decode(ModelVersion, value) for name, value in saved["models"].items()}
        for name, config in connections.items():
            if await content.read_json(models[name].content) != config.public_identity():
                raise ValueError(f"Resume model connection differs: {name}")
        baseline = decode(SolutionVersion, saved["baseline"])
    else:
        harness_dir = root / "baseline-harness"
        harness_dir.mkdir()
        shutil.copy2(EXAMPLE / "baseline.md", harness_dir / "prompt.md")
        (harness_dir / "mcp.json").write_text('{"servers": []}\n')
        harness = await versions.create_harness(
            "3d-minimal-v1", await content.snapshot(harness_dir)
        )
        models = {
            name: await versions.create_model(
                config.model, await content.put_json(config.public_identity())
            )
            for name, config in connections.items()
        }
        baseline = await versions.create_solution(models["task-agent"], harness)
    write_json(
        root / "inputs.json",
        {
            "task": task,
            "baseline": baseline,
            "snapshot": snapshot,
            "scope": SCOPE,
            "models": models,
            "generation_environment": GENERATION_ENVIRONMENT,
            "research_feedback_delivery": "evidence-files-v1",
            "task_max_tokens_per_request": 4096,
            "request_timeout_seconds": 600,
            "task_message": TASK_MESSAGE,
        },
        immutable=True,
    )
    proxies, runtimes = [], {}
    rsi = None
    try:
        for name, connection in connections.items():
            proxy = GatewayProxy(connection.base_url, connection.api_key)
            proxies.append(proxy)
            runtimes[name] = DshAgentRuntime(
                root / "sessions" / name,
                content,
                traces,
                base_url=proxy.url,
                api_key="local-proxy",
                timeout=600,
                max_tokens=12288 if name == "research-agent" else 4096,
                dsh_root=EXAMPLE.parents[1] / ".rsi/local",
                credential_env_names=settings.credential_env_names,
            )
            emit(f"Configured {name}: {connection.model}")
        rsi = DefaultRsiAgent(
            runtimes["rsi-agent"],
            models["rsi-agent"],
            baseline.solution_id,
            benchmark.name,
            benchmark_description=SCOPE + "; only prompt.md may change; no new tools/dependencies",
            target_model_name=models["task-agent"].name,
        )
        message = (
            "Start exactly one iteration with one Research Agent to improve the reusable Harness "
            "for MODEL-001. Only change prompt.md; preserve framework helpers, file output, "
            "Contract V2 precedence, tools, model and generation limits. "
            "Do not embed task-specific "
            "answers or node names. Use training evidence to improve general construction and "
            "self-check instructions. No Qwen-VL, visual scoring or additional iterations. "
            "This is a same-task diagnostic, not held-out evaluation."
        )
        if saved:
            plan = decode(ExperimentPlan, json.loads((root / "rsi-plan.json").read_text()))
            emit("Resume existing RSI plan and completed workflow checkpoints")
        else:
            (root / "rsi-request.txt").write_text(message)
            emit("RSI Agent: request one-round plan")
            plan = await rsi.chat(message)
            write_json(root / "rsi-plan.json", plan)
        check_plan(plan, baseline.solution_id, benchmark.name)
        await rsi.close()
        verifier = IsolatedVerifier(root / "verification", content, emit)
        researcher = DefaultResearchAgent(
            "research-agent-1",
            runtimes["research-agent"],
            models["research-agent"],
            content,
            memory,
            versions,
            root / "research-workspaces",
            editable_path=lambda name: name == "prompt.md",
            evidence_access=lambda entry: (
                entry.kind
                in {"report", "task", "input", "output", "verification", "trajectory", "attachment"}
            ),
            evidence_instructions="Read train/index.json, then report.json and each "
            "tasks/*/verification.json separately. After identifying the diagnostic, read "
            "relevant line ranges of tasks/*/output (the original model.js) and only necessary "
            "trajectory excerpts. Keep each tool response below 6000 bytes; if truncated, "
            "continue with smaller ranges. Never dump the entire source or trajectory. "
            "Do not run or import evidence code.",
            harness_description="Only prompt.md is editable; no new tools or dependencies. "
            "Evidence is provided as separate files. Preserve Contract V2 precedence "
            "and file output. No benchmark-specific answer or node names in the prompt.",
        )
        context = WorkflowContext(
            content,
            memory,
            traces,
            versions,
            FileTaskRuntime(
                root / "tasks",
                content,
                runtimes["task-agent"],
                template,
                emit,
                retry_incomplete=retry_incomplete,
            ),
            rsi,
            (researcher,),
            benchmark,
            verifier,
            VerifierSpec("contract-v2", "external-3d-sandbox", timeout_seconds=360),
            ThreeModelEvaluation(),
            LocalHarnessBuilder(content, root / "builds"),
        )
        service = LocalExperimentService(root / "experiment", context, emit)
        if saved:
            runs = await service.list()
            if len(runs) != 1 or runs[0].plan != plan:
                raise ValueError("Resume requires exactly one matching saved workflow")
            if runs[0].state == RunState.SUCCEEDED:
                emit("Experiment already completed; no model calls made")
                return root
            write_json(root / "resume-history" / f"{new_id('state')}.json", runs[0], immutable=True)
            started = await service.resume(runs[0].run_id)
        else:
            started = await service.start(plan)
        result = await service.tasks[started.run_id]
        write_json(root / "result.json", result)
        if result.state != RunState.SUCCEEDED:
            raise RuntimeError(f"Full workflow did not finish: {result.error}")
        candidate = await versions.get_solution(
            result.research_agents[0].iterations[0].candidate_solution_id
        )
        await content.materialize(candidate.harness.content, root / "candidate-harness")
        emit(f"Finished. Result: {root / 'result.json'}")
    finally:
        try:
            if rsi is not None:
                await rsi.close()
        finally:
            for proxy in proxies:
                proxy.close()
    return root


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tasks", type=Path)
    group.add_argument("--resume", type=Path)
    parser.add_argument("--retry-incomplete-task", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.tasks, resume=args.resume, retry_incomplete=args.retry_incomplete_task))
