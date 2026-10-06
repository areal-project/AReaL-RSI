"""Strict JSON boundary for immutable RSI records and named control inputs."""

from __future__ import annotations

import dataclasses
import json
import re
import types
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, Literal, TypeVar, cast, get_args, get_origin, get_type_hints

T = TypeVar("T")


class WorkspaceOperation(StrEnum):
    LIST = "list"
    READ = "read"
    WRITE = "write"
    REPLACE = "replace"
    CHECK = "check"


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceRequest:
    """A bounded file operation within an explicitly assigned candidate workspace."""

    operation: WorkspaceOperation
    path: str
    content: str = ""
    search: str = ""
    start_line: int = 1
    max_lines: int = 200

    def __post_init__(self) -> None:
        if (
            len(self.content.encode()) > 2 * 1024 * 1024
            or self.start_line < 1
            or not 1 <= self.max_lines <= 500
            or (self.operation is WorkspaceOperation.REPLACE and not self.search)
        ):
            raise ValueError("Invalid workspace operation limits")


def safe_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError(f"Unsafe RSI identifier: {value!r}")
    return value


def encode(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        hints = get_type_hints(type(value))
        result = {}
        for field in dataclasses.fields(value):
            item = getattr(value, field.name)
            if hints[field.name] is float and type(item) is int:
                item = float(item)
            result[field.name] = encode(item)
        return result
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode(item) for item in value]
    return value


def decode(kind: type[T], value: Any) -> T:
    """Reject missing/extra fields and malformed primitive values at disk boundaries."""

    return cast(T, _decode(kind, value))


def _decode(kind: Any, value: Any) -> Any:
    origin = get_origin(kind)
    if origin is types.UnionType:
        options = get_args(kind)
        if value is None and type(None) in options:
            return None
        nonnull = [option for option in options if option is not type(None)]
        if len(nonnull) == 1:
            return _decode(nonnull[0], value)
        raise ValueError("Ambiguous JSON union")
    if origin is tuple:
        if not isinstance(value, list):
            raise ValueError("Expected JSON list for tuple")
        options = get_args(kind)
        if len(options) == 2 and options[1] is Ellipsis:
            return tuple(_decode(options[0], item) for item in value)
        if len(options) != len(value):
            raise ValueError("Tuple length differs from contract")
        return tuple(_decode(option, item) for option, item in zip(options, value, strict=True))
    if origin is list:
        if not isinstance(value, list):
            raise ValueError("Expected JSON list")
        return [_decode(get_args(kind)[0], item) for item in value]
    if origin is dict:
        key_kind, item_kind = get_args(kind)
        if key_kind is not str or not isinstance(value, dict):
            raise ValueError("Expected a string-keyed JSON object")
        return {key: _decode(item_kind, item) for key, item in value.items()}
    if origin is Literal:
        if value not in get_args(kind):
            raise ValueError("Unexpected literal value")
        return value
    if isinstance(kind, type) and issubclass(kind, Enum):
        return kind(value)
    if isinstance(kind, type) and dataclasses.is_dataclass(kind):
        if not isinstance(value, dict):
            raise ValueError(f"Expected {kind.__name__} object")
        hints = get_type_hints(kind)
        fields = {field.name for field in dataclasses.fields(kind)}
        if set(value) != fields:
            raise ValueError(f"{kind.__name__} fields differ from contract")
        return kind(**{name: _decode(hints[name], value[name]) for name in fields})
    if kind is Any:
        return value
    if kind is float and type(value) in (int, float):
        return float(value)
    if kind in (str, int, bool, float) and type(value) is kind:
        return value
    if kind is type(None) and value is None:
        return value
    raise ValueError(f"Unexpected JSON value for {kind}")


def json_bytes(value: Any) -> bytes:
    return json.dumps(encode(value), ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def read_record(path: Path, kind: type[T]) -> T:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Record must be a regular file: {path}")
    raw = path.read_bytes()
    result = decode(kind, json.loads(raw))
    if raw != json_bytes(result):
        raise ValueError(f"Record is not in canonical form: {path}")
    return result
