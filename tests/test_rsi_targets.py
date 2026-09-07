import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from areal_rsi.agents.rsi import DefaultRsiAgent
from areal_rsi.api.base import EvolutionTarget, ExperimentPlan, ModelVersion, TrajectoryRef
from areal_rsi.base.storage import FileContentStore, now
from areal_rsi.modules.runtime import DshAgentRuntime, DshSession


class RsiTargetTest(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_artifact_target_and_unchanged_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            content = FileContentStore(Path(temporary))
            model = ModelVersion("model", "fixture", await content.put_text("model"), now())
            session = Mock(spec=DshSession)
            session.send = AsyncMock(return_value='{"plan":{"goal":"改进产物","max_iterations":2}}')
            session.finish = AsyncMock(return_value=TrajectoryRef("trace"))
            runtime = Mock(spec=DshAgentRuntime)
            runtime.content = content
            runtime.open = AsyncMock(return_value=session)
            artifact = DefaultRsiAgent(
                runtime,
                model,
                "baseline",
                "user-artifact-benchmark",
                workflow_name="artifact-evolution",
                evolution_target=EvolutionTarget.ARTIFACT,
            )
            plan = await artifact.chat("开始两轮实验")
            assert isinstance(plan, ExperimentPlan)
            self.assertEqual(plan.target, EvolutionTarget.ARTIFACT)
            self.assertEqual(plan.workflow, "artifact-evolution")
            default = DefaultRsiAgent(runtime, model, "baseline", "arithmetic-v1")
            plan = await default.chat("开始两轮实验")
            assert isinstance(plan, ExperimentPlan)
            self.assertEqual(plan.target, EvolutionTarget.HARNESS)
