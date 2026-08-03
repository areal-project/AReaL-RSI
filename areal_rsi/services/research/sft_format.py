"""Structural validation for ResearchAgent-produced SFT JSONL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SFTFormatError(ValueError):
    """Raised when candidate SFT data does not match the reference contract."""


def describe_sft_reference(reference_path: str | Path) -> dict[str, Any]:
    """Return a compact contract that a researcher can follow without loading a huge record."""

    reference = _first_jsonl_record(Path(reference_path), label="reference")
    neutral_defaults: dict[str, Any] = {}
    field_types: dict[str, str] = {}
    for key, value in reference.items():
        field_types[key] = _json_type_name(value)
        if key == "messages":
            continue
        neutral_defaults[key] = _neutral_value(value)
    return {
        "required_top_level_fields": field_types,
        "neutral_defaults_except_messages": neutral_defaults,
        "messages_contract": {
            "non_empty": True,
            "first_role": "system",
            "string_content_roles": ["system", "user", "assistant"],
            "tool_calls_require_matching_tool_results": True,
        },
        "tools_contract": "A list; [] is structurally valid.",
    }


def validate_sft_jsonl(
    candidate_path: str | Path,
    *,
    reference_path: str | Path,
) -> dict[str, Any]:
    """Validate candidate records against one known-good reference record."""

    reference = _first_jsonl_record(Path(reference_path), label="reference")
    candidate = Path(candidate_path)
    if not candidate.exists():
        raise SFTFormatError(f"Candidate SFT JSONL does not exist: {candidate}")

    record_count = 0
    with candidate.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SFTFormatError(
                    f"Candidate line {line_number} is not valid JSON: {exc}"
                ) from exc
            _validate_record(record, reference, line_number=line_number)
            record_count += 1
    if record_count == 0:
        raise SFTFormatError("Candidate SFT JSONL contains no records.")
    return {
        "valid": True,
        "record_count": record_count,
        "candidate_path": str(candidate.resolve()),
        "reference_path": str(Path(reference_path).resolve()),
        "reference_top_level_keys": list(reference),
    }


def _first_jsonl_record(path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists():
        raise SFTFormatError(f"{label.capitalize()} SFT JSONL does not exist: {path}")
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SFTFormatError(
                    f"{label.capitalize()} line {line_number} is not valid JSON: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise SFTFormatError(
                    f"{label.capitalize()} record must be a JSON object."
                )
            return payload
    raise SFTFormatError(f"{label.capitalize()} SFT JSONL contains no records.")


def _validate_record(
    record: Any, reference: dict[str, Any], *, line_number: int
) -> None:
    prefix = f"Candidate line {line_number}"
    if not isinstance(record, dict):
        raise SFTFormatError(f"{prefix} must be a JSON object.")
    missing = [key for key in reference if key not in record]
    if missing:
        raise SFTFormatError(f"{prefix} is missing reference fields: {missing}")
    for key, expected in reference.items():
        actual = record[key]
        if expected is not None and not isinstance(actual, type(expected)):
            raise SFTFormatError(
                f"{prefix} field {key!r} must be {type(expected).__name__}, "
                f"got {type(actual).__name__}."
            )
    _validate_messages(record.get("messages"), prefix=prefix)
    _validate_tools(record.get("tools"), prefix=prefix)


def _validate_messages(messages: Any, *, prefix: str) -> None:
    if not isinstance(messages, list) or not messages:
        raise SFTFormatError(f"{prefix} messages must be a non-empty list.")
    first = messages[0]
    if not isinstance(first, dict) or first.get("role") != "system":
        raise SFTFormatError(f"{prefix} messages[0] must be the system message.")
    if not isinstance(first.get("content"), str):
        raise SFTFormatError(f"{prefix} messages[0].content must be a string.")

    tool_calls: set[str] = set()
    tool_results: set[str] = set()
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise SFTFormatError(f"{prefix} messages[{index}] must be an object.")
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            raise SFTFormatError(
                f"{prefix} messages[{index}] has invalid role {role!r}."
            )
        if role in {"system", "user", "assistant"} and not isinstance(
            message.get("content"), str
        ):
            raise SFTFormatError(
                f"{prefix} messages[{index}].content must be a string for role {role}."
            )
        if role == "assistant" and "tool_calls" in message:
            calls = message["tool_calls"]
            if not isinstance(calls, list):
                raise SFTFormatError(
                    f"{prefix} messages[{index}].tool_calls must be a list."
                )
            for call_index, call in enumerate(calls):
                call_id = _validate_tool_call(
                    call,
                    prefix=f"{prefix} messages[{index}].tool_calls[{call_index}]",
                )
                if call_id in tool_calls:
                    raise SFTFormatError(
                        f"{prefix} duplicates tool call id {call_id!r}."
                    )
                tool_calls.add(call_id)
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise SFTFormatError(
                    f"{prefix} messages[{index}].tool_call_id must be a non-empty string."
                )
            if not isinstance(message.get("content"), str):
                raise SFTFormatError(
                    f"{prefix} messages[{index}].content must be a string."
                )
            tool_results.add(call_id)

    orphaned = sorted(tool_results - tool_calls)
    missing_results = sorted(tool_calls - tool_results)
    if orphaned:
        raise SFTFormatError(f"{prefix} has tool results without calls: {orphaned}")
    if missing_results:
        raise SFTFormatError(
            f"{prefix} has tool calls without results: {missing_results}"
        )


def _validate_tool_call(call: Any, *, prefix: str) -> str:
    if not isinstance(call, dict):
        raise SFTFormatError(f"{prefix} must be an object.")
    call_id = call.get("id")
    if not isinstance(call_id, str) or not call_id:
        raise SFTFormatError(f"{prefix}.id must be a non-empty string.")
    if call.get("type") != "function":
        raise SFTFormatError(f"{prefix}.type must be 'function'.")
    function = call.get("function")
    if not isinstance(function, dict):
        raise SFTFormatError(f"{prefix}.function must be an object.")
    if not isinstance(function.get("name"), str) or not function["name"]:
        raise SFTFormatError(f"{prefix}.function.name must be a non-empty string.")
    if not isinstance(function.get("arguments"), dict):
        raise SFTFormatError(f"{prefix}.function.arguments must be an object.")
    return call_id


def _validate_tools(tools: Any, *, prefix: str) -> None:
    if not isinstance(tools, list):
        raise SFTFormatError(f"{prefix} tools must be a list.")
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise SFTFormatError(f"{prefix} tools[{index}] must be a function tool.")
        function = tool.get("function")
        if not isinstance(function, dict):
            raise SFTFormatError(f"{prefix} tools[{index}].function must be an object.")
        if not isinstance(function.get("name"), str) or not function["name"]:
            raise SFTFormatError(
                f"{prefix} tools[{index}].function.name must be a non-empty string."
            )
        if not isinstance(function.get("parameters"), dict):
            raise SFTFormatError(
                f"{prefix} tools[{index}].function.parameters must be an object."
            )


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _neutral_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0
    if isinstance(value, float):
        return 0.0
    if isinstance(value, str):
        return ""
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    return None

