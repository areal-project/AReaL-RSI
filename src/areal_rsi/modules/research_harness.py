"""Public ARealAgentRuntime adapter for the two durable conversation engines.

The engines own model history and native execution. Application owns admission,
tool authorization and committed tool results; no engine calls a product tool
directly. The product observer is also the synchronous model-budget gate.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from areal_agent_runtime.contracts import EventType, TextBlock, ToolSpec
from areal_agent_runtime.contracts.data import DataCoverage, MessageCoverage, UsageCoverage
from areal_agent_runtime.harness_sdk import (
    CancelReason,
    ContentBlock,
    ExecutionChannel,
    ExecutionRequest,
    HarnessEventBuilder,
    HealthStatus,
    InteractionResponse,
    RuntimeCapabilities,
    RuntimeDescriptor,
    RuntimeEvent,
    RuntimeSessionRef,
    RuntimeSessionSpec,
)
from areal_agent_runtime.harness_sdk import (
    ToolCall as RuntimeToolCall,
)
from areal_agent_runtime.harness_sdk import (
    ToolResult as RuntimeToolResult,
)

from areal_rsi.base.storage import new_id
from areal_rsi.modules.agent_runtime import (
    AgentRuntime,
    EventSink,
    ToolCall,
    ToolHandler,
    ToolResult,
    TurnResult,
)

Json = dict[str, Any]


@dataclass
class ConversationInput:
    directory: Path
    message_id: str
    user_message: str | None
    handler: ToolHandler
    observer: EventSink | None
    recovery_state: Json
    retry_id: str | None
    advertised_tools: Sequence[Json] | None
    tool_definitions: Sequence[Json]
    environment: Mapping[str, str]
    calls: dict[str, ToolCall] = field(default_factory=dict)
    refresh_messages: Callable[[], Awaitable[None]] | None = None


class ResearchToolProvider:
    """Application's authorized callback catalog, scoped to one RSI actor."""

    def __init__(self, tools: Sequence[Json], invocation: ConversationInput):
        self.tools = tuple(
            ToolSpec(
                name=tool["name"],
                description=tool.get("description", ""),
                input_schema=tool["parameters"],
            )
            for tool in tools
        )
        self.invocation = invocation

    async def list_tools(self) -> tuple[ToolSpec, ...]:
        return self.tools

    async def health(self) -> HealthStatus:
        return HealthStatus(status="healthy")

    async def invoke(self, call: RuntimeToolCall) -> RuntimeToolResult:
        original = self.invocation.calls[call.call_id]
        if original.name != call.name or original.arguments != call.arguments:
            raise ValueError("Application tool call does not match the pending native call")
        result = await self.invocation.handler(original)
        # Stop is a product decision. Application commits the complete receipt
        # before the engine sees it and ends its native turn.
        return RuntimeToolResult(call_id=call.call_id, value=asdict(result))


class ConversationHarness:
    """A HarnessAdapterPort over an existing backend, with one active execution."""

    def __init__(self, engine: AgentRuntime, invocation: ConversationInput):
        self.engine, self.invocation = engine, invocation
        self.session: RuntimeSessionSpec | None = None
        self.channel: ConversationChannel | None = None

    async def describe(self) -> RuntimeDescriptor:
        return RuntimeDescriptor(
            name=self.engine.backend,
            adapter_version="1.0.0",
            runtime_version=str(self.engine.configuration().get("sdk_version", "1")),
            capabilities=RuntimeCapabilities(
                tool_calling=True,
                application_tool_bridge=True,
                cancel=True,
                steer=True,
                resume=True,
                data_coverage=DataCoverage(
                    messages=MessageCoverage.FINAL,
                    usage=UsageCoverage.NORMALIZED,
                ),
            ),
        )

    async def open_session(self, spec: RuntimeSessionSpec) -> RuntimeSessionRef:
        if self.session is not None and (
            self.session.session_id != spec.session_id
            or self.session.workspace != spec.workspace
            or self.session.agent_environment.digest != spec.agent_environment.digest
            or self.session.model != spec.model
            or self.session.provider != spec.provider
        ):
            raise ValueError("Cannot change an open Harness session binding")
        self.session = spec
        return RuntimeSessionRef(session_id=spec.session_id, runtime_session_id=spec.session_id)

    async def execute(self, request: ExecutionRequest) -> ExecutionChannel:
        if self.session is None or request.session_id != self.session.session_id:
            raise ValueError("Harness session is not open")
        if request.agent_environment.digest != self.session.agent_environment.digest:
            raise ValueError("Harness execution environment differs from its session")
        if request.metadata.get("message_id") != self.invocation.message_id:
            raise ValueError("Cannot execute a different research message during recovery")
        if self.channel is not None and not self.channel.task.done():
            raise ValueError("Harness session already has an active execution")
        self.channel = ConversationChannel(self.engine, self.invocation, request)
        return self.channel

    async def close_session(self, session: RuntimeSessionRef) -> None:
        if self.session is not None and self.session.session_id != session.session_id:
            raise ValueError("Unknown Harness session")
        if self.channel:
            await self.channel.close()
        self.session = None

    async def health(self) -> HealthStatus:
        return HealthStatus(status="healthy")


