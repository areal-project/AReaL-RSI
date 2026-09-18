"""Raw trajectory access through the same tool used by research agents."""

import asyncio
import sys
from pathlib import Path

import pytest

from areal_rsi.api.research import TaskBrief
from areal_rsi.coordination.actions import ResearchActions
from areal_rsi.coordination.coordinator import Coordinator
from areal_rsi.coordination.jobs import ResearchExecution
from areal_rsi.execution import ApprovedCommand
from areal_rsi.modules.responses import ToolCall


@pytest.mark.parametrize("exit_code", [0, 1])
def test_large_original_output_can_be_read_after_success_or_failure(
    tmp_path: Path, exit_code: int
) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("Research", "Inspect trajectories"))
        c.start()
        command = ApprovedCommand(
            "run",
            (
                sys.executable,
                "-c",
                "import pathlib,sys; "
                "pathlib.Path(sys.argv[1], 'trace.txt').write_text('轨' * 3000000 + 'END'); "
                "sys.exit(int(sys.argv[2]))",
                "{output}",
                str(exit_code),
            ),
            ("output",),
            output_subdir=".",
        )
        runner = ResearchExecution(ResearchActions(c), commands={"run": command})
        try:
            submitted = await runner.submit(
                "rsi",
                ToolCall(
                    "run_job",
                    {
                        "command_name": "run",
                        "parameters_json": "{}",
                    },
                    "run",
                ),
            )
            async with asyncio.timeout(10):
                while await runner.poll():
                    await asyncio.sleep(0.02)
            job_id = submitted.output["job_id"]
            for offset, content, next_offset in [
                (0, "轨" * 16000, 16000),
                (2999999, "轨END", None),
            ]:
                result = await runner.handler("rsi")(
                    ToolCall(
                        "read_job_output_file",
                        {
                            "job_id": job_id,
                            "path": "trace.txt",
                            "offset": offset,
                        },
                        str(offset),
                    )
                )
                assert result.output["content"] == content
                assert result.output["next_offset"] == next_offset
        finally:
            runner.close(cancel=True)

    asyncio.run(scenario())
