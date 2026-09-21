"""Transport-neutral research controls. No UI or core implementation dependencies."""

from __future__ import annotations

import types
from dataclasses import MISSING, dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol, get_args, get_origin, get_type_hints

from jsonschema import Draft202012Validator

Json = dict[str, Any]
JsonAction = Literal[
    "recover", "bootstrap", "list", "create", "detail", "delete", "chat",
    "model-settings", "collaboration", "start", "pause", "cancel", "resume", "retry", "stop",
]


@dataclass(frozen=True)
class ArtifactDownload:
    path: Path
    filename: str
    media_type: str = "application/zip"


class ResearchBackend(Protocol):
    async def call(self, action: str, params: Json) -> Json | ArtifactDownload: ...


def _object(properties: Json, required: tuple[str, ...] = ()) -> Json:
    return {
        "type": "object", "properties": properties,
        "required": list(required), "additionalProperties": False,
    }


_STRING = {"type": "string"}
_ID = {"type": "string", "pattern": "^[A-Za-z0-9_-]+$"}
_OPTIONAL_ID = {"anyOf": [_ID, {"type": "null"}]}
_MODEL = _object({
    **{name: _STRING for name in ("base_url", "model", "reasoning_effort", "backend")},
    "api_key": _STRING,
}, ("base_url", "model", "reasoning_effort"))
_COLLABORATION = _object({
    "mode": _STRING, "modules": {"type": "array", "items": _STRING, "uniqueItems": True},
}, ("mode", "modules"))


def _run(properties: Json | None = None, required: tuple[str, ...] = ()) -> Json:
    return _object({"run_id": _ID, **(properties or {})}, ("run_id", *required))


ACTIONS: dict[str, Json] = {
    "recover": _object({}),
    "bootstrap": _object({}),
    "list": _object({}),
    "create": _object({
        "config": {"type": "object"}, "run_id": _ID,
        "message": {"type": "string", "maxLength": 12000}, "message_id": _ID,
        "model_settings": _MODEL, "collaboration": _COLLABORATION,
        "conversation": {"type": "boolean"}, "simulated": {"type": "boolean"},
        "template": _STRING, "objective": _STRING, "instructions": _STRING,
        "max_agents": {"type": "integer", "minimum": 1},
    }),
    "detail": _run({
        "after": {"type": "integer", "minimum": 0},
        "event_limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 1000},
        "goal_before": {"type": ["integer", "null"], "minimum": 1},
        "goal_limit": {"type": "integer", "minimum": 1, "maximum": 50},
    }),
    **{name: _run() for name in ("start", "pause", "cancel", "resume", "delete")},
    "stop": _run({"reason": _STRING}),
    "chat": _run({
        "text": {"type": "string", "minLength": 1, "maxLength": 12000},
        "message_id": _OPTIONAL_ID, "reply_to": _OPTIONAL_ID, "recipient": _OPTIONAL_ID,
    }, ("text",)),
    "retry": _run({"agent_id": _ID}, ("agent_id",)),
    "model-settings": _run(_MODEL["properties"], tuple(_MODEL["required"])),
    "collaboration": _run(_COLLABORATION["properties"], tuple(_COLLABORATION["required"])),
    "export": _run({"candidate_id": _ID}),
}

HTTP_ACTIONS = ACTIONS.keys() - {"recover", "bootstrap", "list", "detail"}

# HTTP cannot select simulation/template controls; those belong to the operator CLI.
HTTP_CREATE_FIELDS = frozenset({
    "message", "message_id", "model_settings", "collaboration", "config", "run_id",
})


_VALIDATORS = {name: Draft202012Validator(spec) for name, spec in ACTIONS.items()}


def validate_request(action: str, params: Json) -> None:
    if action not in _VALIDATORS:
        raise ValueError(f"Unknown research action: {action}")
    error = next(_VALIDATORS[action].iter_errors(params), None)
    if error is not None:
        # JSON-schema messages can echo credentials or private user input.
        location = ".".join(str(part) for part in error.absolute_path) or "request"
        raise ValueError(f"Invalid {action} {location}: {error.validator}")


def http_command(action: str, params: Json, *, run_id: str | None = None) -> Json:
    if action not in HTTP_ACTIONS:
        raise ValueError("Action is not available over HTTP")
    if action == "create":
        if set(params) - HTTP_CREATE_FIELDS:
            raise ValueError("请发送研究需求，或提供完整的 config")
        value = {**params, "conversation": "config" not in params}
    else:
        if "run_id" in params:
            raise ValueError("run_id must be supplied by the URL")
        value = {**params, "run_id": run_id}
    validate_request(action, value)
    return value


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
