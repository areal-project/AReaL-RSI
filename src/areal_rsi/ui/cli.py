"""中文对话 TUI；确认后通过 CLI 子进程启动实验，界面仍可交互。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from areal_rsi.api.base import ExperimentPlan, ExperimentRun, RunState
from areal_rsi.app import LocalApp
from areal_rsi.base.storage import FileRecords, decode, encode, new_id, now, write_json
from areal_rsi.config import ModelConfigurationError


def data_root() -> Path:
    return Path(os.environ.get("RSI_DATA_ROOT", ".rsi/local")).resolve()


async def run_experiment(plan_file: str | None, resume: str | None) -> int:
    root = data_root()
    app = await LocalApp.create(root, lambda text: print(text, flush=True))
    try:
        if resume:
            run = await app.service.resume(resume)
        else:
            assert plan_file is not None
            plan = decode(ExperimentPlan, json.loads(Path(plan_file).read_text()))
            run = await app.service.start(plan)
        print(f"实验已启动 run={run.run_id}", flush=True)
        if run.run_id in app.service.tasks:
            task = app.service.tasks[run.run_id]
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, task.cancel)
            run = await task
        print(json.dumps(encode(run), ensure_ascii=False), flush=True)
        return 0 if run.state == RunState.SUCCEEDED else 1
    finally:
        await app.close()


async def tui() -> int:
    root = data_root()
    transcript = root / "tui" / f"{new_id('conversation')}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    reader: asyncio.Task[None] | None = None
    process: asyncio.subprocess.Process | None = None
    pending: ExperimentPlan | None = None

    def show(role: str, text: str) -> None:
        with transcript.open("a") as stream:
            stream.write(
                json.dumps({"time": now(), "role": role, "text": text}, ensure_ascii=False) + "\n"
            )
        print(f"{role}> {text}", flush=True)

    async def read_process(child: asyncio.subprocess.Process) -> None:
        assert child.stdout is not None
        while line := await child.stdout.readline():
            text = line.decode(errors="replace").strip()
            # 完整状态对象已经写入文件，TUI 展示精简进度。
            if not text.startswith('{"run_id"'):
                show("实验", text)
        code = await child.wait()
        show("系统", f"实验进程结束（exit={code}），可以继续对话或 /status。")

    app = await LocalApp.create(root, lambda text: show("系统", text))
    prompt: PromptSession[str] = PromptSession()
    show("RSI", "你好，我是 RSI Agent。可以日常对话，也可以发起 Harness 自迭代。")
    show("系统", "命令：/status 查看实验，/stop 停止，/resume RUN_ID 恢复，/exit 退出。")
    try:
        with patch_stdout(raw=True):
            while True:
                try:
                    message = (await prompt.prompt_async("你> ")).strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not message:
                    continue
                show("用户", message)
                if message == "/exit":
                    if process is not None and process.returncode is None:
                        show("系统", "实验仍在运行；请先 /stop，或等待完成后退出。")
                        continue
                    break
                if message == "/status":
                    runs = FileRecords(root / "run-states").list(ExperimentRun)
                    show(
                        "系统",
                        "\n".join(
                            f"{r.run_id} {r.state.value} 已完成 "
                            f"{sum(len(a.iterations) for a in r.research_agents)}"
                            f"/{r.plan.max_iterations * r.plan.research_agent_count} 个研究轮次"
                            for r in runs
                        )
                        or "尚无实验",
                    )
                    continue
                if message == "/stop":
                    if process and process.returncode is None:
                        process.terminate()
                        assert reader is not None
                        await reader
                    continue
                if message.startswith("/resume "):
                    if process and process.returncode is None:
                        show("系统", "请先等待当前实验完成。")
                        continue
                    command = [sys.executable, "-m", "areal_rsi", "resume", message.split()[1]]
                elif pending is not None and message in ("确认", "开始", "确认启动", "yes"):
                    plan_file = root / "plans" / f"{new_id('plan')}.json"
                    write_json(plan_file, pending)
                    pending = None
                    command = [sys.executable, "-m", "areal_rsi", "run", "--plan", str(plan_file)]
                else:
                    if message in ("取消", "取消计划"):
                        pending = None
                        show("RSI", "已取消待确认计划。")
                        continue
                    try:
                        reply = await app.rsi.chat(message)
                        if isinstance(reply, ExperimentPlan):
                            if process and process.returncode is None:
                                show("系统", "已有实验正在运行，完成后可启动新实验。")
                                continue
                            pending = reply
                            show(
                                "RSI",
                                f"实验计划：{reply.goal}；{reply.benchmark}；"
                                f"固定模型，仅优化 Harness；{reply.research_agent_count} 条研究线，"
                                f"每条最多 {reply.max_iterations} 轮。"
                                "输入“确认”启动。",
                            )
                        else:
                            show("RSI", reply)
                    except Exception as exc:
                        show("错误", str(exc))
                    continue
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    limit=1024 * 1024,
                )
                reader = asyncio.create_task(read_process(process))
                show("系统", f"已启动实验子进程 pid={process.pid}；可继续输入 /status。")
    finally:
        if process and process.returncode is None:
            process.terminate()
            if reader:
                await reader
        await app.close()
        show("系统", f"会话已保存：{transcript}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="RSI 本地研发入口")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("tui", help="打开对话 TUI")
    run = commands.add_parser("run", help="执行已经确认的计划")
    run.add_argument("--plan", required=True)
    resume = commands.add_parser("resume", help="恢复到最后一个已完成轮次")
    resume.add_argument("run_id")
    commands.add_parser("status", help="列出本地实验")
    web = commands.add_parser("web", help="启动本地只读研究面板，不启动实验")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--run", dest="run_id")
    args = parser.parse_args()
    if args.command == "web":
        from areal_rsi.ui.web import serve

        serve(data_root(), port=args.port, run_id=args.run_id)
        return
    if args.command == "status":
        print(
            json.dumps(
                encode(FileRecords(data_root() / "run-states").list(ExperimentRun)),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    try:
        if args.command == "run":
            code = asyncio.run(run_experiment(args.plan, None))
        elif args.command == "resume":
            code = asyncio.run(run_experiment(None, args.run_id))
        else:
            code = asyncio.run(tui())
    except ModelConfigurationError as exc:
        parser.exit(
            2,
            f"配置错误：{exc}\n"
            "可填写本地 .env（参考 .env.example），然后运行：\n"
            "  uv run --env-file .env --locked rsi tui\n",
        )
    raise SystemExit(code)
