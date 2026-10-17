"""Exercise the real cached-tensor HTTP boundary in the AReaL runtime image."""

from __future__ import annotations

import importlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("torch", reason="AReaL tensor tests run in the Slurm runtime image")

agent_module = importlib.import_module("examples.search.agent")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corrupt",
    [
        None,
        "mask",
        "logprobs",
        "versions",
        "prompt",
        "missing",
        "attention",
        "reward",
        "key",
        "overflow",
    ],
)
async def test_cached_export_audit_accepts_zero_and_rejects_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corrupt: str | None
) -> None:
    ModelResponse = importlib.import_module("areal.api").ModelResponse
    proxy = importlib.import_module("areal.experimental.openai.proxy.server")
    InteractionWithTokenLogpReward = importlib.import_module(
        "areal.experimental.openai.types"
    ).InteractionWithTokenLogpReward
    WorkflowContractError = importlib.import_module(
        "areal.infra.workflow_executor"
    ).WorkflowContractError

    ids = [11, 12]
    interaction = InteractionWithTokenLogpReward(
        reward=0.0,
        model_response=ModelResponse(
            input_tokens=ids,
            output_tokens=[13, 14],
            output_logprobs=[-0.1, -0.2],
            output_versions=[0, 0],
        ),
        output_message_list=[],
    )
    interaction.interaction_id = "completion-1"
    interaction.rollout_reward = 0.0
    interaction.to_tensor_dict()
    exported = proxy.deserialize_interactions(
        proxy.serialize_interactions({"completion-1": interaction})
    )
    cached = exported["completion-1"]
    assert cached.model_response is None
    tensor = cached.to_tensor_dict()
    if corrupt == "mask":
        tensor["loss_mask"][0, 0] = 1
    elif corrupt == "logprobs":
        tensor["logprobs"][0, -1] = float("nan")
    elif corrupt == "versions":
        tensor["versions"][0, -1] = -1
    elif corrupt == "prompt":
        tensor["input_ids"][0, 0] = 99
    elif corrupt == "attention":
        tensor["attention_mask"][0, -1] = False
    elif corrupt == "reward":
        tensor["rewards"][0] = 1.0
    elif corrupt == "key":
        del tensor["versions"]
    calls: tuple[Any, ...] = (
        agent_module.ModelCall(
            2, agent_module.token_digest(ids), ({"role": "user", "content": "question"},)
        ),
    )
    if corrupt in {"missing", "overflow"}:
        calls = calls + calls
    episode = agent_module.Episode(
        "q1",
        0,
        0.0,
        "Exact Answer: wrong",
        1,
        (),
        1.0,
        (),
        calls,
        "BadRequestError" if corrupt == "overflow" else "",
    )
    trace = tmp_path / "episode.json"
    trace.write_text(json.dumps(asdict(episode)))
    agent = agent_module.BrowseCompTrainAgent("unused", "unused", str(tmp_path), "unused")
    monkeypatch.setattr(agent, "_path", lambda data: trace)
    if corrupt and corrupt != "overflow":
        with pytest.raises(WorkflowContractError):
            await agent.audit_interactions({"question_id": "q1"}, exported)
        assert not trace.with_suffix(".audit.json").exists()
    else:
        await agent.audit_interactions({"question_id": "q1"}, exported)
        audit = json.loads(trace.with_suffix(".audit.json").read_text())["interactions"][0]
        assert audit["reward"] == audit["rollout_reward"] == 0.0
        assert audit["input_tokens"] == audit["output_tokens"] == 2
        assert audit["mask_validated"] and audit["hf_template_validated"]


@pytest.mark.asyncio
async def test_failed_agent_persists_partial_episode_and_rejects_tool_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Native overflow recovery can audit preceding turns; tool failures stay failures."""
    agent = agent_module.BrowseCompTrainAgent("unused", "unused", str(tmp_path), "unused")
    failure = agent_module.SearchInfrastructureError("test tool outage")
    episode = agent_module.Episode("q1", 0, 0.0, "", 1, (), 1.0, (), (), type(failure).__name__)
    trace = tmp_path / "failed.json"
    monkeypatch.setattr(agent, "_path", lambda data: trace)

    def fail(*args: object) -> None:
        raise agent_module.EpisodeRunError(episode, failure)

    monkeypatch.setattr(agent, "_run_sync", fail)
    with pytest.raises(agent_module.SearchInfrastructureError):
        await agent.run({"question_id": "q1"}, base_url="unused", api_key="test-runtime-only")
    record = json.loads(trace.read_text())
    assert record["failure_type"] == "SearchInfrastructureError"
    assert record["reward"] == 0.0
    assert "test-runtime-only" not in trace.read_text()
    assert agent.classify_proxy_failure(failure, context_overflow=True) == "system_failure_reject"
