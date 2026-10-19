"""A bound artifact task through the real app-server and an offline model endpoint."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from native_model import Json, NativeModel, function
from test_problems import TextConfig, TextRecipe

from areal_rsi.api.control import CandidateProposal, encode_record
from areal_rsi.api.research import ProblemBinding, SessionConfig, SessionStatus
from areal_rsi.infra.artifacts import write_record
from areal_rsi.infra.research import ResearchSystem


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex >= 0.162.1 is required")
def test_bound_problem_tools_and_background_feedback_cross_native_handoff(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.json"
    write_record(recipe, TextRecipe("correct"))
    proposal = CandidateProposal("r1", "base", "Write the exact text", str(recipe))
    steps: list[str | list[Json]] = [
        function(
            "rsi_set_task",
            {
                "objective": "Improve text",
                "instructions": "Keep business context",
                "acceptance": ["The exact text is verified"],
            },
        ),
        function("rsi_start_goal", {}),
        "Handing off",
        function("rsi_submit_candidate", encode_record(proposal)),
        "Candidate submitted",
        function("rsi_candidate_status", {"candidate_id": "r1"}),
        function("rsi_select_candidate", {"candidate_id": "r1"}),
        function(
            "rsi_update_goal",
            {
                "status": "completed",
                "summary": "Verified exact text",
                "evidence": ["Candidate r1 has verified validation and selection"],
            },
        ),
        "Research completed",
    ]
    model = NativeModel(lambda request: steps.pop(0) if steps else "Unexpected call")

    async def execute() -> None:
        system = ResearchSystem(tmp_path / "system")
        config = write_record(tmp_path / "config.json", TextConfig())
        try:
            run_id = await system.create(
                SessionConfig(
                    str(tmp_path), base_url=model.url, api_key_env="RSI_TEST_KEY", auto_learn=False
                ),
                problem=ProblemBinding("test_problems:TextProblem", config),
            )
            await system.prepare_problem(run_id)
            await system.enter_rsi(run_id, "Keep business context and improve the text")
            async with asyncio.timeout(45):
                while system.detail(run_id).status != SessionStatus.COMPLETED:
                    record = system.detail(run_id)
                    assert record.status not in {SessionStatus.FAILED, SessionStatus.WAITING}, (
                        record.last_error
                    )
                    await asyncio.sleep(0.05)
            assert not steps
            assert system.candidate_status(run_id, "r1")[0].score == 1
            assert system.detail(run_id).source_thread_id != system.detail(run_id).thread_id
            assert "Keep business context" in str(model.requests[-1])
        finally:
            await system.close()

    try:
        with patch.dict(os.environ, {"RSI_TEST_KEY": "offline-fixture"}):
            asyncio.run(execute())
    finally:
        model.close()
