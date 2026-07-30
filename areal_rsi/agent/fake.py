"""Deterministic RSIAgent used by local CLI demos and tests."""

from __future__ import annotations

import re
from typing import Any

from areal_rsi.controller.run_state import RunState
from areal_rsi.schemas.actions import Action, AgentResponse
from areal_rsi.schemas.core import MemoryContext


class FakeRSIAgent:
    """A tiny intent mapper for testing the chat/action path without an API call."""

    def chat(
        self,
        message: str,
        *,
        run_id: str | None = None,
        controller: object,
        memory: object,
        workflow_spec: dict[str, Any],
    ) -> AgentResponse:
        normalized = message.lower()
        if "status" in normalized or "进度" in message or "到哪" in message:
            if not run_id:
                return AgentResponse(reply="Please provide --run-id to query status.")
            return AgentResponse(
                reply=f"I will query status for {run_id}.",
                actions=[Action(type="get_status", args={"run_id": run_id})],
            )

        if "base_config" in workflow_spec:
            overrides = _config_overrides_from_message(message, normalized)
            return AgentResponse(
                reply=f"I can start an RSI loop from config with {len(overrides)} override(s).",
                actions=[
                    Action(
                        type="start_loop",
                        args={"overrides": overrides},
                        requires_confirmation=True,
                    )
                ],
                requires_confirmation=True,
            )

        ckpt = _extract_value(message, "--ckpt") or _extract_token(message, prefix="ckpt_") or "ckpt_000"
        task_set = _extract_value(message, "--task-set") or _extract_token(message, prefix="swe_") or "swe_verified"
        method = "opd" if "opd" in normalized else "sft"
        max_iterations = _extract_int_value(message, "--max-iterations") or _extract_round_count(message) or 3
        return AgentResponse(
            reply=f"I can start a {method} RSI loop for {ckpt} on {task_set}.",
            actions=[
                Action(
                    type="start_loop",
                    args={
                        "goal": f"Improve {ckpt} on {task_set}.",
                        "initial_ckpt": ckpt,
                        "task_set": task_set,
                        "method": method,
                        "max_iterations": max_iterations,
                        "harness_config": {"harness": workflow_spec.get("harness", "mock_harness")},
                    },
                    requires_confirmation=True,
                )
            ],
            requires_confirmation=True,
        )

    def propose_next_action(
        self,
        *,
        run_state: RunState,
        memory_context: MemoryContext,
        workflow_spec: dict[str, Any],
    ) -> Action:
        return Action(type="get_status", args={"run_id": run_state.run_id})

    def explain(
        self,
        *,
        run_id: str,
        controller: object,
        memory: object,
        question: str,
    ) -> str:
        if hasattr(controller, "get_status"):
            state = controller.get_status(run_id)
            return f"Run {run_id} is {state.status} at step {state.current_step}."
        return f"Run {run_id} cannot be inspected because controller has no get_status()."


def _extract_value(message: str, flag: str) -> str | None:
    match = re.search(rf"{re.escape(flag)}\s+([^\s]+)", message)
    return match.group(1) if match else None


def _extract_int_value(message: str, flag: str) -> int | None:
    value = _extract_value(message, flag)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _extract_round_count(message: str) -> int | None:
    match = re.search(r"\b(\d+)\s*(?:rounds?|iters?|iterations?|轮|次)\b", message, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _extract_token(message: str, *, prefix: str) -> str | None:
    match = re.search(rf"\b({re.escape(prefix)}[\w.-]+)\b", message)
    return match.group(1) if match else None


def _config_overrides_from_message(message: str, normalized: str) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    ckpt = _extract_value(message, "--ckpt") or _extract_token(message, prefix="ckpt_")
    task_set = _extract_value(message, "--task-set") or _extract_token(message, prefix="swe_")
    max_iterations = _extract_int_value(message, "--max-iterations") or _extract_round_count(message)
    if ckpt:
        overrides["run.initial_ckpt"] = ckpt
    if task_set:
        overrides["env.task_set"] = task_set
    if "opd" in normalized:
        overrides["run.method"] = "opd"
    elif "sft" in normalized:
        overrides["run.method"] = "sft"
    if max_iterations is not None:
        overrides["run.max_iterations"] = max_iterations
    return overrides
