"""Bind task benchmarks to ResearchApp's frozen-candidate verification jobs.

ResearchApp owns planning, researchers, candidate selection and stopping. This
adapter executes one requested evaluation; it has no research loop or workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import signal
import sys
from pathlib import Path
from typing import Any

from areal_rsi.api.base import (
    BenchmarkSplit,
    HarnessChange,
    VerificationRequest,
    VerifierSpec,
)
from areal_rsi.api.modules import Benchmark, Evaluation, HarnessRuntime, Verifier
from areal_rsi.api.research import ResearchLimits, TaskBrief, VerificationContract
from areal_rsi.base.storage import (
    FileContentStore,
    FileTrajectoryStore,
    FileVersionManager,
    encode,
    json_bytes,
    write_json,
)
from areal_rsi.config import ModelConfig
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.benchmark import AccuracyEvaluation, ArithmeticBenchmark, ArithmeticVerifier
from areal_rsi.modules.gateway import GatewayProxy
from areal_rsi.modules.harness import LocalHarnessRuntime, validate_harness
from areal_rsi.modules.remote.envarena.benchmark import ArenaBenchmark
from areal_rsi.modules.remote.envarena.client import ArenaClient, ArenaConnection
from areal_rsi.modules.remote.envarena.config import ArenaConfig
from areal_rsi.modules.remote.envarena.harness import (
    REMOTE_CHANGE_TYPE,
    ArenaHarnessBuilder,
    snapshot,
)
from areal_rsi.modules.remote.envarena.runtime import (
    ArenaEvaluation,
    ArenaHarnessRuntime,
    ArenaVerifier,
)
from areal_rsi.modules.runtime_package import PackageAgentRuntime, RuntimeSettings

PROTOCOL = "researchapp-harness-benchmark-v1"


def manifest_identity(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(json_bytes(manifest)).hexdigest()


async def prepare(
    benchmark: str,
    directory: Path,
    *,
    arena_config: Path | None = None,
    max_wall_seconds: float = 1200,
    backend: str = "standard",
) -> Path:
    """Prepare operator-owned inputs and a ResearchConfig, without running an experiment."""
    directory = directory.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Use a new setup directory to preserve experiment identities")
    directory.mkdir(parents=True, exist_ok=True)
    seed = directory / "seed"
    manifest: dict[str, Any] = {"protocol_id": PROTOCOL, "benchmark": benchmark}
    allowed: tuple[str, ...]
    if benchmark == "math":
        target = ModelConfig.from_environment("target")
        manifest["target"] = target.public_identity()
        manifest["target_runtime"] = {
            "default": {"kind": "codex-cli", "config": {"modelProtocol": "responses"}}
        }
        shutil.copytree(Path(__file__).parents[1] / "default_harness", seed)
        tasks = await ArithmeticBenchmark().list_tasks(BenchmarkSplit.TRAIN)
        allowed = ("prompt.md", "skills/*")
        name = "Arithmetic harness research"
    elif benchmark == "lcb":
        if arena_config is None:
            raise ValueError("LCB requires a pinned Arena evaluation configuration")
        client = ArenaClient(ArenaConnection.from_environment())
        arena = await ArenaConfig.load(arena_config, client)
        frozen_config = directory / "arena.yaml"
        shutil.copyfile(arena_config, frozen_config)
        manifest["arena_config_sha256"] = hashlib.sha256(frozen_config.read_bytes()).hexdigest()
        manifest["arena_base_url"] = client.connection.base_url
        bench = ArenaBenchmark(client, arena)
        await bench.load()
        harness = await client.version("harnesses", arena.harness)
        model = await client.version("models", arena.model)
        prompt = harness["spec"].get("system_prompt", {})
        if prompt.get("kind", "inline") != "inline" or not prompt.get("content"):
            raise ValueError("This ResearchApp adapter requires an inline system prompt")
        if "system_prompt" not in arena.allowed_changes:
            raise ValueError("The pinned benchmark must allow system_prompt changes")
        seed.mkdir()
        (seed / "prompt.md").write_text(prompt["content"])
        manifest["target"] = {"model_ref": arena.model, "model_name": model["spec"]["model_name"]}
        manifest["benchmark_spec"] = bench.spec
        manifest["harness_ref"] = arena.harness
        tasks = await bench.list_tasks(BenchmarkSplit.TRAIN)
        allowed = ("prompt.md",)
        name = "LiveCodeBench harness research"
    else:
        raise ValueError("Unknown benchmark")
    manifest["train_tasks"] = encode(tasks)
    manifest_path = directory / "manifest.json"
    write_json(manifest_path, manifest, immutable=True)
    identity = manifest_identity(manifest)
    write_json(directory / "public-train.json", encode(tasks), immutable=True)
    config = ResearchConfig(
        TaskBrief(
            name,
            "完成一次真实的 harness 研究端到端实验。固定目标模型、题目划分、运行时和评分器。"
            "先用 verify_candidate(phase='search') 评测 seed_candidate_id，读取 TRAIN 反馈；"
            "然后创建一名研究员，根据反馈修改工作区的 prompt.md 或允许的 skill，"
            "生成与起点不同的通用候选，禁止写入具体题目的答案或针对题号分支。"
            "研究员使用 save_candidate 保存候选，并通过 verify_candidate(phase='search') "
            "执行真实 TRAIN 评测，然后正式报告候选与结果。"
            "RSI 对选中的新候选调用 verify_candidate(phase='final') 完成固定 VALIDATE 复测；"
            "最终反馈仅用于验收，不再修改候选。最后 finish(candidate_id=该候选) 结束研究。"
            "本次是小规模端到端验证，不要求超过基线；正确候选应尽快完成，不扩展题目和预算。",
            ResearchLimits(
                max_agents=1,
                max_nodes=4,
                max_total_tokens=2_000_000,
                max_wall_seconds=max_wall_seconds,
                max_verify_calls=4,
                max_jobs=1,
                allowed_paths=allowed,
            ),
            VerificationContract(
                "accuracy",
                target=None,
                description="Fixed TRAIN feedback and independent VALIDATE confirmation",
                identity={"require_final_confirmation": True},
            ),
        ),
        backend=backend,
        seed_dir=str(seed),
        verifier=ApprovedCommand(
            "evaluate_benchmark",
            (
                sys.executable,
                "-m",
                "areal_rsi.adapters.benchmark_research",
                "evaluate",
                "--manifest",
                str(manifest_path),
                "--identity",
                identity,
                "--workspace",
                "{workspace}",
                "--output",
                "{output}",
                "--phase",
                "{phase}",
            ),
            ("workspace", "output", "phase"),
            timeout_seconds=900,
            public_output_subdir="public",
        ),
        metadata={
            "evaluate_seed_as_baseline": True,
            "allow_web_access": False,
            "agent_tool_instructions": "只修改自己的候选目录；用登记的验证工具运行目标模型，"
            "不要直接调用外部模型或读取宿主评测实现、答案及其他实验。",
            "execution_instructions": "verify_candidate 的 search 使用 TRAIN，final 使用 VALIDATE。"
            "每次验证是真实目标模型执行。完整 TRAIN 轨迹和反馈在验证作业的公开输出目录；"
            "通过 job_status/read_job_output 读取。当前没有额外的 run_job 命令。",
            "verification_identity": {
                "adapter": "content",
                "phases": {
                    phase: {"protocol_id": PROTOCOL, "manifest_id": identity, "split": split}
                    for phase, split in (("search", "train"), ("final", "validate"))
                },
            },
        },
    )
    config.pin_model_settings()
    config.runtime()
    path = directory / "research.json"
    write_json(path, config.public(), immutable=True)
    return path


async def evaluate(
    manifest_path: Path, identity: str, workspace: Path, output: Path, phase: str
) -> dict[str, Any]:
    """Run the fixed evaluator on a frozen candidate; never instantiate a ResearchApp."""
    manifest = json.loads(manifest_path.read_text())
    if manifest_identity(manifest) != identity or manifest.get("protocol_id") != PROTOCOL:
        raise ValueError("Benchmark manifest changed after configuration was frozen")
    if phase not in {"search", "final"}:
        raise ValueError("Benchmark verification phase must be search or final")
    workspace, output = workspace.resolve(), output.resolve()
    run_root = workspace.parents[2]
    job = json.loads((run_root / "jobs" / output.name / "job.json").read_text())
    metadata = job["spec"]["metadata"]
    frozen = json.loads((workspace.parent / "snapshot.json").read_text())
    if metadata["candidate_digest"] != frozen["digest"] or metadata["phase"] != phase:
        raise ValueError("Evaluation job does not match its frozen candidate")
    split = BenchmarkSplit.TRAIN if phase == "search" else BenchmarkSplit.VALIDATE
    output.mkdir(parents=True, exist_ok=True)
    content = FileContentStore(output / "private/content")
    versions = FileVersionManager(output / "private/versions")
    traces = FileTrajectoryStore(output / "private/trajectories", content)
    runtime: PackageAgentRuntime | None = None
    proxy: GatewayProxy | None = None
    runner: HarnessRuntime
    bench: Benchmark
    verifier: Verifier
    aggregator: Evaluation
    try:
        if manifest["benchmark"] == "math":
            validate_harness(workspace)
            target = ModelConfig.from_environment("target")
            if target.public_identity() != manifest["target"]:
                raise ValueError("Target model connection changed after preparation")
            model = await versions.create_model(
                target.model, await content.put_json(manifest["target"])
            )
            harness = await versions.create_harness("candidate", await content.snapshot(workspace))
            proxy = GatewayProxy(target.base_url, target.api_key)
            runtime = PackageAgentRuntime(
                output / "private/sessions",
                content,
                traces,
                settings=RuntimeSettings(manifest["target_runtime"]),
                role="task-agent",
                base_url=proxy.url,
                api_key="local-proxy",
                timeout=300,
                dsh_root=Path.home() / ".cache/areal-rsi",
            )
            runner = LocalHarnessRuntime(runtime, content, output / "private/workspaces")
            bench = ArithmeticBenchmark()
            verifier = ArithmeticVerifier(content)
            aggregator = AccuracyEvaluation()
        else:
            config_path = manifest_path.parent / "arena.yaml"
            if (
                hashlib.sha256(config_path.read_bytes()).hexdigest()
                != manifest["arena_config_sha256"]
            ):
                raise ValueError("Pinned Arena evaluator configuration changed")
            client = ArenaClient(ArenaConnection.from_environment())
            if client.connection.base_url != manifest["arena_base_url"]:
                raise ValueError("Arena endpoint changed after preparation")
            config = await ArenaConfig.load(config_path, client)
            bench = ArenaBenchmark(client, config)
            await bench.load()
            if bench.spec != manifest["benchmark_spec"]:
                raise ValueError("Published benchmark identity changed")
            remote_model = await client.version("models", config.model)
            model = await versions.create_model(
                remote_model["spec"]["model_name"],
                await content.put_json({"ref": encode(config.model)}),
            )
            base = await versions.create_harness(
                "baseline", await snapshot(client, content, config.harness)
            )
            prompt = (workspace / "prompt.md").read_text()
            original = json.loads(await content.read_text(base.content))["spec"]["system_prompt"][
                "content"
            ]
            if prompt == original:
                harness = base
            else:
                change = HarnessChange(
                    await content.put_bytes(
                        json_bytes({"system_prompt": prompt}),
                        media_type=REMOTE_CHANGE_TYPE,
                    ),
                    "ResearchApp candidate system prompt",
                )
                ref = await ArenaHarnessBuilder(client, content, config).build(base, change)
                harness = await versions.create_harness("candidate", ref)
            runner = ArenaHarnessRuntime(
                client, content, traces, bench, config, output / "private/arena"
            )
            verifier = ArenaVerifier(content)
            aggregator = ArenaEvaluation()
        solution = await versions.create_solution(model, harness)
        tasks = await bench.list_tasks(split)
        if isinstance(runner, ArenaHarnessRuntime):
            results = await runner.run_many(
                solution,
                tasks,
                config={
                    "execution_key": str(output),
                    "run_id": run_root.name,
                },
            )
        else:
            results = {task.task_id: await runner.run(solution, task) for task in tasks}
        checks = [
            await verifier.verify(
                VerificationRequest(
                    task,
                    solution,
                    results[task.task_id],
                    VerifierSpec("fixed-benchmark", "injected"),
                )
            )
            for task in tasks
        ]
        report = await aggregator.evaluate(solution, split, checks)
        result = {
            "status": "completed",
            "simulated": False,
            "phase": phase,
            "metric": "accuracy",
            "score": report.score,
            # A completed harness evaluation is comparable even below 100%
            # accuracy. Task correctness is represented by the score/results;
            # ResearchApp applies any user-selected target separately.
            "passed": True,
            "metrics": {**report.metrics, "accuracy": report.score},
            "candidate_id": metadata["candidate_id"],
            "candidate_digest": frozen["digest"],
            "protocol_id": PROTOCOL,
            "manifest_id": identity,
            "split": split.value,
        }
        write_json(output / "evaluation.json", result)
        public = output / "public"
        write_json(
            public / "feedback.json",
            {"score": report.score, "split": split.value, "tasks": encode(checks)},
        )
        if phase == "search":
            for task in tasks:
                task_result = results[task.task_id]
                trace = await traces.get(task_result.trajectory.trajectory_id)
                if trace:
                    write_json(
                        public / (task.task_id + ".json"),
                        {
                            "task": encode(task),
                            "output": await content.read_json(task_result.output),
                            "trajectory": await content.read_json(trace.content),
                        },
                    )
        return result
    finally:
        if runtime:
            await runtime.close()
        if proxy:
            await asyncio.to_thread(proxy.close)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("prepare", help="Write a ResearchApp configuration")
    setup.add_argument("--benchmark", choices=("math", "lcb"), required=True)
    setup.add_argument("--directory", type=Path, required=True)
    setup.add_argument("--arena-config", type=Path)
    setup.add_argument("--time-limit", type=float, default=1200)
    setup.add_argument("--backend", choices=("standard", "codex"), default="standard")
    execute = commands.add_parser("evaluate", help="Operator-owned verification job")
    for name in ("manifest", "workspace", "output"):
        execute.add_argument("--" + name, type=Path, required=True)
    execute.add_argument("--identity", required=True)
    execute.add_argument("--phase", choices=("search", "final"), required=True)
    args = parser.parse_args()

    async def run() -> None:
        task = asyncio.current_task()
        assert task is not None
        if args.command == "prepare":
            print(
                await prepare(
                    args.benchmark,
                    args.directory,
                    arena_config=args.arena_config,
                    max_wall_seconds=args.time_limit,
                    backend=args.backend,
                )
            )
        else:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, task.cancel)
            print(
                json.dumps(
                    await evaluate(
                        args.manifest, args.identity, args.workspace, args.output, args.phase
                    ),
                    ensure_ascii=False,
                )
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
