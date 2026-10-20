"""Transport-neutral research controls. No UI or core implementation dependencies."""

from __future__ import annotations

import json
import re
import types
from contextlib import suppress
from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Literal, TypeVar, cast, get_args, get_origin, get_type_hints

from jsonschema import Draft202012Validator

from areal_rsi.api.research import ProblemBinding, ReasoningEffort

Json = dict[str, Any]
T = TypeVar("T")


@dataclass(slots=True)
class TaskProposal:
    objective: str
    instructions: str
    acceptance: list[str]


@dataclass(slots=True)
class GoalUpdate:
    status: Literal["active", "completed", "blocked"]
    summary: str
    next_step: str = ""
    evidence: list[str] = field(default_factory=list)
    question: str = ""


@dataclass(slots=True)
class StartGoal:
    pass


@dataclass(slots=True)
class CandidateProposal:
    candidate_id: str
    parent_id: str
    hypothesis: str
    recipe: str


@dataclass(slots=True)
class CandidateQuery:
    candidate_id: str = ""


@dataclass(slots=True)
class CandidateEvaluation:
    candidate_id: str
    retry: bool = False


@dataclass(slots=True)
class SelectCandidate:
    candidate_id: str


@dataclass(slots=True)
class LessonDraft:
    slug: str
    title: str
    description: str
    body: str


@dataclass(slots=True)
class TerminalInput:
    action: Literal["submit", "answer", "pause", "close"]
    text: str = ""
    request_id: int | str | None = None
    approved: bool | None = None
    answers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class CreateSession:
    request_id: str
    workspace: str
    message: str
    context: str = ""
    mode: Literal["rsi", "chat"] = "rsi"
    config_file: str = ""
    env_file: str = ""
    problem: ProblemBinding | None = None


@dataclass(slots=True)
class SendMessage:
    request_id: str
    run_id: str
    text: str
    images: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ControlSession:
    request_id: str
    run_id: str
    action: Literal["rsi", "start", "pause", "resume", "stop", "chat", "open"]
    text: str = ""


@dataclass(slots=True)
class AnswerPrompt:
    request_id: str
    run_id: str
    prompt_id: str
    approved: bool | None = None
    answers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class GoalBudget:
    max_total_tokens: int | None = None
    max_wall_seconds: float | None = None


@dataclass(slots=True)
class ConfigureSession:
    request_id: str
    run_id: str
    model: str | None = None
    effort: ReasoningEffort | None = None
    learning_mode: Literal["auto", "guided"] | None = None
    budget: GoalBudget | None = None


@dataclass(slots=True)
class LearnExperience:
    request_id: str
    run_id: str
    feedback: str = ""


@dataclass(slots=True)
class ResearchOperation:
    request_id: str
    run_id: str
    action: Literal["prepare", "finalize"]
    candidate_id: str = ""


@dataclass(slots=True)
class ObserveSession:
    run_id: str
    cursor: int = 0
    wait_seconds: float = 0
    include_tools: bool = False


@dataclass(slots=True)
class ReadRequest:
    request_id: str
    wait_seconds: float = 0


@dataclass(slots=True)
class ListSessions:
    pass


@dataclass(slots=True)
class ReadExperience:
    slug: str = ""


@dataclass(slots=True)
class SessionQuery:
    run_id: str
    view: Literal["detail", "models", "candidates"] = "detail"


Mutation = (
    CreateSession
    | SendMessage
    | ControlSession
    | AnswerPrompt
    | ConfigureSession
    | LearnExperience
    | ResearchOperation
)
Query = ObserveSession | ReadRequest | ListSessions | ReadExperience | SessionQuery


@dataclass(slots=True)
class ServiceShutdown:
    force: bool = False


@dataclass(frozen=True, slots=True)
class Operation:
    arguments: type[Any]
    description: str
    mutating: bool = False


# One public contract powers CLI JSON validation and MCP schemas. Agent-only goal
# completion/candidate tools deliberately remain in coordination.tools.control.
OPERATIONS = {
    "rsi_create": Operation(
        CreateSession,
        "Create an independent session from a workspace, "
        "user request and optional host context summary.",
        True,
    ),
    "rsi_send": Operation(SendMessage, "Forward a user message or steer the current run.", True),
    "rsi_control": Operation(
        ControlSession, "Enter RSI, start, pause, resume, stop, or chat.", True
    ),
    "rsi_answer": Operation(AnswerPrompt, "Relay the user's answer to a current prompt_id.", True),
    "rsi_configure": Operation(
        ConfigureSession,
        "Set model, effort, learning mode or budget. Omitted settings stay unchanged.",
        True,
    ),
    "rsi_learn": Operation(
        LearnExperience, "Start experience curation with optional feedback.", True
    ),
    "rsi_research": Operation(
        ResearchOperation,
        "Prepare a bound research problem or finalize "
        "a selected candidate; runs in the background.",
        True,
    ),
    "rsi_observe": Operation(
        ObserveSession,
        "Replay progress after a cursor and read current "
        "state/questions. Wait at most 20 seconds; disconnection does not stop work.",
    ),
    "rsi_request": Operation(
        ReadRequest,
        "Read an accepted request's result. Reuse its ID after "
        "a timeout; interrupted operations are never automatically replayed.",
    ),
    "rsi_list": Operation(ListSessions, "List existing sessions without starting model work."),
    "rsi_experience": Operation(ReadExperience, "List experience documents, or read one by slug."),
    "rsi_query": Operation(SessionQuery, "Read session details, available models, or candidates."),
}


