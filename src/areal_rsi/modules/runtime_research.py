"""Continuous research sessions through the public ARealAgentRuntime API.

The fixed workflow uses runtime_package.PackageAgentRuntime. This entrypoint
uses the same framework for streaming research, with product-specific adapters
that preserve the existing standard/Codex conversation and recovery formats.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from areal_agent_runtime import ARealAgentRuntime
from areal_agent_runtime.adapters.storage import FileSystemAssetStore, SQLiteStateStore
from areal_agent_runtime.contracts import (
    CancelRunCommand,
    RunSnapshot,
    RunStatus,
    RuntimeSelection,
    StartRunCommand,
    SteerRunCommand,
    TextBlock,
    WorkspaceRef,
)
from areal_agent_runtime.product_sdk import WorkflowProfile

from areal_rsi.base.storage import new_id, write_json
from areal_rsi.modules.agent_runtime import (
    AgentRuntime,
    AgentRuntimeError,
    EventSink,
    RuntimeConfig,
    ToolHandler,
    TurnResult,
)
from areal_rsi.modules.research_harness import (
    ConversationHarness,
    ConversationInput,
    ResearchToolProvider,
)
from areal_rsi.modules.responses import ResponsesRuntime

Json = dict[str, Any]
TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


class ResearchRuntime:
    """The workbench's single runtime entrypoint for every supported backend."""

    def __init__(self, engine: AgentRuntime):
        self.engine = engine
        self.backend = engine.backend
        self._locks: dict[Path, asyncio.Lock] = {}
        self._applications: set[ARealAgentRuntime] = set()

    @classmethod
    def create(
        cls, backend: str, config: RuntimeConfig, *, allow_web_access: bool = True
    ) -> ResearchRuntime:
        if backend == "standard":
            return cls(ResponsesRuntime(config))
        if backend == "codex":
            from areal_rsi.modules.codex import CodexRuntime

            return cls(CodexRuntime(config, allow_web_access=allow_web_access))
        raise ValueError(f"Unknown research backend: {backend}")

    def configure_web(self, enabled: bool) -> None:
        if self.backend == "codex":
            from areal_rsi.modules.codex import CodexRuntime

            assert isinstance(self.engine, CodexRuntime)
            self.engine.allow_web_access = enabled

    def for_interaction(self) -> ResearchRuntime:
        return ResearchRuntime(self.engine.for_interaction())

    def has_session(self, session_dir: Path) -> bool:
        return self.engine.has_session(session_dir)

    def configuration(self) -> Json:
        return {**self.engine.configuration(), "framework": "areal-agent-runtime"}

    async def reconfigure_session(self, session_dir: Path, revision: str) -> None:
        await self.engine.reconfigure_session(session_dir, revision)

    async def close(self) -> None:
        await asyncio.gather(*(app.shutdown() for app in tuple(self._applications)))
        await self.engine.close()

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
            invocation = ConversationInput(
                directory=directory,
                message_id=message_id or new_id("turn"),
                user_message=user_message,
                handler=handler,
                observer=event_sink,
                recovery_state=recovery_state if recovery_state is not None else {},
                retry_id=retry_id,
                advertised_tools=advertised_tools,
                tool_definitions=tools,
                environment=environment or {},
            )
            return await self._run(
                invocation, system_prompt, tools, workspace or directory, incoming_messages
            )

    async def _run(
        self,
        invocation: ConversationInput,
        prompt: str,
        tools: Sequence[Json],
        workspace: Path,
        incoming: Callable[[], list[Json]] | None,
    ) -> TurnResult:
        # Framework environments are immutable. An explicit pre-start model or
        # tool configuration change gets a separate binding; native history is
        # preserved by reconfigure_session and the backend's resume mechanism.
        identity = json.dumps(
            [self.configuration(), prompt, list(tools), str(workspace.resolve())], sort_keys=True
        )
        revision = hashlib.sha256(identity.encode()).hexdigest()
        root = invocation.directory / "areal-runtime" / revision
        binding_path = root / "active.json"
        binding = json.loads(binding_path.read_text()) if binding_path.exists() else {}
        state = SQLiteStateStore(root / "state")
        harness = ConversationHarness(self.engine, invocation)
        provider = ResearchToolProvider(tools, invocation)
        app = ARealAgentRuntime(
            runtimes={self.backend: harness},
            profiles={
                "rsi.actor": WorkflowProfile(
                    profile_id="rsi.actor",
                    role="research",
                    system=(TextBlock(text=prompt),),
                    tool_names=tuple(t.name for t in provider.tools),
                    required_features=("application_tool_bridge", "cancel", "resume", "steer"),
                )
            },
            tool_providers={"rsi": provider},
            state=state,
            asset_store=FileSystemAssetStore(root / "assets"),
            max_active_runs=1,
        )
        self._applications.add(app)
        queued: set[str] = set()
        delivery_lock = asyncio.Lock()

        async def refresh_messages() -> None:
            async with delivery_lock:
                channel = harness.channel
                if not incoming or channel is None or channel.task.done():
                    return
                for message in incoming():
                    key = message["message_id"]
                    if key in queued:
                        continue
                    try:
                        await app.steer_run(
                            SteerRunCommand(
                                run_id=channel.request.run_id,
                                execution_id=channel.request.execution_id,
                                input=(TextBlock(text=json.dumps(message, ensure_ascii=False)),),
                            )
                        )
                    except ValueError:
                        if not channel.task.done():
                            raise
                        return
                    queued.add(key)

        invocation.refresh_messages = refresh_messages
        pump: asyncio.Task[None] | None = None
        run_id: str | None = None
        try:
            previous = await app.get_run(binding["run_id"]) if binding else None
            if (
                previous
                and previous.status == RunStatus.COMPLETED
                and binding["message_id"] == invocation.message_id
                and binding.get("retry_id") == invocation.retry_id
            ):
                return await self._result(state, previous.run_id)
            if previous and previous.status not in TERMINAL:
                if binding["message_id"] != invocation.message_id:
                    raise AgentRuntimeError("An unfinished Runtime run belongs to another message")
                run_id = previous.run_id
            await app.initialize()
            if run_id is None:
                inputs: tuple[TextBlock, ...] = (TextBlock(text=invocation.user_message or ""),)
                if incoming:
                    inputs += tuple(
                        TextBlock(text=json.dumps(message, ensure_ascii=False))
                        for message in incoming()
                    )
                ref = await app.start_run(
                    StartRunCommand(
                        profile="rsi.actor",
                        input=inputs,
                        runtime=RuntimeSelection(
                            selector=self.backend, model=self.configuration().get("model")
                        ),
                        workspace=WorkspaceRef(
                            workspace_id=invocation.directory.name,
                            mount_hint=str(workspace.resolve()),
                        ),
                        session_id=binding.get("session_id"),
                        resume_session=bool(binding),
                        metadata={
                            "message_id": invocation.message_id,
                            "retry_id": invocation.retry_id,
                            "resume_turn": invocation.user_message is None,
                        },
                    )
                )
                run_id = ref.run_id
                write_json(
                    binding_path,
                    {
                        "run_id": run_id,
                        "session_id": ref.session_id,
                        "message_id": invocation.message_id,
                        "retry_id": invocation.retry_id,
                    },
                )
            if event_sink := invocation.observer:
                await event_sink({"type": "runtime_run", "run_id": run_id, "revision": revision})
            if incoming:
                pump = asyncio.create_task(self._updates(refresh_messages))
            snapshot = await self._wait(app, run_id, pump)
            if snapshot.status not in TERMINAL:
                raise AgentRuntimeError("Runtime execution interrupted; resume the same message")
            if snapshot.status != RunStatus.COMPLETED:
                if harness.channel and harness.channel.error:
                    raise harness.channel.error
                raise AgentRuntimeError(
                    f"ARealAgentRuntime run {snapshot.status}: {snapshot.error}"
                )
            # Read the committed result, including after process recovery that
            # finalized a run without invoking the native engine again.
            return await self._result(state, run_id)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if run_id and task and task.cancelling():
                await app.cancel_run(CancelRunCommand(run_id=run_id, reason="Research turn paused"))
            raise
        finally:
            if pump:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
            try:
                await app.shutdown()
            finally:
                self._applications.discard(app)

    @staticmethod
    async def _wait(
        app: ARealAgentRuntime, run_id: str, pump: asyncio.Task[None] | None
    ) -> RunSnapshot:
        if pump is None:
            return await app.wait(run_id)
        waiting = asyncio.create_task(app.wait(run_id))
        try:
            done, _ = await asyncio.wait({waiting, pump}, return_when=asyncio.FIRST_COMPLETED)
            if pump in done:
                pump.result()
            return await waiting
        finally:
            if not waiting.done():
                waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)

    @staticmethod
    async def _result(state: SQLiteStateStore, run_id: str) -> TurnResult:
        for event in reversed(await state.list_events(run_id)):
            if event.data.get("vendorEventType") == "rsi.turn_result" and isinstance(
                event.data.get("payload"), dict
            ):
                return TurnResult(**cast(Json, event.data["payload"]))
        raise AgentRuntimeError("Completed Runtime run has no committed conversation result")

    @staticmethod
    async def _updates(
        refresh: Callable[[], Awaitable[None]],
    ) -> None:
        while True:
            await refresh()
            await asyncio.sleep(0.05)
