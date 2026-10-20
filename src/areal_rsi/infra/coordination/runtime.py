"""One session's Codex connection, turn scheduling and durable event processing."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

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
    LearningRecord,
    PromptQuestion,
    SessionEvent,
    SessionInput,
    SessionPhase,
    SessionRecord,
    SessionStatus,
    ToolReceipt,
    UserPrompt,
)
from areal_rsi.infra.codex import CodexClient, CodexError, NativeEvent
from areal_rsi.infra.config import validate_config
from areal_rsi.infra.coordination import ProblemRun, _local_stage
from areal_rsi.infra.coordination.policies import goal
from areal_rsi.infra.coordination.policies.prompts import CONTINUE, HOST, instructions
from areal_rsi.infra.coordination.store import SessionStore
from areal_rsi.infra.coordination.tools.control import apply_control, definitions
from areal_rsi.infra.experience import ExperienceLibrary
from areal_rsi.infra.storage.artifacts import write_record


@dataclass
class SessionRuntime:
    """Own live state; callers serialize mutations with this session's lock."""

    record: SessionRecord
    client: CodexClient
    lease: TextIO
    store: SessionStore
    experience: ExperienceLibrary
    problem: ProblemRun | None = None
    operation: asyncio.Task[CandidateRecord] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    output: asyncio.Queue[SessionEvent] = field(default_factory=lambda: asyncio.Queue(maxsize=256))
    prompts: dict[int | str, UserPrompt] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None
    did_work: bool = False
    clock: float = field(default_factory=time.monotonic)
    checkpoint: float = field(default_factory=time.monotonic)
    learning_task: asyncio.Task[ExperienceRef] | None = None

    @classmethod
    async def open(
        cls,
        store: SessionStore,
        experience: ExperienceLibrary,
        run_id: str,
        output: asyncio.Queue[SessionEvent] | None = None,
        problem: ProblemRun | None = None,
    ) -> SessionRuntime:
        lease = store.acquire(run_id)
        client: CodexClient | None = None
        try:
            record = store.load(run_id)
            validate_config(record.config)
            client = CodexClient(
                store.root / "codex", store.directory(run_id) / "codex.log", record.config
            )
            live = cls(
                record,
                client,
                lease,
                store,
                experience,
                problem=problem,
                output=output or asyncio.Queue(maxsize=256),
            )
            await client.start()
            await live._restore_thread()
            store.save(record)
            live.task = asyncio.create_task(live._serve(), name=f"rsi-{run_id}")
            return live
        except BaseException:
            try:
                if client:
                    await client.close()
            finally:
                lease.close()
            raise

    @property
    def disconnected(self) -> bool:
        return self.client.closing or (self.client.reader is not None and self.client.reader.done())

    async def _restore_thread(self) -> None:
        record = self.record
        if record.pending_prompts:
            record.pending_prompts.clear()
            record.status = SessionStatus.PAUSED
            self.emit(
                SessionEvent("error", text="连接已重建，旧问题已失效；请恢复任务后重新确认。")
            )
        if record.learning.status == "running":
            record.learning.status = "failed"
            record.learning.error = "总结会话已中断；可通过 /learn 重试。"
        if not record.thread_id:
            result = await self.client.request(
                "thread/start",
                {
                    **self._thread_options(),
                    "dynamicTools": definitions(problem=record.problem is not None),
                },
            )
            record.thread_id = result["thread"]["id"]
            return
        await self.client.request(
            "thread/resume",
            {**self._thread_options(), "threadId": record.thread_id, "excludeTurns": True},
        )
        if record.in_flight or record.active_turn_id:
            await self._reconcile_turn()
        if record.status in {SessionStatus.RUNNING, SessionStatus.IDLE, SessionStatus.WAITING} and (
            record.phase == SessionPhase.LEADER
            or record.in_flight
            or record.pending_inputs
            or record.handoff_pending
        ):
            record.status = SessionStatus.PAUSED

    async def _reconcile_turn(self) -> None:
        """Recognize a persisted completion without replaying uncertain side effects."""
        record = self.record
        result = await self.client.request(
            "thread/turns/list", {"threadId": record.thread_id, "limit": 1, "itemsView": "summary"}
        )
        turns = result["data"]
        if turns and turns[0]["id"] == record.active_turn_id and turns[0]["status"] == "completed":
            goal.settle(record, automatic=False, did_work=True)
            record.last_turn_id = turns[0]["id"]
            record.in_flight = None
        else:
            record.last_error = (
                "上次回合中断或投递结果不确定。历史已恢复；/resume 会先核对已有工作再继续。"
            )
        record.active_turn_id = ""

    async def steer(self, message: SessionInput) -> bool:
        """Persist delivery intent; return false when the input needs a new turn."""
        record = self.record
        if not record.active_turn_id or record.next_phase is not None:
            return False
        previous, record.in_flight = record.in_flight, message
        self.save()
        try:
            await self.client.request(
                "turn/steer",
                {
                    "threadId": record.thread_id,
                    "expectedTurnId": record.active_turn_id,
                    "input": self._input(message),
                    "clientUserMessageId": message.message_id,
                },
            )
        except CodexError as exc:
            if exc.code not in {-32600, -32602}:
                self.fail(exc)
                raise
            # Turn completion raced the steer; the caller queues this input instead.
            record.in_flight = previous
            return False
        record.progress.decision = "active"
        self.save()
        return True

    async def answer_prompt(
        self, prompt: UserPrompt, *, approved: bool | None, answers: dict[str, str] | None
    ) -> None:
        """Deliver a validated user answer before clearing its pending request."""
        result: dict[str, Any]
        if prompt.kind == "approval":
            result = {"decision": "accept" if approved else "decline"}
        else:
            result = {
                "answers": {key: {"answers": [value]} for key, value in (answers or {}).items()}
            }
        await self.client.reply(prompt.request_id, result)
        self.prompts.pop(prompt.request_id)
        if not self.prompts and self.record.status == SessionStatus.WAITING:
            self.record.status = SessionStatus.RUNNING
        self.save()

    async def interrupt(self) -> None:
        if self.record.active_turn_id:
            try:
                await self.client.request(
                    "turn/interrupt",
                    {
                        "threadId": self.record.thread_id,
                        "turnId": self.record.active_turn_id,
                    },
                    timeout=5,
                )
            except CodexError as exc:
                if exc.code not in {-32600, -32602}:
                    # Cancellation may wait on a stalled provider. Stop only our owned process,
                    # retain the uncertain input, and audit history before any later continuation.
                    await self.client.close()
                    self.record.last_turn_id = self.record.active_turn_id
                    self.record.active_turn_id = ""
                    self.record.last_error = (
                        "Codex 未及时确认中断，已关闭本次进程；恢复时先核对已执行工作。"
                    )
                    self.save()
                    self.emit(SessionEvent("error", text=self.record.last_error))

    async def _serve(self) -> None:
        while True:
            try:
                event = await asyncio.wait_for(self.client.events.get(), 0.2)
            except TimeoutError:
                event = None
            async with self.lock:
                try:
                    moment = time.monotonic()
                    if self.record.phase == SessionPhase.LEADER and (
                        self.record.status == SessionStatus.RUNNING
                    ):
                        self.record.seconds_used += moment - self.clock
                    self.clock = moment
                    if event:
                        await self._event(event)
                    if goal.budget_exhausted(self.record) and self.record.status in {
                        SessionStatus.RUNNING,
                        SessionStatus.IDLE,
                    }:
                        self.record.status = SessionStatus.BUDGET_LIMITED
                        self.save()
                        await self.interrupt()
                    await self._drive()
                    if moment - self.checkpoint >= 1 and self.record.active_turn_id:
                        self.save()
                        self.checkpoint = moment
                except Exception as exc:
                    self.fail(exc)

    async def _drive(self) -> None:
        record = self.record
        if record.active_turn_id or self.prompts:
            return
        if goal.apply_phase(record):
            self.save()
        if record.status != SessionStatus.IDLE:
            return
        if record.handoff_pending:
            await self._handoff()
        if goal.should_continue(record) and (self.operation is None or self.operation.done()):
            record.pending_inputs.append(
                SessionInput(
                    uuid.uuid4().hex,
                    CONTINUE,
                    automatic=True,
                )
            )
        if not record.pending_inputs:
            return
        if goal.budget_exhausted(record):
            record.status = SessionStatus.BUDGET_LIMITED
            self.save()
            return
        await self._start_turn(record.pending_inputs.pop(0))

    async def _handoff(self) -> None:
        record = self.record
        thread_id = record.thread_id
        # Reuse a committed Leader fork when recovering a pending handoff.
        if record.phase != SessionPhase.LEADER:
            result = await self.client.request(
                "thread/fork",
                {**self._thread_options(), "threadId": thread_id, "excludeTurns": True},
            )
            thread_id = result["thread"]["id"]
        goal.accept_handoff(record, thread_id)
        self.save()

    async def _start_turn(self, message: SessionInput) -> None:
        record = self.record
        record.in_flight = message
        record.status = SessionStatus.RUNNING
        record.experience = self.experience.documents() if record.config.use_experience else []
        self.did_work = False
        self.save()  # Commit intent before a possibly ambiguous transport operation.
        role = instructions(record)
        if self.problem is not None:
            role += self.problem.context()
        result = await self.client.request(
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
        self.save()

    def _thread_options(self) -> dict[str, Any]:
        return {
            "model": self.record.config.model,
            "cwd": self.record.config.workspace,
            "approvalPolicy": self.record.config.approval_policy,
            "sandbox": self.record.config.sandbox,
            "developerInstructions": HOST,
        }

    @staticmethod
    def _input(message: SessionInput) -> list[dict[str, Any]]:
        return [{"type": "text", "text": message.text}] + [
            {"type": "localImage", "path": image} for image in message.images
        ]

    async def _event(self, event: NativeEvent) -> None:
        record, params, method = self.record, event.params, event.method
        if method == "transport/closed":
            raise CodexError(params["error"])
        if params.get("threadId", record.thread_id) != record.thread_id:
            if event.request_id is not None:
                await self.client.reject(
                    event.request_id, "Thread is not owned by this conversation"
                )
            return
        if event.request_id is not None:
            await self._request(event)
            return
        if method == "item/agentMessage/delta":
            self.publish(
                SessionEvent("delta", record.phase.value, params["delta"], params["itemId"])
            )
        elif method == "item/completed" and params["item"]["type"] == "agentMessage":
            item = params["item"]
            self.emit(SessionEvent("message", record.phase.value, item["text"], item["id"]))
        elif method in {"item/started", "item/completed"}:
            self._item_event(method, params["item"])
        elif method == "item/commandExecution/outputDelta":
            self.publish(SessionEvent("delta", "tool", params["delta"], params["itemId"]))
        elif method == "thread/tokenUsage/updated":
            total = int(params["tokenUsage"]["total"]["totalTokens"])
            previous = record.thread_tokens.get(record.thread_id, 0)
            if record.phase == SessionPhase.LEADER:
                record.tokens_used += total - previous if total >= previous else total
            record.thread_tokens[record.thread_id] = total
        elif method == "serverRequest/resolved":
            self.prompts.pop(params["requestId"], None)
            if (
                not self.prompts
                and record.status == SessionStatus.WAITING
                and record.active_turn_id
            ):
                record.status = SessionStatus.RUNNING
                self.save()
        elif method == "turn/completed":
            self._complete_turn(params["turn"])
        elif method == "error" and not params.get("willRetry", False):
            self.emit(SessionEvent("error", text=public_tool_text(params.get("error"))))

    def _item_event(self, method: str, item: dict[str, Any]) -> None:
        record = self.record
        kind = item["type"]
        if kind in {"commandExecution", "fileChange", "mcpToolCall", "webSearch", "imageView"}:
            self.did_work = True
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
            self.emit(
                SessionEvent(
                    "tool",
                    record.phase.value,
                    public_tool_text(text),
                    item["id"],
                    completed=method == "item/completed",
                ),
            )
        elif kind == "contextCompaction":
            self.emit(
                SessionEvent("tool", text="Codex 上下文压缩：" + method, item_id=item["id"]),
            )

    def _complete_turn(self, turn: dict[str, Any]) -> None:
        record = self.record
        if record.active_turn_id and turn["id"] != record.active_turn_id:
            return
        record.active_turn_id = ""
        record.last_turn_id = turn["id"]
        self.prompts.clear()
        halted = record.status in {
            SessionStatus.PAUSED,
            SessionStatus.STOPPED,
            SessionStatus.BUDGET_LIMITED,
        }
        if turn["status"] == "completed":
            automatic = record.in_flight.automatic if record.in_flight else False
            record.in_flight = None
            if not halted:
                goal.settle(record, automatic=automatic, did_work=self.did_work)
        elif turn["status"] == "failed":
            self.fail(CodexError(public_tool_text(turn.get("error"))))
        elif not halted:
            record.status = SessionStatus.PAUSED
        self.save()
        if (
            record.status == SessionStatus.COMPLETED
            and record.learning.source_turn != record.last_turn_id
        ):
            if record.config.auto_learn:
                self.schedule_learning()
            else:
                record.learning = LearningRecord(
                    "awaiting_feedback",
                    source_turn=record.last_turn_id,
                    document=record.learning.document,
                )
                self.save()
                self.emit(
                    SessionEvent(
                        "message",
                        "curator",
                        "任务已完成，等待你的复盘意见。可用 /learn 意见 指出有效做法、"
                        "问题或需记住的教训；直接 /learn 可跳过补充，按任务历史总结。",
                    ),
                )

    async def _request(self, event: NativeEvent) -> None:
        assert event.request_id is not None
        params = event.params
        if event.method == "item/tool/call":
            await self._control(event)
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
            await self.client.reject(
                event.request_id, "Unsupported client request: " + event.method
            )
            self.emit(SessionEvent("error", text="尚未支持的 Codex 交互：" + event.method))
            return
        prompt.prompt_id = uuid.uuid4().hex
        self.prompts[event.request_id] = prompt
        self.record.status = SessionStatus.WAITING
        self.save()
        self.publish(SessionEvent("prompt", prompt=prompt))

    async def _control(self, event: NativeEvent) -> None:
        """Commit an authorized control receipt before acknowledging its native call."""
        assert event.request_id is not None
        params = event.params
        key = f"{params['threadId']}:{params['turnId']}:{params['callId']}"
        try:
            if params["turnId"] != self.record.active_turn_id:
                raise ValueError("Tool call is not from the current turn")
            updated = copy.deepcopy(self.record)
            name, arguments = params["tool"], params["arguments"]
            if name in {
                "rsi_submit_candidate",
                "rsi_candidate_status",
                "rsi_evaluate_candidate",
                "rsi_select_candidate",
            }:
                receipt = await self._problem_control(updated, key, name, arguments)
            else:
                if name == "rsi_update_goal" and self.problem is not None:
                    update = decode_record(GoalUpdate, arguments)
                    if update.status == "completed":
                        await _local_stage(self.problem.verify_completion)
                receipt = apply_control(updated, key, name, arguments)
            self.store.save(updated)
            self.record = updated
            if name in {"rsi_submit_candidate", "rsi_evaluate_candidate", "rsi_select_candidate"}:
                self.did_work = True
            text, success = receipt.text, receipt.success
            self.emit(SessionEvent("tool", updated.phase.value, params["tool"], completed=True))
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            text, success = str(exc), False
        await self.client.reply(
            event.request_id,
            {
                "contentItems": [{"type": "inputText", "text": text}],
                "success": success,
            },
        )

    def _problem(self) -> ProblemRun:
        if self.problem is None:
            raise ValueError("This session has no bound research problem")
        return self.problem

    def _research_access(self) -> None:
        record = self.record
        if record.phase != SessionPhase.LEADER or record.status not in {
            SessionStatus.RUNNING,
            SessionStatus.IDLE,
        }:
            raise ValueError("Candidate control requires an executing Leader")

    async def submit_candidate(self, proposal: CandidateProposal) -> CandidateFeedback:
        self._research_access()
        problem = self._problem()
        recipe_path = Path(proposal.recipe).expanduser()
        if not recipe_path.is_absolute():
            recipe_path = Path(self.record.config.workspace) / recipe_path
        recipe_path = recipe_path.resolve(strict=True)
        operation = self.operation
        if operation is not None and not operation.done():
            if operation.get_name() != f"candidate-{self.record.run_id}-{proposal.candidate_id}":
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
        self._schedule_candidate(proposal.candidate_id, retry=False)
        return problem.feedback(proposal.candidate_id)[0]

    async def evaluate_candidate(
        self, candidate_id: str, *, retry: bool = False
    ) -> CandidateFeedback:
        self._research_access()
        operation = self.operation
        if operation is not None and not operation.done():
            raise ValueError("A candidate operation is already running; inspect its status")
        problem = self._problem()
        candidate = problem.candidate(candidate_id)
        if candidate.plan is not None and candidate.validation is None:
            candidate.status, candidate.error = CandidateStatus.PREPARED, ""
            write_record(problem.path(candidate_id), candidate)
        self._schedule_candidate(candidate_id, retry=retry)
        return problem.feedback(candidate_id)[0]

    def _schedule_candidate(self, candidate_id: str, *, retry: bool) -> None:
        async def execute() -> CandidateRecord:
            event: SessionEvent | None = None
            try:
                result = await self._problem().evaluate(candidate_id, retry=retry)
                event = SessionEvent(
                    "tool",
                    "leader",
                    public_tool_text(encode_record(self._problem().feedback(candidate_id)[0])),
                    completed=True,
                )
                return result
            except Exception as exc:
                event = SessionEvent("error", text=public_tool_text(str(exc)))
                raise
            finally:
                if event is not None:
                    self.emit(event)

        task = asyncio.create_task(execute(), name=f"candidate-{self.record.run_id}-{candidate_id}")
        # Failures are retained in candidate records and emitted, not lost as task warnings.
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        self.operation = task

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
        self._research_access()
        if name == "rsi_submit_candidate":
            result = encode_record(
                await self.submit_candidate(decode_record(CandidateProposal, arguments))
            )
        elif name == "rsi_evaluate_candidate":
            request = decode_record(CandidateEvaluation, arguments)
            result = encode_record(
                await self.evaluate_candidate(request.candidate_id, retry=request.retry)
            )
        elif name == "rsi_select_candidate":
            request_select = decode_record(SelectCandidate, arguments)
            result = encode_record(
                await _local_stage(self.select_candidate, request_select.candidate_id)
            )
        else:
            request_query = decode_record(CandidateQuery, arguments)
            result = {
                "candidates": [
                    encode_record(item)
                    for item in self._problem().feedback(request_query.candidate_id)
                ]
            }
        receipt = ToolReceipt(name, digest, public_tool_text(result), True)
        record.receipts[key] = receipt
        return receipt

    def select_candidate(self, candidate_id: str) -> CandidateSelection:
        self._research_access()
        operation = self.operation
        if operation is not None and not operation.done():
            raise ValueError("Wait for the running candidate before freezing selection")
        return self._problem().select(candidate_id)

    def schedule_learning(self, feedback: str = "") -> asyncio.Task[ExperienceRef]:
        if self.learning_task and not self.learning_task.done():
            if feedback.strip():
                raise ValueError("正在总结经验；完成后请再次 /learn 提交新反馈。")
            return self.learning_task
        record = self.record
        if record.active_turn_id or record.pending_inputs or not record.last_turn_id:
            raise ValueError("请先结束或暂停当前任务，再总结经验")
        record.learning = LearningRecord(
            "running", source_turn=record.last_turn_id, document=record.learning.document
        )
        self.save()
        snapshot = copy.deepcopy(record)
        self.learning_task = asyncio.create_task(self._learn(snapshot, feedback))
        # Automatic curation has no awaiting caller; errors are persisted and surfaced below.
        self.learning_task.add_done_callback(
            lambda task: task.exception() if not task.cancelled() else None
        )
        return self.learning_task

    async def _learn(self, snapshot: SessionRecord, feedback: str) -> ExperienceRef:
        self.emit(SessionEvent("message", "curator", "正在独立会话中整理可复用经验…"))
        try:
            document = await self.experience.consolidate(snapshot, self.store.root, feedback)
            async with self.lock:
                self.record.learning = LearningRecord(
                    "completed",
                    snapshot.last_turn_id,
                    document,
                )
                self.save()
                self.emit(
                    SessionEvent(
                        "message",
                        "curator",
                        f"经验已沉淀：{document.title}\n{document.path}",
                    ),
                )
            return document
        except BaseException as exc:
            async with self.lock:
                self.record.learning.status = "failed"
                self.record.learning.error = (
                    public_tool_text(str(exc)) or "总结已中断，可 /learn 重试。"
                )
                self.save()
                self.emit(SessionEvent("error", "curator", self.record.learning.error))
            raise

    def publish(self, event: SessionEvent) -> None:
        # The optional live UI must not hold unbounded output while disconnected.
        # Completed messages remain replayable from the durable transcript.
        if self.output.full():
            self.output.get_nowait()
        self.output.put_nowait(event)

    def save(self) -> None:
        self.record.pending_prompts = list(self.prompts.values())
        self.store.save(self.record)
        self.client.keep_awake(
            self.record.status == SessionStatus.RUNNING
            or goal.should_continue(self.record)
            or self.record.learning.status == "running"
        )
        self.publish(SessionEvent("status"))

    def emit(self, event: SessionEvent) -> None:
        self.store.append(self.record.run_id, event)
        self.publish(event)

    def fail(self, error: Exception) -> None:
        self.record.status = SessionStatus.FAILED
        self.record.last_error = public_tool_text(str(error))
        self.save()
        self.emit(SessionEvent("error", text=self.record.last_error))

    async def close(self) -> None:
        try:
            for task in (self.operation, self.task, self.learning_task):
                if task and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            if self.record.status in {SessionStatus.RUNNING, SessionStatus.WAITING} or (
                self.record.phase == SessionPhase.LEADER
                and self.record.status == SessionStatus.IDLE
            ):
                self.record.status = SessionStatus.PAUSED
                self.store.save(self.record)
        finally:
            try:
                await self.client.close()
            finally:
                self.lease.close()
