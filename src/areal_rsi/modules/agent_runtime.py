"""Backend-neutral contract for one durable agent conversation."""

from __future__ import annotations

import fcntl
import math
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

Json = dict[str, Any]
EventSink = Callable[[Json], Awaitable[None]]


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


class AgentRuntime(Protocol):
    backend: str

    def for_interaction(self) -> AgentRuntime: ...

    def has_session(self, session_dir: Path) -> bool: ...

    def configuration(self) -> Json: ...

    async def reconfigure_session(self, session_dir: Path, revision: str) -> None: ...

    async def close(self) -> None: ...

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
    ) -> TurnResult: ...


class AgentRuntimeError(RuntimeError):
    """Actionable runtime error without request credentials."""


@dataclass(frozen=True, kw_only=True)
class RuntimeConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str = "gpt-6-astra"
    reasoning_effort: str = "xhigh"
    max_output_tokens: int = 16384
    timeout: float = 600
    max_model_calls_per_turn: int | None = 32
    # Explicit character limits remain a compatibility override; 0 disables compact.
    compact_after_chars: int | None = None
    # None selects a model-aware operating budget; explicit gateway limits win.
    context_window_tokens: int | None = None
    compact_keep_recent_tokens: int = 8_000
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
        if self.compact_after_chars is not None and self.compact_after_chars < 0:
            raise ValueError("compact_after_chars must be nonnegative (0 disables compact)")
        if (
            self.effective_context_window_tokens <= self.max_output_tokens
            or self.compact_keep_recent_tokens < 0
        ):
            raise ValueError(
                "context window must reserve output space; recent context must be nonnegative"
            )
        if not (
            math.isfinite(self.retry_initial_seconds)
            and math.isfinite(self.retry_max_seconds)
            and 0 < self.retry_initial_seconds <= self.retry_max_seconds
        ):
            raise ValueError("retry delays must be finite and 0 < initial <= maximum")

    @property
    def effective_context_window_tokens(self) -> int:
        if self.context_window_tokens is not None:
            return self.context_window_tokens
        # Astra supports a larger window. Use a conservative operating budget,
        # below the 272K long-context pricing threshold, rather than its maximum.
        # https://developers.openai.com/api/docs/models/gpt-6-astra
        return 256_000 if self.model == "gpt-6-astra" else 128_000

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/responses"):
            return base
        return base + ("/responses" if base.endswith("/v1") else "/v1/responses")



@contextmanager
def session_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".session.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AgentRuntimeError("Another worker owns this session") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
