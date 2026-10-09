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
    EvolutionTarget,
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
from areal_rsi.coordination.collaboration import DEFAULT_MODULES
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.benchmark import AccuracyEvaluation, ArithmeticBenchmark, ArithmeticVerifier
from areal_rsi.modules.gateway import GatewayProxy
from areal_rsi.modules.harness import LocalHarnessRuntime, validate_harness
from areal_rsi.modules.remote.envarena.benchmark import ArenaBenchmark
from areal_rsi.modules.remote.envarena.client import ArenaClient, ArenaConnection, reference
from areal_rsi.modules.remote.envarena.config import ArenaConfig
from areal_rsi.modules.remote.envarena.harness import (
    REMOTE_CHANGE_TYPE,
    ArenaHarnessBuilder,
    snapshot,
    validate_change,
)
from areal_rsi.modules.remote.envarena.runtime import (
    ArenaEvaluation,
    ArenaHarnessRuntime,
    ArenaVerifier,
)
from areal_rsi.modules.runtime_package import PackageAgentRuntime, RuntimeSettings

PROTOCOL = "researchapp-harness-benchmark-v1"
TB21_BENCHMARK = {"key": "tb21-terminus2-full", "version": "2.1.1"}
TB21_HARNESS = {"key": "terminus-2-tb21", "version": "1.0.10"}
TB21_MODEL = {"key": "deepseek-v4-flash-0731-terminus-max", "version": "1.0.2"}
TB21_REWARD = {"key": "tb2-reward", "version": "4.0.16"}
TB21_MODEL_NAME = "DeepSeek-V4-Flash-0731"
TB21_ARENA_BASE_URL = "https://htnn-gateway.alipay.com/envarena/openapi/v1"
TB21_TASK_COUNT = 89
TB21_FIXED_AGENT_ENV = {
    "TERMINUS_TEMPERATURE": "1.0",
    "TERMINUS_TOP_P": "0.95",
    "TERMINUS_TOP_K": "50",
    "TERMINUS_REASONING_EFFORT": "max",
    "TERMINUS_SEPARATE_REASONING": "true",
}
TB21_FROZEN_MODEL_PARAMETERS = frozenset(
    {"temperature", "top_p", "top_k", "reasoning_effort", "separate_reasoning"}
)


