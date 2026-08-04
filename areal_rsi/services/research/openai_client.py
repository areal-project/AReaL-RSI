"""Minimal OpenAI SDK wrapper for the OpenAI-backed ResearchAgent.

Extracted from the removed conversational RSIAgent package: the Researcher only
needs plain text completion plus a JSON extractor, so this keeps just that
surface (no action-tool / chat plumbing).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any


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


def _response_text_from_output(response: Any, *, allow_empty: bool = False) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
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
        if allow_empty:
            return ""
        raise ValueError("OpenAI response did not contain text output.")
    return text
