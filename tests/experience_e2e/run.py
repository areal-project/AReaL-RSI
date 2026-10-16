"""Manual, API-backed study: fresh workspaces/threads, fixed task and independent grading."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from areal_rsi.api.control import encode_record
from areal_rsi.api.research import SessionPhase, SessionStatus
from areal_rsi.infra.config import from_environment
from areal_rsi.infra.research import ResearchSystem

HERE = Path(__file__).resolve().parent


async def trial(
    root: Path, label: str, *, use_docs: bool, learn: bool, seconds: int, tokens: int
) -> None:
    workspace = root / label / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)
    shutil.copy(HERE / "task.md", workspace / "TASK.md")
    config = from_environment(workspace)
    config.effort = "medium"
    config.use_experience = use_docs
    config.auto_learn = False
    config.approval_policy = "never"
    system = ResearchSystem(root / "data")
    started = time.monotonic()
    try:
        run_id = await system.create(config)
        await system.enter_rsi(
            run_id,
            "请读取 TASK.md，调用 rsi_set_task 保存严格按规格实现、测试的任务，然后结束回复。"
            "只准备任务，不要调用 rsi_start_goal，不需要再问问题；宿主会设置预算并启动 Leader。",
        )
        try:
            async with asyncio.timeout(seconds + 300):
                async for event in system.events(run_id):
                    if event.kind in {"message", "error"}:
                        print(
                            json.dumps(
                                {
                                    "trial": label,
                                    "kind": event.kind,
                                    "role": event.role,
                                    "text": event.text,
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                    record = system.detail(run_id)
                    if (
                        record.phase == SessionPhase.INTERACTION
                        and record.goal is not None
                        and record.status == SessionStatus.IDLE
                        and not record.active_turn_id
                        and not record.handoff_pending
                    ):
                        await system.set_budget(
                            run_id, max_total_tokens=tokens, max_wall_seconds=seconds
                        )
                        await system.start(run_id)
                    if record.status in {
                        SessionStatus.COMPLETED,
                        SessionStatus.FAILED,
                        SessionStatus.BUDGET_LIMITED,
                        SessionStatus.WAITING,
                    }:
                        break
        except TimeoutError:
            await system.stop(run_id)
        record = system.detail(run_id)
        if record.active_turn_id:
            if record.status not in {SessionStatus.BUDGET_LIMITED, SessionStatus.STOPPED}:
                await system.pause(run_id)
            async with asyncio.timeout(15):
                while system.detail(run_id).active_turn_id:
                    await asyncio.sleep(0.05)
        record = system.detail(run_id)
        # The grader runs after the attempt; the agent never receives its implementation.
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(HERE / "grade.py"),
            str(workspace / "graph.py"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 40)
            score = (
                json.loads(stdout)
                if process.returncode == 0
                else {
                    "passed": [],
                    "failures": {"load": stderr.decode()[-1000:]},
                    "update_seconds": None,
                }
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            score = {
                "passed": [],
                "failures": {"timeout": "Grading exceeded 40 seconds"},
                "update_seconds": None,
            }
        report = {
            "label": label,
            "run_id": run_id,
            "thread_id": record.thread_id,
            "model": config.model,
            "effort": config.effort,
            "requested_limits": {"tokens": tokens, "active_seconds": seconds},
            "status": record.status,
            "tokens": record.tokens_used,
            "active_seconds": record.seconds_used,
            "elapsed_seconds": time.monotonic() - started,
            "experience": [asdict(ref) for ref in record.experience],
            "score": score,
        }
        result_path = root / label / "result.json"
        result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if learn:
            feedback = (
                "独立检查完成，以下是各类检查的结果。只沉淀通用方法，不要保存该题目的答案、"
                "代码、接口名、具体测试输入或常量。\n" + json.dumps(score, ensure_ascii=False)
            )
            document = await system.learn(run_id, feedback)
            (root / label / "learned.json").write_text(
                json.dumps(encode_record(document), ensure_ascii=False, indent=2)
            )
            print(json.dumps({"learned": document.path}, ensure_ascii=False), flush=True)
    finally:
        await system.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--root", type=Path, default=Path("/tmp/rsi-experience-study-20261015"))
    parser.add_argument("--use-docs", action="store_true")
    parser.add_argument("--learn", action="store_true")
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--tokens", type=int, default=120000)
    args = parser.parse_args()
    load_dotenv(HERE.parents[1] / ".env", override=False)
    if not os.environ.get("RSI_API_KEY"):
        raise SystemExit("Configure the project .env before this explicitly paid study")
    asyncio.run(
        trial(
            args.root,
            args.label,
            use_docs=args.use_docs,
            learn=args.learn,
            seconds=args.seconds,
            tokens=args.tokens,
        )
    )
