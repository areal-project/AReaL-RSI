"""RSIAgent action tool definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from areal_rsi.schemas.actions import Action


@dataclass(slots=True)
class ActionToolCall:
    """Parsed action tool call used inside RSIAgent."""

    name: str
    arguments: dict[str, Any]


def rsi_action_tools() -> list[dict[str, Any]]:
    """Function tools exposed by RSIAgent for user-facing run control."""

    run_id_schema = {
        "type": "object",
        "properties": {"run_id": {"type": "string"}},
        "required": ["run_id"],
        "additionalProperties": False,
    }
    return [
        {
            "type": "function",
            "name": "start_loop",
            "description": "Start an RSI loop from the loaded base config with optional user overrides.",
            "parameters": {
                "type": "object",
                "properties": {
                    "overrides": {
                        "type": "object",
                        "description": (
                            "Dot-path config overrides inferred from the user request, "
                            "for example run.initial_ckpt, env.task_set, run.max_iterations, run.method."
                        ),
                        "additionalProperties": True,
                    },
                    "initial_ckpt": {"type": "string", "description": "Initial checkpoint to improve."},
                    "task_set": {"type": "string", "description": "EnvArena benchmark or task set name."},
                    "method": {"type": "string", "enum": ["sft", "opd"], "description": "Training method."},
                    "max_iterations": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Maximum number of RSI loop iterations.",
                    },
                    "harness_config": {
                        "type": "object",
                        "properties": {"harness": {"type": "string"}},
                        "required": ["harness"],
                        "additionalProperties": False,
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "inspect_run",
            "description": "Inspect detailed state and metadata for an RSI run.",
            "parameters": run_id_schema,
        },
        {
            "type": "function",
            "name": "resume_run",
            "description": "Resume a stopped RSI run.",
            "parameters": run_id_schema,
        },
        {
            "type": "function",
            "name": "stop_run",
            "description": "Stop an RSI run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "reason": {"type": ["string", "null"]},
                },
                "required": ["run_id"],
                "additionalProperties": False,
            },
        },
    ]


def action_from_tool_call(tool_call: ActionToolCall) -> Action:
    """Convert an action tool call into the local controller action contract."""

    if tool_call.name not in {"start_loop", "inspect_run", "resume_run", "stop_run"}:
        raise ValueError(f"Unsupported RSI action tool: {tool_call.name}")
    return Action(
        type=tool_call.name,
        args=dict(tool_call.arguments),
        requires_confirmation=tool_call.name in {"start_loop", "stop_run"},
        metadata={"source": "openai_tool_call"},
    )


def reply_for_actions(actions: list[Action]) -> str:
    """Generate a short local reply for selected action tools."""

    if not actions:
        return "No action selected."
    action = actions[0]
    if action.type == "start_loop":
        overrides = action.args.get("overrides")
        if isinstance(overrides, dict) and overrides:
            return f"Prepared RSI loop from config with {len(overrides)} override(s)."
        if action.args.get("initial_ckpt") and action.args.get("task_set"):
            return (
                f"Prepared RSI loop for {action.args.get('initial_ckpt')} "
                f"on {action.args.get('task_set')}."
            )
        return "Prepared RSI loop from config."
    if action.type == "inspect_run":
        return f"Inspecting run {action.args.get('run_id')}."
    if action.type == "resume_run":
        return f"Resuming run {action.args.get('run_id')}."
    if action.type == "stop_run":
        return f"Stopping run {action.args.get('run_id')}."
    return f"Prepared action {action.type}."
