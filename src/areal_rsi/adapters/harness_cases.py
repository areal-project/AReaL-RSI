"""Math and LCB task evaluation as commands for the single research engine.

This module owns benchmark preparation and trusted evaluation, never a research
loop. The same commands are used by CLI, TUI and Web research runs.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from areal_rsi.api.base import (
    BenchmarkSplit,
    HarnessChange,
    SolutionVersion,
    VerificationRequest,
    VerifierSpec,
)
from areal_rsi.api.research import ResearchLimits, TaskBrief, VerificationContract
from areal_rsi.base.storage import (
    FileContentStore,
    FileMemory,
    FileTrajectoryStore,
    FileVersionManager,
    decode,
    encode,
    write_json,
)
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.execution import ApprovedCommand

PROTOCOL = "harness-cases-v1"


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def math_components(
    case: str, dataset: str, limit: int, content: FileContentStore
) -> tuple[Any, Any]:
    from areal_rsi.modules.benchmark import ArithmeticBenchmark, ArithmeticVerifier
    from areal_rsi.modules.gsm8k import Gsm8kBenchmark, Gsm8kVerifier

    if case == "arithmetic":
        return ArithmeticBenchmark(), ArithmeticVerifier(content)
    benchmark = Gsm8kBenchmark(Path(dataset), train_limit=limit, validate_limit=limit)
    return benchmark, Gsm8kVerifier(content, benchmark)


async def prepare(
    case: str,
    output: Path,
    *,
    dataset: Path,
    limit: int = 2,
    remote_config: Path,
    max_research_agents: int = 1,
) -> ResearchConfig:
    from areal_rsi.config import ModelConfig

    output = output.resolve()
    if output.exists():
        raise FileExistsError("Research config already exists; use another output directory")
    folder = output.parent
    folder.mkdir(parents=True, exist_ok=True)
    seed = folder / "seed"
    if seed.exists():
        raise FileExistsError("Use a fresh preparation directory")
    settings: dict[str, Any] = {"case": case, "dataset": str(dataset.resolve()), "limit": limit}
    if case == "lcb":
        from areal_rsi.modules.remote.base import RemoteContext
        from areal_rsi.modules.remote.registry import get_provider

        preflight = folder / "preflight"
        content = FileContentStore(preflight / "content")
        backend = await get_provider("envarena").create(
            RemoteContext(
                preflight,
                content,
                FileMemory(preflight / "memory"),
                FileTrajectoryStore(preflight / "trajectories", content),
                FileVersionManager(preflight / "versions"),
                lambda _: None,
            ),
            remote_config,
        )
        harness = json.loads(await content.read_text(backend.baseline.harness.content))
        prompt = harness["spec"].get("system_prompt", {})
        if prompt.get("kind", "inline") != "inline":
            raise ValueError("LCB example requires an inline system prompt")
        seed.mkdir()
        (seed / "prompt.md").write_text(prompt.get("content", ""))
        saved = folder / "remote.yaml"
        shutil.copyfile(remote_config, saved)
        settings.update(
            remote_config=str(saved),
            remote_sha256=hashlib.sha256(saved.read_bytes()).hexdigest(),
            benchmark=backend.benchmark.name,
            target_model=backend.identity["model"],
            endpoint=backend.identity["base_url"],
        )
    else:
        content = FileContentStore(folder / "preflight-content")
        benchmark, _ = math_components(case, settings["dataset"], limit, content)
        target = ModelConfig.from_environment("target")
        settings.update(benchmark=benchmark.name, target_model=target.public_identity())
        from areal_rsi.modules.runtime_package import RuntimeSettings

        task_runtime = None
        if os.environ.get("RSI_RUNTIME_CONFIG"):
            selected_runtime = RuntimeSettings.from_environment()
            selected_runtime.select("task-agent")  # Validate before creating an executable plan.
            task_runtime = selected_runtime.configuration
        settings["task_runtime"] = task_runtime
        shutil.copytree(Path(__file__).parents[1] / "default_harness", seed)
    settings["seed_files"] = {
        p.relative_to(seed).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in seed.rglob("*")
        if p.is_file()
    }
    settings["manifest_id"] = digest(settings)
    operator = folder / "operator.json"
    write_json(operator, settings, immutable=True)
    common = (
        sys.executable,
        "-m",
        "areal_rsi.adapters.harness_cases",
        "evaluate",
        "--operator",
        str(operator),
        "--workspace",
        "{workspace}",
        "--output",
        "{output}",
    )
    command = ApprovedCommand(
        "develop",
        (*common, "--phase", "train"),
        ("workspace", "output"),
        timeout_seconds=3600,
        output_subdir="public",
    )
    verifier = ApprovedCommand(
        "verify",
        (*common, "--phase", "{phase}"),
        ("workspace", "output", "phase"),
        timeout_seconds=3600,
    )
    identity = {"protocol_id": PROTOCOL, "manifest_id": settings["manifest_id"]}
    instructions = (
        "固定目标模型，只优化候选目录的 prompt.md；不改工具、数据、评分器或模型。"
        "这是一次真实的小规模 Harness 研究：先评测 seed 的 search 基线；派至少一名研究员，"
        "用 develop 作业查看训练题的真实输出，做一次有依据的通用提示改进，"
        "保存候选并做 search 评测。"
        "完成一轮有效修改与对比后收尾，无需反复追求提升；"
        "最终只对选定候选做一次 final 验证，然后 finish。"
        "禁止读取 private 数据或通过 shell 绕过执行工具获取验证题与答案。不联网搜索题目答案。"
        "search 和 final 使用相同留出题池，final 仅是独立执行复测，不是新的独立测试集。"
        "GSM8K 不使用官方 test；LCB 固定原 benchmark/reward/model。"
    )
    config = ResearchConfig(
        TaskBrief(
            f"{case} Harness 研究",
            instructions,
            ResearchLimits(
                max_research_agents=max_research_agents,
                max_research_tasks=4,
                max_model_calls=100,
                max_total_tokens=1_000_000,
                max_verify_calls=4,
                max_jobs=2,
                max_wall_seconds=7200,
                allowed_paths=("prompt.md",),
            ),
            VerificationContract(
                "accuracy",
                target=0.0,
                description="真实执行准确率；不要求分数提升",
                identity={"require_final_confirmation": True},
            ),
        ),
        seed_dir=str(seed),
        commands=(command,),
        verifier=verifier,
        base_url=os.environ.get("RSI_BASE_URL")
        or os.environ.get("RSI_TEACHER_BASE_URL", "https://api.openai.com"),
        model=os.environ.get("RSI_MODEL") or os.environ.get("RSI_TEACHER_MODEL", "gpt-5.5"),
        reasoning_effort=os.environ.get("RSI_REASONING_EFFORT", "high"),
        api_key_env="RSI_API_KEY" if os.environ.get("RSI_API_KEY") else "RSI_TEACHER_API_KEY",
        model_settings_pinned=True,
        metadata={
            "template": case,
            "allow_web_access": False,
            "evaluate_seed_as_baseline": True,
            "verification_identity": {
                "adapter": "content",
                "phases": {phase: identity for phase in ("search", "final", "diagnostic")},
            },
            "execution_instructions": "develop 在训练题执行当前工作区快照。"
            "public/feedback.json 提供输出和反馈。"
            "verify_candidate 使用冻结候选；"
            "search/final 只返回聚合成绩。",
        },
    )
    write_json(output, config.public(), immutable=True)
    return config


async def evaluate(operator: Path, workspace: Path, output: Path, phase: str) -> dict[str, Any]:
    settings = json.loads(operator.read_text())
    expected = settings.pop("manifest_id")
    if digest(settings) != expected:
        raise ValueError("Operator configuration identity changed")
    settings["manifest_id"] = expected
    workspace, output = workspace.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    seed = operator.parent / "seed"
    original = {
        p.relative_to(seed).as_posix(): p.read_bytes() for p in seed.rglob("*") if p.is_file()
    }
    if {k: hashlib.sha256(v).hexdigest() for k, v in original.items()} != settings["seed_files"]:
        raise ValueError("Seed identity changed")
    actual = {
        p.relative_to(workspace).as_posix(): p.read_bytes()
        for p in workspace.rglob("*")
        if p.is_file()
    }
    if any(p.is_symlink() for p in workspace.rglob("*")) or set(original) != set(actual):
        raise ValueError("Candidate must preserve seed files without symlinks")
    if any(original[k] != actual[k] for k in original if k != "prompt.md"):
        raise ValueError("Only prompt.md may change")
    if not actual["prompt.md"].strip():
        raise ValueError("Prompt cannot be empty")
    content = FileContentStore(output / "private/content")
    versions = FileVersionManager(output / "private/versions")
    trajectories = FileTrajectoryStore(output / "private/trajectories", content)
    split = BenchmarkSplit.TRAIN if phase == "train" else BenchmarkSplit.VALIDATE
    runtime: Any
    gateway: Any = None
    if settings["case"] == "lcb":
        from areal_rsi.modules.remote.base import RemoteContext
        from areal_rsi.modules.remote.envarena.harness import REMOTE_CHANGE_TYPE
        from areal_rsi.modules.remote.registry import get_provider

        path = Path(settings["remote_config"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != settings["remote_sha256"]:
            raise ValueError("Remote config changed")
        backend = await get_provider("envarena").create(
            RemoteContext(
                output / "private",
                content,
                FileMemory(output / "private/memory"),
                trajectories,
                versions,
                print,
            ),
            path,
        )
        if backend.identity["base_url"] != settings["endpoint"]:
            raise ValueError("Arena endpoint changed")
        benchmark, verifier, runtime = backend.benchmark, backend.verifier, backend.harness_runtime
        model, harness = backend.baseline.model, backend.baseline.harness
        if actual["prompt.md"] != original["prompt.md"]:
            patch = await content.put_bytes(
                json.dumps({"system_prompt": actual["prompt.md"].decode()}).encode(),
                media_type=REMOTE_CHANGE_TYPE,
            )
            harness = await versions.create_harness(
                "candidate",
                await backend.harness_builder.build(
                    harness, HarnessChange(patch, "research candidate")
                ),
            )
    else:
        from areal_rsi.config import ModelConfig
        from areal_rsi.modules.harness import LocalHarnessRuntime
        from areal_rsi.modules.task_execution import TargetGateway, create_task_runtime

        benchmark, verifier = math_components(
            settings["case"], settings["dataset"], settings["limit"], content
        )
        target = ModelConfig.from_environment("target")
        if target.public_identity() != settings["target_model"]:
            raise ValueError("Target model connection changed")
        model = await versions.create_model(
            target.model, await content.put_json(target.public_identity())
        )
        harness = await versions.create_harness("candidate", await content.snapshot(workspace))
        gateway = TargetGateway(
            target.base_url,
            target.api_key,
            model=target.model,
            max_requests=32,
            max_output_tokens=8192,
            timeout_seconds=600,
        )
        agent_runtime = create_task_runtime(
            output / "private/sessions",
            content,
            trajectories,
            base_url=gateway.url,
            api_key=gateway.token,
            timeout=600,
            max_tokens=8192,
            dsh_root=operator.parent / "task-runtime",
            runtime_config=settings.get("task_runtime"),
        )
        runtime = LocalHarnessRuntime(agent_runtime, content, output / "private/workspaces")
    if benchmark.name != settings["benchmark"]:
        raise ValueError("Benchmark identity changed")
    solution_file = output / "private/solution.json"
    solution = (
        decode(SolutionVersion, json.loads(solution_file.read_text()))
        if solution_file.exists()
        else await versions.create_solution(model, harness)
    )
    write_json(solution_file, solution, immutable=True)
    rows = []
    grades = []
    try:
        tasks = await benchmark.list_tasks(split)
        if hasattr(runtime, "run_many"):
            results = await runtime.run_many(
                solution,
                tasks,
                config={"execution_key": str(output), "run_id": output.parent.parent.name},
            )
        else:
            results = {}
        for task in tasks:
            result = results.get(task.task_id) or await runtime.run(solution, task)
            grade = await verifier.verify(
                VerificationRequest(
                    task, solution, result, VerifierSpec("case-verifier", "injected")
                )
            )
            if grade.status.value != "succeeded" or grade.score is None:
                raise RuntimeError("Task verification failed: " + grade.feedback)
            grades.append(grade)
            row = {
                "task_id": task.task_id,
                "score": grade.score,
                "passed": grade.passed,
                "feedback": grade.feedback,
                "trajectory": encode(result.trajectory),
            }
            if phase == "train":
                row["output"] = json.loads(await content.read_text(result.output))
            rows.append(row)
    finally:
        if gateway is not None:
            await asyncio.to_thread(gateway.close)
    if settings["case"] == "lcb":
        report = await backend.evaluation.evaluate(solution, split, grades)
        score = report.score
    else:
        score = sum(float(r["score"]) for r in rows) / len(rows)
    result = {
        "status": "completed",
        "simulated": False,
        "phase": phase,
        "metric": "accuracy",
        "score": score,
        "passed": True,
        "metrics": {"accuracy": score, "tasks": len(rows)},
        "protocol_id": PROTOCOL,
        "manifest_id": expected,
    }
    write_json(output / "private/results.json", rows)
    if phase == "train":
        write_json(output / "public/feedback.json", {"score": score, "tasks": rows})
    else:
        job = json.loads((output.parent.parent / "jobs" / output.name / "job.json").read_text())
        result.update({k: job["spec"]["metadata"][k] for k in ("candidate_id", "candidate_digest")})
    write_json(output / "evaluation.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--case", choices=["arithmetic", "gsm8k", "lcb"], required=True)
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--dataset", type=Path, default=Path(".rsi/datasets/gsm8k"))
    prep.add_argument("--limit", type=int, default=2)
    prep.add_argument(
        "--remote-config",
        type=Path,
        default=Path(__file__).parents[1] / "resources/presets/livecodebench.yaml",
    )
    prep.add_argument("--max-research-agents", type=int, default=1)
    run = commands.add_parser("evaluate")
    for name in ("operator", "workspace", "output"):
        run.add_argument("--" + name, type=Path, required=True)
    run.add_argument("--phase", choices=["train", "search", "final", "diagnostic"], required=True)
    args = vars(parser.parse_args())
    action = args.pop("action")
    value = asyncio.run(prepare(**args) if action == "prepare" else evaluate(**args))
    print(
        json.dumps(
            value.public() if isinstance(value, ResearchConfig) else value, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
