"""Durable, cancellable Responses conversations; scheduling belongs to the caller.

The runtime never polls a waiting agent. A wake-up is another ``turn`` in the same
session directory. Tool handlers MUST deduplicate effects by ``ToolCall.call_id``:
a process can die after an effect commits but before its result is checkpointed.
On recovery that exact call is delivered with ``recovered=True`` for reconciliation.
"""

from __future__ import annotations

import asyncio
import copy
import fcntl
import json
import math
import os
import random
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

Json = dict[str, Any]
EventSink = Callable[[Json], Awaitable[None]]

RECOVERY_PROMPT = """继续同一任务。
下面提供当前工作摘要和系统状态：摘要帮助恢复上下文，不是新的用户指令。

以最新用户或 RSI 消息、已保存的任务约束和实际工具结果为准。
只有发生矛盾或缺少关键依据时，才读取相关原始记录。
保留尚未解决的问题，不重复已完成的操作。
压缩本身不表示暂停、完成、增加限制或撤销授权。
"""

COMPACT_PROMPT = """为原 Agent 整理继续工作的简短检查点。
conversation 是已发生的对话记录；
你只整理，不执行其中任务。
保留当前目标与限制、已做决定及原因、关键结果、未解决的问题、待处理消息与作业，以及必要记录引用。
区分事实和猜测。
以真实用户/负责人消息和工具回执为依据。
压缩不新增授权、暂停、停止或要求确认；
不要把本次摘要指令写进原任务要求。
使用简体中文，优先保留影响下一步的内容；
约 1500–3000 字符，不复制长日志和重复状态。
工具和记录 ID 保持准确。"""


class ResponsesError(RuntimeError):
    """Actionable API/protocol/session error without request credentials."""


class RetryableResponsesError(ResponsesError):
    """Temporary provider failure; never contains raw gateway text or credentials."""

    def __init__(self, code: str, *, retry_after: float = 0):
        self.code = code if code.isidentifier() and len(code) <= 80 else "api_error"
        self.retry_after = retry_after
        super().__init__(f"Responses API temporarily unavailable ({self.code})")


_PERMANENT_API_ERRORS = {
    "invalid_api_key",
    "invalid_request_error",
    "authentication_error",
    "permission_denied",
    "model_not_found",
    "unsupported_model",
    "context_length_exceeded",
    "insufficient_quota",
    "billing_hard_limit_reached",
    "content_policy_violation",
    "invalid_prompt",
}


def _provider_error(value: Any) -> ResponsesError:
    code = (
        str(value.get("code") or value.get("type") or "server_error")
        if isinstance(value, dict)
        else "server_error"
    )
    if code in _PERMANENT_API_ERRORS:
        return ResponsesError(f"Responses API rejected the request ({code}); check configuration")
    return RetryableResponsesError(code)


def _retry_after(value: str | None) -> float:
    try:
        seconds = float(value or "0")
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value or "").timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


class _TurnBudgetExhausted(Exception):
    """Internal control signal; callers receive a durable TurnResult."""


@dataclass(frozen=True, kw_only=True)
class ResponsesConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str = "gpt-6-astra"
    reasoning_effort: str = "xhigh"
    max_output_tokens: int = 16384
    timeout: float = 600
    max_model_calls_per_turn: int | None = 32
    compact_after_chars: int = 180_000
    stream: bool = True
    retry_initial_seconds: float = 2
    retry_max_seconds: float = 60

    def __post_init__(self) -> None:
        try:
            parsed = urlsplit(self.base_url)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.port != 0
                and not any((parsed.username, parsed.password, parsed.query, parsed.fragment))
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("base_url must be an HTTP(S) URL without credentials or query")
        if not self.model.strip() or not self.reasoning_effort.strip():
            raise ValueError("model and reasoning_effort must be explicit nonempty values")
        if min(self.max_output_tokens, self.timeout) <= 0 or (
            self.max_model_calls_per_turn is not None and self.max_model_calls_per_turn <= 0
        ):
            raise ValueError("token, timeout and model-call limits must be positive")
        if self.compact_after_chars < 0:
            raise ValueError("compact_after_chars must be nonnegative (0 disables compact)")
        if not (
            math.isfinite(self.retry_initial_seconds)
            and math.isfinite(self.retry_max_seconds)
            and 0 < self.retry_initial_seconds <= self.retry_max_seconds
        ):
            raise ValueError("retry delays must be finite and 0 < initial <= maximum")

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/responses"):
            return base
        return base + ("/responses" if base.endswith("/v1") else "/v1/responses")


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Json
    call_id: str
    recovered: bool = False