class ConversationChannel:
    def __init__(
        self, engine: AgentRuntime, invocation: ConversationInput, request: ExecutionRequest
    ):
        self.engine, self.invocation, self.request = engine, invocation, request
        self.builder = HarnessEventBuilder(
            producer="rsi." + engine.backend,
            runtime_instance_id=new_id("harness"),
            execution_id=request.execution_id,
            agent_id=request.agent.agent_id,
            parent_agent_id=request.agent.parent_agent_id,
        )
        self.queue: asyncio.Queue[RuntimeEvent | None] = asyncio.Queue()
        self.pending: dict[str, asyncio.Future[RuntimeToolResult]] = {}
        self.inbox: dict[str, Json] = {}
        for block in request.input[1:]:
            if not isinstance(block, TextBlock):
                raise ValueError("Research updates must be text envelopes")
            message = json.loads(block.text)
            self.inbox[message["message_id"]] = message
        self.error: BaseException | None = None
        self.result: TurnResult | None = None
        self.task = asyncio.create_task(self.run())

    async def events(self) -> AsyncIterator[RuntimeEvent]:
        while (event := await self.queue.get()) is not None:
            yield event

    async def observe(self, event: Json) -> None:
        if event["type"] == "message_injected":
            self.inbox.pop(event["message_id"], None)
        if self.invocation.observer:
            # Budget refusal must happen before forwarding a model request,
            # even while Application is awaiting another tool's completion.
            await self.invocation.observer(event)
        # Model bytes remain in the engine's private request archive. Do not
        # duplicate streaming responses or private reasoning into the event log.
        if event["type"] not in {"model_stream", "tool_started", "tool_completed"}:
            self.queue.put_nowait(self.builder.raw(event["type"], event))

    async def call(self, call: ToolCall) -> ToolResult:
        if call.call_id in self.pending:
            raise ValueError("Duplicate pending tool call")
        self.invocation.calls[call.call_id] = call
        future: asyncio.Future[RuntimeToolResult] = asyncio.get_running_loop().create_future()
        self.pending[call.call_id] = future
        self.queue.put_nowait(
            self.builder.event(
                EventType.TOOL_REQUESTED,
                {
                    "callId": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "execution": "application",
                },
            )
        )
        try:
            result = await future
            if self.invocation.refresh_messages:
                await self.invocation.refresh_messages()
            if result.error:
                return ToolResult({"error": result.error.message})
            if not isinstance(result.value, dict):
                raise ValueError("Application returned an invalid research tool receipt")
            return ToolResult(**cast(Json, result.value))
        finally:
            self.pending.pop(call.call_id, None)
            self.invocation.calls.pop(call.call_id, None)

    async def send_tool_result(self, result: RuntimeToolResult) -> None:
        future = self.pending.get(result.call_id)
        if future is None or future.done():
            raise ValueError("Tool result does not belong to a pending call")
        future.set_result(result)

    async def steer(self, input: tuple[ContentBlock, ...]) -> None:
        if self.task.done():
            raise ValueError("Harness execution has ended")
        for block in input:
            if not isinstance(block, TextBlock):
                raise ValueError("Research updates must be text envelopes")
            message = json.loads(block.text)
            if not isinstance(message, dict) or not isinstance(message.get("message_id"), str):
                raise ValueError("Research update requires a message identity")
            self.inbox[message["message_id"]] = message

    async def respond(self, response: InteractionResponse) -> None:
        raise NotImplementedError("Research interaction uses the authorized ask_user tool")

    async def cancel(self, reason: CancelReason) -> None:
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def close(self) -> None:
        if not self.task.done():
            await self.cancel(CancelReason())

    async def run(self) -> None:
        self.queue.put_nowait(self.builder.event(EventType.EXECUTION_STARTED, {}))
        invocation = self.invocation
        options = {tool["name"]: tool for tool in invocation.tool_definitions}
        try:
            self.result = await self.engine.turn(
                invocation.directory,
                system_prompt="\n\n".join(
                    block.text for block in self.request.system if isinstance(block, TextBlock)
                ),
                user_message=invocation.user_message,
                tools=[
                    {
                        **options.get(t.name, {}),
                        "type": "function",
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    }
                    for t in self.request.tools
                ],
                handler=self.call,
                message_id=invocation.message_id,
                event_sink=self.observe,
                recovery_state=invocation.recovery_state,
                retry_id=invocation.retry_id,
                incoming_messages=lambda: list(self.inbox.values()),
                advertised_tools=invocation.advertised_tools,
                workspace=Path(self.request.workspace.mount_hint or invocation.directory),
                environment=invocation.environment,
            )
            if self.result.text:
                self.queue.put_nowait(self.builder.message((TextBlock(text=self.result.text),)))
            if self.result.usage:
                self.queue.put_nowait(self.builder.usage(self.result.usage))
            self.queue.put_nowait(self.builder.raw("rsi.turn_result", asdict(self.result)))
            self.queue.put_nowait(
                self.builder.event(
                    EventType.EXECUTION_COMPLETED, {"reason": self.result.stop_reason}
                )
            )
        except BaseException as exc:
            self.error = exc
            self.queue.put_nowait(
                self.builder.event(EventType.EXECUTION_FAILED, {"reason": type(exc).__name__})
            )
        finally:
            self.queue.put_nowait(None)
