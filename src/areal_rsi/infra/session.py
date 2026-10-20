"""Public session operations for ordinary conversations and RSI goals."""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import cast

from areal_rsi.api.control import CandidateProposal
from areal_rsi.api.research import (
    CandidateFeedback,
    CandidateSelection,
    ExperienceRef,
    FinalEvaluation,
    ProblemBinding,
    ProblemSpec,
    ReasoningEffort,
    SessionConfig,
    SessionEvent,
    SessionInput,
    SessionPhase,
    SessionRecord,
    SessionStatus,
    UserPrompt,
)
from areal_rsi.infra.config import available_models, data_root, validate_config
from areal_rsi.infra.coordination import ProblemRun, pin_problem
from areal_rsi.infra.coordination.policies import goal
from areal_rsi.infra.coordination.policies.prompts import DISCUSS, RECOVER
from areal_rsi.infra.coordination.runtime import SessionRuntime
from areal_rsi.infra.coordination.store import SessionStore
from areal_rsi.infra.experience import ExperienceLibrary


class SessionSystem:
    def __init__(self, root: Path | None = None):
        self.root = data_root(root)
        self.store = SessionStore(self.root)
        self.experience = ExperienceLibrary(self.root / "experience")
        self.live: dict[str, SessionRuntime] = {}
        self.open_lock = asyncio.Lock()
        self.problems: dict[str, ProblemRun] = {}

    async def create(self, config: SessionConfig, *, problem: ProblemBinding | None = None) -> str:
        """Save a new ordinary conversation without starting a model turn."""
        run_id = "run-" + uuid.uuid4().hex[:12]
        config = validate_config(config)
        if problem is not None:
            problem = pin_problem(problem, self.store.directory(run_id) / "problem")
        self.store.save(
            SessionRecord(
                run_id,
                config,
                experience=self.experience.documents() if config.use_experience else [],
                problem=problem,
            )
        )
        return run_id

    def _problem(self, run_id: str) -> ProblemRun:
        binding = self.detail(run_id).problem
        if binding is None:
            raise ValueError("This session has no bound research problem")
        if run_id not in self.problems:
            self.problems[run_id] = ProblemRun(
                run_id, self.store.directory(run_id) / "problem", binding
            )
        return self.problems[run_id]

    async def prepare_problem(self, run_id: str) -> ProblemSpec:
        record = self.detail(run_id)
        if record.phase != SessionPhase.CHAT or record.active_turn_id:
            raise ValueError("Prepare the problem before entering RSI")
        problem = self._problem(run_id)
        spec = await asyncio.to_thread(problem.prepare)
        await problem.evaluate("base")
        return spec

    def problem_detail(self, run_id: str) -> ProblemSpec:
        return self._problem(run_id).spec

    def candidate_status(self, run_id: str, candidate_id: str = "") -> list[CandidateFeedback]:
        return self._problem(run_id).feedback(candidate_id)

    def _research_access(self, run_id: str) -> None:
        record = self.detail(run_id)
        if record.phase != SessionPhase.LEADER or record.status not in {
            SessionStatus.RUNNING,
            SessionStatus.IDLE,
        }:
            raise ValueError("Candidate control requires an executing Leader")

    async def submit_candidate(self, run_id: str, proposal: CandidateProposal) -> CandidateFeedback:
        self._research_access(run_id)
        live = await self._attach(run_id)
        async with live.lock:
            return await live.submit_candidate(proposal)

    async def evaluate_candidate(
        self, run_id: str, candidate_id: str, *, retry: bool = False
    ) -> CandidateFeedback:
        self._research_access(run_id)
        live = await self._attach(run_id)
        async with live.lock:
            return await live.evaluate_candidate(candidate_id, retry=retry)

    def select_candidate(self, run_id: str, candidate_id: str) -> CandidateSelection:
        self._research_access(run_id)
        live = self.live.get(run_id)
        if live and live.operation and not live.operation.done():
            raise ValueError("Wait for the running candidate before freezing selection")
        return self._problem(run_id).select(candidate_id)

    async def finalize_problem(self, run_id: str, candidate_id: str) -> FinalEvaluation:
        record = self.detail(run_id)
        if record.active_turn_id or record.status == SessionStatus.RUNNING:
            raise ValueError("Pause or complete research before host final evaluation")
        return await self._problem(run_id).finalize(candidate_id)

    def detail(self, run_id: str) -> SessionRecord:
        return (
            copy.deepcopy(self.live[run_id].record)
            if run_id in self.live
            else self.store.load(run_id)
        )

    def recover_interrupted(self, run_id: str) -> None:
        """Pause abandoned work and expire prompts without connecting to Codex or replaying it."""
        try:
            lease = self.store.acquire(run_id)
        except ValueError:
            return  # Another owner, including an embedded TUI, still holds this session.
        with lease:
            record = self.store.load(run_id)
            original = copy.deepcopy(record)
            if record.status in {SessionStatus.RUNNING, SessionStatus.WAITING} or (
                record.status == SessionStatus.IDLE
                and (
                    record.phase == SessionPhase.LEADER
                    or record.pending_inputs
                    or record.handoff_pending
                    or record.next_phase
                )
            ):
                record.status = SessionStatus.PAUSED
            if record.pending_prompts:
                record.pending_prompts.clear()
                record.last_error = (
                    "Previous questions expired on service restart. Resume explicitly."
                )
            if record.learning.status == "running":
                record.learning.status = "failed"
                record.learning.error = (
                    "Service restarted during curation; inspect experience first."
                )
            if record != original:
                self.store.save(record)

    async def open(self, run_id: str) -> SessionRecord:
        """Reconnect native history; reopening never silently restarts interrupted work."""
        return copy.deepcopy((await self._attach(run_id)).record)

    async def _attach(self, run_id: str, *, reconnect: bool = False) -> SessionRuntime:
        async with self.open_lock:
            live = self.live.get(run_id)
            if live and not (
                reconnect and (live.disconnected or live.record.status == SessionStatus.FAILED)
            ):
                return live
            output = live.output if live else None
            if live:
                del self.live[run_id]
                await live.close()
            problem = self._problem(run_id) if self.detail(run_id).problem is not None else None
            live = await SessionRuntime.open(self.store, self.experience, run_id, output, problem)
            self.live[run_id] = live
            return live

    async def set_model(self, run_id: str, model: str) -> None:
        """Save the model for future turns and curation, leaving active work unchanged."""
        live = await self._attach(run_id)
        async with live.lock:
            config = validate_config(replace(live.record.config, model=model))
            live.record.config.model = config.model.strip()
            live.save()

    async def set_effort(self, run_id: str, effort: str) -> None:
        """Save reasoning effort for future turns and curation without interrupting work."""
        live = await self._attach(run_id)
        async with live.lock:
            config = validate_config(
                replace(live.record.config, effort=cast(ReasoningEffort, effort))
            )
            live.record.config.effort = config.effort
            live.save()

    async def list_models(self, run_id: str) -> list[str]:
        """Query the configured API without starting a model turn or blocking execution."""
        return await asyncio.to_thread(available_models, self.detail(run_id).config)

    async def set_learning_mode(self, run_id: str, mode: str) -> None:
        """Choose automatic curation or waiting for the user's review after goal completion."""
        if mode not in {"auto", "guided"}:
            raise ValueError("用法：/learn-mode auto | guided")
        live = await self._attach(run_id)
        async with live.lock:
            if live.learning_task and not live.learning_task.done():
                raise ValueError("正在总结经验，请在本次总结结束后切换模式")
            if mode == "auto" and live.record.learning.status == "awaiting_feedback":
                live.schedule_learning()
            live.record.config.auto_learn = mode == "auto"
            live.save()

    async def set_budget(
        self,
        run_id: str,
        *,
        max_total_tokens: int | None = None,
        max_wall_seconds: float | None = None,
    ) -> None:
        """Set user-owned goal caps without resetting usage or resuming stopped work."""
        live = await self._attach(run_id)
        async with live.lock:
            record = live.record
            if record.goal is None or record.phase == SessionPhase.CHAT:
                raise ValueError("请先 /rsi 讨论并保存任务；新目标默认不限制预算")
            if record.status in {SessionStatus.COMPLETED, SessionStatus.STOPPED}:
                raise ValueError("任务已结束，不能修改预算")
            record.goal.limits = replace(
                record.goal.limits,
                max_total_tokens=max_total_tokens,
                max_wall_seconds=max_wall_seconds,
            )
            if record.status == SessionStatus.BUDGET_LIMITED and not goal.budget_exhausted(record):
                record.status = SessionStatus.PAUSED
            live.save()

    async def send_message(
        self, run_id: str, text: str, *, images: list[Path] | None = None
    ) -> None:
        """Send or steer an existing conversation, retaining the native thread context."""
        if not text.strip() and not images:
            raise ValueError("消息不能为空")
        paths = [str(path.expanduser().resolve(strict=True)) for path in images or []]
        message = SessionInput(uuid.uuid4().hex, text, paths)
        live = await self._attach(run_id)
        async with live.lock:
            record = live.record
            if record.status in {
                SessionStatus.PAUSED,
                SessionStatus.FAILED,
                SessionStatus.BUDGET_LIMITED,
                SessionStatus.STOPPED,
            }:
                raise ValueError("当前会话已暂停或停止；先 /resume，或 /chat 返回普通模式")
            if live.prompts:
                raise ValueError("请先回答当前问题或批准/拒绝操作")
            live.emit(
                SessionEvent(
                    "message",
                    "user",
                    text + ("\n" + "\n".join(paths) if paths else ""),
                    message.message_id,
                ),
            )
            if await live.steer(message):
                return
            if record.status in {SessionStatus.COMPLETED, SessionStatus.WAITING}:
                record.status = SessionStatus.IDLE
            record.progress.decision = "active"
            record.progress.question = ""
            record.pending_inputs.append(message)
            live.save()

    async def enter_rsi(
        self, run_id: str, text: str = "", *, images: list[Path] | None = None
    ) -> None:
        """Switch the existing ordinary thread to Interaction at a completed-turn boundary."""
        paths = [str(path.expanduser().resolve(strict=True)) for path in images or []]
        if self.detail(run_id).problem is not None:
            problem = self._problem(run_id)
            if not problem.path("base").exists() or problem.candidate("base").validation is None:
                raise ValueError("Prepare and evaluate the bound problem before entering RSI")
        live = await self._attach(run_id)
        async with live.lock:
            record = live.record
            goal.enter_interaction(record)
            record.pending_inputs.append(
                SessionInput(
                    uuid.uuid4().hex,
                    DISCUSS + text,
                    paths,
                )
            )
            live.emit(
                SessionEvent("message", "user", "/rsi " + text + "\n" + "\n".join(paths)),
            )
            live.save()

    async def start(self, run_id: str) -> None:
        """Hand the discussed task and its native history to the executing Leader."""
        live = await self._attach(run_id)
        async with live.lock:
            goal.start(live.record)
            live.save()

    async def pause(self, run_id: str) -> None:
        live = await self._attach(run_id)
        async with live.lock:
            if live.record.status in {SessionStatus.COMPLETED, SessionStatus.STOPPED}:
                return
            live.record.status = SessionStatus.PAUSED
            live.save()
            await live.interrupt()

    async def stop(self, run_id: str) -> None:
        live = await self._attach(run_id)
        async with live.lock:
            live.record.status = SessionStatus.STOPPED
            live.record.pending_inputs.clear()
            live.record.next_phase = None
            live.record.handoff_pending = False
            live.save()
            await live.interrupt()
            if live.operation and not live.operation.done():
                live.operation.cancel()
                await asyncio.gather(live.operation, return_exceptions=True)
            if live.problem is not None:
                problem = live.problem
                for path in (problem.root / "execution" / run_id / "jobs").glob("*/job.json"):
                    job = problem.jobs.detail(run_id, path.parent.name)
                    if not job.terminal or not job.resources_released:
                        await problem.jobs.cancel(run_id, path.parent.name)

    async def resume(self, run_id: str) -> None:
        """Resume explicitly, auditing interrupted work instead of replaying commands."""
        live = await self._attach(run_id, reconnect=True)
        async with live.lock:
            record = live.record
            if record.active_turn_id or live.prompts:
                raise ValueError("上一回合仍在结束或等待回答，请稍后再恢复")
            if goal.budget_exhausted(record):
                raise ValueError("目标预算已耗尽；请 /budget off 取消限制或提高预算，再 /resume")
            if record.status == SessionStatus.COMPLETED:
                return
            if record.in_flight:
                record.pending_inputs.insert(
                    0,
                    SessionInput(
                        uuid.uuid4().hex,
                        RECOVER + record.in_flight.text,
                        record.in_flight.images,
                    ),
                )
                record.in_flight = None
            record.status = SessionStatus.IDLE
            record.progress.decision = "active"
            record.progress.question = ""
            record.empty_continuations = 0
            record.last_error = ""
            live.save()

    async def chat(self, run_id: str) -> None:
        """Return to ordinary work in the current thread, retaining the latest work context."""
        live = await self._attach(run_id, reconnect=True)
        async with live.lock:
            record = live.record
            record.status = SessionStatus.PAUSED
            record.next_phase = SessionPhase.CHAT
            record.handoff_pending = False
            record.pending_inputs.clear()
            live.save()
            await live.interrupt()

    async def answer(
        self,
        run_id: str,
        request_id: int | str | None = None,
        *,
        prompt_id: str = "",
        approved: bool | None = None,
        answers: dict[str, str] | None = None,
    ) -> None:
        live = await self._attach(run_id)
        async with live.lock:
            prompt = (
                next((p for p in live.prompts.values() if p.prompt_id == prompt_id), None)
                if prompt_id
                else live.prompts.get(request_id)
                if request_id is not None
                else None
            )
            if prompt is None:
                raise ValueError("这个问题已失效")
            if prompt.kind == "approval":
                if approved is None:
                    raise ValueError("请选择批准或拒绝")
            else:
                expected = {question.question_id for question in prompt.questions}
                if (
                    not answers
                    or set(answers) != expected
                    or any(not v.strip() for v in answers.values())
                ):
                    raise ValueError("请回答列出的每个问题")
            await live.answer_prompt(prompt, approved=approved, answers=answers)

    async def events(self, run_id: str) -> AsyncIterator[SessionEvent]:
        live = await self._attach(run_id)
        while True:
            yield await live.output.get()

    def prompts(self, run_id: str) -> list[UserPrompt]:
        return list(self.live[run_id].prompts.values()) if run_id in self.live else []

    async def learn(self, run_id: str, feedback: str = "") -> ExperienceRef:
        """Curate reusable experience in a separate Codex conversation."""
        live = await self._attach(run_id)
        async with live.lock:
            learning = live.record.learning
            if (
                not feedback
                and learning.status == "completed"
                and learning.document
                and learning.source_turn == live.record.last_turn_id
            ):
                return learning.document
            task = live.schedule_learning(feedback)
        return await asyncio.shield(task)

    async def close(self) -> None:
        for run_id in list(self.live):
            await self.live.pop(run_id).close()
