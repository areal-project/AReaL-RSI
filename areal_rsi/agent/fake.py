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
        if normalized in {"hi", "hello", "hey"} or message in {"你好", "你是谁", "你是誰"}:
            return AgentResponse(reply="我是 RSIAgent，负责协调 AReaL-RSI 运行。")
        if _is_status_question(message, normalized):
            if not run_id:
                return AgentResponse(reply="请先提供 run_id，或先启动一个 RSI 实验。")
            state = _controller_run_state(controller, run_id)
            if state is not None:
                return AgentResponse(
                    reply=(
                        f"Run {run_id} 当前状态是 {_state_value(state, 'status')}，"
                        f"iteration={_state_value(state, 'iteration')}，ckpt={_state_value(state, 'ckpt')}。"
                    )
                )
            return AgentResponse(reply=f"Run {run_id} 的状态暂时无法读取。")

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
        benchmark = (
            _extract_value(message, "--benchmark")
            or _extract_value(message, "--task-set")
            or _extract_token(message, prefix="swe_")
            or "swe_verified"
        )
        method = "opd" if "opd" in normalized else "sft"
        max_iterations = _extract_int_value(message, "--max-iterations") or _extract_round_count(message) or 3
        return AgentResponse(
            reply=f"I can start a {method} RSI loop for {ckpt} on {benchmark}.",
            actions=[
                Action(
                    type="start_loop",
                    args={
                        "goal": f"Improve {ckpt} on {benchmark}.",
                        "initial_ckpt": ckpt,
                        "benchmark": benchmark,
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
        return Action(type="inspect_run", args={"run_id": run_state.run_id})

    def explain(
        self,
        *,
        run_id: str,
        controller: object,
        memory: object,
        question: str,
    ) -> str:
        state = _controller_run_state(controller, run_id)
        if state is not None:
            return f"Run {run_id} is {_state_value(state, 'status')} at step {_state_value(state, 'current_step')}."
        return f"Run {run_id} cannot be inspected."


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
    benchmark = (
        _extract_value(message, "--benchmark")
        or _extract_value(message, "--task-set")
        or _extract_token(message, prefix="swe_")
    )
    max_iterations = _extract_int_value(message, "--max-iterations") or _extract_round_count(message)
    if ckpt:
        overrides["run.initial_ckpt"] = ckpt
    if benchmark:
        overrides["env.benchmark"] = benchmark
    if "opd" in normalized:
        overrides["run.method"] = "opd"
    elif "sft" in normalized:
        overrides["run.method"] = "sft"
    if max_iterations is not None:
        overrides["run.max_iterations"] = max_iterations
    return overrides


def _is_status_question(message: str, normalized: str) -> bool:
    return (
        "status" in normalized
        or "进度" in message
        or "到哪" in message
        or "状态" in message
        or "跑完" in message
        or "完成" in message
    )


def _controller_run_state(controller: object, run_id: str) -> Any:
    if hasattr(controller, "get_status"):
        return controller.get_status(run_id)
    if hasattr(controller, "_load_state"):
        return controller._load_state(run_id)
    if hasattr(controller, "inspect_run"):
        return controller.inspect_run(run_id)
    return None


def _state_value(state: Any, key: str) -> Any:
    if isinstance(state, dict):
        return state.get(key)
    return getattr(state, key, None)
