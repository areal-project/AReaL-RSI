"""Terminal workbench client. All agent execution lives in ResearchApp on the server."""

from __future__ import annotations

import curses
import time
import unicodedata
from contextlib import suppress
from pathlib import Path
from typing import Any

from areal_rsi.base.storage import new_id
from areal_rsi.coordination.config import load_config
from areal_rsi.ui.launch import WorkbenchClient, ensure_workbench

HELP = "/new  /list  /use ID  /start  /pause  /resume  /stop  /quit；直接输入文字与 Agent 对话"


class TerminalClient:
    def __init__(self, client: WorkbenchClient):
        self.client = client
        self.run_id: str | None = None
        self.lines = [HELP]
        self.after = 0
        self.status = "未选择研究"
        self.pending: tuple[str, dict[str, Any]] | None = None

    def select(self, run_id: str) -> None:
        self.client.request("/api/runs/" + run_id)
        self.run_id, self.after = run_id, 0
        self.lines = [HELP, "已选择：" + run_id]

    def create(self, config: Path | None = None) -> None:
        body: dict[str, Any] = {"run_id": new_id("research")}
        if config:
            cfg = load_config(config.resolve())
            cfg.pin_model_settings()
            body.update(
                config=cfg.public(),
                model_settings={**cfg.model_settings(), "api_key": cfg.api_key()},
            )
        result = self.client.request("/api/runs", body)
        self.select(result["run_id"])
        self.lines.append("配置已准备；/start 开始，或输入需求进行讨论。")

    def submit(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return True
        if text == "/quit":
            return False
        if text == "/help":
            self.lines.append(HELP)
        elif text == "/list":
            for run in self.client.request("/api/runs")["runs"]:
                self.lines.append(f"{run['run_id']} [{run['status']}] {run['objective']}")
        elif text.startswith("/use "):
            self.select(text[5:].strip())
        elif text == "/new":
            self.create()
        elif text.startswith("/new "):
            self.create(Path(text[5:].strip()))
        elif text == "/retry-send":
            if self.pending is None:
                raise ValueError("没有待重试消息")
            path, body = self.pending
            self.client.request(path, body)
            self.pending = None
        elif text in {"/start", "/pause", "/resume", "/stop"}:
            if self.run_id is None:
                raise ValueError("先 /new 或 /use ID")
            self.client.request(f"/api/runs/{self.run_id}{text}", {})
            self.lines.append("操作已提交：" + text)
        elif text.startswith("/"):
            raise ValueError("未知命令；" + HELP)
        else:
            if self.run_id is None:
                self.create()
            body = {"text": text, "message_id": new_id("user")}
            path = f"/api/runs/{self.run_id}/chat"
            self.pending = path, body
            self.client.request(path, body)
            self.pending = None
        return True

    def poll(self) -> None:
        if self.run_id is None:
            return
        detail = self.client.request(f"/api/runs/{self.run_id}?after={self.after}")
        self.status = f"{self.run_id} | {detail['status']}"
        for event in detail.get("events", []):
            self.after = max(self.after, event["sequence"])
            data = event.get("data", {})
            if event["kind"] == "message" and data.get("text"):
                self.lines.append(f"[{data.get('sender', 'agent')}] {data['text']}")
            elif event["kind"] in {"run_completed", "worker_error", "budget_exhausted"}:
                self.lines.append(str(data.get("summary") or data.get("error") or data))
        self.lines = self.lines[-500:]


def wrapped(text: str, width: int) -> list[str]:
    lines, line, used = [], "", 0
    for char in text:
        size = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if char == "\n" or used + size > width:
            lines.append(line)
            line, used = "", 0
        if char != "\n":
            line += char
            used += size
    return [*lines, line]


def run_screen(screen: Any, controller: TerminalClient) -> None:
    curses.curs_set(1)
    screen.timeout(100)
    draft, scroll, next_poll = "", 0, 0.0
    while True:
        if time.monotonic() >= next_poll:
            try:
                controller.poll()
            except (OSError, ValueError) as exc:
                controller.status = "连接失败（保留会话，自动重连）：" + str(exc)
            next_poll = time.monotonic() + 2
        height, width = screen.getmaxyx()
        screen.erase()
        if height >= 5 and width >= 20:

            def put(row: int, value: str, style: int = 0, width: int = width) -> None:
                with suppress(curses.error):
                    screen.addstr(row, 0, wrapped(value, width - 1)[0], style)

            put(0, "AReaL RSI | " + controller.status, curses.A_REVERSE)
            lines = [part for line in controller.lines for part in wrapped(line, width - 1)]
            end = max(0, len(lines) - scroll)
            for row, line in enumerate(lines[max(0, end - height + 4) : end], 1):
                put(row, line)
            put(height - 3, "PgUp/PgDn 滚动 | /help 命令 | 退出终端不会停止后台研究")
            put(height - 2, "> " + wrapped(draft, width - 3)[-1])
        screen.refresh()
        try:
            key = screen.get_wch()
        except curses.error:
            continue
        if key in ("\n", "\r"):
            try:
                if not controller.submit(draft):
                    return
                draft, scroll, next_poll = "", 0, 0
            except (OSError, ValueError) as exc:
                controller.lines.append(f"错误：{exc}；消息失败可 /retry-send")
        elif key == "\x15":
            draft = ""
        elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            draft = draft[:-1]
        elif key == curses.KEY_PPAGE:
            scroll += max(1, height - 4)
        elif key == curses.KEY_NPAGE:
            scroll = max(0, scroll - max(1, height - 4))
        elif key in ("\x03", "\x04"):
            return
        elif isinstance(key, str) and key.isprintable():
            draft += key


def launch_tui(
    root: Path,
    *,
    port: int = 8197,
    config: Path | None = None,
    resume: str | None = None,
    simulated: bool = False,
) -> int:
    if simulated:
        raise ValueError("TUI 使用真实研究；模拟请显式启动测试工作台")
    if config and resume:
        raise ValueError("--config 和 --resume 不能同时使用")
    client, _ = ensure_workbench(port, root)
    controller = TerminalClient(client)
    if resume:
        controller.select(resume)
    elif config:
        controller.create(config)
    curses.wrapper(run_screen, controller)
    print(f"工作台继续运行：{client.url}；研究 ID：{controller.run_id or '未创建'}")
    return 0
