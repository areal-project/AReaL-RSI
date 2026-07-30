"""OpenAI SDK backed agent scaffolding."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
        try:
            from openai import OpenAI, OpenAIError
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI agent support requires the optional dependency `openai`. "
                "Install with `pip install -e '.[agent]'`."
            ) from exc

        try:
            client = OpenAI()
        except OpenAIError as exc:
            raise RuntimeError(
                "OpenAI agent support requires OPENAI_API_KEY to be set in the environment "
                "or an OpenAI client configuration accepted by the SDK."
            ) from exc
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
        response_text = self.client.complete_sync(prompt)
        return agent_response_from_text(response_text)

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
        response = agent_response_from_text(response_text)
        if not response.actions:
            raise ValueError("OpenAIRSIAgent did not return any action.")
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


def default_rsi_agent_config(model: str | None = None) -> OpenAIAgentConfig:
    return OpenAIAgentConfig(
        model=model,
        system_prompt=(
            "You are RSIAgent, the user-facing coordinator for AReaL-RSI. "
            "Explain system state, propose structured actions, and ask for "
            "confirmation before expensive or irreversible operations. Return "
            "machine-readable JSON when asked for actions."
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
        "Convert the user request into a concise reply and structured actions.\n"
        "Return exactly one JSON object with keys: reply, actions, requires_confirmation, metadata.\n"
        "Each action must have keys: type, args, requires_confirmation, metadata.\n"
        "Supported action types are: start_loop, get_status, inspect_run, resume_run, stop_run.\n"
        "Humans describe goals and constraints; workflow steps such as eval, train, and holdout_eval "
        "are internal and must not be returned as user actions.\n\n"
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


def agent_response_from_text(text: str) -> AgentResponse:
    payload = extract_json_object(text)
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
