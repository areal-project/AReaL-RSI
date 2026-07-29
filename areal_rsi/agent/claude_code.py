"""Claude Code SDK backed agent scaffolding."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from areal_rsi.schemas.actions import Action, AgentResponse
from areal_rsi.schemas.core import MemoryContext

if TYPE_CHECKING:
    from areal_rsi.controller.run_state import RunState


@dataclass(slots=True)
class ClaudeCodeAgentConfig:
    """Configuration passed to Claude Code SDK query calls."""

    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: str | None = None
    max_turns: int | None = 3
    cwd: str | Path | None = None
    model: str | None = None


class ClaudeCodeClient:
    """Small lazy wrapper around claude-agent-sdk.

    The dependency is optional so the rest of AReaL-RSI can run local mock
    tests without requiring Claude credentials or the SDK package.
    """

    def __init__(self, config: ClaudeCodeAgentConfig):
        self.config = config

    async def complete(self, prompt: str) -> str:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError as exc:
            raise RuntimeError(
                "Claude Code agent support requires the optional dependency "
                "`claude-agent-sdk`. Install with `pip install -e '.[agent]'`."
            ) from exc

        options = ClaudeAgentOptions(**self._options_kwargs())
        chunks: list[str] = []
        async for message in query(prompt=prompt, options=options):
            text = _message_text(message)
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()

    def complete_sync(self, prompt: str) -> str:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.complete(prompt))
        raise RuntimeError("complete_sync cannot be called from an active event loop; use complete().")

    def _options_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"system_prompt": self.config.system_prompt}
        if self.config.allowed_tools:
            kwargs["allowed_tools"] = self.config.allowed_tools
        if self.config.disallowed_tools:
            kwargs["disallowed_tools"] = self.config.disallowed_tools
        if self.config.permission_mode is not None:
            kwargs["permission_mode"] = self.config.permission_mode
        if self.config.max_turns is not None:
            kwargs["max_turns"] = self.config.max_turns
        if self.config.cwd is not None:
            kwargs["cwd"] = str(self.config.cwd)
        if self.config.model is not None:
            kwargs["model"] = self.config.model
        return kwargs


class ClaudeCodeRSIAgent:
    """RSIAgent implementation scaffold backed by Claude Code SDK."""

    def __init__(self, client: ClaudeCodeClient | None = None, *, config: ClaudeCodeAgentConfig | None = None):
        self.client = client or ClaudeCodeClient(config or default_rsi_agent_config())

    def chat(
        self,
        message: str,
        *,
        run_id: str | None = None,
        controller: object,
        memory: object,
        workflow_spec: dict[str, Any],
    ) -> AgentResponse:
        run_state = None
        memory_context: MemoryContext = {}
        if run_id and hasattr(controller, "get_status"):
            run_state = controller.get_status(run_id)
        if hasattr(memory, "get_context"):
            memory_context = memory.get_context(run_id=run_id)
        prompt = _rsi_chat_prompt(
            message=message,
            run_id=run_id,
            run_state=_to_jsonable(run_state),
            memory_context=memory_context,
            workflow_spec=workflow_spec,
        )
        response_text = self.client.complete_sync(prompt)
        return _agent_response_from_text(response_text)

    def propose_next_action(
        self,
        *,
        run_state: "RunState",
        memory_context: MemoryContext,
        workflow_spec: dict[str, Any],
    ) -> Action:
        prompt = _rsi_next_action_prompt(
            run_state=run_state.to_dict(),
            memory_context=memory_context,
            workflow_spec=workflow_spec,
        )
        response_text = self.client.complete_sync(prompt)
        response = _agent_response_from_text(response_text)
        if not response.actions:
            raise ValueError("ClaudeCodeRSIAgent did not return any action.")
        return response.actions[0]

    def explain(
        self,
        *,
        run_id: str,
        controller: object,
        memory: object,
        question: str,
    ) -> str:
        run_state = controller.get_status(run_id) if hasattr(controller, "get_status") else None
        memory_context = memory.get_context(run_id=run_id) if hasattr(memory, "get_context") else {}
        prompt = _rsi_explain_prompt(
            question=question,
            run_id=run_id,
            run_state=_to_jsonable(run_state),
            memory_context=memory_context,
        )
        return self.client.complete_sync(prompt)


def default_rsi_agent_config() -> ClaudeCodeAgentConfig:
    return ClaudeCodeAgentConfig(
        system_prompt=(
            "You are RSIAgent, the user-facing coordinator for AReaL-RSI. "
            "Explain system state, propose structured actions, and ask for "
            "confirmation before expensive or irreversible operations. Return "
            "machine-readable JSON when asked for actions."
        ),
        allowed_tools=[],
        max_turns=2,
    )


def _rsi_chat_prompt(
    *,
    message: str,
    run_id: str | None,
    run_state: Any,
    memory_context: MemoryContext,
    workflow_spec: dict[str, Any],
) -> str:
    return (
        "Convert the user request into a concise reply and structured actions.\n"
        "Return exactly one JSON object with keys: reply, actions, requires_confirmation, metadata.\n"
        "Each action must have keys: type, args, requires_confirmation, metadata.\n\n"
        f"User message:\n{message}\n\n"
        f"run_id:\n{run_id}\n\n"
        f"run_state:\n{json.dumps(run_state, ensure_ascii=False, default=repr)}\n\n"
        f"memory_context:\n{json.dumps(memory_context, ensure_ascii=False, default=repr)}\n\n"
        f"workflow_spec:\n{json.dumps(workflow_spec, ensure_ascii=False, default=repr)}"
    )


def _rsi_next_action_prompt(
    *,
    run_state: dict[str, Any],
    memory_context: MemoryContext,
    workflow_spec: dict[str, Any],
) -> str:
    return (
        "Propose the next AReaL-RSI action.\n"
        "Return exactly one JSON object with keys: reply, actions, requires_confirmation, metadata.\n"
        "The actions list must contain exactly one action.\n\n"
        f"run_state:\n{json.dumps(run_state, ensure_ascii=False, default=repr)}\n\n"
        f"memory_context:\n{json.dumps(memory_context, ensure_ascii=False, default=repr)}\n\n"
        f"workflow_spec:\n{json.dumps(workflow_spec, ensure_ascii=False, default=repr)}"
    )


def _rsi_explain_prompt(
    *,
    question: str,
    run_id: str,
    run_state: Any,
    memory_context: MemoryContext,
) -> str:
    return (
        "Answer the user's question about this AReaL-RSI run. Be concise and cite run state or memory evidence.\n\n"
        f"question:\n{question}\n\n"
        f"run_id:\n{run_id}\n\n"
        f"run_state:\n{json.dumps(run_state, ensure_ascii=False, default=repr)}\n\n"
        f"memory_context:\n{json.dumps(memory_context, ensure_ascii=False, default=repr)}"
    )


def _agent_response_from_text(text: str) -> AgentResponse:
    payload = _extract_json_object(text)
    actions = [
        Action(
            type=action["type"],
            args=action.get("args", {}),
            requires_confirmation=bool(action.get("requires_confirmation", False)),
            metadata=action.get("metadata", {}),
        )
        for action in payload.get("actions", [])
    ]
    return AgentResponse(
        reply=str(payload.get("reply", "")),
        actions=actions,
        requires_confirmation=bool(payload.get("requires_confirmation", any(a.requires_confirmation for a in actions))),
        metadata=payload.get("metadata", {}),
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"Claude response did not contain a JSON object: {text[:500]}")
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("Claude response JSON must be an object.")
    return payload


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    text = getattr(message, "text", None)
    if isinstance(text, str):
        return text
    return ""


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value
