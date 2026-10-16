"""Controllable app-server boundary for races and failures, not a replacement agent loop."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from areal_rsi.api.research import SessionConfig
from areal_rsi.infra.codex import CodexError, NativeEvent

Json = dict[str, Any]


@dataclass
class FakeThread:
    turns: list[Json] = field(default_factory=list)


class FakeCodex:
    instances: ClassVar[list[FakeCodex]] = []
    threads: ClassVar[dict[str, FakeThread]] = {}

    def __init__(self, home: Path, log: Path, config: SessionConfig):
        self.events: asyncio.Queue[NativeEvent] = asyncio.Queue()
        self.requests: list[tuple[str, Json]] = []
        self.responses: dict[int | str, Json] = {}
        self.reader: asyncio.Task[None] | None = None
        self.thread_id = ""
        self.turn_id = ""
        self.closed = False
        self.closing = False
        self.awake = False
        self.fail_method: str | None = None
        self.instances.append(self)

    async def start(self) -> None:
        return

    def keep_awake(self, active: bool) -> None:
        self.awake = active

    async def request(self, method: str, params: Json, *, timeout: float = 60) -> Json:
        self.requests.append((method, copy.deepcopy(params)))
        if self.fail_method == method:
            raise CodexError("simulated transport outcome unknown")
        if method == "thread/start":
            self.thread_id = f"thread-{len(self.threads)}"
            self.threads[self.thread_id] = FakeThread()
            return {"thread": {"id": self.thread_id}}
        if method == "thread/resume":
            self.thread_id = params["threadId"]
            return {"thread": {"id": self.thread_id}}
        if method == "thread/fork":
            source = self.threads[params["threadId"]]
            self.thread_id = f"thread-{len(self.threads)}"
            self.threads[self.thread_id] = copy.deepcopy(source)
            return {"thread": {"id": self.thread_id}}
        if method == "thread/turns/list":
            return {"data": list(reversed(self.threads[params["threadId"]].turns))[:1]}
        if method == "turn/start":
            self.turn_id = f"turn-{len(self.threads[self.thread_id].turns)}"
            turn = {"id": self.turn_id, "status": "inProgress", "input": params["input"]}
            self.threads[self.thread_id].turns.append(turn)
            return {"turn": turn}
        if method == "turn/steer":
            if not self.turn_id:
                raise CodexError("no active turn", -32600)
            return {"turnId": self.turn_id}
        if method == "turn/interrupt":
            await self.finish(status="interrupted")
            return {}
        raise AssertionError(method)

    async def reply(self, request_id: int | str, result: Json) -> None:
        self.responses[request_id] = result

    async def reject(self, request_id: int | str, message: str) -> None:
        self.responses[request_id] = {"error": message}

    async def finish(self, text: str = "reply", *, status: str = "completed") -> None:
        turn = self.threads[self.thread_id].turns[-1]
        turn["status"] = status
        await self.events.put(
            NativeEvent(
                "item/completed",
                {
                    "threadId": self.thread_id,
                    "turnId": self.turn_id,
                    "item": {"type": "agentMessage", "id": self.turn_id, "text": text},
                },
            )
        )
        await self.events.put(
            NativeEvent(
                "turn/completed",
                {
                    "threadId": self.thread_id,
                    "turn": turn,
                },
            )
        )
        self.turn_id = ""

    async def tool(self, request_id: int, name: str, arguments: Json, *, call_id: str = "") -> Json:
        await self.events.put(
            NativeEvent(
                "item/tool/call",
                {
                    "threadId": self.thread_id,
                    "turnId": self.turn_id,
                    "callId": call_id or str(request_id),
                    "tool": name,
                    "arguments": arguments,
                },
                request_id,
            )
        )
        async with asyncio.timeout(3):
            while request_id not in self.responses:
                await asyncio.sleep(0.01)
        return self.responses[request_id]

    async def close(self) -> None:
        self.keep_awake(False)
        self.closed = True
        self.closing = True
