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

from areal_rsi.api.interfaces import EventSink, ToolCall, ToolHandler, TurnResult
from areal_rsi.api.records import AgentSpec, ModelVersion
from areal_rsi.infra.config import local_codex_auth
from areal_rsi.infra.coordination.config import ResearchConfig, config_from_dict
from areal_rsi.infra.runtime.runtime_package import (
    PackageAgentRuntime,
    PackageSession,
    RuntimeSettings,
)
from areal_rsi.infra.storage.files import (
    FileContentStore,
    FileTrajectoryStore,
    decode,
    now,
    write_json,
)

Json = dict[str, Any]


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
    call_namespace: str | None = None


class ResearchRuntime:
    def __init__(self, config: ResearchConfig, *, settings: RuntimeSettings | None = None):
        config = config_from_dict(config.public())
        config.pin_model_settings()
        self.config = config
        self.connection = config.runtime()
        self.backend = config.backend
        self.allow_web_access = config.metadata.get("allow_web_access", True)
        self.settings = settings or config.runtime_settings()
        self.conversations: dict[Path, Conversation] = {}

    def for_interaction(self) -> ResearchRuntime:
        return ResearchRuntime(self.config, settings=self.settings)

    def configuration(self) -> Json:
        return self.configuration_for("research-agent")

    @staticmethod
    def role_for(agent_id: str) -> str:
        return {"interaction": "interaction-agent", "rsi": "rsi-agent"}.get(
            agent_id, "research-agent"
        )

    def configuration_for(self, agent_id: str) -> Json:
        return {
            **self.config.model_settings(),
            "runtime": "areal-agent-runtime",
            "evolution_target": self.config.evolution_target,
            "harness": self.settings.select(self.role_for(agent_id))["kind"],
            "allow_web_access": self.allow_web_access,
        }

    def has_session(self, session_dir: Path) -> bool:
        return (session_dir / "runtime/session-binding.json").exists()

    async def reconfigure_session(self, session_dir: Path, revision: str) -> None:
        # A native session binds immutable model/configuration. Intake model
        # changes receive a fresh session through ResearchSystem, retaining dialogue.
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
        advertised_tools: Sequence[Json] | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> Conversation:
        role = self.role_for(agent_id)
        kind = self.settings.select(role)["kind"]
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
        catalog = {tool["name"]: tool for tool in definitions}
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
                if connection is None or connection.session.closed:
                    raise ValueError("Runtime session is closed")
                if connection.stop_reason:
                    raise ValueError("本回合已经结束，请停止调用工具并给出简短回复")
                name = request["name"]
                if name not in catalog:
                    raise ValueError("Tool is not available to this agent")
                validate(request["arguments"], catalog[name]["parameters"])
                if connection.call_namespace is None:
                    raise ValueError("Runtime has no active delivery")
                # Native adapters may restart their call sequence each delivery.
                # Scope durable receipts to the session and delivery, retaining
                # existing identities across crashes and explicit retries. Native
                # Run IDs change on recovery and would replay committed effects.
                identity = json.dumps([connection.call_namespace, request["call_id"]])
                call = ToolCall(
                    name, request["arguments"], hashlib.sha256(identity.encode()).hexdigest()
                )
                if connection.sink:
                    await connection.sink(
                        {"type": "tool_started", "name": name, "call_id": call.call_id}
                    )
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
                writer.write((json.dumps({"output": output}, ensure_ascii=False) + "\n").encode())
                await writer.drain()
                if connection.sink:
                    for update in updates:
                        await connection.sink(
                            {"type": "message_injected", "message_id": update["message_id"]}
                        )
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
        runtime: PackageAgentRuntime | None = None
        try:
            content = FileContentStore(directory / "content")
            runtime = PackageAgentRuntime(
                directory,
                content,
                FileTrajectoryStore(directory / "trajectories", content),
                settings=self.settings,
                role=role,
                base_url=self.connection.runtime_base_url,
                api_key=self.config.api_key(),
                codex_auth_file=(
                    local_codex_auth() if self.config.auth_mode == "codex_login" else None
                ),
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
                    for tool in (definitions if advertised_tools is None else advertised_tools)
                ],
            }
            from areal_rsi.infra.coordination.policies.collaboration import build_plan

            plan = build_plan(self.config.mode, self.config.collaboration_modules)
            allow_web = self.allow_web_access and not (plan.supervisor_only and agent_id == "rsi")
            codex_config = {
                "model_reasoning_effort": self.connection.reasoning_effort,
                "web_search": "live" if allow_web else "disabled",
                "features.multi_agent": False,
                "agents.enabled": False,
                "features.goals": False,
                "features.standalone_web_search": allow_web,
            }
            if self.config.auth_mode != "codex_login":
                codex_config["model_providers.rsi.supports_standalone_web_search"] = allow_web
            if self.config.context_window_tokens is not None:
                codex_config["model_context_window"] = self.config.context_window_tokens
            session = await runtime.open(
                spec,
                workspace_root=str(workspace),
                session_id="runtime",
                research_tools=bridge,
                reasoning_effort=self.connection.reasoning_effort,
                context_window_tokens=(
                    self.config.context_window_tokens
                    if kind == "codex-cli"
                    else self.connection.effective_context_window_tokens
                ),
                codex_config=codex_config if kind == "codex-cli" else None,
                environment=environment,
            )
        except BaseException:
            server.close()
            await server.wait_closed()
            if runtime is not None:
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
                from areal_rsi.infra.runtime.runtime_storage import read_journal

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
                advertised_tools=advertised_tools,
                environment=environment,
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
                        "call_id": f"{event.get('runId', '')}:"
                        f"{data.get('callId', event.get('eventId'))}",

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
                else {
                    key: recovery_state[key]
                    for key in ("budget", "goal", "brief")
                    if key in recovery_state and (key != "brief" or recovery_state.get("goal"))
                }
                if "budget" in recovery_state or "goal" in recovery_state
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
        connection.server.close()
        await connection.server.wait_closed()
        for task in tuple(connection.calls):
            task.cancel()
        await asyncio.gather(*connection.calls, return_exceptions=True)
        await connection.runtime.close()

    async def close(self) -> None:
        await asyncio.gather(*(self._close(c) for c in self.conversations.values()))
        self.conversations.clear()
