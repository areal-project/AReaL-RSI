"""OpenAI SDK backed agent scaffolding."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from areal_rsi.agent.action_tools import (
    ActionToolCall,
    action_from_tool_call,
    reply_for_actions,
    rsi_action_tools,
)
from areal_rsi.schemas.actions import Action, AgentResponse
from areal_rsi.schemas.core import MemoryContext

if TYPE_CHECKING:
    from areal_rsi.controller.run_state import RunState


@dataclass(slots=True)
class OpenAIAgentConfig:
    """Configuration passed to OpenAI Responses API calls."""

    system_prompt: str
    model: str | None = None
    max_output_tokens: int = 2048
    temperature: float | None = None

    @property
    def resolved_model(self) -> str:
        return self.model or os.environ.get("OPENAI_MODEL", "gpt-5-mini")


class OpenAIClient:
    """Small lazy wrapper around the official OpenAI Python SDK."""

    def __init__(self, config: OpenAIAgentConfig):
        self.config = config

    def complete_sync(self, prompt: str) -> str:
        client = _openai_client()
        kwargs: dict[str, Any] = {
            "model": self.config.resolved_model,
            "instructions": self.config.system_prompt,
            "input": prompt,
            "max_output_tokens": self.config.max_output_tokens,
        }
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        response = client.responses.create(**kwargs)
        text = getattr(response, "output_text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
        return _response_text_from_output(response)

    def tool_calls_sync(
        self,
        prompt: str,
        *,
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
        max_tool_calls: int = 1,
    ) -> list[ActionToolCall]:
        """Return function tool calls from the Responses API."""

        client = _openai_client()
        kwargs: dict[str, Any] = {
            "model": self.config.resolved_model,
            "instructions": self.config.system_prompt,
            "input": prompt,
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": False,
            "max_tool_calls": max_tool_calls,
            "max_output_tokens": self.config.max_output_tokens,
        }
        if self.config.temperature is not None:
            kwargs["temperature"] = self.config.temperature
        response = client.responses.create(**kwargs)
        return _tool_calls_from_response(response)


class OpenAIRSIAgent:
    """RSIAgent implementation scaffold backed by the OpenAI SDK."""

    def __init__(self, client: OpenAIClient | None = None, *, config: OpenAIAgentConfig | None = None):
        self.client = client or OpenAIClient(config or default_rsi_agent_config())

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
        tool_calls = self.client.tool_calls_sync(prompt, tools=rsi_action_tools())
        if not tool_calls:
            raise ValueError("OpenAIRSIAgent did not return an action tool call.")
        actions = [action_from_tool_call(tool_call) for tool_call in tool_calls]
        return AgentResponse(
            reply=reply_for_actions(actions),
            actions=actions,
            requires_confirmation=any(action.requires_confirmation for action in actions),
            metadata={"tool_call_count": len(tool_calls)},
        )

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
        tool_calls = self.client.tool_calls_sync(
            prompt,
            tools=rsi_action_tools(),
            tool_choice="required",
        )
        if not tool_calls:
            raise ValueError("OpenAIRSIAgent did not return an action tool call.")
        return action_from_tool_call(tool_calls[0])

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


def default_rsi_agent_config(model: str | None = None) -> OpenAIAgentConfig:
    return OpenAIAgentConfig(
        model=model,
        system_prompt=(
            "You are RSIAgent, the user-facing coordinator for AReaL-RSI. "
            "Explain system state and choose the appropriate action tool when "
            "the user asks to run, inspect, resume, stop, or query an RSI run."
        ),
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
        "Choose exactly one RSI action tool for the user request.\n"
        "Use start_loop for goals that ask to improve a checkpoint or run an RSI loop.\n"
        "Use get_status for brief progress queries, inspect_run for detailed run inspection, "
        "resume_run to continue a stopped run, and stop_run to stop a run.\n"
        "Humans describe goals and constraints; workflow steps such as eval, train, and holdout_eval "
        "are internal and must not be represented as user actions.\n\n"
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
        "Choose exactly one RSI action tool for the next step.\n"
        "Workflow steps such as eval, train, and holdout_eval are internal and must not be "
        "represented as user actions.\n\n"
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


def extract_json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"OpenAI response did not contain a JSON object: {text[:500]}")
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("OpenAI response JSON must be an object.")
    return payload


def _openai_client() -> Any:
    try:
        from openai import OpenAI, OpenAIError
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI agent support requires the optional dependency `openai`. "
            "Install with `pip install -e '.[agent]'`."
        ) from exc

    try:
        return OpenAI()
    except OpenAIError as exc:
        raise RuntimeError(
            "OpenAI agent support requires OPENAI_API_KEY to be set in the environment "
            "or an OpenAI client configuration accepted by the SDK."
        ) from exc


def _tool_calls_from_response(response: Any) -> list[ActionToolCall]:
    output = getattr(response, "output", None)
    if output is None and isinstance(response, dict):
        output = response.get("output")
    calls: list[ActionToolCall] = []
    if not isinstance(output, list):
        return calls
    for item in output:
        item_type = _field(item, "type")
        if item_type != "function_call":
            continue
        name = _field(item, "name")
        arguments = _field(item, "arguments")
        if not isinstance(name, str):
            raise ValueError("OpenAI function call did not include a tool name.")
        if not isinstance(arguments, str):
            raise ValueError(f"OpenAI function call {name} did not include JSON arguments.")
        payload = json.loads(arguments)
        if not isinstance(payload, dict):
            raise ValueError(f"OpenAI function call {name} arguments must be a JSON object.")
        calls.append(ActionToolCall(name=name, arguments=payload))
    return calls


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _response_text_from_output(response: Any) -> str:
    output = getattr(response, "output", None)
    chunks: list[str] = []
    if isinstance(output, list):
        for item in output:
            content = getattr(item, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if isinstance(text, str):
                        chunks.append(text)
    text = "\n".join(chunks).strip()
    if not text:
        raise ValueError("OpenAI response did not contain text output.")
    return text


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value
