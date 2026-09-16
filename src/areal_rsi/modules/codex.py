"""Codex conversations with durable RSI tool receipts and asynchronous inbox delivery."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from areal_rsi.base.storage import new_id, write_json
from areal_rsi.modules.agent_runtime import (
    AgentRuntimeError,
    EventSink,
    RuntimeConfig,
    ToolCall,
    ToolHandler,
    ToolResult,
    TurnResult,
    session_lock,
)
from areal_rsi.modules.codex_transport import AppServer, CodexGateway
from areal_rsi.modules.dsh_tools import tool_environment

Json = dict[str, Any]
NATIVE_REPLACEMENTS = {"search_web", "fetch_url", "list_files", "read_file", "write_file"}


class CodexRuntime:
    backend = "codex"

    def __init__(self, config: RuntimeConfig, *, allow_web_access: bool = True):
        self.config = config
        self.allow_web_access = allow_web_access
        self._locks: dict[Path, asyncio.Lock] = {}
        self.connections: set[AppServer] = set()

    def for_interaction(self) -> CodexRuntime:
        return CodexRuntime(
            replace(self.config, max_model_calls_per_turn=None),
            allow_web_access=self.allow_web_access,
        )

    def configuration(self) -> Json:
        return {
            "runtime": self.backend,
            "sdk_version": "0.154.0",
            "model": self.config.model,
            "base_url": self.config.base_url,
            "reasoning_effort": self.config.reasoning_effort,
            "max_output_tokens": self.config.max_output_tokens,
            "max_model_calls_per_turn": self.config.max_model_calls_per_turn,
            "context_window_tokens": self.config.effective_context_window_tokens,
            "web_search": "live" if self.allow_web_access else "disabled",
        }

    def has_session(self, session_dir: Path) -> bool:
        return (session_dir / "codex-session.json").exists()

    async def reconfigure_session(self, session_dir: Path, revision: str) -> None:
        # Model/provider configuration is supplied again on thread/resume.
        if self.has_session(session_dir):
            path = session_dir / "codex-session.json"
            state = json.loads(path.read_text())
            state["model_settings_revision"] = revision
            write_json(path, state)

    async def close(self) -> None:
        await asyncio.gather(*(client.close() for client in tuple(self.connections)))
        self.connections.clear()

    async def turn(
        self,
        session_dir: Path,
        *,
        system_prompt: str,
        user_message: str | None,
        tools: Sequence[Json],
        handler: ToolHandler,
        message_id: str | None = None,
        event_sink: EventSink | None = None,
        recovery_state: Json | None = None,
        retry_id: str | None = None,
        incoming_messages: Callable[[], list[Json]] | None = None,
        advertised_tools: Sequence[Json] | None = None,
        workspace: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> TurnResult:
        directory = session_dir.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        async with self._locks.setdefault(directory, asyncio.Lock()):
            with session_lock(directory):
                conversation = _CodexConversation(self, directory, event_sink)
                return await conversation.run(
                    system_prompt,
                    user_message,
                    message_id,
                    [tool for tool in tools if tool["name"] not in NATIVE_REPLACEMENTS],
                    handler,
                    recovery_state or {},
                    incoming_messages,
                    workspace or directory,
                    environment or {},
                    retry_id,
                    advertised_tools,
                )


class _CodexConversation:
    def __init__(self, runtime: CodexRuntime, directory: Path, sink: EventSink | None):
        self.runtime, self.directory, self.sink = runtime, directory, sink
        self.path = directory / "codex-session.json"
        self.state: Json = (
            json.loads(self.path.read_text())
            if self.path.exists()
            else {
                "schema_version": 1,
                "backend": "codex",
                "thread_id": None,
                "turns": {},
                "calls": {},
                "injections": {},
            }
        )
        if self.state.get("backend") != "codex" or self.state.get("schema_version") != 1:
            raise AgentRuntimeError("Unsupported Codex session schema")
        self.turn: Json = {}
        self.server: AppServer | None = None
        self.gateway: CodexGateway | None = None
        self.stop_reason: str | None = None
        self.tool_lock = asyncio.Lock()
        self.rpc_turn_id: str | None = None
        self.interrupted_turn_id: str | None = None
        self.handler: ToolHandler | None = None
        self.allowed: set[str] = set()
        self.messages: Callable[[], list[Json]] | None = None
        self.recovery: Json = {}
        for name in ("requests", "responses"):
            (directory / name).mkdir(exist_ok=True)

    def save(self) -> None:
        write_json(self.path, self.state)

    async def emit(self, event: Json) -> None:
        if self.sink:
            await self.sink(event)

    def reserve(self, identifier: str) -> None:
        limit = self.turn.get("request_limit")
        if limit is not None and len(self.turn["requests"]) >= limit:
            self.stop_reason = "budget_exhausted"
            raise AgentRuntimeError("Per-turn model-call budget exhausted")
        self.turn["requests"].append(identifier)
        self.save()

    async def interrupt(self) -> None:
        if self.gateway:
            self.gateway.active = False
        if self.server and self.rpc_turn_id and self.interrupted_turn_id != self.rpc_turn_id:
            self.interrupted_turn_id = self.rpc_turn_id
            with suppress(AgentRuntimeError, TimeoutError):
                await asyncio.wait_for(
                    self.server.request(
                        "turn/interrupt",
                        {"threadId": self.state["thread_id"], "turnId": self.rpc_turn_id},
                    ),
                    timeout=5,
                )

    async def tool(self, method: str, params: Json) -> Json:
        if method != "item/tool/call":
            # Native command/file tools run within the configured policy. A tool
            # requiring a user decision must use RSI's durable ask_user instead.
            if method in {
                "item/commandExecution/requestApproval",
                "item/fileChange/requestApproval",
            }:
                return {"decision": "decline"}
            raise AgentRuntimeError("Unsupported Codex client request: " + method)
        async with self.tool_lock:
            if params.get("threadId") != self.state["thread_id"]:
                raise AgentRuntimeError("Tool call belongs to another Codex thread")
            name = params["tool"].removeprefix("rsi.")
            arguments = params.get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if name not in self.allowed or not isinstance(arguments, dict):
                raise AgentRuntimeError("Tool is not available to this agent")
            key = str(params["callId"])
            self.rpc_turn_id = params.get("turnId") or self.rpc_turn_id
            record = self.state["calls"].get(key)
            if record and (record["name"] != name or record["arguments"] != arguments):
                raise AgentRuntimeError("Codex reused a tool call ID with different arguments")
            if record and record.get("result") is not None:
                result = ToolResult(**record["result"])
            elif self.stop_reason:
                result = ToolResult(
                    {"skipped": True, "reason": self.stop_reason},
                    stop=True,
                    reason=self.stop_reason,
                )
            else:
                recovered = record is not None
                self.state["calls"][key] = {
                    "name": name,
                    "arguments": arguments,
                    "status": "running",
                    "message_id": self.turn["message_id"],
                }
                self.save()
                await self.emit(
                    {"type": "tool_started", "call_id": key, "name": name, "recovered": recovered}
                )
                assert self.handler is not None
                try:
                    result = await self.handler(ToolCall(name, arguments, key, recovered))
                except Exception as exc:
                    result = ToolResult(
                        {"error": str(exc).replace(self.runtime.config.api_key, "[redacted]")}
                    )
                self.state["calls"][key].update(status="completed", result=asdict(result))
                if result.stop:
                    self.stop_reason = result.reason
                    self.turn["stop_reason"] = result.reason
                self.save()
                await self.emit({"type": "tool_completed", "call_id": key, "name": name})
            if result.stop:
                self.stop_reason = result.reason
                # Stop while Codex is still awaiting this tool's reply. Our
                # asynchronous reader continues processing the interrupt ACK.
                await self.interrupt()
            output = (
                result.output
                if isinstance(result.output, str)
                else json.dumps(result.output, ensure_ascii=False)
            )
            return {"contentItems": [{"type": "inputText", "text": output}], "success": True}

    async def inject(self) -> None:
        if not self.messages or not self.server or not self.rpc_turn_id or self.stop_reason:
            return
        for message in self.messages():
            key = message["message_id"]
            old = self.state["injections"].get(key)
            if old and old.get("accepted"):
                await self.emit({"type": "message_injected", "message_id": key})
                continue
            if old and old.get("queued_turn_id") == self.rpc_turn_id:
                continue
            text = json.dumps(message, ensure_ascii=False)
            client_id = "rsi-" + hashlib.sha256(key.encode()).hexdigest()[:32]
            self.state["injections"][key] = {
                "accepted": False,
                "text": text,
                "client_id": client_id,
                "message_id": self.turn["message_id"],
            }
            self.save()
            try:
                await self.server.request(
                    "turn/steer",
                    {
                        "threadId": self.state["thread_id"],
                        "expectedTurnId": self.rpc_turn_id,
                        "clientUserMessageId": client_id,
                        "input": [{"type": "text", "text": text}],
                    },
                )
            except AgentRuntimeError:
                # Completion can race a delivery; leave it in the durable inbox.
                return
            # A steer ACK only confirms queuing. An immediate interrupt can
            # discard that queue; wait for the durable userMessage item instead.
            self.state["injections"][key]["queued_turn_id"] = self.rpc_turn_id
            self.save()

    async def acknowledge(self, item: Json) -> None:
        if item.get("type") != "userMessage" or not item.get("clientId"):
            return
        for key, record in self.state["injections"].items():
            if record["client_id"] == item["clientId"] and not record.get("accepted"):
                record["accepted"] = True
                self.save()
                await self.emit({"type": "message_injected", "message_id": key})

    def start_input(self, text: str, client_id: str) -> str:
        # Carry an interrupted steer into the next legitimate wake-up. The
        # runtime keeps the payload even if its caller restarts in the meantime.
        pending = {
            key: json.loads(record["text"])
            for key, record in self.state["injections"].items()
            if not record.get("accepted")
        }
        if self.messages:
            for message in self.messages():
                if not self.state["injections"].get(message["message_id"], {}).get("accepted"):
                    pending[message["message_id"]] = message
        for key, message in pending.items():
            self.state["injections"][key] = {
                "accepted": False,
                "client_id": client_id,
                "text": json.dumps(message, ensure_ascii=False),
                "message_id": self.turn["message_id"],
                "queued_turn_id": None,
            }
        self.turn["start_client_id"] = client_id
        self.save()
        if pending:
            text += "\n\n尚未送达的补充消息（保留原发件人与消息ID）：\n" + json.dumps(
                list(pending.values()),
                ensure_ascii=False,
            )
        return text

    async def finish(self, text: str = "", reason: str = "completed") -> TurnResult:
        usage: Json = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for key in self.turn["requests"]:
            path = self.directory / "responses" / f"{key}.json"
            if path.exists():
                latest = json.loads(path.read_text()).get("usage") or {}
                for name in usage:
                    usage[name] += latest.get(name, 0)
        result = TurnResult(
            self.state["thread_id"] or "", self.turn["message_id"], text, reason, usage
        )
        self.turn.update(status="completed", result=asdict(result))
        self.save()
        await self.emit({"type": "turn_stopped", "reason": reason})
        return result

    async def run(
        self,
        prompt: str,
        message: str | None,
        message_id: str | None,
        tools: Sequence[Json],
        handler: ToolHandler,
        recovery: Json,
        messages: Callable[[], list[Json]] | None,
        workspace: Path,
        environment: Mapping[str, str],
        retry_id: str | None,
        advertised: Sequence[Json] | None,
    ) -> TurnResult:
        key = message_id or self.state.get("active_message_id") or new_id("turn")
        self.turn = self.state["turns"].setdefault(
            key,
            {
                "message_id": key,
                "user_message": message,
                "requests": [],
                "status": "running",
                "request_limit": self.runtime.config.max_model_calls_per_turn,
            },
        )
        self.state["active_message_id"] = key
        self.handler, self.messages, self.recovery = handler, messages, recovery
        self.allowed = {tool["name"] for tool in tools}
        if retry_id and retry_id != self.turn.get("retry_id"):
            self.turn["retry_id"] = retry_id
            limit = self.runtime.config.max_model_calls_per_turn
            self.turn["request_limit"] = len(self.turn["requests"]) + limit if limit else None
            if self.turn.get("result", {}).get("stop_reason") == "budget_exhausted":
                self.turn.pop("result", None)
                self.turn.pop("stop_reason", None)
        self.save()
        for injection_id, record in self.state["injections"].items():
            if record.get("accepted") and record["message_id"] == key:
                await self.emit({"type": "message_injected", "message_id": injection_id})
        if self.turn.get("result"):
            return TurnResult(**self.turn["result"])
        # Reconcile application effects committed before a worker crashed.
        for call_id, record in self.state["calls"].items():
            if record["message_id"] == key and record["status"] == "running":
                result = await handler(ToolCall(record["name"], record["arguments"], call_id, True))
                record.update(status="completed", result=asdict(result))
                if result.stop:
                    self.turn["stop_reason"] = result.reason
                self.save()
        if self.turn.get("stop_reason"):
            return await self.finish(reason=self.turn["stop_reason"])
        attempt = 0
        while True:
            try:
                return await self.connected(prompt, tools, workspace, environment, advertised)
            except asyncio.CancelledError:
                raise
            except (AgentRuntimeError, OSError, TimeoutError) as exc:
                if self.stop_reason:
                    return await self.finish(reason=self.stop_reason)
                detail = str(exc).lower()
                if not any(
                    word in detail
                    for word in (
                        "disconnect",
                        "connection",
                        "stream",
                        "overload",
                        "429",
                        "502",
                        "503",
                        "504",
                        "temporar",
                        "timeout",
                        "timed out",
                        "transport",
                    )
                ):
                    raise
                attempt += 1
                delay = min(
                    self.runtime.config.retry_max_seconds,
                    self.runtime.config.retry_initial_seconds * 2 ** min(attempt - 1, 10),
                )
                await self.emit(
                    {
                        "type": "model_retry",
                        "request_id": "",
                        "attempt": attempt,
                        "error_code": "codex_transport",
                        "delay_seconds": delay,
                    }
                )
                await asyncio.sleep(delay)

    async def connected(
        self,
        prompt: str,
        tools: Sequence[Json],
        workspace: Path,
        environment: Mapping[str, str],
        advertised: Sequence[Json] | None,
    ) -> TurnResult:
        gateway = CodexGateway(
            self.runtime.config,
            self.directory,
            self.emit,
            self.reserve,
            allow_web_access=self.runtime.allow_web_access,
        )
        self.gateway = gateway
        home = self.directory / "codex-home"
        home.mkdir(mode=0o700, exist_ok=True)
        env = tool_environment(environment)
        env["CODEX_HOME"] = str(home)
        env["RSI_CODEX_GATEWAY_TOKEN"] = gateway.token
        server = AppServer(self.directory, env, self.tool)
        self.server = server
        self.runtime.connections.add(server)
        try:
            await gateway.start()
            await server.start()
            advertised_names = {tool["name"] for tool in advertised or tools}
            config: Json = {
                "model_provider": "rsi",
                "model_reasoning_effort": self.runtime.config.reasoning_effort,
                "model_context_window": self.runtime.config.effective_context_window_tokens,
                "model_providers": {
                    "rsi": {
                        "name": "RSI model gateway",
                        "base_url": gateway.url,
                        "env_key": "RSI_CODEX_GATEWAY_TOKEN",
                        "wire_api": "responses",
                        "supports_websockets": False,
                        "supports_standalone_web_search": self.runtime.allow_web_access,
                    }
                },
                "features": {
                    "goals": False,
                    "standalone_web_search": self.runtime.allow_web_access,
                    "enable_request_compression": False,
                },
                "agents": {"enabled": False},
                "web_search": "live" if self.runtime.allow_web_access else "disabled",
                "sandbox_workspace_write": {"network_access": True},
                "shell_environment_policy": {
                    "inherit": "all",
                    "exclude": ["RSI_CODEX_GATEWAY_TOKEN"],
                },
            }
            params: Json = {
                "model": self.runtime.config.model,
                "modelProvider": "rsi",
                "cwd": str(workspace),
                "approvalPolicy": "never",
                "sandbox": "workspace-write",
                "config": config,
                "developerInstructions": prompt,
            }
            fresh = self.state["thread_id"] is None
            params["dynamicTools"] = (
                [
                    {
                        "type": "namespace",
                        "name": "rsi",
                        "description": "研究协作、候选管理与实验作业工具",
                        "tools": [
                            {
                                "type": "function",
                                "name": tool["name"],
                                "description": tool.get("description", ""),
                                "inputSchema": tool["parameters"],
                                "deferLoading": tool["name"] not in advertised_names,
                            }
                            for tool in tools
                        ],
                    }
                ]
                if tools
                else []
            )
            if fresh:
                started = await server.request("thread/start", params)
                self.state["thread_id"] = started["thread"]["id"]
                self.save()
            else:
                started = await server.request(
                    "thread/resume",
                    {
                        **params,
                        "threadId": self.state["thread_id"],
                    },
                )
            if started.get("model") != self.runtime.config.model:
                raise AgentRuntimeError(
                    "Codex selected a different model than the research configuration"
                )
            write_json(
                self.directory / "codex-connection.json",
                {
                    "thread_id": self.state["thread_id"],
                    "model": started.get("model"),
                    "reasoning_effort": started.get("reasoningEffort"),
                    "runtime": "codex",
                },
            )
            if started.get("reasoningEffort") != self.runtime.config.reasoning_effort:
                raise AgentRuntimeError("Codex selected a different reasoning effort")
            previous_turns = started.get("thread", {}).get("turns", [])
            for prior_turn in previous_turns:
                for item in prior_turn.get("items", []):
                    await self.acknowledge(item)
            # A final response may have been committed by Codex just before our
            # worker died. Reconcile that result instead of starting more work.
            previous = next(
                (
                    t
                    for t in reversed(previous_turns)
                    if (
                        t["id"] == self.turn.get("codex_turn_id")
                        or any(
                            item.get("clientId") == self.turn.get("start_client_id")
                            and item.get("clientId") is not None
                            for item in t.get("items", [])
                        )
                    )
                ),
                None,
            )
            if previous and previous.get("status") == "completed":
                text = "\n".join(
                    i["text"]
                    for i in previous.get("items", [])
                    if i.get("type") == "agentMessage" and i.get("phase") != "commentary"
                )
                return await self.finish(text)
            text = self.turn["user_message"] or "继续当前未完成的任务。"
            if self.turn.get("codex_turn_id"):
                text = (
                    "同一任务在执行中断后恢复；预算没有重置。"
                    "核对已接受的作业和工具回执，避免重复提交。"
                )
            text += "\n\n运行时状态快照（不是新的用户请求）：\n" + json.dumps(
                {"authoritative_state": self.recovery}, ensure_ascii=False
            )
            client_id = new_id("rsi_input")
            text = self.start_input(text, client_id)
            started_turn = await server.request(
                "turn/start",
                {
                    "threadId": self.state["thread_id"],
                    "clientUserMessageId": client_id,
                    "input": [{"type": "text", "text": text}],
                },
            )
            self.rpc_turn_id = started_turn["turn"]["id"]
            self.turn["codex_turn_id"] = self.rpc_turn_id
            for record in self.state["injections"].values():
                if record["client_id"] == client_id:
                    record["queued_turn_id"] = self.rpc_turn_id
            self.save()
            answer = ""
            with (self.directory / "codex-events.jsonl").open("a") as events:
                while True:
                    if gateway.error:
                        raise gateway.error
                    if self.stop_reason:
                        await self.interrupt()
                        return await self.finish(reason=self.stop_reason)
                    try:
                        event = await asyncio.wait_for(server.events.get(), timeout=0.1)
                    except TimeoutError:
                        await self.inject()
                        continue
                    if isinstance(event, Exception):
                        raise event
                    events.write(json.dumps(event, ensure_ascii=False) + "\n")
                    events.flush()
                    method, data = event["method"], event.get("params", {})
                    if (
                        method == "model/rerouted"
                        and data.get("toModel") != self.runtime.config.model
                    ):
                        raise AgentRuntimeError("Codex rerouted away from the configured model")
                    if method == "item/completed":
                        item = data.get("item", {})
                        await self.acknowledge(item)
                        if item.get("type") == "agentMessage":
                            await self.emit({"type": "model_output", "text": item.get("text", "")})
                            if item.get("phase") != "commentary":
                                answer = item.get("text", "")
                    if method in {"item/started", "item/completed"}:
                        item = data.get("item", {})
                        if item.get("type") in {"commandExecution", "fileChange", "webSearch"}:
                            await self.emit(
                                {
                                    "type": "tool_started"
                                    if method.endswith("started")
                                    else "tool_completed",
                                    "call_id": item["id"],
                                    "name": item["type"],
                                }
                            )
                    if (
                        method == "turn/completed"
                        and data.get("turn", {}).get("id") == self.rpc_turn_id
                    ):
                        terminal = data["turn"]
                        if self.stop_reason:
                            return await self.finish(reason=self.stop_reason)
                        if terminal.get("status") == "failed":
                            raise AgentRuntimeError(
                                "Codex turn failed: " + json.dumps(terminal.get("error"))
                            )
                        if terminal.get("status") == "interrupted":
                            raise AgentRuntimeError("Codex turn interrupted without an RSI stop")
                        return await self.finish(answer)
                    await self.inject()
        finally:
            await self.interrupt()
            await server.close()
            await gateway.close()
            self.runtime.connections.discard(server)
            self.server = self.gateway = None
