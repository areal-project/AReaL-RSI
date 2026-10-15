"""Bind task benchmarks to ResearchSystem's frozen-candidate verification jobs.

ResearchSystem owns planning, researchers, candidate selection and stopping. This
adapter executes one requested evaluation; it has no research loop or workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import signal
import sys
from pathlib import Path
from typing import Any

from areal_rsi.api.interfaces import Benchmark, Evaluation, HarnessRuntime, Verifier
from areal_rsi.api.records import (
    BenchmarkSplit,
    EvolutionTarget,
    HarnessChange,
    VerificationRequest,
    VerifierSpec,
)
from areal_rsi.api.research import (
    CandidateSnapshot,
    EvidenceRecord,
    HarnessIteration,
    ResearchLimits,
    TaskBrief,
    VerificationContract,
    VerifiedResult,
)
from areal_rsi.infra.config import ModelConfig
from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.infra.coordination.store import ResearchStore
from areal_rsi.infra.execution import ApprovedCommand
from areal_rsi.infra.runtime.gateway import GatewayProxy
from areal_rsi.infra.runtime.runtime_package import PackageAgentRuntime, RuntimeSettings
from areal_rsi.infra.storage.files import (
    FileContentStore,
    FileTrajectoryStore,
    FileVersionManager,
    encode,
    json_bytes,
    write_json,
)
from areal_rsi.infra.tasks.arithmetic import (
    AccuracyEvaluation,
    ArithmeticBenchmark,
    ArithmeticVerifier,
)
from areal_rsi.infra.tasks.counting import CountingBenchmark, count_paths
from areal_rsi.infra.tasks.harness import LocalHarnessRuntime, validate_harness
from areal_rsi.infra.tasks.remote.envarena.benchmark import ArenaBenchmark
from areal_rsi.infra.tasks.remote.envarena.client import ArenaClient, ArenaConnection
from areal_rsi.infra.tasks.remote.envarena.config import ArenaConfig
from areal_rsi.infra.tasks.remote.envarena.harness import (
    REMOTE_CHANGE_TYPE,
    ArenaHarnessBuilder,
    snapshot,
)
from areal_rsi.infra.tasks.remote.envarena.runtime import (
    ArenaEvaluation,
    ArenaHarnessRuntime,
    ArenaVerifier,
)

PROTOCOL = "researchapp-harness-benchmark-v1"

LOOP_INSTRUCTIONS = """完成单题 harness 迭代的本地端到端验收。Leader 亲自完成，固定目标模型与题目。
必须依次执行以下步骤（即使第一轮答对也不能省略）：
1. 将原始工作区 save_candidate 为 v0，用 verify_candidate(phase='diagnostic', wait=true)
   测量基线。通过 job_status 和 read_job_output_file 阅读 feedback.json 及 bounded_paths.json。
2. 用 save_evidence 记录观察、可复用经验、改进假设及局限。references 必须引用刚完成的
   verifications 记录 URI（来自 job_status 或 list_records），不能只引用 job 或候选。
