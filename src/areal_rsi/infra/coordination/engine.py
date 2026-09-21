"""Event-driven model scheduling over the durable Coordinator state machine.

There is no model call on an idle tick. Tools own domain actions; this module owns
run execution, concurrency, API budgets, cancellation and resuming deliveries.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from areal_rsi.api.interfaces import (
    AgentRuntimeError,
    ResearchAgentRuntime,
    RoleConfiguredRuntime,
    ToolHandler,
)
from areal_rsi.api.records import AgentState
from areal_rsi.api.research import AgentActivity, ResearchMessage, RuntimeUsage
from areal_rsi.infra.agents.communication import INTEGRITY_PROMPT
from areal_rsi.infra.agents.interaction.prompt import PROMPT as INTERACTION_PROMPT
from areal_rsi.infra.agents.research.passk_prompt import PROMPT as RESEARCH_PROMPT
from areal_rsi.infra.agents.rsi.passk_prompt import PROMPT as RSI_PROMPT
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.coordination.effective_time import EffectiveTime
from areal_rsi.infra.storage.files import encode, now, write_json

Json = dict[str, Any]
TickHook = Callable[[], Awaitable[bool | None]]


@dataclass(frozen=True)
class ToolSet:
    definitions: list[Json]
    call: ToolHandler
    instructions: str = ""
    advertised: list[Json] | None = None
    workspace: Path | None = None
    environment: dict[str, str] | None = None


class ResearchEngine:
    def __init__(
        self,
        coordinator: Coordinator,
        runtime: ResearchAgentRuntime,
        tools_factory: Callable[[str], ToolSet],
        *,
        research_prompt: str = RESEARCH_PROMPT,
        rsi_prompt: str = RSI_PROMPT,
        max_concurrent_turns: int | None = None,
        tick_hook: TickHook | None = None,
        poll_interval: float = 0.1,
        clock: Callable[[], float] = time.time,
    ):
        self.coordinator = coordinator
        self.runtime = runtime
        self.interaction_runtime = runtime.for_interaction()
        self.tools_factory = tools_factory
        self.research_prompt = research_prompt
        self.rsi_prompt = rsi_prompt
        self.max_concurrent_turns = (
            coordinator.store.brief().limits.max_agents + 2
            if max_concurrent_turns is None
            else max_concurrent_turns
        )
        if self.max_concurrent_turns < 1 or poll_interval <= 0:
            raise ValueError("Concurrency and polling interval must be positive")
        self.tick_hook, self.poll_interval, self.clock = tick_hook, poll_interval, clock
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._owns_run = False
        self._tick_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._jobs_pending = False
        self.effective: EffectiveTime | None = None

    @property
    def active_agents(self) -> tuple[str, ...]:
        return tuple(agent_id for agent_id, task in self._tasks.items() if not task.done())

    def wake(self) -> None:
        """Notify the serving event loop after a user/control/job message is enqueued."""
        self._wake.set()

    def _acquire(self) -> None:
        if self._owns_run:
            self._initialize_effective_time()
            return
        try:
            self.coordinator.acquire()
        except BlockingIOError:
            raise AgentRuntimeError("Another live worker owns this run's execution lock") from None
        self._owns_run = True
        try:
            self.effective = None
            self._initialize_effective_time()
            self.coordinator.store.event("worker_started", pid=os.getpid())
        except BaseException:
            self._release_ownership()
            raise

    def _release_ownership(self) -> None:
        self.effective = None
        if self._owns_run:
            self._owns_run = False
            self.coordinator.release()

    def _initialize_effective_time(self) -> None:
        # Intake can change the budget while this worker already owns the lock.
        # Bind the clock to the frozen research brief, before dispatching researchers.
        c = self.coordinator
        if self.effective is not None or c.state.status not in {"running", "paused"}:
            return
        if c.store.brief().limits.max_effective_seconds is None:
            return
        self.effective = EffectiveTime(c.state)
        if c.state.status == "paused":
            self.effective.close_worker(self.effective.data.heartbeat_at)
        else:
            self.effective.open_worker(self.clock())
        c.save()

    def _wall_reason(self) -> str | None:
        started = self.coordinator.state.started_at
        if started is None:
            return None
        elapsed = self.clock() - datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()
        max_seconds = self.coordinator.store.brief().limits.max_wall_seconds
        if max_seconds is not None and elapsed >= max_seconds:
            return "Run wall-time budget exhausted"
        return None

    def _pause_for_budget(self, reason: str) -> None:
        if self.coordinator.state.status not in {"running", "planned"}:
            return
        self.coordinator.state.budget_stop_reason = reason
        self.coordinator.save()
        if self.coordinator.state.status == "running":
            self.coordinator.pause()
        self.coordinator.store.event("budget_exhausted", reason=reason)
        self.wake()

    async def _event(self, agent_id: str, event: Json) -> None:
        kind = event["type"]
        if kind == "api_availability":
            if self.effective is not None:
                self.effective.availability(agent_id, event["available"], event["timestamp"])
                self.coordinator.save()
            return
        if kind == "runtime_usage":
            settled = self.coordinator.state.runtime_usage
            if event["event_id"] not in settled:
                settled[event["event_id"]] = RuntimeUsage(agent_id, event["usage"])
                self.coordinator.state.total_tokens += event["usage"].get("total_tokens", 0)
                self.coordinator.save()
                limit = self.coordinator.store.brief().limits.max_total_tokens
                if limit is not None and self.coordinator.state.total_tokens >= limit:
                    self._pause_for_budget("Run token budget exhausted")
        if kind == "runtime_turn_started":
            # A native harness turn can contain many provider calls. Do not
            # misrepresent it as one model request in comparison statistics.
            self.coordinator.state.runtime_turns = self.coordinator.state.runtime_turns + 1
            self.coordinator.save()
        if kind in {"runtime_turn_started", "tool_started", "tool_completed"}:
            self.coordinator.state.agent_activity[agent_id] = AgentActivity(
                kind, event.get("name"), now()
            )
            self.coordinator.save()
        if kind == "message_injected":
            self.coordinator.receive_during_turn(agent_id, event["message_id"])
        if kind == "model_output":
            self.coordinator.store.event(
                "assistant_output", agent_id=agent_id, text=event.get("text", "")
            )
            self.wake()
            return
        data = {
            key: value for key, value in event.items() if key not in {"type", "agent_id", "kind"}
        }
        if "kind" in event:
            data["request_kind"] = event["kind"]
        self.coordinator.store.event(kind, agent_id=agent_id, **data)
        self.wake()

    def _model_message(self, message: ResearchMessage) -> Json:
        """Keep a delivery's origin beside its text, including the first message."""
        value: Json = encode(message)
        researcher = next(
            (
                actor
                for actor in self.coordinator.store.run().research_agents
                if actor.agent_id == message.sender
            ),
            None,
        )
        if researcher is not None:
            value["sender_direction"] = researcher.direction
        return value

    def _recovery_state(self, agent_id: str) -> Json:
        c = self.coordinator
        actor = c.actor(agent_id)
        current = next(
            (node for node in actor.iterations if node.iteration_id == actor.current_iteration_id),
            None,
        )
        run = c.store.run()
        message_id = c.state.active.get(agent_id)
        message = c.store.get("messages", message_id, ResearchMessage) if message_id else None
        assignment = c.store.public_record(current.assignment_ref) if current else None
        return {
            "run_id": c.store.run_id,
            "status": c.state.status,
            "goal": encode(c.state.goal),
            "simulated": c.state.simulated,
            "brief": encode(c.store.brief()),
            "seed_candidate_id": c.state.seed_candidate_id,
            "best": c.store.public_record(run.best_result_ref) if run.best_result_ref else None,
            "baseline": c.store.public_record(run.baseline_result_ref)
            if run.baseline_result_ref
            else None,
            "execution_setup": c.state.execution_setup,
            "withdrawn_candidates": c.store.withdrawals(),
            "plan": c.state.plan,
            "agent_id": agent_id,
            "current_message": {
                "message_id": message.message_id,
                "sender": message.sender,
                "reply_to": message.reply_to,
            }
            if message
            else None,
            "followup_message_ids": c.state.turn_followups.get(agent_id, []),
            "waiting_for_user": encode(c.user_questions())
            if agent_id in {"rsi", "interaction"}
            else [],
            "direction": actor.direction,
            "collaborators": c.collaborators(agent_id),
            "current_node": encode(current),
            # A completion notification is a new delivery, not a replacement
            # assignment. Keep the task itself available after wakeups/compaction.
            "current_assignment": {
                key: assignment[key]
                for key in ("assignment_id", "instruction", "completion_criteria")
            }
            if assignment
            else None,
            "budget": c.budget(observed_at=self.clock()),
            "directions": [
                {
                    "agent_id": researcher.agent_id,
                    "direction": researcher.direction,
                    "state": researcher.state,
                    "current_iteration_id": researcher.current_iteration_id,
                }
                for researcher in run.research_agents
            ]
            if agent_id in {"rsi", "interaction"}
            else [],
        }

    async def _drive(self, agent_id: str) -> None:
        c = self.coordinator
        if not self._actor_allowed(agent_id):
            return
        try:
            message, resumed = c.begin_turn(agent_id)
            if self.effective is not None and agent_id not in {"rsi", "interaction"}:
                self.effective.start_agent(agent_id, self.clock())
                c.save()
            actor = c.actor(agent_id)
            iteration_id = actor.current_iteration_id
            session = c.store.paths.agent(agent_id) / "sessions" / (actor.session_id or agent_id)
            state = self._recovery_state(agent_id)
            handoff = session / "backend-handoff.json"
            if handoff.exists():
                state["backend_handoff"] = json.loads(handoff.read_text())
            toolset = self.tools_factory(agent_id)
            if agent_id == "rsi":
                prompt = self.rsi_prompt.replace(
                    "{{max_agents}}", str(c.store.brief().limits.max_agents)
                ).replace("{{tool_instructions}}", toolset.instructions)
            elif agent_id == "interaction":
                prompt = (
                    INTERACTION_PROMPT + "\n\n" + toolset.instructions + "\n\n" + INTEGRITY_PROMPT
                )
            else:
                prompt = self.research_prompt.replace("{{tool_instructions}}", toolset.instructions)
            prompt_id = hashlib.sha256(prompt.encode()).hexdigest()
            write_json(session / "prompts" / f"{prompt_id}.json", {"prompt": prompt})
            runtime = self.interaction_runtime if agent_id == "interaction" else self.runtime
            configuration = (
                runtime.configuration_for(agent_id)
                if isinstance(runtime, RoleConfiguredRuntime)
                else runtime.configuration()
            )
            write_json(
                session / "configuration.json",
                {**configuration, "prompt_id": prompt_id},
            )

            async def event_sink(event: Json) -> None:
                await self._event(agent_id, event)
                # Usage is accounted for above, including deduplicated journal replay.
                # Rebuilding the full view for each usage blocks the shared event loop;
                # refresh at tool/message boundaries, as on the original native path.
                if event["type"] in {"tool_completed", "message_injected"}:
                    # Keep the object passed to runtime current throughout a long
                    # turn; checkpoint creation takes its own immutable copy.
                    latest = self._recovery_state(agent_id)
                    if "backend_handoff" in state:
                        latest["backend_handoff"] = state["backend_handoff"]
                    state.clear()
                    state.update(latest)

            retry = c.state.turn_retries.get(agent_id)

            def incoming_messages() -> list[Json]:
                allowed = c.turn_update_senders(agent_id)
                updates = (
                    c.store.get("messages", identifier, ResearchMessage)
                    for identifier in c.state.inboxes.get(agent_id, [])
                    if identifier != message.message_id
                )
                return [
                    self._model_message(update) for update in updates if update.sender in allowed
                ]

            # Peer reports and progress use the same envelope whether they wake
            # an idle actor or arrive during its turn. Literal user input stays
            # literal; archived turns resume their original payload unchanged.
            delivered_text = (
                message.text
                if message.sender == "user"
                else json.dumps(self._model_message(message), ensure_ascii=False)
            )
            result = await runtime.turn(
                session,
                system_prompt=prompt,
                user_message=None if resumed and runtime.has_session(session) else delivered_text,
                message_id=message.message_id,
                tools=toolset.definitions,
                advertised_tools=toolset.advertised,
                handler=toolset.call,
                event_sink=event_sink,
                recovery_state=state,
                retry_id=retry.retry_id
                if retry is not None
                and retry.mode != "report"
                and retry.message_id == message.message_id
                else None,
                incoming_messages=incoming_messages,
                workspace=toolset.workspace,
                environment=toolset.environment,
            )
            user_messages = [
                identifier
                for identifier in c.current_message_ids(agent_id)
                if c.store.get("messages", identifier, ResearchMessage).sender == "user"
            ]
            if (
                (agent_id == "interaction" or (agent_id == "rsi" and user_messages))
                and result.stop_reason == "completed"
                and result.text.strip()
            ):
                c.enqueue(
                    agent_id,
                    "user",
                    result.text.strip(),
                    key=agent_id + "-response:" + message.message_id,
                    reply_to=(user_messages or c.current_message_ids(agent_id))[-1],
                )
            if result.stop_reason == "completed" and agent_id not in {"rsi", "interaction"}:
                self._complete_research_turn(
                    agent_id, iteration_id, message.message_id, result.text
                )
            else:
                c.end_turn(agent_id)
        except asyncio.CancelledError:
            actor = c.actor(agent_id)
            if c.state.status in {"succeeded", "completed"}:
                actor.state, actor.wait_reason = AgentState.STOPPED, "completed"
                c.save_actor(actor)
                c.end_turn(agent_id)
            elif actor.state not in {AgentState.STOPPED, AgentState.FAILED}:
                paused = c.state.status == "paused"
                actor.state = AgentState.PAUSED if paused else AgentState.WAITING
                actor.wait_reason = "run_paused" if paused else "worker_interrupted"
                c.save_actor(actor)
                c.store.event("agent_suspended", agent_id=agent_id, reason=actor.wait_reason)
            raise
        except Exception as exc:
            c.end_turn(agent_id, error=f"{type(exc).__name__}: {exc}")
        finally:
            self.wake()

    def _actor_allowed(self, agent_id: str) -> bool:
        status = self.coordinator.state.status
        return status == "running" or (status == "planned" and agent_id == "interaction")

    def _complete_research_turn(
        self, agent_id: str, iteration_id: str | None, message_id: str, text: str
    ) -> None:
        c = self.coordinator
        node = next(n for n in c.actor(agent_id).iterations if n.iteration_id == iteration_id)
        if node.report_ref is None and not c.pending_updates(agent_id):
            if not text.strip():
                c.end_turn(
                    agent_id,
                    error="Research Agent returned an empty final reply",
                    missing_report=True,
                )
                return
            c.report(
                agent_id,
                key="final:" + message_id,
                fields={"message": text.strip(), "references": node.references},
            )
        c.end_turn(agent_id)

    async def tick(self) -> int:
        """Poll jobs, apply controls and dispatch ready actors without awaiting their turns."""
        async with self._tick_lock:
            self._acquire()
            reason = self._wall_reason()
            if reason is not None:
                self._pause_for_budget(reason)
            if self.tick_hook is not None:
                self._jobs_pending = bool(await self.tick_hook())
            status = self.coordinator.state.status
            cancelled = []
            for agent_id, task in self._tasks.items():
                state = self.coordinator.actor(agent_id).state
                if not task.done() and (
                    not self._actor_allowed(agent_id)
                    or state in {AgentState.STOPPED, AgentState.PAUSED}
                ):
                    task.cancel()
                    cancelled.append(task)
            if cancelled:
                await asyncio.gather(*cancelled, return_exceptions=True)
            for agent_id, task in tuple(self._tasks.items()):
                if task.done():
                    if not task.cancelled():
                        task.result()
                    del self._tasks[agent_id]
            if status in {"running", "planned"}:
                for actor in self.coordinator.actors():
                    if actor.state == AgentState.PAUSED and actor.wait_reason == "run_paused":
                        actor.state, actor.wait_reason = AgentState.WAITING, "resume"
                        self.coordinator.save_actor(actor)
                self.coordinator.reconcile_report_replies()
                for agent_id in self.coordinator.pending():
                    if (
                        self._actor_allowed(agent_id)
                        and agent_id not in self._tasks
                        and len(self._tasks) < self.max_concurrent_turns
                    ):
                        self._tasks[agent_id] = asyncio.create_task(
                            self._drive(agent_id),
                            name=f"research:{self.coordinator.store.run_id}:{agent_id}",
                        )
            return len(self._tasks)

    async def _wait(self) -> None:
        if self._wake.is_set():
            self._wake.clear()
            await asyncio.sleep(0)
            return
        with suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), self.poll_interval)
        self._wake.clear()

    async def run_until_idle(self, *, timeout: float | None = None) -> None:
        """Drain queued work and jobs indicated by tick_hook, then release ownership."""

        async def run() -> None:
            while True:
                active = await self.tick()
                if self.coordinator.state.status not in {"running", "planned"}:
                    return
                if not active and not self.coordinator.pending() and not self._jobs_pending:
                    return
                await self._wait()

        try:
            if timeout is None:
                await run()
            else:
                await asyncio.wait_for(run(), timeout)
        finally:
            await self.close()

    async def serve(self) -> None:
        """Run model turns; the owner closes us after any result confirmation."""
        self._acquire()
        try:
            while self.coordinator.state.status not in {
                "stopped",
                "succeeded",
                "failed",
                "completed",
            }:
                await self.tick()
                await self._wait()
        finally:
            await self._stop_turns()

    async def _stop_turns(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        if self.effective is not None:
            self.effective.close_worker(self.clock())
            self.coordinator.save()
            self.effective = None

    async def close(self) -> None:
        """Stop local execution while retaining active delivery IDs for another worker."""
        try:
            await self._stop_turns()
        finally:
            self._release_ownership()
