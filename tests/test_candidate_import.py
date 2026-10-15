"""Lossless import of frozen research products."""

import asyncio
from pathlib import Path

from areal_rsi.api.interfaces import ToolCall
from areal_rsi.api.research import TaskBrief
from areal_rsi.infra.coordination.coordinator import Coordinator
from areal_rsi.infra.coordination.tools.actions import ResearchActions


def test_import_preserves_binary_products_and_does_not_overwrite_work(tmp_path: Path) -> None:
    async def scenario() -> None:
        c = Coordinator.create(tmp_path, TaskBrief("组合数据产物", ""))
        c.start()
        source = tmp_path / "source"
        source.mkdir()
        body = b"\x00\xff\x01\xfe"
        (source / "data.bin").write_bytes(body)
        candidate = await c.store.snapshot(source, agent_id="rsi", description="数据")
        actions = ResearchActions(c)
        agent = c.spawn(key="combine", direction="组合产物", instruction="复用数据")["agent_id"]
        handler = actions.handler(agent)
        args = {"candidate_id": candidate.candidate_id, "destination": "imports/data"}
        result = await handler(ToolCall("import_candidate", args, "copy"))
        target = Path(result.output["directory"])
        assert (target / "data.bin").read_bytes() == body
        assert any(
            candidate.candidate_id in ref.uri for ref in c.actor(agent).iterations[-1].references
        )
        replay = await handler(ToolCall("import_candidate", args, "copy-again"))
        assert replay.output == result.output
        (target / "data.bin").write_bytes(b"local edits")
        refused = await handler(ToolCall("import_candidate", args, "do-not-overwrite"))
        assert "error" in refused.output
        assert (target / "data.bin").read_bytes() == b"local edits"
        escape = await handler(
            ToolCall("import_candidate", {**args, "destination": "../escape"}, "escape")
        )
        assert "error" in escape.output
        assert (source / "data.bin").read_bytes() == body

    asyncio.run(scenario())