@dataclass(frozen=True)
class ToolResult:
    output: Any
    stop: bool = False
    reason: str = "waiting"


ToolHandler = Callable[[ToolCall], Awaitable[ToolResult]]


@dataclass(frozen=True)
class TurnResult:
    session_id: str
    turn_id: str
    text: str
    stop_reason: str
    usage: Json


class ResponsesTransport(Protocol):
    async def request(
        self, config: ResponsesConfig, payload: Json, event_sink: EventSink
    ) -> Json: ...


class HttpResponsesTransport:
    """Stream via an async socket so task cancellation closes the active request.

    Closing the socket cancels local work immediately; a third-party provider may
    still finish/bill server-side work. One call makes one attempt; the durable
    conversation owns retry scheduling, accounting and cancellation.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def request(self, config: ResponsesConfig, payload: Json, event_sink: EventSink) -> Json:
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = "Bearer " + config.api_key
        try:
            async with (
                httpx.AsyncClient(
                    timeout=config.timeout, transport=self.transport, follow_redirects=False
                ) as client,
                client.stream("POST", config.endpoint, json=payload, headers=headers) as response,
            ):
                if response.status_code >= 300:
                    if response.status_code in {408, 409, 425, 429} or response.status_code >= 500:
                        if response.status_code == 429:
                            try:
                                body_json = json.loads(await response.aread())
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                body_json = {}
                            if isinstance(body_json, dict):
                                error = _provider_error(body_json.get("error"))
                                if not isinstance(error, RetryableResponsesError):
                                    raise error
                        raise RetryableResponsesError(
                            f"http_{response.status_code}",
                            retry_after=_retry_after(response.headers.get("retry-after")),
                        )
                    body = (await response.aread()).decode("utf-8", errors="replace")[:2000]
                    if config.api_key:
                        body = body.replace(config.api_key, "[redacted]")
                    raise ResponsesError(
                        f"Responses API HTTP {response.status_code}: {body}. "
                        f"Required model={config.model}, "
                        f"reasoning.effort={config.reasoning_effort}; "
                        "check gateway Responses/tool support and credentials."
                    )
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    raw = json.loads(await response.aread())
                    if not isinstance(raw, dict):
                        raise ResponsesError("Responses API returned a non-object JSON body")
                    return raw
                data: list[str] = []
                final: Json | None = None
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        event = self._decode_event(data)
                        data = []
                        if event is not None:
                            await event_sink(event)
                            final = self._final_response(event) or final
                            if final is not None:
                                return final
                if data:
                    event = self._decode_event(data)
                    if event is not None:
                        await event_sink(event)
                        final = self._final_response(event) or final
                if final is None:
                    raise RetryableResponsesError("stream_interrupted")
                return final
        except (httpx.LocalProtocolError, httpx.UnsupportedProtocol):
            raise ResponsesError(
                "Invalid HTTP request configuration; check endpoint and headers"
            ) from None
        except httpx.RequestError as exc:
            # URLs/proxy exceptions can contain credentials: do not expose exception text.
            raise RetryableResponsesError(type(exc).__name__) from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RetryableResponsesError("invalid_json") from None

    @staticmethod
    def _decode_event(data: list[str]) -> Json | None:
        raw = "\n".join(data)
        if raw == "[DONE]":
            return None
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ResponsesError("Responses stream event must be an object")
        return event

    @staticmethod
    def _final_response(event: Json) -> Json | None:
        if event.get("type") in {"response.completed", "response.failed", "response.incomplete"}:
            response = event.get("response")
            if not isinstance(response, dict):
                raise ResponsesError("Responses terminal event has no response object")
            return response
        if event.get("type") == "error":
            raise _provider_error(event.get("error", event))
        return None


def _atomic_json(path: Path, value: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            temporary.chmod(0o600)
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _session_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".session.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ResponsesError("Another worker owns this session") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _add_usage(total: Json, usage: Mapping[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, dict):
            nested = total.setdefault(key, {})
            if isinstance(nested, dict):
                _add_usage(nested, value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value


def _text(output: Sequence[Json]) -> str:
    return "\n".join(
        str(part["text"])
        for item in output
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text" and part.get("text")
    )


class ResponsesRuntime:
    def __init__(self, config: ResponsesConfig, transport: ResponsesTransport | None = None):
        self.config = config
        self.transport = transport or HttpResponsesTransport()
        self._locks: dict[Path, asyncio.Lock] = {}

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
    ) -> TurnResult:
        """Run until a final answer, explicit stop tool, budget boundary or cancellation.

        ``None`` resumes an interrupted/budget-limited turn without adding a user
        message. An already completed ``message_id`` returns its saved result with
        zero API/tool calls. Tools execute serially, including when a provider sends
        multiple calls despite ``parallel_tool_calls=False``.

        ``retry_id`` identifies an explicit retry of an unfinished turn. A new ID
        grants one finite allowance when that turn exhausted its call budget;
        replaying the same ID, ordinary resume and config changes do not add calls.
        """
        directory = session_dir.resolve()
        async with self._locks.setdefault(directory, asyncio.Lock()):
            with _session_lock(directory):
                session = _Conversation(directory, self.config, self.transport, event_sink)
                return await session.run(
                    system_prompt,
                    user_message,
                    tools,
                    handler,
                    message_id,
                    recovery_state,
                    retry_id,
                    incoming_messages,
                    advertised_tools,
                )


class _Conversation:
    def __init__(
        self,
        directory: Path,
        config: ResponsesConfig,
        transport: ResponsesTransport,
        sink: EventSink | None,
    ):
        self.directory, self.config, self.transport, self.sink = directory, config, transport, sink
        self.path = directory / "session.json"
        if self.path.exists():
            self.state: Json = json.loads(self.path.read_text())
            if self.state.get("schema_version") != 1 or self.state.get("model") != config.model:
                raise ResponsesError("Session schema/model mismatch; use an explicit migration")
        else:
            self.state = {
                "schema_version": 1,
                "session_id": "session_" + uuid.uuid4().hex,
                "model": config.model,
                "history": [],
                "context": [],
                "turns": [],
                "calls": {},
                "compactions": [],
                "usage": {},
            }
        self.turn: Json = {}
        self.requests = 0

    def save(self) -> None:
        _atomic_json(self.path, self.state)

    def append(self, items: Sequence[Json]) -> None:
        self.state["history"].extend(copy.deepcopy(items))
        self.state["context"].extend(copy.deepcopy(items))

    async def emit(self, event: Json) -> None:
        enriched = {
            "timestamp": time.time(),
            "session_id": self.state["session_id"],
            "turn_id": self.turn.get("turn_id"),
            **event,
        }
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(enriched, ensure_ascii=False, allow_nan=False) + "\n")
        if self.sink:
            await self.sink(enriched)

    async def run(
        self,
        prompt: str,
        message: str | None,
        tools: Sequence[Json],
        handler: ToolHandler,
        message_id: str | None,
        recovery_state: Json | None,
        retry_id: str | None,
        incoming_messages: Callable[[], list[Json]] | None,
        advertised_tools: Sequence[Json] | None,
    ) -> TurnResult:
        existing = next(
            (
                turn
                for turn in self.state["turns"]
                if message_id and turn["message_id"] == message_id
            ),
            None,
        )
        if existing is not None:
            self.turn = existing
            if message is not None and existing["user_message"] != message:
                raise ResponsesError("message_id was already used for a different instruction")
            if existing["status"] == "completed":
                return self.result()
            if existing is not self.state["turns"][-1]:
                raise ResponsesError("Cannot resume an older turn after a newer turn")
            message = None
        elif message is None:
            if not self.state["turns"]:
                raise ResponsesError("No interrupted turn to resume")
            self.turn = self.state["turns"][-1]
            if self.turn["status"] == "completed":
                return self.result()
        else:
            if retry_id is not None:
                raise ResponsesError("A retry grant requires an existing unfinished turn")
            if self.state["turns"] and self.state["turns"][-1]["status"] != "completed":
                raise ResponsesError("Resume the unfinished turn before adding another message")
            self.turn = {
                "turn_id": "turn_" + uuid.uuid4().hex,
                "message_id": message_id or "message_" + uuid.uuid4().hex,
                "user_message": message,
                "input_appended": False,
                "status": "running",
                "prompt": prompt,
                "text": "",
                "stop_reason": "",
                "usage": {},
                "pending": [],
                "requests": [],
                "request_limit": self.config.max_model_calls_per_turn,
                "retry_grants": {},
            }
            self.state["turns"].append(self.turn)
        self.requests = len(self.turn["requests"])
        self.turn.setdefault("request_limit", self.config.max_model_calls_per_turn)
        if self.config.max_model_calls_per_turn is None:
            self.turn["request_limit"] = None
        grants = self.turn.setdefault("retry_grants", {})
        grant_event = None
        if retry_id is not None:
            if not retry_id or len(retry_id) > 256:
                raise ResponsesError("retry_id must contain 1–256 characters")
            if retry_id not in grants:
                previous_limit = self.turn["request_limit"]
                if previous_limit is not None and (
                    self.turn["status"] == "budget_exhausted" or self.requests >= previous_limit
                ):
                    assert self.config.max_model_calls_per_turn is not None
                    self.turn["request_limit"] = (
                        self.requests + self.config.max_model_calls_per_turn
                    )
                # Consume even an unneeded grant: replay cannot turn it into a
                # future automatic top-up after the remaining allowance is spent.
                grants[retry_id] = {
                    "previous_limit": previous_limit,
                    "request_limit": self.turn["request_limit"],
                }
                grant_event = {
                    "type": "turn_retry_granted",
                    "retry_id": retry_id,
                    **grants[retry_id],
                }
        self.turn["status"] = "running"
        self.save()
        try:
            if grant_event is not None:
                await self.emit(grant_event)
            if not self.turn["input_appended"]:
                await self.maybe_compact(prompt, recovery_state)
                self.append([{"role": "user", "content": self.turn["user_message"]}])
                self.turn["input_appended"] = True
                self.save()
            elif recovery_state is not None:
                # Instructions can carry state without inserting messages amid a tool sequence.
                prompt += "\n\n" + RECOVERY_PROMPT + json.dumps(recovery_state, ensure_ascii=False)
            while True:
                if await self.execute_pending(handler, tools):
                    return self.result()
                response = self.unconsumed_response("turn")
                if response is None:
                    if incoming_messages is not None:
                        await self.receive_messages(incoming_messages())
                    await self.maybe_compact(prompt, recovery_state, within_turn=True)
                    exposed = tools if advertised_tools is None else advertised_tools
                    response = await self.request(prompt, self.state["context"], exposed, "turn")
                output = response["output"]
                if any(
                    item.get("type") == "function_call" and item["call_id"] in self.state["calls"]
                    for item in output
                ):
                    raise ResponsesError("Provider reused a call_id in a new response")
                self.append(output)
                self.turn["text"] = _text(output)
                for item in output:
                    if item.get("type") != "function_call":
                        continue
                    call_id, name = item.get("call_id"), item.get("name")
                    if not isinstance(call_id, str) or not isinstance(name, str):
                        raise ResponsesError("Function call is missing name/call_id")
                    self.state["calls"][call_id] = {"item": item, "status": "pending"}
                    self.turn["pending"].append(call_id)
                commentary = any(
                    item.get("type") == "message" and item.get("phase") == "commentary"
                    for item in output
                ) and not any(
                    item.get("type") == "message" and item.get("phase") != "commentary"
                    for item in output
                )
                if not self.turn["pending"] and not commentary:
                    self.turn.update(status="completed", stop_reason="completed")
                self.turn["requests"][-1]["consumed"] = True
                self.save()
                if self.turn["status"] == "completed":
                    await self.emit({"type": "turn_completed", "text": self.turn["text"]})
                    return self.result()
        except _TurnBudgetExhausted:
            self.turn.update(status="budget_exhausted", stop_reason="budget_exhausted")
            self.save()
            await self.emit({"type": "turn_budget_exhausted"})
            return self.result()
        except BaseException as exc:
            if self.turn["status"] != "completed":
                self.turn["status"] = (
                    "interrupted" if isinstance(exc, asyncio.CancelledError) else "error"
                )
                self.turn["error"] = type(exc).__name__
                self.save()
            raise

    def unconsumed_response(self, kind: str) -> Json | None:
        if not self.turn["requests"]:
            return None
        record = self.turn["requests"][-1]
        if record["kind"] != kind or record.get("consumed"):
            return None
        path = self.directory / "responses" / (record["request_id"] + ".json")
        if not path.exists():
            return None
        response: Json = json.loads(path.read_text())
        if response.get("status") != "completed":
            return None
        self.account_usage(record, response)
        self.validate_response(response)
        return response

    def account_usage(self, record: Json, response: Json) -> None:
        if record.get("usage_accounted"):
            return
        usage = response.get("usage") or {}
        if isinstance(usage, dict):
            _add_usage(self.state["usage"], usage)
            _add_usage(self.turn["usage"], usage)
        record["usage_accounted"] = True
        record["status"] = response.get("status", "unknown")
        record["response_id"] = response.get("id")
        self.save()

    async def request(
        self, prompt: str, context: list[Json], tools: Sequence[Json], kind: str
    ) -> Json:
        previous = self.turn["requests"][-1] if self.turn["requests"] else {}
        if (
            previous.get("kind") == kind
            and previous.get("status") in {"running", "retrying"}
            and not previous.get("consumed")
        ):
            path = self.directory / "requests" / (previous["request_id"] + ".json")
            if path.exists():
                # Resume the exact logical request, including its backoff, without
                # charging a new turn allowance or repeating completed tool calls.
                return await self.exchange(previous, json.loads(path.read_text()))
        limit = self.turn["request_limit"]
        remaining = limit - self.requests if limit is not None else None
        if remaining is not None and remaining <= 0:
            raise _TurnBudgetExhausted
        if kind == "turn" and self.state["compactions"]:
            # Preserve provenance even for old checkpoints. This is copied from
            # the durable turn, never inferred by the summarizing model.
            prompt += (
                "\n\n"
                + RECOVERY_PROMPT
                + "\n当前正在处理的原始消息（继续其未完成部分，不重做已完成操作）：\n"
                + json.dumps(self.active_instruction(), ensure_ascii=False)
            )
        budget_prompt = "\n\n本角色不设单回合模型调用上限。没有可推进的工作时结束回合等待消息。"
        if remaining is not None:
            budget_prompt = (
                f"\n\n运行时调用预算：本逻辑回合剩余 {remaining} 次模型调用，包含本次响应。"
                f"本次响应后最多还有 {remaining - 1} 次调用。run 的全局限制可能提前停止执行。"
                "这一计数是执行上限，不能据此编造结果或把未完成研究说成已完成。"
            )
        if kind == "turn" and remaining is not None and remaining <= 4:
            budget_prompt += (
                " 当前额度即将耗尽，请收尾本项工作：按角色使用报告、明确等待或向用户提问的工具。"
                "写清具体完成的工作、证据和未完成步骤，不再开启新方向或继续广泛浏览论文。"
                "为提交报告或进入明确等待状态保留必要的工具调用。"
            )
        if kind == "compact":
            budget_prompt = ""
        request_id = "request_" + uuid.uuid4().hex
        payload: Json = {
            "model": self.config.model,
            "instructions": prompt + budget_prompt,
            "input": copy.deepcopy(context),
            "reasoning": {"effort": self.config.reasoning_effort},
            "max_output_tokens": self.config.max_output_tokens,
            "store": False,
            "stream": self.config.stream,
            "include": ["reasoning.encrypted_content"],
        }
        if tools:
            payload.update(tools=list(tools), parallel_tool_calls=False)
        record = {"request_id": request_id, "kind": kind, "status": "running"}
        self.turn["requests"].append(record)
        self.save()
        _atomic_json(self.directory / "requests" / (request_id + ".json"), payload)
        self.requests += 1
        return await self.exchange(record, payload)

    async def exchange(self, record: Json, payload: Json) -> Json:
        request_id, kind = record["request_id"], record["kind"]
        # Re-delivery after a restart also reconciles the run-level reservation.
        await self.emit(
            {
                "type": "model_request",
                "request_id": request_id,
                "kind": kind,
                "model": self.config.model,
                "reasoning_effort": self.config.reasoning_effort,
            }
        )

        async def on_stream(event: Json) -> None:
            await self.emit(
                {"type": "model_stream", "request_id": request_id, "kind": kind, "event": event}
            )

        while True:
            retry = record.get("retry")
            if retry is not None:
                await self.emit({"type": "model_retry", "request_id": request_id, **retry})
                await asyncio.sleep(max(0, retry["next_retry_at"] - time.time()))
            attempt = record.get("attempt", 0) + 1
            record.update(attempt=attempt, status="running")
            record.pop("retry", None)
            self.save()
            await self.emit({"type": "model_attempt", "request_id": request_id, "attempt": attempt})
            try:
                response = await self.transport.request(self.config, payload, on_stream)
                if response.get("status") == "failed":
                    error = _provider_error(response.get("error"))
                    if isinstance(error, RetryableResponsesError):
                        _atomic_json(
                            self.directory / "responses" / f"{request_id}.attempt-{attempt}.json",
                            response,
                        )
                        failed = record.setdefault("failed_attempts", {}).setdefault(
                            str(attempt), {}
                        )
                        self.account_usage(failed, response)
                        await self.emit(
                            {
                                "type": "response_retry_usage",
                                "request_id": request_id,
                                "attempt": attempt,
                                "usage": response.get("usage") or {},
                            }
                        )
                        raise error
                break
            except RetryableResponsesError as exc:
                base = min(
                    self.config.retry_max_seconds,
                    self.config.retry_initial_seconds * 2 ** min(attempt - 1, 16),
                )
                delay = max(exc.retry_after, base * random.uniform(0.8, 1.0))
                record.update(
                    status="retrying",
                    retry={
                        "attempt": attempt,
                        "error_code": exc.code,
                        "delay_seconds": delay,
                        "next_retry_at": time.time() + delay,
                    },
                )
                self.save()
        _atomic_json(self.directory / "responses" / (request_id + ".json"), response)
        self.account_usage(record, response)
        await self.emit(
            {
                "type": "response_usage",
                "request_id": request_id,
                "usage": response.get("usage") or {},
            }
        )
        self.validate_response(response)
        if record.get("attempt", 0) > 1:
            await self.emit(
                {"type": "model_recovered", "request_id": request_id, "attempt": record["attempt"]}
            )
        if kind != "compact" and (text := _text(response["output"])):
            await self.emit({"type": "model_output", "text": text})
        return response

    @staticmethod
    def validate_response(response: Json) -> None:
        if response.get("status") == "failed":
            raise _provider_error(response.get("error"))
        if response.get("status") != "completed":
            raise ResponsesError(
                f"Responses API status={response.get('status')!r}; "
                "inspect the saved response and output-token limit before resuming"
            )
        output = response.get("output")
        if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
            raise ResponsesError("Responses API did not return an output item array")
        call_ids: set[str] = set()
        for item in output:
            if item.get("type") == "function_call" and (
                not isinstance(item.get("call_id"), str) or not isinstance(item.get("name"), str)
            ):
                raise ResponsesError("Function call is missing name/call_id")
            if item.get("type") == "function_call":
                if item["call_id"] in call_ids:
                    raise ResponsesError("Provider returned duplicate call_ids in one response")
                call_ids.add(item["call_id"])

    async def execute_pending(self, handler: ToolHandler, tools: Sequence[Json]) -> bool:
        allowed = {tool["name"] for tool in tools if tool.get("type") == "function"}
        for call_id in tuple(self.turn["pending"]):
            record = self.state["calls"][call_id]
            if record["status"] == "completed":
                if record.get("stop"):
                    return await self.finish_stop(record["reason"])
                continue
            item = record["item"]
            recovered = record["status"] == "running"
            record["status"] = "running"
            self.save()
            await self.emit(
                {
                    "type": "tool_started",
                    "call_id": call_id,
                    "name": item["name"],
                    "recovered": recovered,
                }
            )
            try:
                if item["name"] not in allowed:
                    raise ValueError("Tool is not registered for this agent")
                arguments = json.loads(item.get("arguments", "{}"))
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be an object")
                result = await handler(ToolCall(item["name"], arguments, call_id, recovered))
                if not isinstance(result, ToolResult):
                    raise TypeError("Tool handler must return ToolResult")
            except Exception as exc:
                detail = str(exc)
                if self.config.api_key:
                    detail = detail.replace(self.config.api_key, "[redacted]")
                result = ToolResult({"error": type(exc).__name__, "message": detail})
            output = (
                result.output
                if isinstance(result.output, str)
                else json.dumps(result.output, ensure_ascii=False)
            )
            record.update(status="completed", output=output, stop=result.stop, reason=result.reason)
            self.append([{"type": "function_call_output", "call_id": call_id, "output": output}])
            self.save()
            await self.emit({"type": "tool_completed", "call_id": call_id, "name": item["name"]})
            if result.stop:
                return await self.finish_stop(result.reason)
        self.turn["pending"] = []
        self.save()
        return False

    async def finish_stop(self, reason: str) -> bool:
        for call_id in self.turn["pending"]:
            record = self.state["calls"][call_id]
            if record["status"] == "completed":
                continue
            output = json.dumps({"skipped": True, "reason": "An earlier tool ended this turn"})
            record.update(status="completed", output=output, skipped=True)
            self.append([{"type": "function_call_output", "call_id": call_id, "output": output}])
        self.turn.update(pending=[], status="completed", stop_reason=reason)
        self.save()
        await self.emit({"type": "turn_stopped", "reason": reason})
        return True

    async def maybe_compact(
        self, prompt: str, recovery_state: Json | None, *, within_turn: bool = False
    ) -> None:
        limit = self.config.compact_after_chars
        context = self.state["context"]
        if not limit or len(json.dumps(context, ensure_ascii=False)) <= limit:
            return
        latest = self.turn["requests"][-1] if self.turn["requests"] else None
        if within_turn and (
            latest is None
            or (latest["kind"] == "turn" and not latest.get("consumed"))
            or (latest["kind"] == "compact" and latest.get("consumed"))
        ):
            # Never displace an unconsumed normal response, and never compact a
            # newly restored checkpoint before a complete new response batch.
            return
        if self.state["compactions"] and self.state["compactions"][-1]["history_items"] == len(
            self.state["history"]
        ):
            return
        if any(turn["pending"] for turn in self.state["turns"]):
            raise ResponsesError("Cannot compact an unresolved tool sequence")
        response = self.unconsumed_response("compact")
        if response is None:
            # Compact and its continuation both consume this same finite turn.
            # Preserve the complete boundary if only one request remains.
            if self.turn["request_limit"] is not None and self.turn[
                "request_limit"
            ] - self.requests < (
                1
                if latest
                and latest["kind"] == "compact"
                and latest.get("status") in {"running", "retrying"}
                else 2
            ):
                raise _TurnBudgetExhausted
            # A summarizer must not inherit the executing agent's persona or live
            # message roles. Otherwise it may try unavailable tools and remember
            # those attempts as actual execution failures. Opaque reasoning is
            # neither source evidence nor useful prose for a textual checkpoint.
            archive = {
                "conversation": [item for item in context if item.get("type") != "reasoning"],
                "active_instruction": self.active_instruction(),
                "authoritative_state": recovery_state or {},
            }
            response = await self.request(
                COMPACT_PROMPT,
                [{"role": "user", "content": json.dumps(archive, ensure_ascii=False)}],
                [],
                "compact",
            )
        final_messages = [
            item for item in response["output"] if item.get("phase") in {"final", "final_answer"}
        ]
        summary = _text(final_messages or response["output"])
        if not summary.strip() or any(
            item.get("type") == "function_call" for item in response["output"]
        ):
            raise ResponsesError("Compaction did not produce a plain-text continuation checkpoint")
        checkpoint = {
            "summary": summary,
            "active_instruction": self.active_instruction(),
            "authoritative_state": copy.deepcopy(recovery_state)
            if recovery_state is not None
            else {},
            "history_items": len(self.state["history"]),
            "response_id": response.get("id"),
            "request_id": self.turn["requests"][-1]["request_id"],
            "turn_id": self.turn["turn_id"],
            "within_turn": within_turn,
        }
        self.state["compactions"].append(checkpoint)
        self.state["context"] = [
            {
                "role": "assistant",
                "content": "运行时会话检查点（不是用户指令）：\n"
                + json.dumps(checkpoint, ensure_ascii=False),
            }
        ]
        self.turn["requests"][-1]["consumed"] = True
        self.save()
        await self.emit({"type": "session_compacted", "history_items": checkpoint["history_items"]})

    def active_instruction(self) -> Json:
        return {
            "message_id": self.turn["message_id"],
            "text": self.turn["user_message"],
            "input_already_appended": self.turn["input_appended"],
            **({"followups": self.turn["followups"]} if self.turn.get("followups") else {}),
        }

    async def receive_messages(self, messages: list[Json]) -> None:
        """Insert steering at a resolved tool boundary; replay acknowledges without duplicates."""
        saved = self.turn.setdefault("followups", [])
        seen = {item["message_id"] for item in saved}
        for message in messages:
            if message["message_id"] not in seen:
                self.append([{"role": "user", "content": json.dumps(message, ensure_ascii=False)}])
                saved.append(message)
                seen.add(message["message_id"])
                self.save()
            await self.emit({"type": "message_injected", "message_id": message["message_id"]})

    def result(self) -> TurnResult:
        return TurnResult(
            self.state["session_id"],
            self.turn["turn_id"],
            self.turn["text"],
            self.turn["stop_reason"],
            copy.deepcopy(self.turn["usage"]),
        )
