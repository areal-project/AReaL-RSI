"""DSH terminal transport. The Node view owns rendering; ResearchSystem owns all work."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, get_args

from areal_rsi.api.control import TerminalInput, decode_record, encode_record
from areal_rsi.api.research import ReasoningEffort, SessionStatus
from areal_rsi.infra.config import from_environment
from areal_rsi.infra.research import ResearchSystem

HELP = """Enter 发送 · Shift+Enter 换行 · Esc 暂停 · Ctrl+O 展开工具 · Ctrl+Q 保存退出
/rsi [目标] 沿用上下文讨论 · /start 执行 · /chat 普通模式
/pause 暂停 · /resume 继续 · /stop 停止 · /status 查看目标
/model [模型名] 查看 API 模型列表或切换模型 · /effort [强度] 查看或设置推理强度
/budget 查看预算 · /budget off 取消限制 · /budget <tokens|off> <秒|off> 设置总上限
/learn [复盘反馈] 独立总结经验 · /docs 查看经验目录
/learn-mode [auto|guided] 自动总结或等待人工复盘
/image 路径 [说明] 附加图片 · /sessions 列出会话 · /open ID · /new · /quit"""


def launch(args: argparse.Namespace) -> int:
    directory = Path(__file__).parent / "dsh"
    node = shutil.which("node")
    if not node:
        raise ValueError("DSH TUI requires Node.js >= 22")
    version = subprocess.check_output([node, "--version"], text=True).strip()
    if int(version.removeprefix("v").split(".")[0]) < 22:
        raise ValueError("DSH TUI requires Node.js >= 22")
    if not (directory / "node_modules/@earendil-works/pi-tui").exists():
        subprocess.run(
            ["npm", "ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=directory,
            check=True,
        )
    subprocess.run([node, str(directory / "patch.mjs")], check=True)
    arguments = [node, str(directory / "main.mjs")]
    for name in ("workspace", "data_root", "resume", "env_file"):
        if value := getattr(args, name):
            arguments.extend(["--" + name.replace("_", "-"), str(value)])
    return subprocess.run(arguments, env={**os.environ, "RSI_PYTHON": sys.executable}).returncode


async def serve(args: argparse.Namespace) -> int:
    system = ResearchSystem(args.data_root)
    config = system.detail(args.resume).config if args.resume else from_environment(args.workspace)
    run_id = args.resume or await system.create(config)
    images: list[Path] = []
    consumer: asyncio.Task[None] | None = None
    background: set[asyncio.Task[None]] = set()

    def send(kind: str, **fields: Any) -> None:
        print(json.dumps({"type": kind, **fields}, ensure_ascii=False), flush=True)

    async def consume() -> None:
        async for event in system.events(run_id):
            if event.kind == "status":
                send("state", record=encode_record(system.detail(run_id)))
            else:
                send("event", event=encode_record(event))

    def schedule(coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        background.add(task)
        task.add_done_callback(background.discard)
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)

    async def show_models(selected_run: str) -> None:
        try:
            models = await system.list_models(selected_run)
            current = system.detail(selected_run).config.model
            text = (
                "API 返回的模型（并非都支持 Agent 工具调用）：\n"
                + "\n".join(("* " if model == current else "  ") + model for model in models)
                if models
                else "API 返回的模型列表为空。"
            )
        except (ValueError, RuntimeError, OSError) as exc:
            text = f"{exc}；仍可通过 /model 模型名 手动设置。"
        if run_id == selected_run:
            send("notice", text=text)

    async def learn(selected_run: str, feedback: str) -> None:
        try:
            await system.learn(selected_run, feedback)
        except (ValueError, RuntimeError, OSError) as exc:
            send("notice", text=str(exc))

    async def connect() -> None:
        nonlocal consumer, config
        await system.open(run_id)
        config = system.detail(run_id).config
        send("ready", record=encode_record(system.detail(run_id)), help=HELP)
        for event in system.store.transcript(run_id):
            send("event", event=encode_record(event))
        consumer = asyncio.create_task(consume())

    async def disconnect() -> None:
        if consumer:
            consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer
        await system.close()

    try:
        await connect()
        reader = asyncio.StreamReader(limit=16 * 1024 * 1024)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader),
            sys.stdin,
        )
        try:
            while line := await reader.readline():
                try:
                    request = decode_record(TerminalInput, json.loads(line))
                    if request.action == "close":
                        break
                    if request.action == "pause":
                        await system.pause(run_id)
                    elif request.action == "answer":
                        if request.request_id is None:
                            raise ValueError("Missing request ID")
                        await system.answer(
                            run_id,
                            request.request_id,
                            approved=request.approved,
                            answers=request.answers or None,
                        )
                    elif request.action == "submit":
                        text = request.text.strip()
                        if not text.startswith("/"):
                            await system.send_message(run_id, text, images=images)
                            images.clear()
                            continue
                        name, _, body = text.partition(" ")
                        body = body.strip()
                        controls = {
                            "/pause": system.pause,
                            "/resume": system.resume,
                            "/stop": system.stop,
                            "/start": system.start,
                            "/chat": system.chat,
                        }
                        if name in controls:
                            await controls[name](run_id)
                        elif name == "/budget":
                            if body:
                                parts = body.split()
                                if parts == ["off"]:
                                    parts = ["off", "off"]
                                try:
                                    if len(parts) != 2:
                                        raise ValueError
                                    tokens = None if parts[0] == "off" else int(parts[0])
                                    seconds = None if parts[1] == "off" else float(parts[1])
                                except ValueError:
                                    raise ValueError(
                                        "用法：/budget off 或 /budget <tokens|off> <秒|off>"
                                    ) from None
                                await system.set_budget(
                                    run_id, max_total_tokens=tokens, max_wall_seconds=seconds
                                )
                            record = system.detail(run_id)
                            limits = record.goal.limits if record.goal else None
                            token_cap = limits.max_total_tokens if limits else None
                            time_cap = limits.max_wall_seconds if limits else None
                            send(
                                "notice",
                                text=f"Token：{record.tokens_used} / {token_cap or '不限'}；"
                                f"活跃秒：{record.seconds_used:.1f} / {time_cap or '不限'}。\n"
                                "上限包含已用量；修改预算不会自动恢复，暂停后用 /resume 继续。",
                            )
                        elif name in {"/model", "/effort"}:
                            if body:
                                if name == "/model":
                                    await system.set_model(run_id, body)
                                else:
                                    if body not in get_args(ReasoningEffort):
                                        raise ValueError(
                                            "可选 effort：" + " / ".join(get_args(ReasoningEffort))
                                        )
                                    await system.set_effort(run_id, body)
                                hint = "从下一回合生效；已经开始的执行或总结继续使用原设置。"
                            elif name == "/model":
                                hint = "用法：/model 模型名；正在查询 API 模型列表…"
                            else:
                                hint = "用法：/effort " + " | ".join(get_args(ReasoningEffort))
                            config = system.detail(run_id).config
                            send(
                                "notice",
                                text=f"当前模型：{config.model} · effort：{config.effort}\n{hint}",
                            )
                            if name == "/model" and not body:
                                schedule(show_models(run_id))
                        elif name == "/rsi":
                            await system.enter_rsi(run_id, body, images=images)
                            images.clear()
                        elif name == "/learn":
                            schedule(learn(run_id, body))
                        elif name == "/learn-mode":
                            if body:
                                await system.set_learning_mode(run_id, body)
                            config = system.detail(run_id).config
                            send(
                                "notice",
                                text="经验总结模式："
                                + (
                                    "auto；RSI 完成后自动总结。"
                                    if config.auto_learn
                                    else "guided；等待 /learn 复盘意见；/learn 可跳过补充。"
                                ),
                            )
                        elif name == "/docs":
                            send(
                                "notice",
                                text=system.experience.context(system.experience.snapshot()),
                            )
                        elif name == "/image":
                            parts = shlex.split(body)
                            if not parts:
                                raise ValueError("用法：/image 路径 [说明]")
                            path = Path(parts[0]).expanduser()
                            if not path.is_absolute():
                                path = Path(system.detail(run_id).config.workspace) / path
                            path = path.resolve(strict=True)
                            if not path.is_file():
                                raise ValueError("图片路径必须是文件")
                            images.append(path)
                            if len(parts) > 1:
                                await system.send_message(
                                    run_id, " ".join(parts[1:]), images=images
                                )
                                images.clear()
                            else:
                                send("notice", text="已附加图片：" + str(path))
                        elif name in {"/new", "/open"}:
                            if system.detail(run_id).status in {
                                SessionStatus.RUNNING,
                                SessionStatus.WAITING,
                            }:
                                raise ValueError("请先暂停当前任务")
                            if name == "/open":
                                system.detail(body)
                            await disconnect()
                            run_id = body if name == "/open" else await system.create(config)
                            images.clear()
                            await connect()
                        elif name == "/sessions":
                            send(
                                "notice",
                                text="\n".join(
                                    f"{record.run_id} · {record.phase.value} · "
                                    f"{record.status.value} · "
                                    + (
                                        record.goal.objective
                                        if record.goal
                                        else record.config.workspace
                                    )
                                    for record in system.store.list_sessions()[:20]
                                ),
                            )
                        elif name == "/status":
                            record = system.detail(run_id)
                            send(
                                "notice",
                                text=(record.goal.objective if record.goal else "普通会话")
                                + "\n"
                                + record.progress.summary
                                + "\n"
                                + "\n".join(record.progress.evidence),
                            )
                        elif name == "/help":
                            send("notice", text=HELP)
                        elif name == "/quit":
                            break
                        else:
                            raise ValueError("未知命令；输入 /help 查看帮助")
                except (ValueError, RuntimeError, OSError) as exc:
                    send("notice", text=str(exc))
        finally:
            transport.close()
        return 0
    finally:
        await disconnect()
        for task in background:
            task.cancel()
        if background:
            await asyncio.gather(*background, return_exceptions=True)
