"""A terminal view over ResearchSystem; no scheduling or model logic lives here."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, ClassVar

from rich.markdown import Markdown
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from areal_rsi.infra.coordination.config import ResearchConfig
from areal_rsi.infra.research import ResearchSystem

HELP = (
    "输入需求开始沟通。/start 开始 · /pause 暂停 · /resume 恢复 · /stop 停止 · "
    "/retry 重试 · /export PATH 导出 · /new 新任务 · /quit 保存退出"
)


class ResearchTUI(App[None]):
    TITLE = "AReaL RSI"
    ENABLE_COMMAND_PALETTE = False
    SUB_TITLE = "Leader goal"
    CSS = """
    #body { height: 1fr; }
    #conversation { width: 3fr; padding: 1 2; }
    #sidebar { width: 1fr; min-width: 24; max-width: 45; padding: 1; border-left: solid $accent; }
    #status { height: auto; margin-bottom: 1; }
    #goal { height: 1fr; overflow-y: auto; }
    Input { dock: bottom; }
    """
    BINDINGS: ClassVar = [("ctrl+q", "save_quit", "保存退出"), ("ctrl+p", "pause", "暂停")]

    def __init__(
        self,
        system: ResearchSystem,
        config: ResearchConfig,
        resume: str | None = None,
    ) -> None:
        super().__init__()
        self.system = system
        self.config = config
        self.run_id = resume
        self.cursor = 0
        self.polling = False
        self.closed = False
        self.last_output: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield RichLog(id="conversation", wrap=True, highlight=False, markup=False)
            with Vertical(id="sidebar"):
                yield Static("尚未开始", id="status", markup=False)
                yield Static("Leader 将在这里记录目标与进展。", id="goal", markup=False)
        yield Input(placeholder="需求、补充说明，或 /help", id="input")
        yield Footer()

    def write(self, text: str, *, style: str = "") -> None:
        self.query_one(RichLog).write(Text(text, style=style))

    async def on_mount(self) -> None:
        self.write(HELP, style="dim")
        self.query_one(Input).focus()
        if self.run_id:
            try:
                detail = await self.system.detail(self.run_id)
                if detail["status"] in {"planned", "running"}:
                    await self.system.resume(self.run_id)
            except (OSError, ValueError) as exc:
                self.write(str(exc), style="red")
        await self.poll()
        self.set_interval(0.2, self.poll)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.clear()
        if not text:
            return
        try:
            if text.startswith("/"):
                await self.command(text)
            elif self.run_id:
                await self.system.send_message(self.run_id, text)
            else:
                self.run_id = await self.system.create(self.config, message="")
                await self.system.send_message(self.run_id, text)
            await self.poll()
        except (OSError, ValueError, RuntimeError) as exc:
            self.write(str(exc), style="red")

    async def command(self, text: str) -> None:
        args = shlex.split(text)
        name = args[0]
        if name == "/help":
            self.write(HELP)
        elif name == "/quit":
            await self.action_save_quit()
        elif name == "/new":
            if self.run_id:
                detail = await self.system.detail(self.run_id)
                if detail["status"] in {"planned", "running", "paused"}:
                    raise ValueError("请先 /stop 当前任务，再创建新任务")
            self.run_id, self.cursor = None, 0
            self.query_one(RichLog).clear()
            self.last_output.clear()
            self.query_one("#status", Static).update("尚未开始")
            self.query_one("#goal", Static).update("")
            self.write(HELP)
        elif name == "/start":
            if not self.run_id:
                self.run_id = await self.system.create(self.config)
            await self.system.start(self.run_id)
        elif name in {"/pause", "/resume", "/stop", "/retry", "/export"}:
            if not self.run_id:
                raise ValueError("尚无任务，请先输入需求")
            if name == "/pause":
                await self.system.pause(self.run_id)
            elif name == "/resume":
                await self.system.resume(self.run_id)
            elif name == "/stop":
                await self.system.stop(self.run_id)
            elif name == "/retry":
                detail = await self.system.detail(self.run_id)
                agent = "interaction" if detail["status"] == "planned" else "rsi"
                await self.system.retry(self.run_id, agent)
            else:
                if len(args) != 2:
                    raise ValueError("用法：/export 新目录")
                await self.system.export(self.run_id, Path(args[1]).expanduser().resolve())
                self.write(f"已导出到 {args[1]}")
        else:
            raise ValueError("未知命令。输入 /help 查看帮助")

    async def poll(self) -> None:
        if not self.run_id or self.polling or self.closed:
            return
        self.polling = True
        try:
            detail = await self.system.detail(self.run_id, after=self.cursor)
            for event in detail["events"]:
                self.render_event(event)
            self.cursor = detail["event_cursor"]
            budget = detail.get("budget", {})
            self.query_one("#status", Static).update(
                f"{self.run_id}\n{detail['mode']} · {detail['status']}\n"
                f"Tokens: {budget.get('total_tokens', 0)}\n"
                f"验证: {budget.get('verify_calls', 0)}\n"
                f"工作区: {detail.get('workspace') or '待启动'}\n"
                f"{'执行中' if detail['worker_alive'] else '状态已保存'}"
            )
            goal = detail.get("goal") or {}
            self.query_one("#goal", Static).update(
                "\n\n".join(
                    filter(
                        None,
                        [
                            goal.get("summary", ""),
                            goal.get("current_step", ""),
                            "\n".join(goal.get("next_steps", [])),
                            goal.get("blocker", ""),
                        ],
                    )
                )
            )
        except (OSError, ValueError) as exc:
            self.write(str(exc), style="red")
        finally:
            self.polling = False

    def render_event(self, event: dict[str, Any]) -> None:
        kind = event["kind"]
        data = event.get("data", {})
        agent = event.get("agent_id") or ""
        role = {"rsi": "Leader", "interaction": "Interaction", "user": "你"}.get(agent, agent)
        if kind == "assistant_output":
            self.last_output[agent] = data.get("text", "").strip()
            self.write(role, style="bold cyan")
            self.query_one(RichLog).write(Markdown(data.get("text", "")))
        elif kind == "message" and data.get("text"):
            sender = data.get("sender", "")
            if self.last_output.get(sender) != data["text"].strip():
                label = {"rsi": "Leader", "interaction": "Interaction", "user": "你"}.get(
                    sender, sender
                )
                self.write(f"{label}: {data['text']}")
        elif kind in {"tool_started", "tool_completed"}:
            self.write(f"{role} · {data.get('name', kind)}", style="dim")
        elif kind in {"agent_failed", "worker_failed", "budget_exhausted", "run_stopped"}:
            self.write(str(data), style="yellow")

    async def action_pause(self) -> None:
        try:
            await self.command("/pause")
        except (ValueError, OSError) as exc:
            self.write(str(exc), style="red")

    async def action_save_quit(self) -> None:
        await self.system.close()
        self.closed = True
        self.exit()

    async def on_unmount(self) -> None:
        if not self.closed:
            await self.system.close()