3. 基于这些经验修改 prompt.md 或 skills/*.md；用 save_candidate 保存 v1，然后
   verify_candidate(phase='search', wait=true)。保存前先归档经验，不要提前保存后续版本。
4. 阅读 v1 的真实评测反馈，再 save_evidence（引用 v1 的 verification），改进并保存 v2，
   再做 search。v0 -> v1 -> v2 必须是父子版本链，三份内容不同；只改描述不算新版本。
5. 从已评测的 v1/v2 中选定一个候选做 final，阅读公开 loop.json；最终复测通过后
   用 update_goal 完成本 run，evidence 列出验证及经验记录，并报告各版本的分数和改动。
目标是至少三版、两次基于反馈的改进和一次最终复测；不要求解对或分数提升。
每个作业只调用目标模型解一道题；final 是同一道题的新会话复测，不是独立测试集。
将题目相关的观察保存在 save_evidence；harness 只放通用策略，不得硬编码答案或题号分支。
只修改 prompt.md/skills/*.md，不修改 mcp.json/tools，不在工作区增加实验记录或临时文件。
起点已经答对时，可改进边界检查、独立交叉验证和推导清晰度；仍需真实运行两版改进。
最多正常运行四次评测（diagnostic、search、search、final），基础设施故障时可重试。
只有 verification.status='succeeded' 的结果才算完成一轮（score=0 也有效）。
遇到 infra_error/超时必须先重试同一候选；不能引用失败记录推进下一版或声称验收完成。
保存后核对 candidate_id 和 digest；随后 verify_candidate 必须使用刚保存的准确 ID。
仅使用登记的验证工具调用目标模型，不读取宿主评分器或其他实验。"""


def local_target(*, reuse_research: bool = False) -> ModelConfig:
    if not reuse_research:
        return ModelConfig.from_environment("target")
    config = ResearchConfig(TaskBrief("Math loop", "Local harness iteration"))
    config.pin_model_settings()
    if config.auth_mode != "api_key":
        raise ValueError("Math loop requires RSI_TARGET_* when research uses Codex login")
    return ModelConfig.validated(
        config.model, config.connection_settings().runtime_base_url, config.api_key(), "RSI_"
    )


def math_loop_history(store: ResearchStore, selected: str) -> list[HarnessIteration]:
    """Audit causal, distinct measured versions before allowing final confirmation."""
    candidates = {c.candidate_id: c for c in store.records("candidates", CandidateSnapshot)}
    evidence = store.records("evidence", EvidenceRecord)
    chains: dict[str, list[HarnessIteration]] = {}
    results = sorted(store.records("verifications", VerifiedResult), key=lambda r: r.created_at)
    for result in results:
        if (
            result.status != "succeeded"
            or result.passed is not True
            or result.simulated
            or result.contract_digest != store.contract_digest()
            or result.goal_revision != store.brief().revision
            or result.phase == "final"
        ):
            continue
        candidate = candidates[result.candidate_id]
        if result.phase == "diagnostic":
            chains.setdefault(candidate.candidate_id, [HarnessIteration(candidate, result)])
            continue
        parent_id = candidate.parent_candidate_id or ""
        previous = chains.get(parent_id)
        # An accidental extra save of identical bytes is an alias, not a new iteration.
        # It must neither inflate the count nor break the next genuine improvement.
        while previous is None and parent_id:
            parent_candidate = candidates[parent_id]
            ancestor = candidates.get(parent_candidate.parent_candidate_id or "")
            if ancestor is None or ancestor.content.digest != parent_candidate.content.digest:
                break
            parent_id = ancestor.candidate_id
            previous = chains.get(parent_id)
        if not previous or candidate.content.digest in {
            item.candidate.content.digest for item in previous
        }:
            continue
        parent = previous[-1].verification
        ref = store.paths.reference(store.root / "verifications" / f"{parent.result_id}.json")
        lesson = next(
            (
                e for e in evidence
                if e.agent_id == candidate.source_agent_id
                and e.summary.strip()
                and ref in e.references
                and parent.created_at < e.created_at < candidate.created_at < result.created_at
            ),
            None,
        )
        if lesson is not None:
            chains[candidate.candidate_id] = [
                *previous, HarnessIteration(candidate, result, lesson)
            ]
    valid = [
        chain for chain in chains.values()
        if len(chain) >= 3
        and selected in {item.candidate.candidate_id for item in chain[1:]}
    ]
    if not valid:
        raise ValueError(
            "Math loop needs diagnostic v0 -> search v1 -> search v2 with distinct contents; "
            "before saving each new version, save_evidence referencing its parent's verification. "
            "Select an evaluated search candidate for final."
        )
    return max(valid, key=len)


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
    if benchmark in {"math", "math-loop"}:
        reuse_research = benchmark == "math-loop" and not any(
            os.environ.get("RSI_TARGET_" + key, "").strip()
            for key in ("MODEL", "BASE_URL", "API_KEY")
        )
        target = local_target(reuse_research=reuse_research)
        manifest["target_source"] = "research" if reuse_research else "target"
        manifest["target"] = target.public_identity()
        manifest["target_runtime"] = {
            "default": {"kind": "codex-cli", "config": {"modelProtocol": "responses"}}
        }
        if benchmark == "math-loop":
            tasks = await CountingBenchmark().list_tasks(BenchmarkSplit.TRAIN)
            (seed / "skills").mkdir(parents=True)
            write_json(seed / "mcp.json", {"servers": []})
            (seed / "prompt.md").write_text(
                "Solve the user's mathematical problem. Explain your reasoning concisely.\n"
                "You can run Python to check your work. End with one line: #### NUMBER\n"
            )
            (seed / "skills/reasoning.md").write_text(
                "# Mathematical reasoning\n\nIdentify the constraints and check the result.\n"
            )
            allowed = ("prompt.md", "skills/*.md")
            name = "Single-problem math harness iteration"
        else:
            shutil.copytree(Path(__file__).parents[2] / "default_harness", seed)
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
            raise ValueError("This ResearchSystem adapter requires an inline system prompt")
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
            "Leader 亲自完成本次任务，不创建 Researcher。"
            "先将工作区起点 save_candidate 为自己的候选，用 verify_candidate(phase='diagnostic') "
            "执行 TRAIN 基线诊断；诊断结果仅作为反馈，不参与最佳候选排名。"
            "然后修改通用 prompt.md 或允许的 skill，生成与起点不同的候选，"
            "禁止写入具体题目的答案或针对题号分支。保存新候选并用 "
            "verify_candidate(phase='search') 执行真实 TRAIN 评测，报告候选和证据。"
            "在研究预算内分析与改进，选定候选后调用 final 验证。"
            "只有同一候选通过固定 VALIDATE 最终复测后，才能用 update_goal 完成本 run。"
            "本次是小规模端到端验证，不要求超过基线，不扩展题目或改变评测协议。",
            ResearchLimits(
                max_agents=1,
                max_wall_seconds=max_wall_seconds,
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
        evolution_target=EvolutionTarget.HARNESS,
        seed_dir=str(seed),
        verifier=ApprovedCommand(
            "evaluate_benchmark",
            (
                sys.executable,
                "-m",
                "areal_rsi.infra.adapters.benchmark_research",
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
            final_candidate_policy="multiple" if benchmark == "math-loop" else "single",
        ),
        metadata={
            "time_limit_scope": "exploration",
            "allow_web_access": False,
            "execution_instructions": "verify_candidate 的 diagnostic/search 使用 TRAIN，"
            "final 使用 VALIDATE。"
            "每次验证是真实目标模型执行。完整 TRAIN 轨迹和反馈在验证作业的公开输出目录；"
            "通过 job_status/read_job_output_file 读取。Leader 选定候选后主动请求 final；"
            "同一候选通过最终复测后，用 update_goal 完成本 run。"
            "只修改自己的工作区；用登记的验证工具运行目标模型，"
            "不要直接调用外部模型或读取宿主评测实现、答案及其他实验。"
            "当前没有额外的 run_job 命令。",
            "verification_identity": {
                "adapter": "content",
                "phases": {
                    phase: {"protocol_id": PROTOCOL, "manifest_id": identity, "split": split}
                    for phase, split in (
                        ("diagnostic", "train"),
                        ("search", "train"),
                        ("final", "validate"),
                    )
                },
            },
        },
    )
    if benchmark == "math-loop":
        config.brief.instructions = LOOP_INSTRUCTIONS
        config.brief.acceptance = [
            "三份不同内容的父子 harness 版本：diagnostic v0、search v1、search v2",
            "每次改进之前归档引用上一版 verification 的经验，再保存下一版并评测",
            "选定已评测版本在同题新会话做 final；允许 accuracy=0，报告所有分数和证据",
        ]
        assert config.brief.verification is not None
        config.brief.verification.description = (
            "Single fixed problem; final repeats it in a fresh session, not a held-out evaluation"
        )
        config.metadata["execution_instructions"] = LOOP_INSTRUCTIONS
        config.metadata["verification_identity"]["phases"]["final"]["split"] = "train"
        config.metadata["time_limit_scope"] = "total"
    config.pin_model_settings()
    config.runtime()
    path = directory / "research.json"
    write_json(path, config.public(), immutable=True)
    return path


async def evaluate(
    manifest_path: Path, identity: str, workspace: Path, output: Path, phase: str
) -> dict[str, Any]:
    """Run the fixed evaluator on a frozen candidate; never instantiate a ResearchSystem."""
    manifest = json.loads(manifest_path.read_text())
    if manifest_identity(manifest) != identity or manifest.get("protocol_id") != PROTOCOL:
        raise ValueError("Benchmark manifest changed after configuration was frozen")
    if phase not in {"diagnostic", "search", "final"}:
        raise ValueError("Benchmark verification phase must be diagnostic, search or final")
    workspace, output = workspace.resolve(), output.resolve()
    run_root = workspace.parents[2]
    job = json.loads((run_root / "jobs" / output.name / "job.json").read_text())
    metadata = job["spec"]["metadata"]
    frozen = json.loads((workspace.parent / "snapshot.json").read_text())
    if metadata["candidate_digest"] != frozen["digest"] or metadata["phase"] != phase:
        raise ValueError("Evaluation job does not match its frozen candidate")
    is_loop = manifest["benchmark"] == "math-loop"
    split = BenchmarkSplit.VALIDATE if phase == "final" and not is_loop else BenchmarkSplit.TRAIN
    output.mkdir(parents=True, exist_ok=True)
    history: list[HarnessIteration] = []
    if is_loop and phase == "final":
        try:
            history = math_loop_history(
                ResearchStore(run_root.parent.parent, run_root.name), metadata["candidate_id"]
            )
        except ValueError as exc:
            write_json(output / "public/feedback.json", {"error": str(exc)})
            raise
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
        if manifest["benchmark"] in {"math", "math-loop"}:
            validate_harness(workspace)
            target = local_target(reuse_research=manifest.get("target_source") == "research")
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
                timeout=120 if is_loop else 300,
                dsh_root=Path.home() / ".cache/areal-rsi",
            )
            runner = LocalHarnessRuntime(runtime, content, output / "private/workspaces")
            bench = CountingBenchmark() if is_loop else ArithmeticBenchmark()
            if encode(await bench.list_tasks(BenchmarkSplit.TRAIN)) != manifest["train_tasks"]:
                raise ValueError("Benchmark tasks changed after preparation")
            verifier = ArithmeticVerifier(
                content, answers={"bounded_paths": str(count_paths())} if is_loop else None
            )
            aggregator = AccuracyEvaluation()
        elif manifest["benchmark"] == "lcb":
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
                    "ResearchSystem candidate system prompt",
                )
                ref = await ArenaHarnessBuilder(client, content, config).build(base, change)
                harness = await versions.create_harness("candidate", ref)
            runner = ArenaHarnessRuntime(
                client, content, traces, bench, config, output / "private/arena"
            )
            verifier = ArenaVerifier(content)
            aggregator = ArenaEvaluation()
        else:
            raise ValueError("Unknown benchmark")
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
            # ResearchSystem applies any user-selected target separately.
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
        if history:
            write_json(
                public / "loop.json",
                {
                    "iterations": encode(history),
                    "selected_candidate_id": metadata["candidate_id"],
                    "final_score": report.score,
                    "same_problem_repeated": True,
                },
            )
        write_json(
            public / "feedback.json",
            {"score": report.score, "split": split.value, "tasks": encode(checks)},
        )
        if phase != "final":
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
    except TimeoutError:
        write_json(output / "public/feedback.json", {
            "error": "Target model execution timed out; this is an infrastructure failure, "
            "not an accuracy score. Retry the same candidate before advancing the iteration.",
        })
        raise
    finally:
        if runtime:
            await runtime.close()
        if proxy:
            await asyncio.to_thread(proxy.close)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    execute = commands.add_parser("evaluate", help="Operator-owned verification job")
    for name in ("manifest", "workspace", "output"):
        execute.add_argument("--" + name, type=Path, required=True)
    execute.add_argument("--identity", required=True)
    execute.add_argument("--phase", choices=("diagnostic", "search", "final"), required=True)
    args = parser.parse_args()

    async def run() -> None:
        task = asyncio.current_task()
        assert task is not None
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
