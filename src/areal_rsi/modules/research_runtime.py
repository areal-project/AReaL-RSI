"""Research workbench boundary over the original package-backed RSI runtime.

Provider requests, native tools, history, compaction and retry policy belong to
AReaL Agent Runtime. This adapter translates RSI deliveries, scoped task tools
and public runtime events; it does not implement a harness or a model loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import ValidationError, validate

from areal_rsi.api.base import AgentSpec, ModelVersion
from areal_rsi.base.storage import FileContentStore, FileTrajectoryStore, decode, now, write_json
from areal_rsi.coordination.config import ResearchConfig
from areal_rsi.modules.agent_runtime import EventSink, ToolCall, ToolHandler, ToolResult, TurnResult
from areal_rsi.modules.runtime_package import PackageAgentRuntime, PackageSession, RuntimeSettings

Json = dict[str, Any]
ACKNOWLEDGE_MESSAGES = {
    "name": "acknowledge_messages",
    "description": "确认已读 incoming_messages 中的消息；不代表同意或采纳。未确认的消息会保留。",
    "parameters": {
        "type": "object",
        "properties": {"message_ids": {"type": "array", "items": {"type": "string"}}},
        "required": ["message_ids"],
        "additionalProperties": False,
    },
}


def usage_event(event: Json) -> Json:
    values = event.get("data", {}).get("usage", {})
    normalized = {
        output: values[source]
        for source, output in (
            ("inputTokens", "input_tokens"),
            ("outputTokens", "output_tokens"),
            ("totalTokens", "total_tokens"),
        )
        if isinstance(values.get(source), (int, float))
        and not isinstance(values[source], bool)
        and values[source] >= 0
    }
    if "total_tokens" not in normalized and {"input_tokens", "output_tokens"} <= normalized.keys():
        normalized["total_tokens"] = normalized["input_tokens"] + normalized["output_tokens"]
    return {"type": "runtime_usage", "event_id": event["eventId"], "usage": normalized}


@dataclass
class Conversation:
    runtime: PackageAgentRuntime
    session: PackageSession
    server: asyncio.Server
    handler: ToolHandler
    sink: EventSink | None = None
    incoming: Callable[[], list[Json]] | None = None
    stop_reason: str | None = None
    calls: set[asyncio.Task[Any]] = field(default_factory=set)
    call_namespace: str = ""
    offered_messages: set[str] = field(default_factory=set)
    closing: bool = False


class ResearchRuntime:
    def __init__(self, config: ResearchConfig, *, settings: RuntimeSettings | None = None):
        self.config = config
        # Retain the existing connection validation and model-window policy;
        # ResponsesConfig is configuration only, never an execution backend.
        self.connection = config.runtime()
        self.backend = config.backend
        self.allow_web_access = config.metadata.get("allow_web_access", True)
        self.settings = settings or RuntimeSettings(
            {
                "default": {
                    "kind": "codex-cli" if self.backend == "codex" else "dsh",
                    "config": {"modelProtocol": "responses", "timeoutSeconds": 86400}
                    if self.backend == "codex"
                    else {"reasoningEffort": self.connection.reasoning_effort},
                }
            }
        )
        self.conversations: dict[Path, Conversation] = {}

    def for_interaction(self) -> ResearchRuntime:
        return ResearchRuntime(self.config, settings=self.settings)

    def configuration(self) -> Json:
        return {
            **self.config.model_settings(),
            "runtime": "areal-agent-runtime",
            "harness": self.settings.select("research-agent")["kind"],
            "allow_web_access": self.allow_web_access,
        }

    def has_session(self, session_dir: Path) -> bool:
        return (session_dir / "runtime/session-binding.json").exists()

    async def reconfigure_session(self, session_dir: Path, revision: str) -> None:
        # A native session binds immutable model/configuration. Intake model
        # changes receive a fresh session through ResearchApp, retaining dialogue.
        marker = session_dir / "runtime-revision.json"
        if (
            marker.exists()
            and json.loads(marker.read_text())["revision"] != revision
            and self.has_session(session_dir)
        ):
            raise ValueError("Model changes require a new Runtime session")
        write_json(marker, {"revision": revision})

    async def _open(
        self,
        directory: Path,
        workspace: Path,
        prompt: str,
        definitions: Sequence[Json],
        handler: ToolHandler,
        agent_id: str,
    ) -> Conversation:
        request_path = directory / "runtime/request.json"
        if not request_path.exists() and any(
            (directory / name).exists() for name in ("session.json", "codex-session.json")
        ):
            raise ValueError(
                "This session uses the old standalone harness; create a new research run"
            )
        saved = json.loads(request_path.read_text()) if request_path.exists() else {}
        previous_bridge = saved.get("research_tools", {})
        token = previous_bridge.get("token") or secrets.token_hex(32)
        catalog = {tool["name"]: tool for tool in (*definitions, ACKNOWLEDGE_MESSAGES)}
        connection: Conversation | None = None

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.current_task()
            if connection is not None and task is not None:
                connection.calls.add(task)
            try:
                async with asyncio.timeout(10):
                    request = json.loads(await reader.readline())
                if not hmac.compare_digest(str(request.get("token", "")), token):
                    raise ValueError("Invalid tool capability")
                if connection is None or connection.session.closed or connection.closing:
                    raise ValueError("Runtime session is closed")
                if connection.stop_reason and request["name"] != "acknowledge_messages":
                    raise ValueError("本回合已经结束，请停止调用工具并给出简短回复")
                name = request["name"]
                if name not in catalog:
                    raise ValueError("Tool is not available to this agent")
                validate(request["arguments"], catalog[name]["parameters"])
                # Native adapters may restart their call sequence each delivery.
                # Scope durable receipts to the session and delivery, retaining
                # stable identities when the same delivery resumes after a crash.
                identity = json.dumps([connection.call_namespace, request["call_id"]])
                call = ToolCall(
                    name, request["arguments"], hashlib.sha256(identity.encode()).hexdigest()
                )
                if connection.sink:
                    await connection.sink(
                        {"type": "tool_started", "name": name, "call_id": call.call_id}
                    )
                if name == "acknowledge_messages":
                    acknowledged = []
                    for identifier in request["arguments"]["message_ids"]:
                        if identifier in connection.offered_messages and connection.sink:
                            await connection.sink(
                                {"type": "message_injected", "message_id": identifier}
                            )
                            acknowledged.append(identifier)
                    result = ToolResult({"acknowledged": acknowledged})
                else:
                    result = await connection.handler(call)
                output: Any = result.output
                if result.stop:
                    connection.stop_reason = result.reason
                    output = {
                        "result": output,
                        "turn_finished": True,
                        "instruction": "当前回合已结束，请给出简短回复并等待下一条消息。",
                    }
                # A completion can race wait_for_job. Keep it in the inbox when
                # this tool ends the turn, so the scheduler can wake the agent.
                updates = connection.incoming() if connection.incoming and not result.stop else []
                if updates:
                    output = {"result": output, "incoming_messages": updates}
                    connection.offered_messages.update(update["message_id"] for update in updates)
                writer.write((json.dumps({"output": output}, ensure_ascii=False) + "\n").encode())
                await writer.drain()
                if connection.sink:
                    await connection.sink(
                        {"type": "tool_completed", "name": name, "call_id": call.call_id}
                    )
            except (ValueError, KeyError, ValidationError) as exc:
                writer.write((json.dumps({"error": str(exc)}) + "\n").encode())
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                if connection is not None and task is not None:
                    connection.calls.discard(task)

        server = await asyncio.start_server(
            serve, "127.0.0.1", previous_bridge.get("port", 0), limit=2**20
        )
        content = FileContentStore(directory / "content")
        runtime = PackageAgentRuntime(
            directory,
            content,
            FileTrajectoryStore(directory / "trajectories", content),
            settings=self.settings,
            role="research-agent",
            base_url=self.connection.endpoint.removesuffix("/responses"),
            api_key=self.config.api_key(),
            timeout=86400,
            max_tokens=self.config.max_output_tokens,
            dsh_root=Path(
                os.environ.get("RSI_RUNTIME_CACHE", str(Path.home() / ".cache/areal-rsi"))
            ),
        )
        prompt_path = directory / "system-prompt.json"
        if prompt_path.exists():
            prompt = json.loads(prompt_path.read_text())["prompt"]
        else:
            write_json(prompt_path, {"prompt": prompt}, immutable=True)
        model = ModelVersion(
            "research-model",
            self.connection.model,
            await content.put_text(self.connection.model),
            now(),
        )
        spec = AgentSpec(
            agent_id,
            "areal-agent-runtime@v1",
            model,
            prompt_templates=(await content.put_text(prompt),),
            tools=("bash@v1", "editor@v1"),
        )
        bridge = {
            "port": server.sockets[0].getsockname()[1],
            "token": token,
            "tools": [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "inputSchema": tool["parameters"],
                }
                for tool in catalog.values()
            ],
        }
        if (
            self.backend == "codex"
            and self.config.metadata.get("execution_interface") == "cli"
            and agent_id not in {"rsi", "interaction"}
        ):
            bridge["shell_commands"] = self.config.metadata["shell_commands"]
            # The CLI retains the same scoped capabilities. Hide the duplicate
            # submission/wait interface from the model's MCP catalog.
            bridge["shell_only_tools"] = [
                "run_job", "rerun_job", "wait_for_job", "verify_candidate", "collect_job_output",
            ]
        from areal_rsi.coordination.collaboration import build_plan

        plan = build_plan(self.config.mode, self.config.collaboration_modules)
        allow_web = self.allow_web_access and not (plan.supervisor_only and agent_id == "rsi")
        codex_config = {
            "model_reasoning_effort": self.connection.reasoning_effort,
            "web_search": "live" if allow_web else "disabled",
            "features.multi_agent": False,
            "agents.enabled": False,
            "features.goals": False,
            "features.standalone_web_search": allow_web,
            "model_providers.rsi.supports_standalone_web_search": allow_web,
        }
        if self.config.context_window_tokens is not None:
            codex_config["model_context_window"] = self.config.context_window_tokens
        try:
            session = await runtime.open(
                spec,
                workspace_root=str(workspace),
                session_id="runtime",
                research_tools=bridge,
                context_window_tokens=(
                    self.config.context_window_tokens
                    if self.backend == "codex"
                    else self.connection.effective_context_window_tokens
                ),
                codex_config=codex_config if self.backend == "codex" else None,
            )
        except BaseException:
            server.close()
            await server.wait_closed()
            await runtime.close()
            raise
        connection = Conversation(runtime, session, server, handler)
        self.conversations[directory] = connection
        return connection

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
        if workspace is None:
            raise ValueError("Runtime requires an explicit, isolated workspace")
        receipt = (
            session_dir
            / "deliveries"
            / (hashlib.sha256(f"{message_id}:{retry_id}".encode()).hexdigest() + ".json")
        )
        if message_id is not None and receipt.exists():
            completed: TurnResult = decode(TurnResult, json.loads(receipt.read_text()))
            return completed
        connection = self.conversations.get(session_dir)
        if connection is not None and connection.session.closed:
            await self._close(connection)
            connection = None
        recovering = connection is None and self.has_session(session_dir)
        fresh_session = connection is None and not recovering
        if connection is None:
            if event_sink is not None:
                from areal_rsi.modules.runtime_storage import read_journal

                history, _ = read_journal(session_dir / "runtime/data/state", boundary=session_dir)
                for event in history:
                    if event.type == "usage.updated":
                        await event_sink(usage_event(event.model_dump(mode="json", by_alias=True)))
            connection = await self._open(
                session_dir,
                workspace,
                system_prompt,
                tools,
                handler,
                (recovery_state or {}).get("agent_id", "research-agent"),
            )
        connection.call_namespace = json.dumps(
            [
                str(session_dir.resolve()),
                message_id if message_id is not None else secrets.token_hex(16),
            ]
        )
        connection.handler, connection.sink = handler, event_sink
        connection.incoming, connection.stop_reason = incoming_messages, None
        usage: Json = {}

        async def on_event(event: Json) -> None:
            kind, data = event["type"], event.get("data", {})
            normalized = usage_event(event) if kind == "usage.updated" else None
            if normalized is not None:
                for name, value in normalized["usage"].items():
                    usage[name] = usage.get(name, 0) + value
            if event_sink is None:
                return
            if kind == "runtime.status" and "apiRetry" in data:
                await event_sink({"type": "api_retry", **data["apiRetry"]})
            if kind == "runtime.status" and "apiAvailable" in data:
                await event_sink(
                    {
                        "type": "api_availability",
                        "available": data["apiAvailable"],
                        "timestamp": datetime.fromisoformat(
                            event["time"].replace("Z", "+00:00")
                        ).timestamp(),
                    }
                )
            if normalized is not None:
                await event_sink(normalized)
            elif kind == "message.completed" and not event.get("parentAgentId"):
                text = "\n".join(
                    b["text"] for b in data.get("content", []) if b.get("type") == "text"
                )
                if data.get("role", "assistant") == "assistant":
                    await event_sink({"type": "model_output", "text": text})
            elif kind in {"tool.started", "tool.completed", "tool.failed"}:
                await event_sink(
                    {
                        "type": "tool_started" if kind == "tool.started" else "tool_completed",
                        "name": data.get("name", "native"),
                        "call_id": data.get("callId"),
                    }
                )

        if event_sink:
            await event_sink({"type": "runtime_turn_started", "message_id": message_id})
        message = user_message or "继续上次尚未完成的任务；先检查已有操作与结果，避免重复提交。"
        if recovery_state:
            # Existing native history carries the task. A fresh session after a
            # configuration change also needs its archived dialogue and current brief.
            context = (
                recovery_state
                if recovering or (fresh_session and "backend_handoff" in recovery_state)
                else {"budget": recovery_state["budget"]}
                if "budget" in recovery_state
                else {}
            )
            if context:
                message += "\n\n当前任务状态：\n" + json.dumps(context, ensure_ascii=False)
        try:
            text = await connection.session.send(message, event_sink=on_event)
        finally:
            if connection.session.closed:
                # Cancellation may commit final usage while the parent stops
                # reading stdout. Reconcile those durable facts too; the engine
                # deduplicates by the canonical Runtime event ID.
                for event in connection.session.events:
                    if event["type"] == "usage.updated":
                        await on_event(event)
        result = TurnResult(
            session_dir.name,
            connection.session.run_ids[-1],
            text,
            connection.stop_reason or "completed",
            usage,
        )
        if message_id is not None:
            write_json(receipt, result, immutable=True)
        return result

    async def _close(self, connection: Conversation) -> None:
        connection.closing = True
        connection.server.close()
        calls = tuple(connection.calls)
        for task in calls:
            task.cancel()
        await asyncio.gather(*calls, return_exceptions=True)
        await connection.server.wait_closed()
        await connection.runtime.close()

    async def close(self) -> None:
        await asyncio.gather(*(self._close(c) for c in self.conversations.values()))
        self.conversations.clear()

    async def close_session(self, session_dir: Path) -> None:
        """Caller first cancels the turn; then retire its bridge and native worker."""
        if connection := self.conversations.pop(session_dir, None):
            await self._close(connection)
