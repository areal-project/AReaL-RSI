"""Unified commands keep status/final evaluation independent of a model session."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_problems import TextConfig, TextRecipe

from areal_rsi.api.research import ProblemBinding, SessionConfig
from areal_rsi.infra.artifacts import write_record
from areal_rsi.infra.research import ResearchSystem
from areal_rsi.ui.cli import run


def test_research_cli_waits_while_leader_fork_is_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fake_codex import FakeCodex, Json
    from test_research import eventually

    from areal_rsi.api.control import CandidateProposal
    from areal_rsi.api.research import CandidateStatus, SessionPhase

    async def execute() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        class SlowForkCodex(FakeCodex):
            async def request(self, method: str, params: Json, *, timeout: float = 60) -> Json:
                if method == "thread/fork":
                    entered.set()
                    await release.wait()
                return await super().request(method, params, timeout=timeout)

        monkeypatch.setattr("areal_rsi.infra.research.CodexClient", SlowForkCodex)
        system = ResearchSystem(tmp_path / "system")
        monkeypatch.setattr("areal_rsi.ui.cli.ResearchSystem", lambda root: system)
        monkeypatch.setattr(
            "areal_rsi.ui.cli.from_environment",
            lambda workspace: SessionConfig(str(tmp_path), auto_learn=False),
        )
        config = tmp_path / "config.json"
        objective = tmp_path / "objective.txt"
        objective.write_text("Improve text and hand off to Leader")
        write_record(config, TextConfig())
        recipe = tmp_path / "recipe.json"
        write_record(recipe, TextRecipe("correct"))
        args = argparse.Namespace(
            command="research",
            data_root=system.root,
            resume=None,
            problem="test_problems:TextProblem",
            config=config,
            objective=objective,
            workspace=tmp_path,
        )
        task = asyncio.create_task(run(args))
        try:
            await eventually(lambda: bool(system.live))
            run_id = next(iter(system.live))
            await eventually(lambda: bool(system.detail(run_id).active_turn_id))
            client = SlowForkCodex.instances[-1]
            assert (
                await client.tool(
                    1,
                    "rsi_set_task",
                    {
                        "objective": "Improve text",
                        "instructions": "Exact text",
                        "acceptance": ["Verified correct text"],
                    },
                )
            )["success"]
            assert (await client.tool(2, "rsi_start_goal", {}))["success"]
            await client.finish("Handing off")
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.sleep(0.05)
            assert not task.done(), "Pending Leader fork is not CLI completion"
            release.set()
            await eventually(
                lambda: (
                    system.detail(run_id).phase == SessionPhase.LEADER
                    and bool(system.detail(run_id).active_turn_id)
                )
            )
            await system.submit_candidate(
                run_id, CandidateProposal("r1", "base", "Exact text", str(recipe))
            )
            await eventually(
                lambda: system.candidate_status(run_id, "r1")[0].status == CandidateStatus.COMPLETED
            )
            system.select_candidate(run_id, "r1")
            assert (
                await client.tool(
                    3,
                    "rsi_update_goal",
                    {
                        "status": "completed",
                        "summary": "Verified",
                        "evidence": ["Candidate r1"],
                    },
                )
            )["success"]
            await client.finish("Complete")
            assert await asyncio.wait_for(task, 5) == 0
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await system.close()

    asyncio.run(execute())


def test_status_and_final_evaluation_do_not_open_codex(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def execute() -> None:
        system = ResearchSystem(tmp_path / "system")
        config = write_record(tmp_path / "config.json", TextConfig())
        run_id = await system.create(
            SessionConfig(str(tmp_path)),
            problem=ProblemBinding("test_problems:TextProblem", config),
        )
        await system.prepare_problem(run_id)
        problem = system._problem(run_id)
        recipe = tmp_path / "recipe.json"
        write_record(recipe, TextRecipe("correct"))
        problem.create_candidate("r1", "base", "Exact answer", recipe)
        await problem.evaluate("r1")
        problem.select("r1")
        args = argparse.Namespace(command="status", data_root=system.root, resume=run_id)
        assert await run(args) == 0
        output = json.loads(capsys.readouterr().out)
        assert output["session"]["thread_id"] == ""
        assert len(output["candidates"]) == 2
        args.command, args.candidate = "finalize", "r1"
        assert await run(args) == 0
        assert json.loads(capsys.readouterr().out)["target_met"]
        assert not system.detail(run_id).thread_id
        await system.close()

    asyncio.run(execute())


def test_research_help_lists_standard_inputs_without_loading_a_task(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "areal_rsi", "research", "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    for option in ("--problem", "--config", "--objective", "--resume", "--root"):
        assert option in result.stdout