def manifest_identity(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(json_bytes(manifest)).hexdigest()


def _validate_tb21(
    client: ArenaClient,
    config: ArenaConfig,
    benchmark: ArenaBenchmark,
    harness: dict[str, Any],
    model: dict[str, Any],
    *,
    require_train: bool = True,
) -> tuple[str, ...]:
    """Reject any drift from the operator-approved TB2.1 experiment identity."""
    if client.connection.base_url != TB21_ARENA_BASE_URL:
        raise ValueError("TB2.1 必须使用固定的旧 Arena OpenAPI 地址")
    for name, actual, expected in (
        ("benchmark", config.benchmark, TB21_BENCHMARK),
        ("harness", config.harness, TB21_HARNESS),
        ("model", config.model, TB21_MODEL),
    ):
        if actual != expected:
            raise ValueError(f"TB2.1 {name} 引用与固定实验身份不一致")
    if benchmark.reference_field != "env_ref":
        raise ValueError("TB2.1 必须是 env_refs Benchmark")
    if reference(benchmark.spec.get("reward_ref")) != TB21_REWARD:
        raise ValueError("TB2.1 Reward 引用与固定实验身份不一致")
    task_ids = tuple(
        reference(entry[benchmark.reference_field])["key"]
        for entry in benchmark.spec[benchmark.entry_collection]
    )
    if len(task_ids) != TB21_TASK_COUNT or len(set(task_ids)) != TB21_TASK_COUNT:
        raise ValueError("TB2.1 全量 Benchmark 必须恰好包含 89 个唯一 env_ref")
    if model.get("spec", {}).get("model_name") != TB21_MODEL_NAME:
        raise ValueError("TB2.1 模型版本未指向固定的 DeepSeek-V4-Flash-0731")
    agent = harness.get("spec", {}).get("agent", {})
    env = agent.get("env", {}) if isinstance(agent, dict) else {}
    if not isinstance(env, dict) or any(
        key in env and str(env[key]).lower() != expected
        for key, expected in TB21_FIXED_AGENT_ENV.items()
    ):
        raise ValueError("TB2.1 初始 Harness 与固定采样或推理参数冲突")
    if require_train and not config.train_cases:
        raise ValueError("TB2.1 TRAIN 必须是固定 89 题中的非空子集")
    if any(key not in task_ids for key in config.train_cases):
        raise ValueError("TB2.1 TRAIN 必须是固定 89 题中的非空子集")
    if any(key not in task_ids for key in config.validate_cases):
        raise ValueError("TB2.1 VALIDATE 必须来自固定 89 题")
    return task_ids


def _tb21_change(workspace: Path, allowed: tuple[str, ...]) -> dict[str, Any]:
    path = workspace / "change.json"
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("change.json 必须是 JSON object")
    if not value:
        return value
    value = validate_change(value, allowed)
    parameters = value.get("model_parameters", {})
    if isinstance(parameters, dict) and set(parameters) & TB21_FROZEN_MODEL_PARAMETERS:
        raise ValueError("TB2.1 固定采样与推理参数不能由候选修改")
    return value


async def prepare(
    benchmark: str,
    directory: Path,
    *,
    arena_config: Path | None = None,
    max_wall_seconds: float = 1200,
    backend: str = "standard",
    research_agents: int = 1,
    search_verifications: int = 30,
    split_manifest: Path | None = None,
    minimum_iterations_per_agent: int | None = None,
    codex_proxy_port: int | None = None,
) -> Path:
    """Prepare operator-owned inputs and a ResearchConfig, without running an experiment."""
    if type(research_agents) is not int or research_agents < 1:
        raise ValueError("research_agents 必须是正整数")
    if type(search_verifications) is not int or search_verifications < 1:
        raise ValueError("search_verifications 必须是正整数")
    if minimum_iterations_per_agent is not None and (
        type(minimum_iterations_per_agent) is not int or minimum_iterations_per_agent < 1
    ):
        raise ValueError("minimum_iterations_per_agent 必须是正整数")
    if codex_proxy_port is not None and (
        type(codex_proxy_port) is not int or not 1024 <= codex_proxy_port <= 65535
    ):
        raise ValueError("codex_proxy_port 必须是 1024 到 65535 的端口")
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
    elif benchmark in {"lcb", "tb21"}:
        if arena_config is None:
            raise ValueError("Remote benchmark requires a pinned Arena evaluation configuration")
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
        prompt: dict[str, Any] = {}
        if benchmark == "lcb":
            prompt = harness["spec"].get("system_prompt", {})
            if prompt.get("kind", "inline") != "inline" or not prompt.get("content"):
                raise ValueError("This ResearchApp adapter requires an inline system prompt")
            if "system_prompt" not in arena.allowed_changes:
                raise ValueError("The pinned benchmark must allow system_prompt changes")
        full_task_ids: tuple[str, ...] = ()
        if benchmark == "tb21":
            full_task_ids = _validate_tb21(client, arena, bench, harness, model)
            if not arena.allowed_changes or set(arena.allowed_changes) - {
                "system_prompt",
                "skills",
                "tools",
                "mcp",
                "model_parameters",
            }:
                raise ValueError("TB2.1 allowed_changes 包含不支持的 Harness 变更")
            seed.mkdir()
            write_json(seed / "change.json", {}, immutable=True)
        else:
            seed.mkdir()
            (seed / "prompt.md").write_text(prompt["content"])
        manifest["target"] = {"model_ref": arena.model, "model_name": model["spec"]["model_name"]}
        manifest["benchmark_spec"] = bench.spec
        manifest["harness_ref"] = arena.harness
        manifest["allowed_changes"] = list(arena.allowed_changes)
        if benchmark == "tb21":
            manifest["full_task_ids"] = list(full_task_ids)
            manifest["reward_ref"] = TB21_REWARD
            if split_manifest is not None:
                split_plan = json.loads(split_manifest.read_text())
                if not isinstance(split_plan, dict):
                    raise ValueError("TB2.1 split manifest 必须是 JSON object")
                train_ids = split_plan.get("train_task_ids")
                dev_ids = split_plan.get("dev_task_ids")
                test_ids = split_plan.get("test_task_ids")
                portfolios = split_plan.get("research_portfolios")
                groups = (train_ids, dev_ids, test_ids)
                if any(
                    not isinstance(group, list)
                    or not group
                    or any(not isinstance(key, str) for key in group)
                    or len(set(group)) != len(group)
                    for group in groups
                ):
                    raise ValueError("TB2.1 TRAIN/DEV/TEST 必须是非空、无重复的 task key 列表")
                assert isinstance(train_ids, list) and isinstance(dev_ids, list)
                assert isinstance(test_ids, list)
                if (
                    set(train_ids) & set(dev_ids)
                    or set(train_ids) & set(test_ids)
                    or set(dev_ids) & set(test_ids)
                ):
                    raise ValueError("TB2.1 TRAIN/DEV/TEST 必须互斥")
                if set(train_ids + dev_ids + test_ids) != set(full_task_ids):
                    raise ValueError("TB2.1 TRAIN/DEV/TEST 必须恰好覆盖固定 89 题")
                if tuple(train_ids) != arena.train_cases or tuple(dev_ids) != arena.validate_cases:
                    raise ValueError("Arena 配置的 TRAIN/VALIDATE 与 split manifest 不一致")
                if (
                    not isinstance(portfolios, list)
                    or not portfolios
                    or any(not isinstance(item, dict) for item in portfolios)
                ):
                    raise ValueError("TB2.1 research_portfolios 必须是非空列表")
                ids = [item.get("id") for item in portfolios]
                if any(not isinstance(value, str) or not value for value in ids) or len(
                    set(ids)
                ) != len(ids):
                    raise ValueError("每个 research portfolio 必须有唯一 id")
                for item in portfolios:
                    role = item.get("role")
                    portfolio_tasks = item.get("train_task_ids")
                    if role not in {"domain", "fusion"}:
                        raise ValueError("research portfolio role 只支持 domain/fusion")
                    if (
                        not isinstance(item.get("direction"), str)
                        or not isinstance(portfolio_tasks, list)
                        or not portfolio_tasks
                        or not set(portfolio_tasks) <= set(train_ids)
                    ):
                        raise ValueError("research portfolio 必须声明 TRAIN 子集和研究方向")
                    if role == "fusion" and set(portfolio_tasks) != set(train_ids):
                        raise ValueError("fusion portfolio 必须覆盖全部 TRAIN")
                if len(portfolios) != research_agents:
                    raise ValueError("research_agents 必须等于 research_portfolios 数量")
                manifest.update(
                    train_task_ids=train_ids,
                    dev_task_ids=dev_ids,
                    test_task_ids=test_ids,
                    research_portfolios=portfolios,
                    split_plan_sha256=hashlib.sha256(split_manifest.read_bytes()).hexdigest(),
                )
        tasks = await bench.list_tasks(BenchmarkSplit.TRAIN)
        allowed = ("change.json",) if benchmark == "tb21" else ("prompt.md",)
        name = (
            "Terminal-Bench 2.1 DeepSeek Terminus harness research"
            if benchmark == "tb21"
            else "LiveCodeBench harness research"
        )
    else:
        raise ValueError("Unknown benchmark")
    manifest["train_tasks"] = encode(tasks)
    manifest_path = directory / "manifest.json"
    write_json(manifest_path, manifest, immutable=True)
    identity = manifest_identity(manifest)
    write_json(directory / "public-train.json", encode(tasks), immutable=True)
    phase_identity = {
        phase: {"protocol_id": PROTOCOL, "manifest_id": identity, "split": split}
        for phase, split in (
            ("diagnostic", "train"),
            ("search", "validate" if benchmark == "tb21" else "train"),
            ("final", "validate"),
        )
    }
    metadata: dict[str, Any] = {
        "time_limit_scope": "exploration",
        "allow_web_access": False,
        "agent_tool_instructions": "只修改自己的候选目录；用登记的验证工具运行目标模型，"
        "不要直接调用外部模型或读取宿主评测实现、答案及其他实验。",
        "execution_instructions": "verify_candidate 的 diagnostic/search 使用 TRAIN，"
        "final 使用 VALIDATE。"
        "每次验证是真实目标模型执行。完整 TRAIN 轨迹和反馈在验证作业的公开输出目录；"
        "通过 job_status/read_job_output_file 读取。final 由系统在探索停止后执行；"
        "研究员不自行请求 final。当前没有额外的 run_job 命令。",
        "verification_identity": {"adapter": "content", "phases": phase_identity},
    }
    verifier_timeout = 900.0
    candidate_instructions = "修改通用 prompt.md 或允许的 skill；每个候选只做一项可解释的通用改进。"
    if benchmark == "tb21":
        held_out = "test_task_ids" in manifest
        final_task_ids = manifest.get("test_task_ids", manifest["full_task_ids"])
        full_identity = {
            "protocol_id": PROTOCOL,
            "manifest_id": identity,
            "split": "test" if held_out else "full",
            "task_ids": final_task_ids,
            "benchmark_ref": TB21_BENCHMARK,
            "reward_ref": TB21_REWARD,
            "model_ref": TB21_MODEL,
            "sample_numbers": 1,
        }
        metadata.update(
            full_benchmark_evaluation=True,
            evaluate_seed_as_baseline=True,
        )
        if held_out:
            metadata["research_portfolios"] = manifest["research_portfolios"]
            metadata["minimum_search_verifications_per_agent"] = minimum_iterations_per_agent or 10
            if backend == "codex":
                metadata["codex_compatibility_proxy"] = True
                if codex_proxy_port is None:
                    raise ValueError("TB2.1 Codex 兼容代理必须配置稳定端口")
                metadata["codex_compatibility_proxy_port"] = codex_proxy_port
        else:
            metadata["stop_after_search_verifications"] = search_verifications
        metadata["verification_identity"]["phases"].update(
            baseline=full_identity,
            final=full_identity,
        )
        metadata["execution_instructions"] = (
            (
                "系统先在封存 TEST 上自动完成 baseline。"
                if held_out
                else "系统先在固定 89 题上自动完成 baseline。"
            )
            + "diagnostic 使用分配给当前 Agent 的 TRAIN，search 使用共享 DEV；"
            + (
                "探索停止后 RSI 冻结唯一最佳候选，并在同一封存 TEST 上执行 final。"
                if held_out
                else "探索停止后 RSI 冻结唯一最佳候选，并在同一 89 题上执行 full final。"
            )
            + (
                "每个 Research Agent 至少完成 "
                f"{minimum_iterations_per_agent or 10} 个可信 search verification "
                "后系统才停止探索。"
                if held_out
                else f"累计完成 {search_verifications} 个可信 search verification 后，"
                "系统自动停止探索并冻结候选。"
            )
            + "不得自行请求 baseline/final，也不得把额外重试当作 Harness 收益。"
        )
        candidate_instructions = (
            "只修改 change.json。它必须是 JSON object，顶层 key 只能来自固定的 allowed_changes；"
            'system_prompt 接受完整字符串或 {"append": "..."}，model_parameters 只接受'
            "适配器白名单参数。temperature/top_p/top_k/reasoning_effort/separate_reasoning 已冻结，"
            "不得修改。每个候选只做一项可解释、可复用、与题号无关的 Harness 改进。"
        )
        verifier_timeout = max(verifier_timeout, arena.timeout_seconds + 300)
    task_instructions = (
        "完成一次真实的 harness 研究端到端实验。固定目标模型、题目划分、运行时和评分器。"
        "RSI 按冻结的研究组合启动研究员；领域研究员只分析自己领域的 TRAIN 轨迹，"
        "融合研究员分析全部 TRAIN 并综合领域证据。"
        "先将工作区起点 save_candidate 为自己的候选，用 verify_candidate(phase='diagnostic') "
        "执行 TRAIN 基线诊断；诊断结果仅作为反馈，不参与最佳候选排名。"
        + candidate_instructions
        + "生成与起点不同的候选，禁止写入具体题目的答案或针对题号分支。"
        "保存新候选并用 verify_candidate(phase='search') 执行真实选择集评测，报告候选和证据。"
        "在研究预算内继续分析与改进，不自行调用 final、finish 或 stop_run。"
        "探索停止时系统会冻结 DEV 上最佳的唯一正式候选，并在封存 TEST 上独立最终复测。"
        "本次是小规模端到端验证，不要求超过基线，不扩展题目或改变评测协议。"
    )
    config = ResearchConfig(
        TaskBrief(
            name,
            task_instructions,
            ResearchLimits(
                max_agents=research_agents,
                max_nodes=research_agents,
                max_wall_seconds=max_wall_seconds,
                max_jobs=research_agents,
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
        collaboration_modules=DEFAULT_MODULES,
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
            timeout_seconds=verifier_timeout,
            public_output_subdir="public",
        ),
        metadata=metadata,
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
    if phase not in {"diagnostic", "search", "final", "baseline"}:
        raise ValueError(
            "Benchmark verification phase must be diagnostic, search, final or baseline"
        )
    workspace, output = workspace.resolve(), output.resolve()
    run_root = workspace.parents[2]
    job = json.loads((run_root / "jobs" / output.name / "job.json").read_text())
    metadata = job["spec"]["metadata"]
    frozen = json.loads((workspace.parent / "snapshot.json").read_text())
    if metadata["candidate_digest"] != frozen["digest"] or metadata["phase"] != phase:
        raise ValueError("Evaluation job does not match its frozen candidate")
    full_tb21 = manifest["benchmark"] == "tb21" and phase in {"baseline", "final"}
    split = (
        BenchmarkSplit.VALIDATE
        if phase == "final" or full_tb21 or (manifest["benchmark"] == "tb21" and phase == "search")
        else BenchmarkSplit.TRAIN
    )
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
            if manifest["benchmark"] == "tb21":
                remote_harness = await client.version("harnesses", config.harness)
                remote_model = await client.version("models", config.model)
                _validate_tb21(client, config, bench, remote_harness, remote_model)
                proposal = _tb21_change(workspace, tuple(manifest["allowed_changes"]))
                if not proposal:
                    harness = base
                else:
                    change = HarnessChange(
                        await content.put_bytes(
                            json_bytes(proposal), media_type=REMOTE_CHANGE_TYPE
                        ),
                        "ResearchApp TB2.1 candidate",
                    )
                    ref = await ArenaHarnessBuilder(client, content, config).build(base, change)
                    harness = await versions.create_harness("candidate", ref)
            else:
                prompt = (workspace / "prompt.md").read_text()
                original = json.loads(await content.read_text(base.content))["spec"][
                    "system_prompt"
                ]["content"]
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
        if manifest["benchmark"] == "tb21" and isinstance(bench, ArenaBenchmark):
            if full_tb21:
                selected_ids = manifest.get("test_task_ids", manifest["full_task_ids"])
                tasks = await bench.tasks_for(selected_ids, BenchmarkSplit.VALIDATE)
            elif phase == "diagnostic" and "research_portfolios" in manifest:
                portfolio_id = metadata.get("research_portfolio_id")
                portfolio = next(
                    (
                        item
                        for item in manifest["research_portfolios"]
                        if item["id"] == portfolio_id
                    ),
                    None,
                )
                if portfolio is None:
                    raise ValueError("Diagnostic job lacks a frozen research portfolio")
                tasks = await bench.tasks_for(portfolio["train_task_ids"], BenchmarkSplit.TRAIN)
            else:
                tasks = await bench.list_tasks(split)
        else:
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
            "split": ("test" if "test_task_ids" in manifest else "full")
            if full_tb21
            else split.value,
        }
        if full_tb21:
            result.update(
                task_ids=manifest.get("test_task_ids", manifest["full_task_ids"]),
                benchmark_ref=TB21_BENCHMARK,
                reward_ref=TB21_REWARD,
                model_ref=TB21_MODEL,
                sample_numbers=1,
            )
        write_json(output / "evaluation.json", result)
        public = output / "public"
        write_json(
            public / "feedback.json",
            {
                "score": report.score,
                "split": ("test" if "test_task_ids" in manifest else "full")
                if full_tb21
                else split.value,
                **(
                    {}
                    if manifest["benchmark"] == "tb21" and phase != "diagnostic"
                    else {"tasks": encode(checks)}
                ),
            },
        )
        publish_task_traces = (
            phase == "diagnostic" if manifest["benchmark"] == "tb21" else phase != "final"
        )
        if publish_task_traces:
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
    setup.add_argument("--benchmark", choices=("math", "lcb", "tb21"), required=True)
    setup.add_argument("--directory", type=Path, required=True)
    setup.add_argument("--arena-config", type=Path)
    setup.add_argument("--time-limit", type=float, default=1200)
    setup.add_argument("--backend", choices=("standard", "codex", "runtime"), default="standard")
    setup.add_argument("--research-agents", type=int, default=1)
    setup.add_argument("--search-verifications", type=int, default=30)
    setup.add_argument("--split-manifest", type=Path)
    setup.add_argument("--minimum-iterations-per-agent", type=int)
    setup.add_argument("--codex-proxy-port", type=int)
    execute = commands.add_parser("evaluate", help="Operator-owned verification job")
    for name in ("manifest", "workspace", "output"):
        execute.add_argument("--" + name, type=Path, required=True)
    execute.add_argument("--identity", required=True)
    execute.add_argument(
        "--phase", choices=("diagnostic", "search", "final", "baseline"), required=True
    )
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
                    research_agents=args.research_agents,
                    search_verifications=args.search_verifications,
                    split_manifest=args.split_manifest,
                    minimum_iterations_per_agent=args.minimum_iterations_per_agent,
                    codex_proxy_port=args.codex_proxy_port,
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
