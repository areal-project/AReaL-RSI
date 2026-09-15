from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from areal_rsi.adapters.harness_cases import evaluate, prepare
from areal_rsi.api.base import SolutionVersion, Task, TaskResult, TrajectoryRef


def test_math_uses_actual_verifier_and_rejects_out_of_scope_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("RSI_TARGET_MODEL", "test-target")
        monkeypatch.setenv("RSI_TARGET_BASE_URL", "https://example.test/v1")
        config = await prepare(
            "arithmetic",
            tmp_path / "research.json",
            dataset=tmp_path,
            remote_config=tmp_path / "unused",
        )
        from areal_rsi.modules.harness import LocalHarnessRuntime

        async def run(
            self: LocalHarnessRuntime, solution: SolutionVersion, task: Task
        ) -> TaskResult:
            # Deliberately wrong answer: exercise the unchanged mathematical verifier.
            result = await self.content.put_json(
                {
                    "task_id": task.task_id,
                    "solution_id": solution.solution_id,
                    "response": "#### -999",
                }
            )
            return TaskResult(result, TrajectoryRef("fixture"))

        monkeypatch.setattr(LocalHarnessRuntime, "run", run)
        result = await evaluate(
            tmp_path / "operator.json", Path(config.seed_dir or ""), tmp_path / "output", "train"
        )
        assert result["score"] == 0
        assert result["metrics"]["tasks"] == 3
        assert len(json.loads((tmp_path / "output/public/feedback.json").read_text())["tasks"]) == 3
        shutil.copytree(tmp_path / "seed", tmp_path / "candidate")
        (tmp_path / "candidate/mcp.json").write_text("{}")
        with pytest.raises(ValueError, match=r"Only prompt\.md"):
            await evaluate(
                tmp_path / "operator.json", tmp_path / "candidate", tmp_path / "bad", "train"
            )

    asyncio.run(scenario())


def test_operator_identity_is_checked_before_execution(tmp_path: Path) -> None:
    from areal_rsi.base.storage import write_json

    write_json(tmp_path / "operator.json", {"manifest_id": "wrong", "case": "arithmetic"})
    with pytest.raises(ValueError, match="identity changed"):
        asyncio.run(evaluate(tmp_path / "operator.json", tmp_path, tmp_path / "out", "train"))


def test_develop_publishes_only_public_feedback_with_private_runtime_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from areal_rsi.api.research import TaskBrief
    from areal_rsi.coordination.actions import ResearchActions
    from areal_rsi.coordination.coordinator import Coordinator
    from areal_rsi.coordination.jobs import ResearchExecution
    from areal_rsi.modules.responses import ToolCall

    async def scenario() -> None:
        monkeypatch.setenv("RSI_TARGET_MODEL", "test-target")
        monkeypatch.setenv("RSI_TARGET_BASE_URL", "https://example.test/v1")
        cfg = await prepare(
            "arithmetic",
            tmp_path / "setup/research.json",
            dataset=tmp_path,
            remote_config=tmp_path / "unused",
        )
        c = Coordinator.create(tmp_path / "data", TaskBrief("output boundary", ""))
        c.start()
        actions = ResearchActions(c)
        await actions.initialize_workspace("rsi")
        execution = ResearchExecution(
            actions, commands={"develop": cfg.commands[0]}, simulated=True, mock_duration=0
        )
        try:
            receipt = await execution.submit(
                "rsi",
                ToolCall(
                    "run_job",
                    {"command_name": "develop", "parameters_json": "{}", "candidate_id": ""},
                    "test-development",
                ),
            )
            job_id = receipt.output["job_id"]
            output = c.store.root / "execution-outputs" / job_id
            (output / "public").mkdir()
            (output / "public/feedback.json").write_text('{"score": 0.0}')
            (output / "private").mkdir()
            (output / "private/runtime-alias").symlink_to(tmp_path)
            for _ in range(20):
                await execution.poll()
                if execution.manager.get(job_id).terminal:
                    break
                await asyncio.sleep(0.01)
            response = await execution.handler("rsi")(
                ToolCall(
                    "read_job_output_file",
                    {"job_id": job_id, "path": "feedback.json", "offset": 0},
                    "read-public",
                )
            )
            assert json.loads(response.output["content"]) == {"score": 0.0}
            with pytest.raises(ValueError):
                await execution.handler("rsi")(
                    ToolCall(
                        "read_job_output_file",
                        {"job_id": job_id, "path": "../private/runtime-alias", "offset": 0},
                        "read-private",
                    )
                )
        finally:
            execution.close()

    asyncio.run(scenario())
