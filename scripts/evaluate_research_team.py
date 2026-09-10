"""Run real model agents on a small CPU research problem.

Scores come from executing candidate policies, not scripted model responses.
This exercises behavior; it does not measure KernelBench or secure an untrusted agent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import shutil
import sys
import time
from pathlib import Path

from areal_rsi.api.research import (
    CandidateSnapshot,
    ResearchLimits,
    TaskBrief,
    VerificationContract,
    VerifiedResult,
)
from areal_rsi.base.storage import now, write_json
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.agent_tools import AgentTools
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.engine import ResearchEngine, ToolSet
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.coordination.sources import register_sources
from areal_rsi.coordination.store import ResearchStore
from areal_rsi.execution import ApprovedCommand, JobRecord
from areal_rsi.modules.responses import ResponsesRuntime

REPOSITORY = Path(__file__).resolve().parents[1]
LAB = REPOSITORY / "examples" / "research_lab"
INSTRUCTIONS = """优化一个通用在线缓存替换与准入策略，
能兼顾稳定热点、扫描、热点迁移、循环访问、短期重复及混合负载。
起点是 LRU。只修改 policy.py，不修改执行器、评测数据或记录。
不能根据具体 key 数值、评测样本或负载名称特判。
接口：Policy(capacity) 创建对象。
每次请求调用 access(key, cache)，cache 是当前缓存 key 的只读 tuple。
命中时返回 None；
未命中且缓存未满时返回 None；
未命中且已满时，返回一个被淘汰的缓存 key，或返回传入 key 表示不收录。
策略只能使用当前和过往请求，不能偷看未来。
capacity 应使用传入值。每个负载重新创建策略，评测平均命中率。
可以实现自己的开发测试。
正式评测每类 6000 个请求、capacity=64，公开反馈提供分项命中率。
执行器及其随机种子属于验证边界，不得读取或修改；
请根据问题定义、公开反馈和你们自行构建的例子研究。
这是真实模型的 CPU 研究流程测试，不是实际 KernelBench 性能。
已经授权直接开始，资源均在本机，不需要 GPU。
如何分工由你决定。最多 {agents} 个研究员，在可用时间内持续推进目标。
报告应让我能够看懂尝试和判断。
"""


def load_environment(path: Path | None) -> None:
    if path is None:
        return
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in {
            "RSI_API_KEY",
            "RSI_BASE_URL",
            "RSI_MODEL",
            "RSI_REASONING_EFFORT",
            "NO_PROXY",
            "no_proxy",
        }:
            parts = shlex.split(value, comments=True)
            os.environ[key.strip()] = parts[0] if parts else ""


async def main(args: argparse.Namespace) -> None:
    load_environment(args.env_file)
    brief = TaskBrief(
        "改进通用在线缓存策略，使六类负载的平均命中率达到 0.80",
        INSTRUCTIONS.format(agents=args.agents),
        ResearchLimits(
            max_agents=args.agents,
            max_nodes=30,
            max_model_calls=600,
            max_total_tokens=12_000_000,
            max_verify_calls=25,
            max_jobs=args.agents + 1,
            max_wall_seconds=args.minutes * 60,
            allowed_paths=("policy.py",),
        ),
        VerificationContract(
            "hit_rate", target=0.80, description="相同负载协议下的平均命中率，公开分项反馈"
        ),
    )
    c = (
        Coordinator(ResearchStore(args.root, args.resume))
        if args.resume
        else Coordinator.create(args.root, brief)
    )
    brief = c.store.brief()
    actions = ResearchActions(c)
    if not args.resume:
        clean_seed = c.store.root / "setup" / "seed"
        clean_seed.mkdir(parents=True)
        shutil.copyfile(LAB / "cache_seed" / "policy.py", clean_seed / "policy.py")
        seed = await c.store.snapshot(clean_seed, agent_id="operator", description="LRU 起点")
        c.state.update(seed_candidate_id=seed.candidate_id, baseline_candidate_id=seed.candidate_id)
        c.save()
    command = ApprovedCommand(
        "verify",
        (sys.executable, str(LAB / "cache_workload.py"), "{workspace}", "{output}"),
        ("workspace", "output"),
        timeout_seconds=30,
        public_output_subdir="public",
    )
    config = ResearchConfig(brief, seed_dir=str(LAB / "cache_seed"), verifier=command)
    write_json(c.store.root / "coordination" / "config.json", config.public())

    def verify(job: JobRecord, candidate: CandidateSnapshot, phase: str) -> VerifiedResult:
        if job.state != "succeeded":
            return VerifiedResult(
                job.job_id,
                candidate.candidate_id,
                c.store.contract_digest(),
                now(),
                "infra_error",
                error=job.error or job.state,
                phase=phase,
            )
        result = json.loads(
            (c.store.root / "execution-outputs" / job.job_id / "result.json").read_text()
        )
        return VerifiedResult(
            job.job_id,
            candidate.candidate_id,
            c.store.contract_digest(),
            now(),
            "succeeded",
            result["score"],
            result["passed"],
            result["metrics"],
            phase=phase,
        )

    execution = ResearchExecution(actions, verifier=command, read_verification=verify)
    execution.instructions = (
        "这是已接入的 CPU 缓存研究测试。verify_candidate 测量固定候选，wait_for_job 等待完成。"
        "read_job_output_file 可读取 public 下的 feedback.json。"
        "开发时可自行构建短测试，避免把临时测试文件保存在候选工作区。"
    )
    basic = AgentTools(
        actions,
        args.tool_root or args.root,
        environment=lambda: {"PYTHONDONTWRITEBYTECODE": "1"},
        credential_names=(config.api_key_env,),
    )
    register_sources(actions)
    for agent_id in ("rsi",):
        await actions.initialize_workspace(agent_id)
    if not args.resume:
        c.start()
    runtime = ResponsesRuntime(config.runtime())
    engine = ResearchEngine(
        c,
        runtime,
        lambda aid: ToolSet(
            actions.definitions(aid, include_hidden=True),
            actions.handler(aid),
            basic.instructions(aid),
            actions.definitions(aid),
        ),
        tick_hook=execution.poll,
    )
    started, last_log = time.monotonic(), 0.0
    print(
        json.dumps({"run_id": c.store.run_id, "root": str(c.store.root), "agents": args.agents}),
        flush=True,
    )
    try:
        while c.state["status"] == "running":
            await engine.tick()
            elapsed = time.monotonic() - started
            if elapsed - last_log >= 15:
                best = c.store.run().best_result_ref
                print(
                    json.dumps(
                        {
                            "seconds": round(elapsed),
                            "calls": c.state["model_calls"],
                            "nodes": sum(len(a.iterations) for a in c.store.run().research_agents),
                            "agents": list(engine.active_agents),
                            "best": c.store.public_record(best)["score"] if best else None,
                            "status": c.state["status"],
                        }
                    ),
                    flush=True,
                )
                last_log = elapsed
            if c.state["status"] == "running" and (
                c.user_questions() or elapsed >= args.minutes * 60
            ):
                c.pause()
            await asyncio.sleep(0.1)
    finally:
        await engine.close()
        execution.close(cancel=c.state["status"] in {"succeeded", "stopped", "failed", "paused"})
        await basic.close()
        best = c.store.run().best_result_ref
        write_json(
            c.store.root / "lab-summary.json",
            {
                "status": c.state["status"],
                "elapsed_seconds": time.monotonic() - started,
                "model_calls": c.state["model_calls"],
                "total_tokens": c.state["total_tokens"],
                "nodes": sum(len(a.iterations) for a in c.store.run().research_agents),
                "best": c.store.public_record(best) if best else None,
            },
        )
        print(json.dumps({"done": c.state["status"], "run_id": c.store.run_id}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPOSITORY / ".rsi" / "research-lab")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--tool-root", type=Path)
    parser.add_argument("--agents", type=int, default=3)
    parser.add_argument("--resume", help="Resume this lab run after a worker interruption")
    parser.add_argument("--minutes", type=float, default=20)
    asyncio.run(main(parser.parse_args()))
