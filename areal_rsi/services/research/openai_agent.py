"""OpenAI SDK backed ResearchAgent scaffold."""

from __future__ import annotations

import json
from typing import Any

from areal_rsi.services.research.openai_client import (
    OpenAIAgentConfig,
    OpenAIClient,
    extract_json_object,
)
from areal_rsi.schemas.core import Dataset, MemoryContext, ModelRef, TrainRun, Traj


class OpenAIResearchAgent:
    """ResearchAgent implementation scaffold backed by the OpenAI SDK."""

    def __init__(self, client: OpenAIClient | None = None, *, config: OpenAIAgentConfig | None = None):
        self.client = client or OpenAIClient(config or default_research_agent_config())

    def generate_dataset(
        self,
        trajs: list[Traj],
        memory_context: MemoryContext,
        current_ckpt: str,
        *,
        envarena: object | None = None,
        model_ref: ModelRef | None = None,
        harness_config: dict[str, Any] | None = None,
        traj_filter: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> Dataset:
        prompt = (
            "Convert EnvArena trajectories into an AReaL-RSI dataset.\n"
            "Return exactly one JSON object with keys: dataset_id, examples, metadata.\n"
            "Each example must include: task, experience, source_traj_ids, metadata.\n\n"
            f"current_ckpt:\n{current_ckpt}\n\n"
            f"traj_filter:\n{json.dumps(traj_filter or {}, ensure_ascii=False, default=repr)}\n\n"
            f"harness_config:\n{json.dumps(harness_config or {}, ensure_ascii=False, default=repr)}\n\n"
            f"model_ref:\n{json.dumps(model_ref or {}, ensure_ascii=False, default=repr)}\n\n"
            f"memory_context:\n{json.dumps(memory_context, ensure_ascii=False, default=repr)}\n\n"
            f"trajs:\n{json.dumps(trajs, ensure_ascii=False, default=repr)}\n\n"
            f"config:\n{json.dumps(config or {}, ensure_ascii=False, default=repr)}"
        )
        response_text = self.client.complete_sync(prompt)
        payload = extract_json_object(response_text)
        if "examples" not in payload:
            raise ValueError("ResearchAgent response must include `examples`.")
        return payload

    def choose_ckpt(self, train_runs: list[TrainRun], memory_context: MemoryContext) -> str:
        prompt = (
            "Choose the next checkpoint for AReaL-RSI.\n"
            "Return exactly one JSON object: {\"ckpt\": \"...\", \"reason\": \"...\"}.\n\n"
            f"train_runs:\n{json.dumps(train_runs, ensure_ascii=False, default=repr)}\n\n"
            f"memory_context:\n{json.dumps(memory_context, ensure_ascii=False, default=repr)}"
        )
        response_text = self.client.complete_sync(prompt)
        payload = extract_json_object(response_text)
        if "ckpt" not in payload:
            raise ValueError("ResearchAgent checkpoint response must include `ckpt`.")
        return str(payload["ckpt"])


def default_research_agent_config(model: str | None = None) -> OpenAIAgentConfig:
    return OpenAIAgentConfig(
        model=model,
        system_prompt=(
            "You are ResearchAgent, a worker agent for AReaL-RSI. "
            "Analyze EnvArena trajectories, synthesize concise reusable "
            "experience, and emit structured datasets. Do not talk to the "
            "human directly; return machine-readable JSON."
        ),
    )
