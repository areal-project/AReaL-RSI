"""Dispatch validated control requests and retain durable receipts across client disconnects."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

from areal_rsi.api.control import (
    OPERATIONS,
    AnswerPrompt,
    ConfigureSession,
    ControlSession,
    CreateSession,
    LearnExperience,
    ListSessions,
    Mutation,
    ObserveSession,
    Query,
    ReadExperience,
    ReadRequest,
    ResearchOperation,
    SendMessage,
    ServiceRequest,
    ServiceShutdown,
    SessionQuery,
    decode_record,
    encode_record,
    public_tool_text,
)
from areal_rsi.api.research import RequestReceipt, SessionStatus, SessionSummary
from areal_rsi.infra.config import load_config
from areal_rsi.infra.session import SessionSystem

PROTOCOL = 1


def _wait_seconds(value: float) -> float:
    if not 0 <= value <= 20:
        raise ValueError("wait_seconds must be between 0 and 20")
    return value


class SessionService:
    """Own sessions and durable request receipts, independently of connected clients."""

    def __init__(self, root: Path):
        self.system = SessionSystem(root)
        self.version = version("areal-rsi")
        directory = self.system.root / "service"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.journal = sqlite3.connect(directory / "requests.sqlite3")
        (directory / "requests.sqlite3").chmod(0o600)
        self.journal.execute(
            "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, payload TEXT, receipt TEXT)"
        )
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.shutdown = asyncio.Event()

    def recover(self) -> None:
        """A crashed operation has an uncertain outcome; never replay it implicitly."""
        for _request_id, raw in self.journal.execute("SELECT id, receipt FROM requests").fetchall():
            receipt = decode_record(RequestReceipt, json.loads(raw))
            if receipt.status == "running":
                receipt.status = "interrupted"
                receipt.error = (
                    "Service restarted; inspect session/files before issuing a new request."
                )
                self._save_receipt(receipt)
        for record in self.system.store.list_sessions():
            self.system.recover_interrupted(record.run_id)

    def _receipt(self, request_id: str) -> RequestReceipt:
        row = self.journal.execute(
            "SELECT receipt FROM requests WHERE id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown request ID")
        return decode_record(RequestReceipt, json.loads(row[0]))

    def _save_receipt(self, receipt: RequestReceipt) -> None:
        with self.journal:
            self.journal.execute(
                "UPDATE requests SET receipt = ? WHERE id = ?",
                (json.dumps(encode_record(receipt)), receipt.request_id),
            )

    async def handle(self, request: ServiceRequest) -> dict[str, Any]:
        if request.protocol != PROTOCOL:
            raise ValueError(
                "Service protocol mismatch; finish active work and restart the service"
            )
        if request.operation == "service_info":
            return {
                "protocol": PROTOCOL,
                "version": self.version,
                "pid": os.getpid(),
                "executable": sys.executable,
                "data_root": str(self.system.root),
            }
        if request.operation == "service_stop":
            force = decode_record(ServiceShutdown, request.arguments).force
            if not force and (
                any(not task.done() for task in self.tasks.values())
                or any(
                    r.status in {SessionStatus.RUNNING, SessionStatus.WAITING}
                    or r.learning.status == "running"
                    for r in self.system.store.list_sessions()
                )
            ):
                raise ValueError("Active work exists; pause it first or use service stop --force")
            self.shutdown.set()
            return {"stopping": True}
        if request.client_version and request.client_version != self.version:
            raise ValueError(
                f"Running service {self.version}, client {request.client_version}; "
                "use service stop and restart before using the updated client"
            )
        spec = OPERATIONS.get(request.operation)
        if spec is None:
            raise ValueError("Unknown operation")
        command = decode_record(spec.arguments, request.arguments)
        if not spec.mutating:
            return await self._read(cast(Query, command))
        request_id = command.request_id
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id):
            raise ValueError("request_id must be 1–100 letters, digits, underscores or hyphens")
        payload = json.dumps(
            {"operation": request.operation, "arguments": encode_record(command)}, sort_keys=True
        )
        existing = self.journal.execute(
            "SELECT payload FROM requests WHERE id = ?", (request_id,)
        ).fetchone()
        if existing:
            if existing[0] != payload:
                raise ValueError("Request ID already belongs to different arguments")
        else:
            receipt = RequestReceipt(request_id, "running")
            with self.journal:
                self.journal.execute(
                    "INSERT INTO requests VALUES (?, ?, ?)",
                    (request_id, payload, json.dumps(encode_record(receipt))),
                )
            task = asyncio.create_task(self._execute(request_id, cast(Mutation, command)))
            self.tasks[request_id] = task
            task.add_done_callback(lambda _: self.tasks.pop(request_id, None))
        return await self._read(ReadRequest(request_id, 1))

    async def _execute(self, request_id: str, command: Mutation) -> None:
        receipt = self._receipt(request_id)
        try:
            receipt.result = await self._mutate(command)
            receipt.status = "completed"
        except asyncio.CancelledError:
            receipt = self._receipt(request_id)
            receipt.status = "interrupted"
            receipt.error = "Service stopped during operation; inspect state before retrying."
        except Exception as exc:
            receipt = self._receipt(request_id)
            receipt.status = "failed"
            receipt.error = public_tool_text(str(exc))
        self._save_receipt(receipt)

    async def _mutate(self, command: Mutation) -> dict[str, Any]:
        system = self.system
        match command:
            case CreateSession():
                workspace = Path(command.workspace).expanduser()
                if not workspace.is_absolute():
                    raise ValueError("workspace must be an absolute path")
                for value in (command.config_file, command.env_file):
                    if value and not Path(value).expanduser().is_absolute():
                        raise ValueError("config_file and env_file must be absolute paths")
                config = load_config(
                    workspace,
                    config_file=Path(command.config_file) if command.config_file else None,
                    env_file=Path(command.env_file) if command.env_file else None,
                    root=system.root,
                )
                run_id = await system.create(config.session, problem=command.problem)
                self._save_receipt(
                    RequestReceipt(command.request_id, "running", {"run_id": run_id})
                )
                text = (
                    "External harness context summary (not the original conversation):\n"
                    + command.context
                    + "\n\nUser request:\n"
                    + command.message
                    if command.context
                    else command.message
                )
                if command.problem is not None:
                    # Baseline preparation can be long; the same request remains observable.
                    await system.prepare_problem(run_id)
                if command.mode == "rsi":
                    await system.enter_rsi(run_id, text)
                elif text:
                    await system.send_message(run_id, text)
                return {"run_id": run_id}
            case SendMessage():
                if any(not Path(p).expanduser().is_absolute() for p in command.images):
                    raise ValueError("Image paths must be absolute")
                await system.send_message(
                    command.run_id, command.text, images=[Path(p) for p in command.images]
                )
            case ControlSession():
                if command.text and command.action != "rsi":
                    raise ValueError("Control text is only used when entering RSI; use rsi_send")
                if command.action == "rsi":
                    await system.enter_rsi(command.run_id, command.text)
                else:
                    controls = {
                        "start": system.start,
                        "pause": system.pause,
                        "resume": system.resume,
                        "stop": system.stop,
                        "chat": system.chat,
                        "open": system.open,
                    }
                    await controls[command.action](command.run_id)
            case AnswerPrompt():
                await system.answer(
                    command.run_id,
                    prompt_id=command.prompt_id,
                    approved=command.approved,
                    answers=command.answers or None,
                )
            case ConfigureSession():
                if command.model is not None:
                    await system.set_model(command.run_id, command.model)
                if command.effort is not None:
                    await system.set_effort(command.run_id, command.effort)
                if command.learning_mode is not None:
                    await system.set_learning_mode(command.run_id, command.learning_mode)
                if command.budget is not None:
                    await system.set_budget(
                        command.run_id,
                        max_total_tokens=command.budget.max_total_tokens,
                        max_wall_seconds=command.budget.max_wall_seconds,
                    )
            case LearnExperience():
                return encode_record(await system.learn(command.run_id, command.feedback))
            case ResearchOperation():
                if command.action == "prepare":
                    return encode_record(await system.prepare_problem(command.run_id))
                return encode_record(
                    await system.finalize_problem(command.run_id, command.candidate_id)
                )
            case _:
                raise ValueError("Unsupported mutation")
        return {"run_id": command.run_id}

    def _summary(self, run_id: str) -> SessionSummary:
        record = self.system.detail(run_id)
        return SessionSummary(
            run_id,
            record.config.workspace,
            record.phase,
            record.status,
            record.goal.objective if record.goal else "",
            record.progress,
            record.last_error,
            record.learning,
            record.pending_prompts,
        )

    async def _read(self, command: Query) -> dict[str, Any]:
        system = self.system
        match command:
            case ReadRequest():
                task = self.tasks.get(command.request_id)
                seconds = _wait_seconds(command.wait_seconds)
                if task and seconds:
                    await asyncio.wait({task}, timeout=seconds)
                return encode_record(self._receipt(command.request_id))
            case ObserveSession():
                deadline = asyncio.get_running_loop().time() + _wait_seconds(command.wait_seconds)
                initial = self._summary(command.run_id)
                while True:
                    page = system.store.read_events(
                        command.run_id, command.cursor, include_tools=command.include_tools
                    )
                    state = self._summary(command.run_id)
                    if (
                        page.events
                        or page.cursor != command.cursor
                        or state != initial
                        or state.prompts
                        or state.status != SessionStatus.RUNNING
                        or asyncio.get_running_loop().time() >= deadline
                    ):
                        return {**encode_record(page), "session": encode_record(state)}
                    await asyncio.sleep(0.2)
            case ListSessions():
                return {
                    "sessions": [
                        encode_record(self._summary(r.run_id))
                        for r in system.store.list_sessions()[:50]
                    ]
                }
            case ReadExperience():
                documents = system.experience.documents()
                if not command.slug:
                    return {"documents": [encode_record(doc) for doc in documents]}
                document = next((doc for doc in documents if doc.slug == command.slug), None)
                if document is None:
                    raise ValueError("Unknown experience slug")
                return {
                    "document": encode_record(document),
                    "text": Path(document.path).read_text(),
                }
            case SessionQuery():
                if command.view == "models":
                    return {"models": await system.list_models(command.run_id)}
                if command.view == "candidates":
                    return {
                        "candidates": [
                            encode_record(c) for c in system.candidate_status(command.run_id)
                        ]
                    }
                return {"session": encode_record(system.detail(command.run_id))}
            case _:
                raise ValueError("Unsupported query")

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.system.close()
        self.journal.close()

