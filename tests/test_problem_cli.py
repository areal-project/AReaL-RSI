"""Unified commands keep status/final evaluation independent of a model session."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_problems import TextConfig, TextRecipe

from areal_rsi.api.research import AppConfig, ProblemBinding, SessionConfig
from areal_rsi.infra.session import SessionSystem
from areal_rsi.infra.storage.artifacts import write_record
from areal_rsi.ui.cli import run


@pytest.mark.parametrize(
    ("entrypoint", "config_type"),
    [
        ("examples.tir.problem:TIRProblem", "ExperimentConfig"),
        ("examples.search.problem:SearchProblem", "SearchProblemConfig"),
    ],
)
def test_console_script_loads_source_examples_with_documented_pythonpath(
    tmp_path: Path, entrypoint: str, config_type: str
) -> None:
    repository = Path(__file__).resolve().parents[1]
    config = tmp_path / "config.json"
    config.write_text("{}")
    objective = tmp_path / "objective.md"
    objective.write_text("Validate adapter loading without starting a model or GPU job")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("RSI_")}
    environment["PYTHONPATH"] = str(repository)
    result = subprocess.run(
        [
            str(Path(sys.executable).parent / "areal-rsi"),
            "research",
            "--problem",
            entrypoint,
            "--config",
            str(config),
            "--objective",
            str(objective),
            "--root",
            str(tmp_path / "state"),
            "--env-file",
            os.devnull,
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    # Reaching the adapter's schema proves import succeeded; invalid config prevents execution.
    assert result.returncode == 1
    assert f"Invalid {config_type}" in result.stderr
    assert "Cannot load research problem" not in result.stderr


@pytest.mark.parametrize("pending_interaction", [False, True])
def test_research_cli_resumes_paused_chat_and_pending_interaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pending_interaction: bool
) -> None:
    from fake_codex import FakeCodex
    from test_session import eventually

    from areal_rsi.api.research import SessionInput, SessionPhase, SessionStatus

    FakeCodex.instances.clear()
    FakeCodex.threads.clear()
    monkeypatch.setattr("areal_rsi.infra.coordination.runtime.CodexClient", FakeCodex)

    async def execute() -> None:
        system = SessionSystem(tmp_path / "system")
        monkeypatch.setattr("areal_rsi.ui.cli.SessionSystem", lambda root: system)
        config = write_record(tmp_path / "config.json", TextConfig())
        run_id = await system.create(
            SessionConfig(str(tmp_path), auto_learn=False),
            problem=ProblemBinding(
                "test_problems:TextProblem", config, objective="Resume the text task"
            ),
        )
        if pending_interaction:
            await system.prepare_problem(run_id)
        record = system.detail(run_id)
        record.status = SessionStatus.PAUSED
        if pending_interaction:
            record.next_phase = SessionPhase.INTERACTION
            record.pending_inputs.append(SessionInput("transition", "Resume the text task"))
        system.store.save(record)
        args = argparse.Namespace(
            command="research",
            data_root=system.root,
            resume=run_id,
            problem=None,
            config=None,
            objective=None,
            workspace=tmp_path,
        )
        task = asyncio.create_task(
            run(args, AppConfig(SessionConfig(str(tmp_path), auto_learn=False), str(system.root)))
        )
        try:
            await eventually(
                lambda: (
                    task.done()
                    or (
                        system.detail(run_id).phase == SessionPhase.INTERACTION
                        and bool(system.detail(run_id).active_turn_id)
                    )
                )
            )
            if task.done():
                await task
                pytest.fail("Research CLI exited before resuming the interaction")
            assert system.candidate_status(run_id, "base")[0].score == 0
            assert system.detail(run_id).next_phase is None
            problem = system._problem(run_id)
            assert len(list((problem.root / "execution").rglob("job.json"))) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await system.close()

    asyncio.run(execute())


def test_research_cli_waits_while_leader_fork_is_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fake_codex import FakeCodex, Json
    from test_session import eventually

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

        monkeypatch.setattr("areal_rsi.infra.coordination.runtime.CodexClient", SlowForkCodex)
        system = SessionSystem(tmp_path / "system")
        monkeypatch.setattr("areal_rsi.ui.cli.SessionSystem", lambda root: system)
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
        task = asyncio.create_task(
            run(args, AppConfig(SessionConfig(str(tmp_path), auto_learn=False), str(system.root)))
        )
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
        system = SessionSystem(tmp_path / "system")
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
        assert (
            await run(
                args, AppConfig(SessionConfig(str(tmp_path), auto_learn=False), str(system.root))
            )
            == 0
        )
        output = json.loads(capsys.readouterr().out)
        assert output["session"]["thread_id"] == ""
        assert len(output["candidates"]) == 2
        args.command, args.candidate = "finalize", "r1"
        assert (
            await run(
                args, AppConfig(SessionConfig(str(tmp_path), auto_learn=False), str(system.root))
            )
            == 0
        )
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