@dataclass(slots=True)
class ServiceRequest:
    operation: str
    arguments: Json = field(default_factory=dict)
    protocol: int = 1
    client_version: str = ""


def record_schema(kind: type[Any]) -> Json:
    return _type_schema(kind)


def encode_record(value: Any) -> Json:
    """Encode a named record at a JSON boundary."""
    return cast(Json, json.loads(json.dumps(asdict(value), ensure_ascii=False, allow_nan=False)))


def decode_record(kind: type[T], value: Any) -> T:
    """Validate once, then use typed fields throughout the implementation."""
    validate_record(kind, value)
    return cast(T, _decode(kind, value))


def _decode(kind: Any, value: Any) -> Any:
    origin, args = get_origin(kind), get_args(kind)
    if origin is types.UnionType:
        for option in args:
            if Draft202012Validator(_type_schema(option)).is_valid(value):
                return _decode(option, value)
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    if isinstance(kind, type) and is_dataclass(kind):
        hints = get_type_hints(kind)
        return kind(**{name: _decode(hints[name], item) for name, item in value.items()})
    if origin in (list, tuple):
        items = [_decode(args[0], item) for item in value]
        return tuple(items) if origin is tuple else items
    if origin is dict:
        return {name: _decode(args[1], item) for name, item in value.items()}
    return value


def _object(properties: Json, required: tuple[str, ...] = ()) -> Json:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _type_schema(kind: Any) -> Json:
    """Validate fixed contracts; extensible Any/JSON payloads stay at their boundary."""
    origin, args = get_origin(kind), get_args(kind)
    if origin is types.UnionType:
        return {"anyOf": [_type_schema(item) for item in args]}
    if origin is Literal:
        return {"enum": list(args)}
    if isinstance(kind, type) and issubclass(kind, Enum):
        return {"enum": [item.value for item in kind]}
    if isinstance(kind, type) and is_dataclass(kind):
        hints = get_type_hints(kind)
        return _object(
            {item.name: _type_schema(hints[item.name]) for item in fields(kind)},
            tuple(
                item.name
                for item in fields(kind)
                if item.default is MISSING and item.default_factory is MISSING
            ),
        )
    if origin in (list, tuple):
        if origin is tuple and len(args) > 1 and args[-1] is not Ellipsis:
            return {
                "type": "array",
                "prefixItems": [_type_schema(item) for item in args],
                "minItems": len(args),
                "maxItems": len(args),
            }
        return {"type": "array", "items": _type_schema(args[0])}
    if origin is dict:
        return {"type": "object", "additionalProperties": _type_schema(args[1])}
    primitive = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        type(None): "null",
    }
    return {"type": primitive[kind]} if kind in primitive else {}


_RECORD_VALIDATORS: dict[type[Any], Draft202012Validator] = {}


def validate_record(kind: type[Any], value: Any) -> None:
    if kind not in _RECORD_VALIDATORS:
        _RECORD_VALIDATORS[kind] = Draft202012Validator(_type_schema(kind))
    error = next(_RECORD_VALIDATORS[kind].iter_errors(value), None)
    if error is not None:
        location = ".".join(str(part) for part in error.absolute_path) or "request"
        raise ValueError(f"Invalid {kind.__name__} {location}: {error.validator}")


def public_tool_text(value: Any, limit: int = 6000) -> str:
    """Bounded public excerpts; redact before encoding to preserve structured summaries."""

    def redact(text: str) -> str:
        text = re.sub(
            r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
            "[私钥已隐藏]",
            text,
            flags=re.S,
        )
        text = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", r"\1 [已隐藏]", text)
        text = re.sub(
            r"""(?ix)(["']?(?:[\w-]*(?:password|secret|token|api[_-]?key|private[_-]?key)
            |authorization|cookie|set-cookie)["']?\s*[:=]\s*)
            (?:"[^"\n]*"|'[^'\n]*'|[^\s,;"'\\]+)""",
            r"\1[已隐藏]",
            text,
        )
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[密钥已隐藏]", text)
        return re.sub(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+", "[令牌已隐藏]", text)

    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                key: "[已隐藏]"
                if re.search(
                    r"(?i)(password|secret|api.?key|private.?key|authorization|cookie|credential|^env$"
                    r"|(?:^|[_-])(?:access|refresh|id)?[_-]?token$)",
                    str(key),
                )
                else clean(child)
                for key, child in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        return redact(item) if isinstance(item, str) else item

    if value is None:
        return ""
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        with suppress(ValueError):
            value = json.loads(value)
    cleaned = clean(value)
    text = cleaned if isinstance(cleaned, str) else json.dumps(cleaned, ensure_ascii=False)
    return text[:limit] + ("\n…（摘要已截断）" if len(text) > limit else "")
