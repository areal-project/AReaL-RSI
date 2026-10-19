"""Conversation lifecycle and RSI handoff over one native Codex execution kernel."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TextIO, cast

from areal_rsi.api.control import (
    CandidateEvaluation,
    CandidateProposal,
    CandidateQuery,
    GoalUpdate,
    SelectCandidate,
    decode_record,
    encode_record,
    public_tool_text,
)
from areal_rsi.api.research import (
    CandidateFeedback,
    CandidateRecord,
    CandidateSelection,
    CandidateStatus,
    ExperienceRef,
    FinalEvaluation,
    GoalProgress,
    LearningRecord,
    ProblemBinding,
    ProblemSpec,
    PromptQuestion,
    ReasoningEffort,
    SessionConfig,
    SessionEvent,
    SessionInput,
    SessionPhase,
    SessionRecord,
    SessionStatus,
    ToolReceipt,
    UserPrompt,
)
from areal_rsi.infra.agents import HOST, instructions
from areal_rsi.infra.artifacts import write_record
from areal_rsi.infra.codex import CodexClient, CodexError, NativeEvent
from areal_rsi.infra.config import available_models, data_root, validate_config
from areal_rsi.infra.coordination import ProblemRun, pin_problem
from areal_rsi.infra.coordination.policies.goal import budget_exhausted, settle, should_continue
from areal_rsi.infra.coordination.store import SessionStore
from areal_rsi.infra.coordination.tools.control import apply_control, definitions
from areal_rsi.infra.experience import ExperienceLibrary


@dataclass
class LiveSession:
    record: SessionRecord
    client: CodexClient
    lease: TextIO
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    output: asyncio.Queue[SessionEvent] = field(default_factory=asyncio.Queue)
    prompts: dict[int | str, UserPrompt] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None
    did_work: bool = False
    clock: float = field(default_factory=time.monotonic)
    checkpoint: float = field(default_factory=time.monotonic)
    learning_task: asyncio.Task[ExperienceRef] | None = None


class ResearchSystem:
    def __init__(self, root: Path | None = None):
        self.root = data_root(root)
        self.store = SessionStore(self.root)
        self.experience = ExperienceLibrary(self.root / "experience")
        self.live: dict[str, LiveSession] = {}
        self.open_lock = asyncio.Lock()
        self.problems: dict[str, ProblemRun] = {}
        self.operations: dict[str, asyncio.Task[CandidateRecord]] = {}

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
        problem = self._problem(run_id)
        recipe_path = Path(proposal.recipe).expanduser()
        if not recipe_path.is_absolute():
            recipe_path = Path(self.detail(run_id).config.workspace) / recipe_path
        recipe_path = recipe_path.resolve(strict=True)
        operation = self.operations.get(run_id)
        if operation is not None and not operation.done():
            if operation.get_name() != f"candidate-{run_id}-{proposal.candidate_id}":
                raise ValueError("A candidate operation is already running; inspect its status")
            await asyncio.to_thread(
                problem.create_candidate,
                proposal.candidate_id,
                proposal.parent_id,
                proposal.hypothesis,
                recipe_path,
            )
            return problem.feedback(proposal.candidate_id)[0]
        await asyncio.to_thread(
            problem.create_candidate,
            proposal.candidate_id,
            proposal.parent_id,
            proposal.hypothesis,
            recipe_path,
        )
        self._schedule_candidate(run_id, proposal.candidate_id, retry=False)
        return problem.feedback(proposal.candidate_id)[0]

    async def evaluate_candidate(
        self, run_id: str, candidate_id: str, *, retry: bool = False
    ) -> CandidateFeedback:
        self._research_access(run_id)
        operation = self.operations.get(run_id)
        if operation is not None and not operation.done():
            raise ValueError("A candidate operation is already running; inspect its status")
        problem = self._problem(run_id)
        candidate = problem.candidate(candidate_id)
        if candidate.plan is not None and candidate.validation is None:
            candidate.status, candidate.error = CandidateStatus.PREPARED, ""
            write_record(problem.path(candidate_id), candidate)
        self._schedule_candidate(run_id, candidate_id, retry=retry)
        return problem.feedback(candidate_id)[0]

    def _schedule_candidate(self, run_id: str, candidate_id: str, *, retry: bool) -> None:
        async def execute() -> CandidateRecord:
            event: SessionEvent | None = None
            try:
                result = await self._problem(run_id).evaluate(candidate_id, retry=retry)
                event = SessionEvent(
                    "tool",
                    "leader",
                    public_tool_text(
                        encode_record(self._problem(run_id).feedback(candidate_id)[0])
                    ),
                    completed=True,
                )
                return result
            except Exception as exc:
                event = SessionEvent("error", text=public_tool_text(str(exc)))
                raise
            finally:
                if event is not None:
                    if run_id in self.live:
                        self._emit(self.live[run_id], event)
                    else:
                        self.store.append(run_id, event)

        task = asyncio.create_task(execute(), name=f"candidate-{run_id}-{candidate_id}")
        # Failures are retained in candidate records and emitted, not lost as task warnings.
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        self.operations[run_id] = task

    async def _problem_control(
        self, record: SessionRecord, key: str, name: str, arguments: Any
    ) -> ToolReceipt:
        digest = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()
        if key in record.receipts:
            receipt = record.receipts[key]
            if receipt.tool != name or receipt.arguments_digest != digest:
                raise ValueError("A tool call ID cannot be reused with different arguments")
            return receipt
        if record.status != SessionStatus.RUNNING:
            raise ValueError("Candidate tools require a running turn")
        self._research_access(record.run_id)
        if name == "rsi_submit_candidate":
            result = encode_record(
                await self.submit_candidate(
                    record.run_id, decode_record(CandidateProposal, arguments)
                )
            )
        elif name == "rsi_evaluate_candidate":
            request = decode_record(CandidateEvaluation, arguments)
            result = encode_record(
                await self.evaluate_candidate(
                    record.run_id, request.candidate_id, retry=request.retry
                )
            )
        elif name == "rsi_select_candidate":
            request_select = decode_record(SelectCandidate, arguments)
            result = encode_record(
                self.select_candidate(record.run_id, request_select.candidate_id)
            )
        else:
            request_query = decode_record(CandidateQuery, arguments)
            result = {
                "candidates": [
                    encode_record(item)
                    for item in self.candidate_status(record.run_id, request_query.candidate_id)
                ]
            }
        receipt = ToolReceipt(name, digest, public_tool_text(result), True)
        record.receipts[key] = receipt
        return receipt

    def select_candidate(self, run_id: str, candidate_id: str) -> CandidateSelection:
        self._research_access(run_id)
        operation = self.operations.get(run_id)
        if operation is not None and not operation.done():
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

    async def open(self, run_id: str) -> SessionRecord:
        """Reconnect native history; reopening never silently restarts interrupted work."""
        return copy.deepcopy((await self._attach(run_id)).record)

    async def set_model(self, run_id: str, model: str) -> None:
        """Save the model for future turns and curation, leaving active work unchanged."""
        live = await self._attach(run_id)
        async with live.lock:
            config = validate_config(replace(live.record.config, model=model))
            live.record.config.model = config.model.strip()
            self._save(live)

    async def set_effort(self, run_id: str, effort: str) -> None:
        """Save reasoning effort for future turns and curation without interrupting work."""
        live = await self._attach(run_id)
        async with live.lock:
            config = validate_config(
                replace(live.record.config, effort=cast(ReasoningEffort, effort))
            )
            live.record.config.effort = config.effort
            self._save(live)

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
                self._schedule_learning(live)
            live.record.config.auto_learn = mode == "auto"
            self._save(live)

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
            if record.status == SessionStatus.BUDGET_LIMITED and not budget_exhausted(record):
                record.status = SessionStatus.PAUSED
            self._save(live)

    async def _attach(self, run_id: str, *, reconnect: bool = False) -> LiveSession:
        async with self.open_lock:
            output: asyncio.Queue[SessionEvent] | None = None
            if run_id in self.live:
                live = self.live[run_id]
                disconnected = live.client.closing or (
                    live.client.reader is not None and live.client.reader.done()
                )
                if not reconnect or not (
                    disconnected or live.record.status == SessionStatus.FAILED
                ):
                    return live
                output = live.output
                await self._detach(run_id)
            lease = self.store.acquire(run_id)
            client: CodexClient | None = None
            try:
                record = self.store.load(run_id)
                if record.learning.status == "running":
                    record.learning.status = "failed"
                    record.learning.error = "总结会话已中断；可通过 /learn 重试。"
                validate_config(record.config)
                client = CodexClient(
                    self.root / "codex", self.store.directory(run_id) / "codex.log", record.config
                )
                await client.start()
                params = self._thread_options(record)
                if record.thread_id:
                    await client.request(
                        "thread/resume",
                        {
                            **params,
                            "threadId": record.thread_id,
                            "excludeTurns": True,
                        },
                    )
                    # Inspect persisted outcomes, including a turn completed before a client crash.
                    if record.in_flight or record.active_turn_id:
                        result = await client.request(
                            "thread/turns/list",
                            {
                                "threadId": record.thread_id,
                                "limit": 1,
                                "itemsView": "summary",
                            },
                        )
                        turns = result["data"]
                        if (
                            turns
                            and turns[0]["id"] == record.active_turn_id
                            and (turns[0]["status"] == "completed")
                        ):
                            settle(record, automatic=False, did_work=True)
                            record.last_turn_id = turns[0]["id"]
                            record.in_flight = None
                        else:
                            record.last_error = (
                                "上次回合中断或投递结果不确定。历史已恢复；"
                                "/resume 会先核对已有工作再继续。"
                            )
                        record.active_turn_id = ""
                    if record.status in {
                        SessionStatus.RUNNING,
                        SessionStatus.IDLE,
                        SessionStatus.WAITING,
                    } and (
                        record.phase == SessionPhase.LEADER
                        or record.in_flight
                        or record.pending_inputs
                        or record.handoff_pending
                    ):
                        record.status = SessionStatus.PAUSED
                else:
                    result = await client.request(
                        "thread/start",
                        {
                            **params,
                            "dynamicTools": definitions(problem=record.problem is not None),
                        },
                    )
                    record.thread_id = result["thread"]["id"]
                self.store.save(record)
                live = LiveSession(record, client, lease, output=output or asyncio.Queue())
                self.live[run_id] = live
                live.task = asyncio.create_task(self._serve(live), name=f"rsi-{run_id}")
                return live
            except BaseException:
                if client:
                    await client.close()
                lease.close()
                raise

    @staticmethod
    def _thread_options(record: SessionRecord) -> dict[str, Any]:
        return {
            "model": record.config.model,
            "cwd": record.config.workspace,
            "approvalPolicy": record.config.approval_policy,
            "sandbox": record.config.sandbox,
            "developerInstructions": HOST,
        }

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
            self._emit(
                live,
                SessionEvent(
                    "message",
                    "user",
                    text + ("\n" + "\n".join(paths) if paths else ""),
                    message.message_id,
                ),
            )
            if record.active_turn_id and record.next_phase is None:
                previous = record.in_flight
                record.in_flight = message
                self._save(live)
                try:
                    await live.client.request(
                        "turn/steer",
                        {
                            "threadId": record.thread_id,
                            "expectedTurnId": record.active_turn_id,
                            "input": self._input(message),
                            "clientUserMessageId": message.message_id,
                        },
                    )
                    record.progress.decision = "active"
                    self._save(live)
                    return
                except CodexError as exc:
                    if exc.code not in {-32600, -32602}:
                        self._fail(live, exc)
                        raise
                    # A rejected steer raced turn completion; queue it for the next idle boundary.
                    record.in_flight = previous
            if record.status in {SessionStatus.COMPLETED, SessionStatus.WAITING}:
                record.status = SessionStatus.IDLE
            record.progress.decision = "active"
            record.progress.question = ""
            record.pending_inputs.append(message)
            self._save(live)

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
            if record.phase != SessionPhase.CHAT or record.next_phase is not None:
                raise ValueError("当前已在 RSI 流程中；/chat 可返回普通模式")
            if record.status not in {SessionStatus.IDLE, SessionStatus.RUNNING}:
                raise ValueError("先 /resume 恢复或 /chat 返回普通模式，再进入 RSI")
            record.next_phase = SessionPhase.INTERACTION
            record.pending_inputs.append(
                SessionInput(
                    uuid.uuid4().hex,
                    "用户已通过 /rsi 进入需求讨论。基于此前完整上下文讨论并整理 RSI 目标。\n"
                    + text,
                    paths,
                )
            )
            self._emit(
                live,
                SessionEvent("message", "user", "/rsi " + text + "\n" + "\n".join(paths)),
            )
            self._save(live)

    async def start(self, run_id: str) -> None:
        """Hand the discussed task and its native history to the executing Leader."""
        live = await self._attach(run_id)
        async with live.lock:
            record = live.record
            if record.phase != SessionPhase.INTERACTION or record.goal is None:
                raise ValueError("请先 /rsi 讨论并保存任务配置")
            if record.status not in {SessionStatus.IDLE, SessionStatus.RUNNING}:
                raise ValueError("请先恢复需求讨论")
            record.handoff_pending = True
            self._save(live)

    async def pause(self, run_id: str) -> None:
        live = await self._attach(run_id)
        async with live.lock:
            if live.record.status in {SessionStatus.COMPLETED, SessionStatus.STOPPED}:
                return
            live.record.status = SessionStatus.PAUSED
            self._save(live)
            await self._interrupt(live)

    async def stop(self, run_id: str) -> None:
        live = await self._attach(run_id)
        async with live.lock:
            live.record.status = SessionStatus.STOPPED
            live.record.pending_inputs.clear()
            live.record.next_phase = None
            live.record.handoff_pending = False
            self._save(live)
            await self._interrupt(live)
        operation = self.operations.get(run_id)
        if operation is not None and not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        if live.record.problem is not None:
            problem = self._problem(run_id)
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
            if budget_exhausted(record):
                raise ValueError("目标预算已耗尽；请 /budget off 取消限制或提高预算，再 /resume")
            if record.status == SessionStatus.COMPLETED:
                return
            if record.in_flight:
                record.pending_inputs.insert(
                    0,
                    SessionInput(
                        uuid.uuid4().hex,
                        "恢复中断的工作。先核对会话历史、文件和进程状态，保留已经完成的修改，"
                        "不要重复有副作用的操作。上次输入可能已被接收：\n" + record.in_flight.text,
                        record.in_flight.images,
                    ),
                )
                record.in_flight = None
            record.status = SessionStatus.IDLE
            record.progress.decision = "active"
            record.progress.question = ""
            record.empty_continuations = 0
            record.last_error = ""
            self._save(live)

    async def chat(self, run_id: str) -> None:
        """Return to ordinary work in the current thread, retaining the latest work context."""
        live = await self._attach(run_id, reconnect=True)
        async with live.lock:
            record = live.record
            record.status = SessionStatus.PAUSED
            record.next_phase = SessionPhase.CHAT
            record.handoff_pending = False
            record.pending_inputs.clear()
            self._save(live)
            await self._interrupt(live)

    async def answer(
        self,
        run_id: str,
        request_id: int | str,
        *,
        approved: bool | None = None,
        answers: dict[str, str] | None = None,
    ) -> None:
        live = await self._attach(run_id)
        async with live.lock:
            prompt = live.prompts.get(request_id)
            if prompt is None:
                raise ValueError("这个问题已失效")
            if prompt.kind == "approval":
                if approved is None:
                    raise ValueError("请选择批准或拒绝")
                result: dict[str, Any] = {"decision": "accept" if approved else "decline"}
            else:
                expected = {question.question_id for question in prompt.questions}
                if (
                    not answers
                    or set(answers) != expected
                    or any(not v.strip() for v in answers.values())
                ):
                    raise ValueError("请回答列出的每个问题")
                result = {"answers": {key: {"answers": [value]} for key, value in answers.items()}}
            await live.client.reply(request_id, result)
            live.prompts.pop(request_id)
            if not live.prompts and live.record.status == SessionStatus.WAITING:
                live.record.status = SessionStatus.RUNNING
            self._save(live)

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
            task = self._schedule_learning(live, feedback)
        return await asyncio.shield(task)

    def _schedule_learning(
        self, live: LiveSession, feedback: str = ""
    ) -> asyncio.Task[ExperienceRef]:
        if live.learning_task and not live.learning_task.done():
            if feedback.strip():
                raise ValueError("正在总结经验；完成后请再次 /learn 提交新反馈。")
            return live.learning_task
        record = live.record
        if record.active_turn_id or record.pending_inputs or not record.last_turn_id:
            raise ValueError("请先结束或暂停当前任务，再总结经验")
        record.learning = LearningRecord(
            "running", source_turn=record.last_turn_id, document=record.learning.document
        )
        self._save(live)
        snapshot = copy.deepcopy(record)
        live.learning_task = asyncio.create_task(self._learn(live, snapshot, feedback))
        # Automatic curation has no awaiting caller; errors are persisted and surfaced below.
        live.learning_task.add_done_callback(
            lambda task: task.exception() if not task.cancelled() else None
        )
        return live.learning_task

    async def _learn(
        self, live: LiveSession, snapshot: SessionRecord, feedback: str
    ) -> ExperienceRef:
        self._emit(live, SessionEvent("message", "curator", "正在独立会话中整理可复用经验…"))
        try:
            document = await self.experience.consolidate(snapshot, self.root, feedback)
            async with live.lock:
                live.record.learning = LearningRecord(
                    "completed",
                    snapshot.last_turn_id,
                    document,
                )
                self._save(live)
                self._emit(
                    live,
                    SessionEvent(
                        "message",
                        "curator",
                        f"经验已沉淀：{document.title}\n{document.path}",
                    ),
                )
            return document
        except BaseException as exc:
            async with live.lock:
                live.record.learning.status = "failed"
                live.record.learning.error = (
                    public_tool_text(str(exc)) or "总结已中断，可 /learn 重试。"
                )
                self._save(live)
                self._emit(live, SessionEvent("error", "curator", live.record.learning.error))
            raise

    def _save(self, live: LiveSession) -> None:
        self.store.save(live.record)
        live.client.keep_awake(
            live.record.status == SessionStatus.RUNNING
            or should_continue(live.record)
            or live.record.learning.status == "running"
        )
        live.output.put_nowait(SessionEvent("status"))

    def _emit(self, live: LiveSession, event: SessionEvent) -> None:
        self.store.append(live.record.run_id, event)
        live.output.put_nowait(event)

    def _fail(self, live: LiveSession, error: Exception) -> None:
        live.record.status = SessionStatus.FAILED
        live.record.last_error = public_tool_text(str(error))
        self._save(live)
        self._emit(live, SessionEvent("error", text=live.record.last_error))

    @staticmethod
    def _input(message: SessionInput) -> list[dict[str, Any]]:
        return [{"type": "text", "text": message.text}] + [
            {"type": "localImage", "path": image} for image in message.images
        ]

    async def _interrupt(self, live: LiveSession) -> None:
        if live.record.active_turn_id:
            try:
                await live.client.request(
                    "turn/interrupt",
                    {
                        "threadId": live.record.thread_id,
                        "turnId": live.record.active_turn_id,
                    },
                    timeout=5,
                )
            except CodexError as exc:
                if exc.code not in {-32600, -32602}:
                    # Cancellation may wait on a stalled provider. Stop only our owned process,
                    # retain the uncertain input, and audit history before any later continuation.
                    await live.client.close()
                    live.record.last_turn_id = live.record.active_turn_id
                    live.record.active_turn_id = ""
                    live.record.last_error = (
                        "Codex 未及时确认中断，已关闭本次进程；恢复时先核对已执行工作。"
                    )
                    self._save(live)
                    self._emit(live, SessionEvent("error", text=live.record.last_error))

    async def _drive(self, live: LiveSession) -> None:
        record = live.record
        if record.active_turn_id or live.prompts:
            return
        if record.next_phase is not None and (
            record.status == SessionStatus.IDLE or record.next_phase == SessionPhase.CHAT
        ):
            record.phase = record.next_phase
            record.next_phase = None
            record.goal = None
            record.progress = GoalProgress()
            record.in_flight = None
            record.last_error = ""
            record.empty_continuations = 0
            record.status = SessionStatus.IDLE
            self._save(live)
        if record.status != SessionStatus.IDLE:
            return
        if record.handoff_pending:
            assert record.goal is not None
            source = record.thread_id
            # A committed fork ID is reused after restart; only the source-to-Leader boundary forks.
            if record.phase != SessionPhase.LEADER:
                result = await live.client.request(
                    "thread/fork",
                    {
                        **self._thread_options(record),
                        "threadId": source,
                        "excludeTurns": True,
                    },
                )
                record.source_thread_id = source
                record.thread_id = result["thread"]["id"]
                record.thread_tokens[record.thread_id] = record.thread_tokens.get(source, 0)
                record.phase = SessionPhase.LEADER
                record.tokens_used = 0
                record.seconds_used = 0
                record.progress = GoalProgress()
                self._save(live)
            record.handoff_pending = False
            record.pending_inputs.insert(
                0,
                SessionInput(
                    uuid.uuid4().hex,
                    "现在由 Leader 执行已讨论并保存的目标。"
                    "继承此前业务和视觉上下文，先检查现状再推进。",
                ),
            )
            self._save(live)
        operation = self.operations.get(record.run_id)
        if should_continue(record) and (operation is None or operation.done()):
            record.pending_inputs.append(
                SessionInput(
                    uuid.uuid4().hex,
                    "目标仍未完成。根据既有上下文、保存的进展与真实结果继续执行；"
                    "若已经完成请核对完成标准并报告证据；若确实阻塞请说明原因。",
                    automatic=True,
                )
            )
        if not record.pending_inputs:
            return
        if budget_exhausted(record):
            record.status = SessionStatus.BUDGET_LIMITED
            self._save(live)
            return
        message = record.pending_inputs.pop(0)
        record.in_flight = message
        record.status = SessionStatus.RUNNING
        record.experience = self.experience.documents() if record.config.use_experience else []
        live.did_work = False
        self._save(live)  # Commit intent before a possibly ambiguous transport operation.
        role = instructions(record)
        if record.problem is not None:
            problem = self._problem(record.run_id)
            role += problem.context()
        result = await live.client.request(
            "turn/start",
            {
                "threadId": record.thread_id,
                "input": self._input(message),
                "clientUserMessageId": message.message_id,
                "model": record.config.model,
                "effort": record.config.effort,
                "additionalContext": {
                    "rsi-role": {"kind": "application", "value": role},
                    "rsi-experience": {
                        "kind": "application",
                        "value": (
                            self.experience.context(record.experience)
                            if record.phase != SessionPhase.INTERACTION
                            else "本阶段只整理用户需求；经验文档在 Leader 执行时按需提供。"
                        ),
                    },
                },
            },
        )
        record.active_turn_id = result["turn"]["id"]
        self._save(live)

    async def _serve(self, live: LiveSession) -> None:
        while True:
            try:
                event = await asyncio.wait_for(live.client.events.get(), 0.2)
            except TimeoutError:
                event = None
            async with live.lock:
                try:
                    moment = time.monotonic()
                    if live.record.phase == SessionPhase.LEADER and (
                        live.record.status == SessionStatus.RUNNING
                    ):
                        live.record.seconds_used += moment - live.clock
                    live.clock = moment
                    if event:
                        await self._event(live, event)
                    if budget_exhausted(live.record) and live.record.status in {
                        SessionStatus.RUNNING,
                        SessionStatus.IDLE,
                    }:
                        live.record.status = SessionStatus.BUDGET_LIMITED
                        self._save(live)
                        await self._interrupt(live)
                    await self._drive(live)
                    if moment - live.checkpoint >= 1 and live.record.active_turn_id:
                        self._save(live)
                        live.checkpoint = moment
                except Exception as exc:
                    self._fail(live, exc)

    async def _event(self, live: LiveSession, event: NativeEvent) -> None:
        record, params, method = live.record, event.params, event.method
        if method == "transport/closed":
            raise CodexError(params["error"])
        if params.get("threadId", record.thread_id) != record.thread_id:
            if event.request_id is not None:
                await live.client.reject(
                    event.request_id, "Thread is not owned by this conversation"
                )
            return
        if event.request_id is not None:
            await self._request(live, event)
            return
        if method == "item/agentMessage/delta":
            live.output.put_nowait(
                SessionEvent("delta", record.phase.value, params["delta"], params["itemId"])
            )
        elif method == "item/completed" and params["item"]["type"] == "agentMessage":
            item = params["item"]
            self._emit(live, SessionEvent("message", record.phase.value, item["text"], item["id"]))
        elif method in {"item/started", "item/completed"}:
            item = params["item"]
            kind = item["type"]
            if kind in {"commandExecution", "fileChange", "mcpToolCall", "webSearch", "imageView"}:
                live.did_work = True
                text = str(item.get("command") or item.get("tool") or kind)
                if kind == "fileChange":
                    text += "\n" + "\n".join(change["path"] for change in item.get("changes", []))
                if method == "item/completed":
                    if kind == "commandExecution":
                        text += f"\nexit={item.get('exitCode')}\n" + (
                            item.get("aggregatedOutput") or ""
                        )
                    elif kind == "mcpToolCall":
                        text += "\n" + public_tool_text(item.get("result") or item.get("error"))
                self._emit(
                    live,
                    SessionEvent(
                        "tool",
                        record.phase.value,
                        public_tool_text(text),
                        item["id"],
                        completed=method == "item/completed",
                    ),
                )
            elif kind == "contextCompaction":
                self._emit(
                    live,
                    SessionEvent("tool", text="Codex 上下文压缩：" + method, item_id=item["id"]),
                )
        elif method == "item/commandExecution/outputDelta":
            live.output.put_nowait(SessionEvent("delta", "tool", params["delta"], params["itemId"]))
        elif method == "thread/tokenUsage/updated":
            total = int(params["tokenUsage"]["total"]["totalTokens"])
            previous = record.thread_tokens.get(record.thread_id, 0)
            if record.phase == SessionPhase.LEADER:
                record.tokens_used += total - previous if total >= previous else total
            record.thread_tokens[record.thread_id] = total
        elif method == "serverRequest/resolved":
            live.prompts.pop(params["requestId"], None)
            if (
                not live.prompts
                and record.status == SessionStatus.WAITING
                and record.active_turn_id
            ):
                record.status = SessionStatus.RUNNING
                self._save(live)
        elif method == "turn/completed":
            turn = params["turn"]
            if record.active_turn_id and turn["id"] != record.active_turn_id:
                return
            record.active_turn_id = ""
            record.last_turn_id = turn["id"]
            live.prompts.clear()
            if turn["status"] == "completed":
                automatic = record.in_flight.automatic if record.in_flight else False
                record.in_flight = None
                if record.status not in {
                    SessionStatus.PAUSED,
                    SessionStatus.STOPPED,
                    SessionStatus.BUDGET_LIMITED,
                }:
                    settle(record, automatic=automatic, did_work=live.did_work)
            elif turn["status"] == "failed":
                self._fail(live, CodexError(public_tool_text(turn.get("error"))))
            elif record.status not in {
                SessionStatus.PAUSED,
                SessionStatus.STOPPED,
                SessionStatus.BUDGET_LIMITED,
            }:
                record.status = SessionStatus.PAUSED
            self._save(live)
            if (
                record.status == SessionStatus.COMPLETED
                and record.learning.source_turn != record.last_turn_id
            ):
                if record.config.auto_learn:
                    self._schedule_learning(live)
                else:
                    record.learning = LearningRecord(
                        "awaiting_feedback",
                        source_turn=record.last_turn_id,
                        document=record.learning.document,
                    )
                    self._save(live)
                    self._emit(
                        live,
                        SessionEvent(
                            "message",
                            "curator",
                            "任务已完成，等待你的复盘意见。可用 /learn 意见 指出有效做法、"
                            "问题或需记住的教训；直接 /learn 可跳过补充，按任务历史总结。",
                        ),
                    )
        elif method == "error" and not params.get("willRetry", False):
            self._emit(live, SessionEvent("error", text=public_tool_text(params.get("error"))))

    async def _request(self, live: LiveSession, event: NativeEvent) -> None:
        assert event.request_id is not None
        params = event.params
        if event.method == "item/tool/call":
            key = f"{params['threadId']}:{params['turnId']}:{params['callId']}"
            try:
                if params["turnId"] != live.record.active_turn_id:
                    raise ValueError("Tool call is not from the current turn")
                updated = copy.deepcopy(live.record)
                name, arguments = params["tool"], params["arguments"]
                if name in {
                    "rsi_submit_candidate",
                    "rsi_candidate_status",
                    "rsi_evaluate_candidate",
                    "rsi_select_candidate",
                }:
                    receipt = await self._problem_control(updated, key, name, arguments)
                else:
                    if name == "rsi_update_goal" and updated.problem is not None:
                        update = decode_record(GoalUpdate, arguments)
                        if update.status == "completed":
                            self._problem(updated.run_id).verify_completion()
                    receipt = apply_control(updated, key, name, arguments)
                self.store.save(updated)
                live.record = updated
                if name in {
                    "rsi_submit_candidate",
                    "rsi_evaluate_candidate",
                    "rsi_select_candidate",
                }:
                    live.did_work = True
                text, success = receipt.text, receipt.success
                self._emit(
                    live, SessionEvent("tool", updated.phase.value, params["tool"], completed=True)
                )
            except (OSError, TypeError, ValueError, RuntimeError) as exc:
                text, success = str(exc), False
            await live.client.reply(
                event.request_id,
                {
                    "contentItems": [{"type": "inputText", "text": text}],
                    "success": success,
                },
            )
            return
        if event.method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            prompt = UserPrompt(
                event.request_id,
                "approval",
                public_tool_text(
                    params.get("command") or params.get("reason") or "允许此文件修改？"
                ),
            )
        elif event.method == "item/tool/requestUserInput":
            questions = [
                PromptQuestion(
                    item["id"],
                    item["question"],
                    [option["label"] for option in item.get("options") or []],
                )
                for item in params["questions"]
            ]
            prompt = UserPrompt(event.request_id, "question", "请补充以下信息", questions)
        else:
            await live.client.reject(
                event.request_id, "Unsupported client request: " + event.method
            )
            self._emit(live, SessionEvent("error", text="尚未支持的 Codex 交互：" + event.method))
            return
        live.prompts[event.request_id] = prompt
        live.record.status = SessionStatus.WAITING
        self._save(live)
        live.output.put_nowait(SessionEvent("prompt", prompt=prompt))

    async def _detach(self, run_id: str) -> None:
        live = self.live.pop(run_id)
        if live.task:
            live.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await live.task
        if live.learning_task and not live.learning_task.done():
            live.learning_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await live.learning_task
        try:
            if live.record.status in {SessionStatus.RUNNING, SessionStatus.WAITING} or (
                live.record.phase == SessionPhase.LEADER
                and live.record.status == SessionStatus.IDLE
            ):
                live.record.status = SessionStatus.PAUSED
                self.store.save(live.record)
            await live.client.close()
        finally:
            live.lease.close()

    async def close(self) -> None:
        operations = list(self.operations.values())
        for task in operations:
            if not task.done():
                task.cancel()
        await asyncio.gather(*operations, return_exceptions=True)
        for run_id in list(self.live):
            await self._detach(run_id)
