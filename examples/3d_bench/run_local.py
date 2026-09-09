"""One real RSI round over a selected 3D task suite, with explicit sample reuse."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from adapter import ThreeModelBenchmark, ThreeModelEvaluation, load_tasks
from environment import ENVIRONMENT_VERSION, PUBLIC_FILES, ModelEnvironment, ValidationService

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
from areal_rsi.modules.runtime import sandbox_wrapper
from areal_rsi.modules.runtime_package import PackageAgentRuntime, RuntimeSettings
from areal_rsi.workflows.service import LocalExperimentService

TASK_MESSAGE = "Read TARGET.md and CAPABILITIES.md, then implement src/model.js."
SCOPE = "shared train/validate diagnostic; Contract V2 and build, no visual or held-out score"
GENERATION_ENVIRONMENT = ENVIRONMENT_VERSION
EXAMPLE = Path(__file__).resolve().parent
DATASET = EXAMPLE.parents[1] / ".rsi/datasets/3d-model-v2"
CASE = EXAMPLE / "cases"
BASELINE_PROMPT = """Complete TARGET.md using the supplied Three.js framework.
Only edit the body of createModel in src/model.js and preserve the framework helpers.
Use the editor tool to write src/model.js directly; do not embed the full source in
python -c, shell heredocs, or workspace.run arguments. Use bash only to run the
available build and validation commands. Use CONTRACT_V2.md as the authoritative
interface and deliver src/model.js as a file.
"""


def prepare_public_workspace(template: Path, workspace: Path) -> None:
    """Copy only the public model inputs into a fresh Task Agent workspace."""
    package = json.loads((template / "package.json").read_text())
    if package.get("type") != "module":
        raise ValueError("Contract V2 template must explicitly declare type=module")
    for name in ("src/model.js", "CONTRACT_V2.md", "package.json"):
        destination = workspace / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(template / name, destination)


class DiagnosticBenchmark:
    """Explicit TRAIN/VALIDATE overlap, never a held-out benchmark result."""

    def __init__(self, task, snapshot, *, scope=None):
        self.tasks = (task,) if isinstance(task, Task) else tuple(task)
        if not self.tasks:
            raise ValueError("The benchmark task list cannot be empty")
        label = self.tasks[0].task_id if len(self.tasks) == 1 else f"{len(self.tasks)} tasks"
        scope = scope if scope is not None else f"{label} {SCOPE}"
        identity = json.dumps(
            {"task": encode(task), "snapshot": snapshot, "scope": scope}, sort_keys=True
        ).encode()
        prefix = (
            f"3d-{label.lower().replace('-', '')}-diagnostic-"
            if len(self.tasks) == 1
            else "3d-suite-diagnostic-"
        )
        self.name = prefix + hashlib.sha256(identity).hexdigest()[:16]

    async def list_tasks(self, split):
        if split not in (BenchmarkSplit.TRAIN, BenchmarkSplit.VALIDATE):
            raise ValueError("Unsupported split")
        return tuple(decode(Task, encode(task)) for task in self.tasks)


class FileTaskRuntime:
    def __init__(
        self,
        root,
        content,
        runtime,
        template,
        emit,
        *,
        retry_incomplete=False,
        environment=None,
        reuse_outputs=False,
    ):
        self.root, self.content, self.runtime = root, content, runtime
        self.template, self.emit = template, emit
        self.retry_incomplete = retry_incomplete
        self.environment = environment
        self.reuse_outputs = reuse_outputs
        self.reuse_locks = {}

    async def run(self, solution, task, *, config=None):
        if not self.reuse_outputs:
            return await self._generate(solution, task, config=config)
        identity = {"solution": encode(solution), "task": encode(task)}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        # TRAIN deliberately uses the recorded baseline samples in same-suite experiments.
        # A per-sample lock also prevents duplicate paid calls from concurrent consumers.
        async with self.reuse_locks.setdefault(key, asyncio.Lock()):
            cache = self.root / "shared-samples" / f"{key}.json"
            if cache.exists():
                saved = json.loads(cache.read_text())
                if saved["identity"] != identity:
                    raise ValueError("Shared sample identity mismatch")
                execution_key = config["execution_key"]
                write_json(
                    self.root
                    / "reused"
                    / (hashlib.sha256(execution_key.encode()).hexdigest() + ".json"),
                    {
                        "execution_key": execution_key,
                        "origin": saved["execution_key"],
                        "result": saved["result"],
                    },
                    immutable=True,
                )
                self.emit(f"Reuse recorded Task sample: {execution_key}")
                return decode(TaskResult, saved["result"])
            result = await self._generate(solution, task, config=config)
            write_json(
                cache,
                {"identity": identity, "execution_key": config["execution_key"], "result": result},
                immutable=True,
            )
            return result

    async def _generate(self, solution, task, *, config=None):
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
        service = None
        if self.environment is None:
            # Kept for small offline fixtures; real runs always use the complete environment.
            prepare_public_workspace(self.template, workspace)
        else:
            await asyncio.to_thread(self.environment.prepare_workspace, workspace)
        (workspace / "TARGET.md").write_text(str(task.input))
        await self.content.materialize(solution.harness.content, folder / "harness")
        prompt = await self.content.put_text((folder / "harness/prompt.md").read_text())
        self.emit(f"Task Agent start: {execution_key}")
        # Start the service only after fallible preparation; always close it below.
        try:
            if self.environment is not None:
                service = ValidationService(self.environment, folder / "self-checks")
                service.install_client(workspace)
            return await self._execute(solution, task, folder, workspace, prompt, service)
        finally:
            if service is not None:
                await asyncio.to_thread(service.close)

    async def _execute(self, solution, task, folder, workspace, prompt, service):
        """Run one generation while the caller owns the validation service lifetime."""
        fixed = (
            ("package.json", "CONTRACT_V2.md", "TARGET.md")
            if service is None
            else (
                *(name for name in PUBLIC_FILES if name != "src/model.js"),
                "TARGET.md",
                "CAPABILITIES.md",
                "scripts/check-model.mjs",
                "scripts/validate-model.sh",
            )
        )
        hashes = {
            name: hashlib.sha256((workspace / name).read_bytes()).hexdigest() for name in fixed
        }
        write_json(folder / "public-inputs.json", hashes, immutable=True)
        session = None
        try:
            session = await self.runtime.open(
                AgentSpec(
                    "task-agent",
                    "deepseek-harness@v1",
                    solution.model,
                    (prompt,),
                    tools=("bash@v1", "editor@v1"),
                ),
                workspace_root=str(workspace),
            )
            response = await session.send(TASK_MESSAGE)
            (folder / "response.txt").write_text(response)
        finally:
            if session is not None:
                trace = await session.finish()
        for name, digest in hashes.items():
            path = workspace / name
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Task Agent modified fixed input: {name}")
        source = workspace / "src/model.js"
        if source.is_symlink() or source.parent.is_symlink() or not source.is_file():
            raise ValueError("Task output is missing or a symlink")
        result = TaskResult(
            await self.content.put_bytes(source.read_bytes(), media_type="text/javascript"), trace
        )
        write_json(folder / "task-result.json", result, immutable=True)
        self.emit(f"Task Agent finished: {task.task_id}; output={folder}")
        return result


class IsolatedVerifier:
    def __init__(self, root, content, emit, *, environment=None):
        self.root, self.content, self.emit = root, content, emit
        self.environment = environment

    async def verify(self, request):
        result = request.result
        source = await self.content.read_bytes(result.output)
        if result.output.media_type != "text/javascript":
            raise ValueError("Expected model.js file contents")
        folder = self.root / result.trajectory.trajectory_id
        if self.environment is not None:
            # One source may serve both TRAIN and VALIDATE; retain their receipts separately.
            key = hashlib.sha256(json.dumps(request.metadata, sort_keys=True).encode()).hexdigest()
            folder = folder / key[:16]
            # Final evaluation never trusts the agent's local check result or project files.
            folder.mkdir(parents=True, exist_ok=True)
            checked = await asyncio.to_thread(
                self.environment.validate, source, folder / new_id("check")
            )
            passed = checked["passed"]
            feedback = "\n".join(
                f"{name}: {'passed' if item['returncode'] == 0 else 'failed'}\n{item['stderr']}"
                for name, item in checked["checks"].items()
            )
            if "receipt_error" in checked:
                feedback += "\n" + checked["receipt_error"]
            verification = VerificationResult(
                request.task.task_id,
                result.trajectory.trajectory_id,
                passed,
                float(passed),
                {
                    "contract_v2": float(
                        checked["checks"]["contract"]["returncode"] == 0
                        and "receipt_error" not in checked
                    ),
                    "build": float(checked["checks"]["build"]["returncode"] == 0),
                },
                feedback,
                attachments=(await self.content.put_json(checked),),
            )
            write_json(folder / "verification.json", verification)
            self.emit(f"Verifier: passed={passed}; trajectory={result.trajectory.trajectory_id}")
            return verification
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
                        "scope": f"{request.task.task_id} {SCOPE}",
                    }
                ),
            ),
        )
        write_json(folder / "verification.json", verification)
        self.emit(f"Verifier: passed={passed}; trajectory={result.trajectory.trajectory_id}")
        return verification


def check_plan(plan, baseline_id, benchmark_name):
    if not isinstance(plan, ExperimentPlan) or (
        plan.max_iterations != 3
        or plan.research_agent_count != 1
        or plan.target != EvolutionTarget.HARNESS
        or plan.baseline_solution_id != baseline_id
        or plan.benchmark != benchmark_name
        or plan.workflow != "harness-evolution"
    ):
        raise ValueError("RSI plan exceeds the authorized one-round prompt experiment")


async def main(
    tasks, *, resume=None, retry_incomplete=False, all_tasks=False, task_ids=None, concurrency=None
):
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
    prepared = DATASET / "runtime" / ENVIRONMENT_VERSION
    if not prepared.exists():
        prepared.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            subprocess.run, ["bash", str(EXAMPLE / "run.sh"), "prepare", str(prepared)], check=True
        )
    environment = ModelEnvironment(template, prepared)
    environment.check()
    # The prepared runtime is checked before any model call; generated files are
    # validated independently after each Task Agent run.
    saved = None
    if resume is not None:
        root = resume.resolve()
        root.relative_to((DATASET / "outputs").resolve())
        saved = json.loads((root / "inputs.json").read_text())
        expected = {
            "snapshot": snapshot,
            "generation_environment": GENERATION_ENVIRONMENT,
            "research_feedback_delivery": "evidence-files-v1",
            "task_max_tokens_per_request": 4096,
            "request_timeout_seconds": 600,
            "task_message": TASK_MESSAGE,
        }
        if any(saved.get(key) != value for key, value in expected.items()):
            raise ValueError("Resume configuration differs from recorded experiment")
        selected = tuple(decode(Task, item) for item in saved.get("tasks", [saved.get("task")]))
        concurrency = saved.get("concurrency", 1)
        reuse_outputs = saved.get("reuse_baseline_samples", False)
        scope = saved["scope"]
    else:
        if all_tasks or task_ids:
            selected = load_tasks(tasks)
            if task_ids:
                requested = set(task_ids)
                if len(requested) != len(task_ids) or not requested <= {
                    t.task_id for t in selected
                }:
                    raise ValueError("Unknown or duplicate selected task IDs")
                selected = tuple(task for task in selected if task.task_id in requested)
        else:
            selected = tuple(await ThreeModelBenchmark(tasks).list_tasks(BenchmarkSplit.TRAIN))
        concurrency = concurrency if concurrency is not None else (4 if len(selected) > 1 else 1)
        if type(concurrency) is not int or not 1 <= concurrency <= 8:
            raise ValueError("Concurrency must be between 1 and 8")
        reuse_outputs = len(selected) > 1
        scope = (
            f"{selected[0].task_id} {SCOPE}"
            if len(selected) == 1
            else (
                f"{len(selected)} tasks; shared TRAIN/VALIDATE with reused baseline samples; "
                "Contract V2 and build; no visual or held-out score"
            )
        )
        root = DATASET / "outputs" / new_id("model001_full" if len(selected) == 1 else "suite_full")
        root.mkdir(parents=True)
    benchmark = DiagnosticBenchmark(
        selected[0] if len(selected) == 1 else selected, snapshot, scope=scope
    )

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
        (harness_dir / "prompt.md").write_text(BASELINE_PROMPT)
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
        saved
        or {
            "tasks": selected,
            "baseline": baseline,
            "snapshot": snapshot,
            "scope": scope,
            "concurrency": concurrency,
            "reuse_baseline_samples": reuse_outputs,
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
    runtime_settings = RuntimeSettings.from_environment()
    rsi = None
    try:
        for name, connection in connections.items():
            proxy = GatewayProxy(connection.base_url, connection.api_key)
            proxies.append(proxy)
            runtimes[name] = PackageAgentRuntime(
                root / "sessions" / name,
                content,
                traces,
                settings=runtime_settings,
                role=name,
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
            benchmark_description=scope + "; only prompt.md may change; no new tools/dependencies",
            target_model_name=models["task-agent"].name,
        )
        message = (
            "Start exactly three iterations with one Research Agent to improve the reusable "
            "Harness "
            f"for the selected {len(selected)} 3D modeling tasks. "
            "Only change prompt.md; preserve framework helpers, file output, "
            "Contract V2 precedence, tools, model and generation limits. "
            "Do not embed task-specific "
            "answers or node names. Use training evidence to improve general construction and "
            "self-check instructions. No Qwen-VL or visual scoring. "
            "This is a same-suite diagnostic, not held-out evaluation. "
            "For multi-task runs, TRAIN reuses baseline samples without another generation."
        )
        if saved:
            plan = decode(ExperimentPlan, json.loads((root / "rsi-plan.json").read_text()))
            emit("Resume existing RSI plan and completed workflow checkpoints")
        else:
            (root / "rsi-request.txt").write_text(message)
            emit("RSI Agent: request three-iteration plan")
            plan = await rsi.chat(message)
            write_json(root / "rsi-plan.json", plan)
        check_plan(plan, baseline.solution_id, benchmark.name)
        await rsi.close()
        verifier = IsolatedVerifier(root / "verification", content, emit, environment=environment)
        researcher = DefaultResearchAgent(
            "research-agent-1",
            runtimes["research-agent"],
            models["research-agent"],
            content,
            memory,
            versions,
            root / "research-workspaces",
            editable_path=lambda name: name == "prompt.md",
            file_tools_only=True,
            evidence_access=lambda entry: (
                entry.kind
                in {"report", "task", "input", "output", "verification", "trajectory", "attachment"}
            ),
            evidence_instructions="Read train/index.json, then report.json and each "
            "tasks/*/verification.json separately. After identifying the diagnostic, read "
            "relevant line ranges of tasks/*/output (the original model.js) and only necessary "
            "trajectory excerpts. Use inspect_json, read_json pointers and search_file. "
            "Continue paginated responses using next_offset. Never read the entire trajectory. "
            "Do not run or import evidence code.",
            harness_description="Only prompt.md is editable; no new tools or dependencies. "
            "Task Agent has an installed Three.js/Vite project and CAPABILITIES.md. "
            "It can run npm run build and npm run validate:model -- .validation/result.json; "
            "the latter calls the host's isolated Contract V2 checker and build. "
            "It cannot inspect verifier source; there is no /output mount. "
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
                environment=environment,
                reuse_outputs=reuse_outputs,
            ),
            rsi,
            (researcher,),
            benchmark,
            verifier,
            VerifierSpec("contract-v2", "external-3d-sandbox", timeout_seconds=360),
            ThreeModelEvaluation(),
            LocalHarnessBuilder(content, root / "builds"),
            evaluation_concurrency=concurrency,
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
        # Export the final iteration, not the first intermediate prompt.
        candidate = await versions.get_solution(
            result.research_agents[0].iterations[-1].candidate_solution_id
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
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--retry-incomplete-task", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        main(
            CASE,
            resume=args.resume,
            retry_incomplete=args.retry_incomplete_task,
        )
    )
